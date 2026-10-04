"""The release page: where the philosophy is enforced instead of remembered.

WHY THIS EXISTS
    On 2026-10-04 three releases of Gorilla OpenCode were published with the
    plain-language track behind a link at the bottom of the page:

        ## Full notes
        - v0.1.137-release-notes.layman.md — plain English

    The link was relative, so on a release page it did not even open. A complete
    layman document had been written, validated, attached, and then hidden from
    the one reader it was written for, on the page where that reader decides
    whether to download anything. PHILOSOPHY.md calls this "transparent in
    theory, a closed door in practice", and says: "No one should have to trust a
    summary they cannot verify."

    The writer had the philosophy, the guide and the generator, and still did
    it, by copying the shape of an earlier page. A rule that depends on being
    remembered is not a rule. So the page is now BUILT from the two tracks, and
    CHECKED, by code.

WHAT IT DOES
    compose()  builds the page in a fixed order:
                 1. the opening, written for someone who has never heard of the
                    project: what it is, whether to download it, why it matters
                 2. the plain-language track, IN FULL, on the page
                 3. extra material (what the program printed, pictures)
                 4. the developer track, IN FULL, on the page
    check()    refuses a page that breaks the order, hides either track behind
               a link, uses a link that cannot open from a release page, or
               opens without telling the reader whether to bother.

WHAT IT CANNOT DO
    Judge whether the opening is well written, whether a comparison is true, or
    whether the reader would understand it. LAYMAN_GUIDE.md is for that part.

Nothing here contacts the network. Publishing the page is a separate step.
"""
import re
from pathlib import Path

PHILOSOPHY = Path(__file__).resolve().parent / "PHILOSOPHY.md"

# The opening must answer these, in the reader's words, before anything else.
OPENING_QUESTIONS = (
    ("what it is", r"(?im)^#{2,3} .*\bwhat (?:is this|this is|it is)\b"),
    ("whether to download it", r"(?im)^#{2,3} .*\bshould you\b"),
    ("why it matters", r"(?im)^#{2,3} .*\bwhy (?:this|it) matters\b"),
)
# How far down the page the opening may start. A reader who has to scroll past
# a changelog to learn what the program is has already left.
OPENING_WITHIN_LINES = 40

LAYMAN_MARK = "<!-- plain-language track: in full, on this page -->"
DEVELOPER_MARK = "<!-- developer track: in full, on this page -->"

_LINK = re.compile(r"(?<!!)\[([^\]]*)\]\(([^)\s]+)\)")
_IMAGE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)\)")


def _body(track_md: str) -> str:
    """A rendered track without its own title block: the page has one title."""
    lines = track_md.replace("\r\n", "\n").strip().split("\n")
    i = 0
    if lines and lines[0].startswith("# "):
        i = 1
        while i < len(lines) and not lines[i].startswith("## "):
            i += 1
    return "\n".join(lines[i:]).strip()


def _demote(md: str) -> str:
    """One heading level down, outside code fences, so a track's sections sit
    under the page's own section for it."""
    out, fenced = [], False
    for line in md.split("\n"):
        if line.lstrip().startswith("```"):
            fenced = not fenced
        if not fenced and re.match(r"#{1,5} ", line):
            line = "#" + line
        out.append(line)
    return "\n".join(out)


def compose(opening: str, layman_md: str, developer_md: str, extra: str = "") -> str:
    """Build the page. The order is the point and is not configurable."""
    parts = [
        opening.replace("\r\n", "\n").strip(),
        "---",
        "# In plain language: everything in this release",
        "",
        "This is the complete explanation, not a summary of one. Nothing below is "
        "behind a link.",
        "",
        LAYMAN_MARK,
        "",
        _demote(_body(layman_md)),
    ]
    if extra.strip():
        parts += ["", "---", "", extra.replace("\r\n", "\n").strip()]
    parts += [
        "",
        "---",
        "",
        "# For developers: how it works, and how to check it",
        "",
        "Written for someone who will audit, fork or change the code. It covers "
        "the same release as the plain-language part above; neither is a summary "
        "of the other.",
        "",
        DEVELOPER_MARK,
        "",
        _demote(_body(developer_md)),
        "",
    ]
    return "\n".join(parts)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def check(page: str, layman_md: str = "", developer_md: str = "") -> list:
    """Every reason the page must not be published. Empty means none found."""
    page = page.replace("\r\n", "\n")
    findings = []

    # 1. The opening answers the reader's three questions, near the top.
    lines = page.split("\n")
    for label, pattern in OPENING_QUESTIONS:
        m = re.search(pattern, page)
        if not m:
            findings.append(f"the page never tells the reader {label}: no heading matching {pattern!r}")
            continue
        at = page.count("\n", 0, m.start()) + 1
        if label == "what it is" and at > OPENING_WITHIN_LINES:
            findings.append(f"the page does not say {label} until line {at}; it must be within the first "
                            f"{OPENING_WITHIN_LINES} lines")

    # 2. Both tracks are ON the page, whole, plain language first.
    for name, track in (("plain-language", layman_md), ("developer", developer_md)):
        if not track:
            continue
        body = _norm(_demote(_body(track)))
        if body not in _norm(page):
            findings.append(f"the {name} track is not on the page in full: its text was not found. "
                            f"A reader must not have to open another file to read it")
    li, di = page.find(LAYMAN_MARK), page.find(DEVELOPER_MARK)
    if li < 0:
        findings.append("the page has no plain-language part (marker missing): build it with compose()")
    if di >= 0 and li >= 0 and di < li:
        findings.append("the developer part comes before the plain-language part")

    # 3. No link that sends the reader away for what should be here, and no
    #    link that cannot open from a release page at all.
    for text, target in _LINK.findall(page):
        if re.search(r"release-notes\.(?:layman|developer)\.md", target) or \
           re.search(r"release-notes\.(?:layman|developer)\.md", text):
            findings.append(f"link [{text}]({target}) sends the reader to the notes file instead of "
                            f"putting the notes on the page")
        elif not re.match(r"(?:https?://|#|mailto:)", target):
            findings.append(f"link [{text}]({target}) is relative: it does not open from a release page")
    for alt, target in _IMAGE.findall(page):
        if not target.startswith("https://"):
            findings.append(f"image ({target}) is relative: it does not show on a release page")
    if re.search(r"(?im)^#{1,3} +full notes\b", page):
        findings.append('the page has a "Full notes" section: that is the closed door. The notes go on the page')

    return findings


def philosophy_text() -> str:
    return PHILOSOPHY.read_text(encoding="utf-8")
