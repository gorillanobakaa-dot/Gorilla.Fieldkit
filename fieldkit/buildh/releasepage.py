# Version: 1.0.0 · updated 26-10-19
"""The release page, assembled from its parts in a fixed order, then held to release-cover.

    fieldkit build-harness release-page TASK dir=<releases-draft/<release>>

Born 2026-10-19 (158.0 build 2): the page was put together by an inline script that was never kept, the way build 1's
page was put together by hand overnight with Linux facts on it. The order and the treatment of each part are data in
<dir>/PAGE.parts, one part per line: `FILE [demote] [no-claims] [heading=<line>]`:
  demote     a rendered track's '## ' headings become '### ' under the part's own heading
  no-claims  the rendered 'Claim sources' list is left out (it stays in the track's own file for review)
  heading=   a line put before the part (the rest of the line, spaces included)
The first line of a rendered track (its '# ' title) is dropped; the page has one title, in PAGE-HEAD.md.
"""
from pathlib import Path


def parse(parts_file):
    out = []
    for raw in Path(parts_file).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        head = None
        if " heading=" in line:
            line, head = line.split(" heading=", 1)
        bits = line.split()
        out.append({"file": bits[0], "demote": "demote" in bits[1:], "no_claims": "no-claims" in bits[1:],
                    "heading": head})
    return out


def assemble(folder):
    """-> (page text, [missing part files]). Nothing is written when a part is missing."""
    folder = Path(folder)
    parts, missing, out = parse(folder / "PAGE.parts"), [], []
    for p in parts:
        f = folder / p["file"]
        if not f.is_file():
            missing.append(p["file"])
            continue
        lines = f.read_text(encoding="utf-8").split("\n")
        if p["demote"] and lines and lines[0].startswith("# "):
            lines = lines[1:]
        text = "\n".join(lines)
        if p["no_claims"] and "## Claim sources" in text:
            text = text[:text.index("## Claim sources")]
        if p["demote"]:
            text = "\n".join(("#" + l) if l.startswith("## ") else l for l in text.split("\n"))
        out.append(((p["heading"] + "\n\n") if p["heading"] else "") + text.strip() + "\n")
    return "\n".join(out), missing


def write(folder, out_name="RELEASE-BODY.md"):
    text, missing = assemble(folder)
    if missing:
        raise FileNotFoundError(f"parts missing: {missing}")
    (Path(folder) / out_name).write_text(text, encoding="utf-8", newline="\n")
    return Path(folder) / out_name, len(text)
