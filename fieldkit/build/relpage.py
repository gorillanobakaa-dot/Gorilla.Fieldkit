"""A Go app's GitHub release page from its notes, the same shape every release (Gorilla OpenCode v0.1.155, v0.1.156):
the opening, then the plain-language track and the developer track, each in full on the page, and the File|Bytes|
SHA-256 table measured from SHA256SUMS (relfiles.table: every file hashed again).

    fieldkit release page v0.1.157 --src C:/.../Gorilla.Opencode --dir <folder with SHA256SUMS-v0.1.157.txt>
                          [--commit 935260a] [--out page.md]

Owner, 2026-10-27: "we should have a script like this that we only modify small parts of it and reuse it" - the
v0.1.156 session wrote this page by hand first (Documents/Scripts/GorillaOpencodeReleasePage/release.py); this is it,
adopted. What a release changes is its notes, not this code.

Inputs, relative to --src:
    ReleaseNotes/openings/<v>.md                   the page's first part, as written
    Changelogs/<v>-release-notes.layman.md         its H1 and date block (above the first `---`) dropped,
    Changelogs/<v>-release-notes.developer.md      headings moved one level down
The table goes where the developer track has `<!-- files table -->`, else before its "Test coverage and CI"
heading, else at its end.
"""
import re
from pathlib import Path

from . import relfiles

TABLE_MARK = "<!-- files table -->"
LAY_HEAD = ("# In plain language: everything in this release\n\n"
            "This is the complete explanation, not a summary of one. Nothing below is behind a link.\n\n"
            "<!-- plain-language track: in full, on this page -->\n\n")
DEV_HEAD = ("# For developers: how it works, and how to check it\n\n"
            "Written for someone who will audit, fork or change the code. It covers the same release as the "
            "plain-language part above; neither is a summary of the other.\n\n"
            "<!-- developer track: in full, on this page -->\n\n")


def track(text):
    """A notes file as a page section: the part above the first `---` line dropped, ## -> ### and so on."""
    parts = text.split("\n---\n", 1)
    body = parts[1] if len(parts) == 2 else text
    return re.sub(r"^(#{2,5}) ", lambda m: "#" + m.group(1) + " ", body.strip(), flags=re.M)


def place_table(dev, table):
    if TABLE_MARK in dev:
        return dev.replace(TABLE_MARK, table, 1)
    m = re.search(r"^#{2,6} Test coverage and CI", dev, flags=re.M)
    if m:
        return dev[:m.start()] + table + "\n\n" + dev[m.start():]
    return dev + "\n\n" + table


def compose(src, version, files_dir, commit=""):
    """-> the page (str). Raises ValueError (a notes file missing, a file not matching SHA256SUMS)."""
    src, files_dir = Path(src), Path(files_dir)
    v = version if version.startswith("v") else "v" + version
    need = {"opening": src / "ReleaseNotes" / "openings" / f"{v}.md",
            "layman": src / "Changelogs" / f"{v}-release-notes.layman.md",
            "developer": src / "Changelogs" / f"{v}-release-notes.developer.md",
            "sums": files_dir / f"SHA256SUMS-{v}.txt"}
    missing = [f"{k}: {p}" for k, p in need.items() if not p.is_file()]
    if missing:
        raise ValueError("missing " + "; ".join(missing))
    read = lambda k: need[k].read_text(encoding="utf-8")
    built = f" (every file built from {commit}, vcs.modified=false)" if commit else ""
    table = f"Measured from {need['sums'].name}{built}:\n\n" + relfiles.table(need["sums"], files_dir).strip()
    return (read("opening").strip() + "\n\n---\n" + LAY_HEAD + track(read("layman")) + "\n\n---\n\n" + DEV_HEAD
            + place_table(track(read("developer")), table) + "\n")
