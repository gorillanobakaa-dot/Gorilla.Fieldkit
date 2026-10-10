"""
WORKSPACE - The one place that knows what an exam folder looks like, and who
the student is.

Every other script asks this module for paths, so a folder can be renamed in
exactly one place.

The layout a student sees:

    Exam_1/
      1 - DROP YOUR STUDY MATERIALS HERE/   lecture slides, readings, handbook
            Assignment brief/                the brief and the marking rubric
            My notes/                        the student's own notes and drafts
      2 - Research notes/                    the AI's working notes
      3 - Drafts/                            drafts, skeletons and templates
      4 - HERE IS YOUR WORK, <first name>/   finished .docx / .pptx files
      Extracted PDF.PowerPoint/              the text of every study file,
                                             page-referenced (see INDEX.md)
      EXAM_CONFIG.md

The numbers keep the folders in workflow order in Explorer. The first name in
the output folder comes from the student profile, so a future user gets their
own name; with no profile it reads "4 - HERE IS YOUR WORK".

Anonymous marking (Rule 3.4) is unaffected: the folder name lives only on the
student's own disk. The files inside it are still named and headed with the
Student ID alone, and run_pipeline.py still checks that.

Older exam folders created with the original layout (00_Source_Materials,
01_Research_Notes, 02_Drafts, 03_Final_Submission) are recognised and used
as they are. Nothing is moved unless migrate() is called with apply=True.

The student profile is fieldkit academic's (profile.py): local/academic/profile.json, asked for by
`fieldkit academic init`, never assumed. Generalised 2026-10-10 from the harness built for one UK student: the work
folder, the cloud rule and the messages are settings now, not his.

Usage:
    python workspace.py <exam_folder>                 show the layout in use
    python workspace.py <exam_folder> --migrate       preview moving an old
                                                      folder to the new layout
    python workspace.py <exam_folder> --migrate --apply
"""
import os
import re
import sys
import json
import shutil
import argparse

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HARNESS_DIR = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.expanduser("~")

MATERIALS = "1 - DROP YOUR STUDY MATERIALS HERE"
BRIEF = "Assignment brief"
NOTES = "My notes"
RESEARCH = "2 - Research notes"
DRAFTS = "3 - Drafts"
OUTPUT_PREFIX = "4 - HERE IS YOUR WORK"
# The text of every study file, extracted once, page by page, so a language
# model reads a small clean file instead of re-sending whole PDFs and decks.
EXTRACTED = "Extracted PDF.PowerPoint"

LEGACY = {
    "materials": "00_Source_Materials",
    "research": "01_Research_Notes",
    "drafts": "02_Drafts",
    "output": "03_Final_Submission",
}

FOLDER_HELP = {
    "materials": ("Drop ALL your study materials here: lecture slides, "
                  "readings, PDFs, the module handbook, photos of handouts. "
                  "Every common format is read, including scanned pages."),
    "brief": ("Put the assignment brief and the marking rubric here. The "
              "harness reads them to work out the document type, the word "
              "count and the learning outcomes."),
    "notes": ("Your own notes and preliminary work. They are read for "
              "content, and checked for overlap - if a note was copied from "
              "a source, the draft will be flagged here first."),
    "research": "The AI's working notes, literature summaries and analysis.",
    "drafts": ("Drafts, the section skeleton and the Word or PowerPoint "
               "template for this assignment."),
    "output": ("Your finished work, ready to submit. Files here carry your "
               "Student ID only, never your name (anonymous marking)."),
    "extracted": ("The text of every study file, extracted automatically, one "
                  "file per source with page and slide numbers. Start with "
                  "INDEX.md. Do not edit these files - they are rebuilt when a "
                  "source changes."),
}


# ==========================================================================
# Keeping the work on this computer, not in the cloud
# ==========================================================================
#
# Windows turns on "Known Folder Move" by default, which quietly makes the
# Documents library mean C:\Users\<name>\OneDrive\Documents. When the internet
# goes down, so does the work.
#
# The physical folder C:\Users\<name>\Documents is a separate, local folder
# that OneDrive never syncs once the library has moved, so the work lives
# there, addressed by its full path, never through the library.

CLOUD_FOLDERS = (
    ("onedrive", "OneDrive"),
    ("dropbox", "Dropbox"),
    ("google drive", "Google Drive"),
    ("googledrive", "Google Drive"),
    ("my drive", "Google Drive"),
    ("icloud drive", "iCloud Drive"),
    ("icloakdrive", "iCloud Drive"),
    ("box sync", "Box"),
)

WORK_ROOT_NAME = "Academic work"          # under Documents, unless the profile or fieldkit.local.json names a folder


def cloud_roots():
    """Every folder the OneDrive client syncs on this computer, from its own settings.

    The folder name alone is not enough: OneDrive's folder can be renamed or
    moved, and a junction can point into it from a path that looks local. So
    the roots come from where OneDrive itself records them: the OneDrive,
    OneDriveConsumer and OneDriveCommercial environment variables and
    HKCU\\Software\\Microsoft\\OneDrive\\Accounts\\*\\UserFolder. Reading these
    changes nothing and needs no internet. HARNESS_CLOUD_ROOTS (separated by
    os.pathsep) adds more, for the tests.
    """
    roots = []
    for var in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        if os.environ.get(var):
            roots.append(os.environ[var])
    if os.environ.get("HARNESS_CLOUD_ROOTS"):
        roots.extend(p for p in os.environ["HARNESS_CLOUD_ROOTS"].split(os.pathsep) if p)
    if os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\OneDrive\Accounts") as accounts:
                i = 0
                while True:
                    try:
                        sub = winreg.EnumKey(accounts, i)
                    except OSError:
                        break
                    i += 1
                    try:
                        with winreg.OpenKey(accounts, sub) as account:
                            folder, _ = winreg.QueryValueEx(account, "UserFolder")
                            if folder:
                                roots.append(folder)
                    except OSError:
                        pass
        except OSError:
            pass
    unique = []
    for root in roots:
        key = os.path.normcase(os.path.abspath(os.path.expandvars(root)))
        if key not in unique:
            unique.append(key)
    return unique


def _cloud_by_name(full):
    for part in full.replace("/", os.sep).split(os.sep):
        low = part.strip().lower()
        for marker, name in CLOUD_FOLDERS:
            if low == marker or low.startswith(marker + " -") \
                    or low.startswith(marker + "-"):
                return name
    return ""


def cloud_service(path):
    """The cloud folder this path sits inside, or '' if it is really local.

    Checked one path component at a time, so 'OneDrive' and
    'OneDrive - Some Company' are both caught wherever they sit in the path;
    then against the folders OneDrive says it syncs (cloud_roots); and both
    for the path as written and for where it really leads (junctions and
    links resolved).
    """
    if not path:
        return ""
    try:
        full = os.path.abspath(os.path.expanduser(str(path)))
    except (TypeError, ValueError):
        return ""
    forms = [full]
    try:
        real = os.path.realpath(full)
        if os.path.normcase(real) != os.path.normcase(full):
            forms.append(real)
    except (OSError, ValueError):
        pass
    roots = cloud_roots()
    for form in forms:
        name = _cloud_by_name(form)
        if name:
            return name
        key = os.path.normcase(form)
        for root in roots:
            if key == root or key.startswith(root.rstrip("\\/") + os.sep):
                return "OneDrive"
    return ""


class CloudSaveRefused(OSError):
    """Raised instead of writing a file into a folder that syncs to the internet."""


def refuse_cloud_save(path, what="the file"):
    """Stop before anything is written into a cloud folder, when the profile asks for that. Fail closed.

    For a student on slow or paid internet (the first user's was a metered mobile connection), a file saved in
    OneDrive is uploaded behind their back, and when the signal drops it can vanish from the laptop. The profile's
    support.refuse_cloud turns this on; most students sync their work and want to.
    """
    if not support().get("refuse_cloud"):
        return
    service = cloud_service(path)
    if service:
        raise CloudSaveRefused(
            "NOT SAVING TO THE CLOUD: %s would go into %s (%s), and your profile asks for your work to stay on this "
            "computer. Use a folder under %s." % (what, service, path, local_work_root()))


def is_local(path):
    """True when nothing is going to sync this path to the internet."""
    return not cloud_service(path)


def documents_folder():
    """Where Windows currently sends 'Documents'. May be inside OneDrive."""
    if os.name == "nt":
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer"
                r"\User Shell Folders")
            try:
                value, _ = winreg.QueryValueEx(key, "Personal")
            finally:
                winreg.CloseKey(key)
            return os.path.expandvars(value)
        except Exception:
            pass
    return os.path.join(HOME, "Documents")


def local_work_root():
    """Where the student's work goes, guaranteed to stay on this computer.

    HARNESS_WORK_DIR overrides it, but a cloud path is refused even then:
    the point of this is that the work survives the internet going down.
    """
    chosen = os.environ.get("HARNESS_WORK_DIR") or (load_profile().get("work_dir") or "")
    if chosen and (is_local(chosen) or not support().get("refuse_cloud")):
        return os.path.expanduser(chosen)
    return os.path.join(HOME, "Documents", WORK_ROOT_NAME)


def cloud_warning(path, what="the work"):
    """A plain warning when path is in the cloud and the profile wants the work kept local, else ''."""
    service = cloud_service(path)
    if not service or not support().get("refuse_cloud"):
        return ""
    return (
        "\n  [WARNING] %s is saved in %s (in the cloud), not on this computer."
        "\n            Folder: %s"
        "\n            If the internet goes down, you cannot reach it."
        "\n            Move it here: %s"
        % (what.capitalize(), service, path, local_work_root()))


# ==========================================================================
# Student profile
# ==========================================================================

DEFAULT_PROFILE = {
    "name": "",
    "student_id": "[Student ID]",
    "programme": "",
    "institution": "",
    "style": "apa7",
    "work_language": "en",
    "support": {"short_messages": True, "agreed": [], "refuse_cloud": False,
                "dyslexia_note_on_submissions": False},
}


def support(profile=None):
    """The agreed adjustments and accessibility settings, with defaults for anything missing."""
    data = (profile or load_profile()).get("support") or {}
    out = dict(DEFAULT_PROFILE["support"])
    if isinstance(data, dict):
        out.update(data)
    return out


def profile_path():
    from . import profile as prof
    return os.environ.get("HARNESS_PROFILE") or str(prof.path())


def load_profile(path=None):
    """The student profile (profile.py, merged over its country), or the defaults when there is none yet."""
    from . import profile as prof
    merged = dict(DEFAULT_PROFILE)
    p = path or os.environ.get("HARNESS_PROFILE")
    data = prof.load(os.path.dirname(p) if p else None)
    if data:
        merged.update(data)
        merged["institution"] = data.get("university") or ""
    return merged


def first_name(profile):
    name = (profile or {}).get("name", "") or ""
    parts = name.strip().split()
    return parts[0] if parts else ""


def output_folder_name(profile=None):
    """'4 - HERE IS YOUR WORK, <first name>', or without a name if none is set."""
    name = first_name(profile if profile is not None else load_profile())
    # Characters Windows will not accept in a folder name.
    name = re.sub(r'[<>:"/\\|?*]', "", name)
    return "%s, %s" % (OUTPUT_PREFIX, name) if name else OUTPUT_PREFIX


# ==========================================================================
# Layout
# ==========================================================================

class Workspace(object):
    """Resolved paths for one exam folder, in whichever layout it uses."""

    def __init__(self, exam_path, profile=None):
        self.root = os.path.abspath(exam_path)
        self.profile = profile if profile is not None else load_profile()
        self.legacy = self._is_legacy()

        if self.legacy:
            self.materials = os.path.join(self.root, LEGACY["materials"])
            self.brief = None
            self.notes = None
            self.research = os.path.join(self.root, LEGACY["research"])
            self.drafts = os.path.join(self.root, LEGACY["drafts"])
            self.output = os.path.join(self.root, LEGACY["output"])
            self.extracted = os.path.join(self.root, EXTRACTED)
        else:
            self.materials = os.path.join(self.root, MATERIALS)
            self.brief = os.path.join(self.materials, BRIEF)
            self.notes = os.path.join(self.materials, NOTES)
            self.research = os.path.join(self.root, RESEARCH)
            self.drafts = os.path.join(self.root, DRAFTS)
            self.output = self._existing_output() or os.path.join(
                self.root, output_folder_name(self.profile))
            self.extracted = os.path.join(self.root, EXTRACTED)

        self.config = os.path.join(self.root, "EXAM_CONFIG.md")

    def _is_legacy(self):
        """An old-style folder: it has 00_Source_Materials and no new layout."""
        old = os.path.isdir(os.path.join(self.root, LEGACY["materials"]))
        new = os.path.isdir(os.path.join(self.root, MATERIALS))
        return old and not new

    def _existing_output(self):
        """Reuse an existing output folder even if the profile name changed."""
        if not os.path.isdir(self.root):
            return None
        for entry in sorted(os.listdir(self.root)):
            if entry.startswith(OUTPUT_PREFIX) and os.path.isdir(
                    os.path.join(self.root, entry)):
                return os.path.join(self.root, entry)
        return None

    # -- helpers ---------------------------------------------------------

    def folders(self):
        """(role, path) for every folder this layout uses."""
        roles = [("materials", self.materials), ("brief", self.brief),
                 ("notes", self.notes), ("research", self.research),
                 ("drafts", self.drafts), ("output", self.output),
                 ("extracted", self.extracted)]
        return [(r, p) for r, p in roles if p]

    def create(self, write_readmes=True):
        """Create every folder, with a short README explaining each."""
        refuse_cloud_save(self.root, "the assignment folder")
        os.makedirs(self.root, exist_ok=True)
        for role, path in self.folders():
            os.makedirs(path, exist_ok=True)
            if write_readmes:
                readme = os.path.join(path, "README.txt")
                if not os.path.exists(readme):
                    with open(readme, "w", encoding="utf-8") as f:
                        f.write(FOLDER_HELP[role] + "\n")
        return self

    def role_of(self, path):
        """Whether a file is a study source, the brief, or the student's notes.

        The plagiarism check reports each differently: echoing the brief's
        question is expected, a match against the student's own notes is a
        prompt to check the note, and only a match against a source is
        plagiarism.
        """
        full = os.path.abspath(path)
        if self.brief and full.startswith(os.path.abspath(self.brief) + os.sep):
            return "brief"
        if self.notes and full.startswith(os.path.abspath(self.notes) + os.sep):
            return "notes"
        return "source"

    def describe(self):
        lines = ["Exam folder: %s" % self.root,
                 "Layout:      %s" % ("original (00_...)" if self.legacy
                                      else "current")]
        for role, path in self.folders():
            rel = os.path.relpath(path, self.root)
            flag = "" if os.path.isdir(path) else "   (not created yet)"
            lines.append("  %-10s %s%s" % (role, rel, flag))
        return "\n".join(lines)

    # -- migration -------------------------------------------------------

    def migrate(self, apply=False):
        """Move an original-layout folder to the current layout.

        Returns a list of (from, to) moves. With apply=False nothing is
        touched, so the plan can be shown and confirmed first.
        """
        if not self.legacy:
            return []
        target = Workspace.__new__(Workspace)
        target.root = self.root
        target.profile = self.profile
        target.legacy = False
        target.materials = os.path.join(self.root, MATERIALS)
        target.brief = os.path.join(target.materials, BRIEF)
        target.notes = os.path.join(target.materials, NOTES)
        target.research = os.path.join(self.root, RESEARCH)
        target.drafts = os.path.join(self.root, DRAFTS)
        target.output = os.path.join(self.root,
                                     output_folder_name(self.profile))

        moves = []
        for old_role, new_path in (("materials", target.materials),
                                   ("research", target.research),
                                   ("drafts", target.drafts),
                                   ("output", target.output)):
            old_path = os.path.join(self.root, LEGACY[old_role])
            if os.path.isdir(old_path):
                moves.append((old_path, new_path))

        if apply:
            for old_path, new_path in moves:
                if os.path.exists(new_path):
                    # Merge rather than overwrite.
                    for entry in os.listdir(old_path):
                        src = os.path.join(old_path, entry)
                        dst = os.path.join(new_path, entry)
                        if not os.path.exists(dst):
                            shutil.move(src, dst)
                    try:
                        os.rmdir(old_path)
                    except OSError:
                        pass
                else:
                    shutil.move(old_path, new_path)
            os.makedirs(target.brief, exist_ok=True)
            os.makedirs(target.notes, exist_ok=True)
            self.__init__(self.root, self.profile)
        return moves


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Show or migrate an exam folder's layout")
    parser.add_argument("exam_folder")
    parser.add_argument("--migrate", action="store_true",
                        help="Preview moving an original-layout folder")
    parser.add_argument("--apply", action="store_true",
                        help="With --migrate, actually move the folders")
    args = parser.parse_args()

    ws = Workspace(args.exam_folder)
    print(ws.describe())

    if args.migrate:
        if not ws.legacy:
            print("\nAlready in the current layout; nothing to migrate.")
            sys.exit(0)
        moves = ws.migrate(apply=args.apply)
        print("\n%s:" % ("Moved" if args.apply else "Would move"))
        for old, new in moves:
            print("  %s\n    -> %s" % (os.path.relpath(old, ws.root),
                                      os.path.relpath(new, ws.root)))
        if not args.apply:
            print("\nNothing has been changed. Re-run with --apply to move.")
