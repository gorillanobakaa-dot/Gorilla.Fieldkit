"""
SETUP EXAM - Creates an assignment folder, ready for the student to fill.

    Exam_1/
      1 - DROP YOUR STUDY MATERIALS HERE/
            Assignment brief/
            My notes/
      2 - Research notes/
      3 - Drafts/                       skeleton + Word/PowerPoint template
      4 - HERE IS YOUR WORK, <name>/    finished work lands here
      EXAM_CONFIG.md

The folder layout and the student's name and Student ID come from
workspace.py and student_profile.json, so nothing here is specific to one
student.

With --type, it also writes, into the drafts folder:
  * skeleton.md - the section headings for that type, each with its
    guidance and suggested length, for drafting in markdown, and
  * "TEMPLATE - <type>.docx" (or .pptx for a poster or presentation) - the
    same structure as a ready-formatted Word or PowerPoint file, for writing
    in directly.

Re-running is safe. An existing EXAM_CONFIG.md is updated in place rather
than rewritten, so ticked checklist items survive, and an existing skeleton or
template is never overwritten.

Easiest route: create the folder, drop the brief into "Assignment brief",
then let read_brief.py propose the type and word count:

    python setup_exam.py "<work folder>/Exam_1"
    python read_brief.py "<work folder>/Exam_1" --apply

Or set the type directly:

    python setup_exam.py "<exam_folder>" --type reflective-essay --words 1500

Options:
    --module "HWSC4005"        module code
    --deadline "28 Sept 2026"  submission deadline
    --type report              document type (see --list-types)
    --title "Essay title"      title for the skeleton and template
    --student-id 12345678      overrides the profile
    --words 2500               word-count target
    --no-template              skip the Word/PowerPoint template
    --list-types               list the document types and exit
"""
import os
import re
import sys
import argparse
from datetime import datetime

HARNESS_DIR = os.path.dirname(os.path.abspath(__file__))
if HARNESS_DIR not in sys.path:
    sys.path.insert(0, HARNESS_DIR)

from .workspace import Workspace, load_profile

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


# --------------------------------------------------------------------------
# EXAM_CONFIG.md
# --------------------------------------------------------------------------

def _config_fields(module, deadline, canonical, target_words, upload=None):
    return [
        ("Module", module or "TBD"),
        ("Deadline", deadline or "TBD"),
        ("Upload", upload or "TBD"),
        ("Document type", canonical or "TBD"),
        ("Word count target", format(target_words, ",") if target_words else "TBD"),
    ]


def _new_config(ws, module, deadline, canonical, target_words, upload=None,
                style_label="your referencing style",
                plagiarism_tool="Plagiarism check"):
    lines = ["# EXAM CONFIGURATION"]
    for name, value in _config_fields(module, deadline, canonical,
                                      target_words, upload):
        lines.append("**%s:** %s" % (name, value))
    lines += [
        "**Created:** %s" % datetime.now().strftime("%Y-%m-%d %H:%M"),
        "**Status:** Not Started",
        "",
        "<!-- run_pipeline.py reads 'Document type', 'Word count target' and",
        "     'Module' from this file, so keep them accurate. -->",
        "",
        "---",
        "",
        "## Source Materials Checklist",
        "- [ ] Assignment brief and marking rubric in `%s`"
        % os.path.relpath(ws.brief or ws.materials, ws.root),
        "- [ ] Module handbook, lecture slides and readings in `%s`"
        % os.path.relpath(ws.materials, ws.root),
        "- [ ] Own notes and preliminary work in `%s`"
        % os.path.relpath(ws.notes or ws.materials, ws.root),
        "- [ ] Every source confirmed readable by `read_sources.py`",
        "",
        "## Submission Requirements",
        "- [ ] File format confirmed (Word / PowerPoint)",
        "- [ ] Word count target confirmed against the brief",
        "- [ ] Referencing style confirmed (%s)" % style_label,
        "- [ ] Anonymous marking requirement checked",
        "- [ ] %s upload date noted" % plagiarism_tool,
        "",
        "## AI Workflow Status",
        "- [ ] Source materials read and analysed",
        "- [ ] Structure planned against the document type",
        "- [ ] First draft written",
        "- [ ] Template guidance removed",
        "- [ ] Document-type structure check passed",
        "- [ ] Reference audit passed (bidirectional 1:1, alphabetical order)",
        "- [ ] External reference validation passed (identifiers resolve)",
        "- [ ] Plagiarism pre-check passed",
        "- [ ] Word count calibrated",
        "- [ ] Final document built and in `%s`"
        % os.path.relpath(ws.output, ws.root),
        "",
    ]
    return "\n".join(lines)


def update_config_fields(text, fields):
    """Replace '**Field:** value' lines in place; add any that are missing."""
    for name, value in fields:
        if value is None:
            continue
        pattern = r"(\*\*" + re.escape(name) + r":\*\*)[^\n]*"
        if re.search(pattern, text):
            text = re.sub(pattern, lambda m: m.group(1) + " " + str(value),
                          text, count=1)
        else:
            text = re.sub(r"(# EXAM CONFIGURATION\n)",
                          lambda m: m.group(1) + "**%s:** %s\n" % (name, value),
                          text, count=1)
    return text


def _append_section(text, heading, lines):
    """Add a '## heading' section unless one is already there."""
    if re.search(r"^##\s+" + re.escape(heading) + r"\s*$", text,
                 flags=re.MULTILINE):
        return text
    return text.rstrip("\n") + "\n\n## %s\n%s\n" % (heading, "\n".join(lines))


# --------------------------------------------------------------------------
# Set-up
# --------------------------------------------------------------------------

def setup_exam(exam_path, module_name=None, deadline=None, doc_type=None,
               title=None, student_id=None, target_words=None,
               learning_outcomes=None, templates=True, verbose=True,
               upload=None, style_label=None, plagiarism_tool=None):
    profile = load_profile()
    style_label = style_label or "your referencing style"
    plagiarism_tool = plagiarism_tool or "Plagiarism check"
    student_id = student_id or profile.get("student_id") or "[Student ID]"

    # A folder inside OneDrive looks local and is not. Say so now, while the
    # folder is still empty and easy to move.
    from .workspace import cloud_warning, local_work_root
    warning = cloud_warning(exam_path, "this assignment")
    if warning and verbose:
        print(warning)
        print("            For example: %s"
              % os.path.join(local_work_root(),
                             os.path.basename(os.path.normpath(exam_path))))
    # Workspace.create() below refuses a cloud folder (CloudSaveRefused), so
    # nothing is created there; the lines above say where to put it instead.

    ws = Workspace(exam_path, profile).create()

    canonical, spec = None, None
    if doc_type:
        from .document_types import DOCUMENT_TYPES, resolve_type, type_names
        canonical = resolve_type(doc_type)
        if canonical is None:
            print("ERROR: unknown document type %r." % doc_type)
            print("Known types:")
            for name in type_names():
                print("  %s" % name)
            return False
        spec = DOCUMENT_TYPES[canonical]
        if target_words is None:
            target_words = spec["typical_words"]

    if not deadline:
        try:
            from .extract_submission_date import find_submission_date_in_exam
            b_date, _, _ = find_submission_date_in_exam(ws)
            if b_date:
                deadline = b_date
        except Exception:
            pass

    # -- EXAM_CONFIG.md: create, or update in place ------------------------
    if os.path.isfile(ws.config):
        with open(ws.config, "r", encoding="utf-8") as f:
            config = f.read()
        config = update_config_fields(config, [
            ("Module", module_name), ("Deadline", deadline), ("Upload", upload),
            ("Document type", canonical),
            ("Word count target",
             format(target_words, ",") if target_words else None)])
    else:
        config = _new_config(ws, module_name, deadline, canonical,
                             target_words, upload, style_label,
                             plagiarism_tool)

    if spec:
        from .document_types import checklist_lines
        type_lines = checklist_lines(canonical)
        heading = type_lines[0][3:]          # "## Label Requirements" -> label
        config = _append_section(config, heading, type_lines[1:])

    if learning_outcomes:
        config = _append_section(
            config, "Learning Outcomes (from the brief)",
            ["Tick each one off only when the draft visibly addresses it.", ""]
            + ["- [ ] %s" % lo for lo in learning_outcomes])

    with open(ws.config, "w", encoding="utf-8") as f:
        f.write(config)

    # -- skeleton and template ---------------------------------------------
    skeleton_path, template_path = None, None
    if spec:
        from .document_types import build_skeleton
        skeleton_path = os.path.join(ws.drafts, "skeleton.md")
        if os.path.exists(skeleton_path):
            if verbose:
                print("Skeleton already exists, left untouched.")
            skeleton_path = None
        else:
            with open(skeleton_path, "w", encoding="utf-8") as f:
                f.write(build_skeleton(canonical, title or "[Title]",
                                       student_id, target_words))

        if templates:
            try:
                from .build_templates import build_template, template_filename
                candidate = os.path.join(ws.drafts, template_filename(canonical))
                if os.path.exists(candidate):
                    if verbose:
                        print("Template already exists, left untouched.")
                else:
                    template_path = build_template(
                        canonical, ws.drafts, title, student_id,
                        module_name or "[Module Code]", target_words,
                        verbose=False)
            except ImportError as e:
                print("[WARNING] Template not built - %s is not installed."
                      % e.name)

    if verbose:
        _report(ws, spec, target_words, skeleton_path, template_path,
                learning_outcomes)
    return True


def _report(ws, spec, target_words, skeleton_path, template_path, outcomes):
    print("Exam folder ready: %s" % ws.root)
    if ws.legacy:
        print("  (original folder layout - run workspace.py --migrate to "
              "update it)")
    for role, path in ws.folders():
        rel = os.path.relpath(path, ws.root)
        note = {"brief": "   <- the assignment brief and rubric",
                "notes": "   <- your own notes and preliminary work",
                "output": "   <- your finished work appears here"}.get(role, "")
        print("  %s%s" % (rel, note))
    print("  EXAM_CONFIG.md")
    if spec:
        print("")
        print("  Document type: %s" % spec["label"])
        if target_words:
            print("  Word target:   %s (the brief overrides this)"
                  % format(target_words, ","))
        if skeleton_path:
            print("  Skeleton:      %s" % os.path.relpath(skeleton_path, ws.root))
        if template_path:
            print("  Template:      %s" % os.path.relpath(template_path, ws.root))
        if outcomes:
            print("  Learning outcomes recorded: %d" % len(outcomes))
    elif ws.brief:
        print("")
        print("  Next: put the assignment brief in '%s', then run"
              % os.path.relpath(ws.brief, ws.root))
        print("        python read_brief.py \"%s\"" % ws.root)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Set up an assignment folder")
    parser.add_argument("exam_path", nargs="?",
                        help="Full path to the exam folder")
    parser.add_argument("--module", default=None, help="Module code")
    parser.add_argument("--deadline", default=None, help="Submission deadline")
    parser.add_argument("--type", dest="doc_type", default=None,
                        help="Document type (see --list-types)")
    parser.add_argument("--title", default=None,
                        help="Title for the skeleton and template")
    parser.add_argument("--student-id", default=None,
                        help="Overrides the student profile")
    parser.add_argument("--words", dest="target_words", type=int, default=None,
                        help="Word-count target")
    parser.add_argument("--no-template", action="store_true",
                        help="Skip the Word/PowerPoint template")
    parser.add_argument("--list-types", action="store_true",
                        help="List the document types and exit")
    args = parser.parse_args()

    if args.list_types:
        from .document_types import DOCUMENT_TYPES, type_names
        print("Document types (%d):" % len(DOCUMENT_TYPES))
        for name in type_names():
            print("  %s" % name)
        sys.exit(0)

    if not args.exam_path:
        parser.error("exam_path is required (or pass --list-types)")

    ok = setup_exam(args.exam_path, args.module, args.deadline, args.doc_type,
                    args.title, args.student_id, args.target_words,
                    templates=not args.no_template)
    sys.exit(0 if ok else 1)
