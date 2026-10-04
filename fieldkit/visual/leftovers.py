"""Leftovers: Mozilla's art and words where Gorilla's branding belongs.

    BRAND-001  a picture (ART_EXT) in the Gorilla branding directory is byte-identical to one in Mozilla's branding
               directories (nightly, official, aurora, unofficial): placeholder art shipped as ours (doctrine 6a:
               every .ico was once Mozilla's blue globe). Build files (moz.build, jar.mn) may legitimately match.
    BRAND-002  an SVG in the Gorilla branding directory carries path data from one of Mozilla's branding SVGs: the
               Nightly or Firefox wordmark (2026-10-02: the About window showed "Nightly"). Path data that every one
               of the four Mozilla branding directories carries is channel-neutral (the "PDF" letters of
               document_pdf.svg, 2026-10-04), not a brand mark, and is not indexed; a shape only some channels carry
               (a logo, a wordmark) still is. With any of the four directories absent nothing is excluded.
    BRAND-003  user-visible text (comments removed) in the branding directory names Nightly, Firefox or a mozilla.org
               host. Legitimate mentions go in the allowlist file with a reason.
"""
import hashlib
import re
from pathlib import Path

MOZILLA_BRANDING = ("nightly", "official", "aurora", "unofficial")
TEXT_EXT = (".ftl", ".properties", ".js", ".nsi", ".nsh", ".xml", ".svg", ".css", ".ini", ".sh", ".mn", ".build",
            ".json", ".manifest", ".txt", ".inc")
# art only: build files (moz.build, jar.mn, manifests) are legitimately the same as Mozilla's
ART_EXT = (".png", ".ico", ".bmp", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".icns", ".car")
PATH_D = re.compile(r"\sd=\"([^\"]{40,})\"")
WORDS = [
    (re.compile(r"\bNightly\b"), "Nightly"),
    (re.compile(r"nightly\.mozilla\.org", re.I), "nightly.mozilla.org"),
    (re.compile(r"Firefox (Nightly|Developer Edition|Beta)"), "a Firefox channel name"),
    (re.compile(r"Mozilla (Firefox|Developer Preview|Corporation|Foundation)"), "a Mozilla product/company name"),
    (re.compile(r"\b(?:[\w-]+\.)*mozilla\.(?:org|com|net)\b", re.I), "a mozilla.org/com host"),
    (re.compile(r"\bfirefox\.com\b", re.I), "firefox.com"),
]
# 'Firefox' as a word is only a finding in text a user reads (brand strings, installer, tile manifests)
FIREFOX_WORD = re.compile(r"(?<![\w-])Firefox(?![\w-])")
USER_TEXT = (".ftl", ".properties", ".nsi", ".nsh", ".xml")


def strip_comments(text, ext):
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    if ext in (".js", ".css", ".json", ".svg", ".mn", ".manifest", ".inc"):
        text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    if ext in (".js", ".json"):
        text = re.sub(r"(?m)(^|[^:\"'])//.*$", r"\1", text)
    if ext in (".ftl", ".properties", ".nsi", ".nsh", ".sh", ".build", ".mn", ".ini", ".inc", ".txt"):
        text = re.sub(r"(?m)^\s*[#;].*$", "", text)
    if ext in (".nsi", ".nsh"):
        text = re.sub(r"(?m)\s;.*$", "", text)
    return text


def _sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def mozilla_index(branding_root):
    """-> ({sha256: 'nightly/content/x.svg'}, {path d: 'nightly/content/about-wordmark.svg'}). The path index leaves
    out path data present in ALL FOUR Mozilla branding directories (channel-neutral, see BRAND-002)."""
    hashes, paths, per_dir = {}, {}, {}
    for name in MOZILLA_BRANDING:
        d = Path(branding_root) / name
        if not d.is_dir():
            continue
        mine = per_dir.setdefault(name, set())
        for f in sorted(d.rglob("*")):
            if not f.is_file():
                continue
            rel = f.relative_to(branding_root).as_posix()
            hashes.setdefault(_sha(f), rel)
            if f.suffix.lower() == ".svg":
                for m in PATH_D.finditer(f.read_text(encoding="utf-8", errors="replace")):
                    key = " ".join(m.group(1).split())
                    paths.setdefault(key, rel)
                    mine.add(key)
    if len(per_dir) == len(MOZILLA_BRANDING):
        for key in set.intersection(*per_dir.values()):
            del paths[key]
    return hashes, paths


def check(branding_dir, tree):
    """-> [item]. `branding_dir` is the Gorilla branding directory, inside the tree."""
    branding_dir, tree = Path(branding_dir), Path(tree)
    items = []
    root = branding_dir.parent
    hashes, paths = mozilla_index(root)
    if not hashes:
        items.append(_item("BRAND-001", root.relative_to(tree).as_posix(), "UNVERIFIABLE",
                           f"none of Mozilla's branding directories ({', '.join(MOZILLA_BRANDING)}) is in the tree: "
                           f"nothing to compare against"))
    for f in sorted(branding_dir.rglob("*")):
        if not f.is_file():
            continue
        rel = f.relative_to(tree).as_posix()
        same = hashes.get(_sha(f)) if f.suffix.lower() in ART_EXT else None
        if same:
            items.append(_item("BRAND-001", rel, "FAIL", f"byte-identical to Mozilla's {same}"))
        ext = f.suffix.lower()
        if ext == ".svg":
            text = f.read_text(encoding="utf-8", errors="replace")
            hits = sorted({paths[d] for d in (" ".join(m.group(1).split()) for m in PATH_D.finditer(text)) if d in paths})
            if hits:
                items.append(_item("BRAND-002", rel, "FAIL", f"carries path data from Mozilla's {', '.join(hits)} (a wordmark or logo)"))
            elif paths:
                items.append(_item("BRAND-002", rel, "PASS", "no path data from Mozilla's branding SVGs"))
        if ext in TEXT_EXT or f.name in ("configure.sh", "moz.build", "jar.mn"):
            try:
                text = strip_comments(f.read_text(encoding="utf-8", errors="replace"), ext)
            except OSError as e:
                items.append(_item("BRAND-003", rel, "UNVERIFIABLE", f"cannot read: {e}"))
                continue
            text = re.sub(r"<path[^>]*>", "", text) if ext == ".svg" else text
            found = []
            for ln, line in enumerate(text.splitlines(), 1):
                spans = []
                for rx, what in WORDS + ([(FIREFOX_WORD, "Firefox")] if ext in USER_TEXT else []):
                    for m in rx.finditer(line):
                        if any(m.start() < e and s < m.end() for s, e in spans):
                            continue            # one finding per piece of text (nightly.mozilla.org is also a host)
                        spans.append(m.span())
                        found.append((ln, what, m.group(0), line.strip()[:100]))
            for ln, what, word, line in found:
                items.append(_item("BRAND-003", f"{rel}:{ln}:{word}", "FAIL", f"{what}: {line}"))
            if not found:
                items.append(_item("BRAND-003", rel, "PASS", "no Mozilla/Nightly/Firefox branding text"))
    return items


def _item(rule, item, verdict, evidence):
    return {"layer": "static", "rule": rule, "item": item, "verdict": verdict, "evidence": evidence}
