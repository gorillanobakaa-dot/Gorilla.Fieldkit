"""Finished files: a Word document, a slide deck or a poster from the draft, and templates to write straight into.

    fieldkit academic build DRAFT.md [--as docx|pptx|poster] [--to FOLDER] [--contents]
    fieldkit academic template TYPE|all [FOLDER] [--guidance]
    fieldkit academic finish FILE.docx|.pptx

Everything is in the student's layout (layout.py): the country's page size and margins, the referencing style's
line spacing, headings and labels in the work language, the language Word spell-checks in. The pages carry the
Student ID and module, never the name; the file properties carry no name either.

build sorts the reference list alphabetically and puts it on a page of its own. A draft inside an assignment folder
lands in that folder's "4 - HERE IS YOUR WORK" folder; anywhere else, beside the draft. An existing file is never
overwritten: the new one gets " (2)", " (3)" ...

finish is for a file written by hand in Word or PowerPoint: it adds the Student ID header and page numbers if they
are missing, sets the page size and the language, empties the author fields, and reports comments and tracked
changes (they carry the author's name and must go before submitting). The words are never touched.
"""
import os

from . import layout
from . import profile as prof


def _who(student_id=None, module=None):
    p = prof.load() or {}
    return student_id or p.get("student_id") or "[Student ID]", module or "[Module Code]"


def _free(path):
    base, ext = os.path.splitext(path)
    n = 2
    while os.path.exists(path):
        path = "%s (%d)%s" % (base, n, ext)
        n += 1
    return path


def _module_of(folder):
    cfg = os.path.join(folder, "EXAM_CONFIG.md")
    if os.path.isfile(cfg):
        import re
        m = re.search(r"^\*\*Module:\*\*\s*(.+)$", open(cfg, encoding="utf-8").read(), flags=re.M)
        if m and m.group(1).strip() not in ("TBD", ""):
            return m.group(1).strip()
    return None


def _assignment_of(path):
    """The assignment folder a file sits in (the one holding EXAM_CONFIG.md), or None."""
    d = os.path.dirname(os.path.abspath(path))
    for _ in range(4):
        if os.path.isfile(os.path.join(d, "EXAM_CONFIG.md")):
            return d
        d = os.path.dirname(d)
    return None


def build(draft, kind=None, to=None, contents=False, student_id=None, module=None):
    if not draft.lower().endswith((".md", ".txt")):
        raise ValueError("build reads a markdown draft (.md); a Word or PowerPoint file: fieldkit academic finish FILE")
    if not os.path.isfile(draft):
        raise FileNotFoundError(f"no such file: {draft}")
    lay = layout.apply()
    folder = _assignment_of(draft)
    sid, mod = _who(student_id, module or (folder and _module_of(folder)))
    if to is None:
        if folder:
            from .workspace import Workspace
            to = Workspace(folder).output
        else:
            to = os.path.dirname(os.path.abspath(draft))
    kind = kind or "docx"
    stem = os.path.splitext(os.path.basename(draft))[0]
    ext = "docx" if kind == "docx" else "pptx"
    out = _free(os.path.join(to, f"{stem}.{ext}" if kind != "poster" else f"{stem} - poster.pptx"))
    if kind == "docx":
        from .build_word import build_word_document
        build_word_document(draft, out, sid, mod, verbose=False, contents_page=contents)
    elif kind == "pptx":
        from .build_pptx import build_presentation_from_markdown
        build_presentation_from_markdown(draft, out, sid, mod, verbose=False)
    elif kind == "poster":
        from .build_pptx import build_poster_from_markdown
        build_poster_from_markdown(draft, out, sid, mod, verbose=False)
    else:
        raise ValueError(f"--as {kind!r}: docx, pptx or poster")
    return {"ok": True, "file": out, "page": lay["page"], "font": f"{lay['font']} {lay['size']}",
            "line_spacing": lay["line_spacing"], "language": lay["lang_tag"], "student_id": sid,
            "next": f'fieldkit academic refs "{out}"'}


def template(doc_type, folder=None, guidance=False, title=None, words=None, student_id=None, module=None):
    from .build_templates import build_all_templates, build_template
    from .document_types import resolve_type
    layout.apply()
    folder = folder or os.getcwd()
    target = folder
    if os.path.isfile(os.path.join(folder, "EXAM_CONFIG.md")):
        from .workspace import Workspace
        target = Workspace(folder).drafts
        module = module or _module_of(folder)
    sid, mod = _who(student_id, module)
    if doc_type == "all":
        paths = build_all_templates(target, sid, mod, verbose=False)
        return {"ok": True, "templates": len(paths), "folder": target, "next": "fieldkit academic types"}
    key = resolve_type(doc_type)
    if key is None:
        raise ValueError(f"unknown type {doc_type!r}: fieldkit academic types")
    from .build_templates import template_filename
    if os.path.exists(os.path.join(target, template_filename(key))):
        return {"ok": True, "status": "exists", "file": os.path.join(target, template_filename(key)),
                "next": "write in it; then fieldkit academic finish FILE"}
    path = build_template(key, target, title, sid, mod, words, verbose=False, guidance=guidance)
    return {"ok": True, "status": "created", "file": path, "next": "write in it; then fieldkit academic finish FILE"}


def finish(path, student_id=None, module=None):
    if not path.lower().endswith((".docx", ".pptx")):
        raise ValueError("finish takes a .docx or .pptx")
    if not os.path.isfile(path):
        raise FileNotFoundError(f"no such file: {path}")
    lay = layout.apply()
    folder = _assignment_of(path)
    sid, mod = _who(student_id, module or (folder and _module_of(folder)))
    from .office_finalise import finalise, review_marks
    added = []
    marks, authors = 0, set()
    if path.lower().endswith(".docx"):
        from .build_word import add_page_furniture
        added = add_page_furniture(path, sid, mod)
        marks, authors = review_marks(path)
    finalise(path)
    out = {"ok": not marks, "file": path, "added": added or ["nothing: header, page numbers and page size were there"],
           "language": lay["lang_tag"], "author_fields": "emptied"}
    if marks:
        out["comments_or_tracked_changes"] = marks
        out["fix"] = "accept or reject every tracked change and delete every comment in Word (Review tab), then finish again"
        out["next"] = f'fieldkit academic finish "{path}"'
    else:
        out["next"] = f'fieldkit academic refs "{path}"'
    if authors:
        out["names_in_review_marks"] = len(authors)
    return out
