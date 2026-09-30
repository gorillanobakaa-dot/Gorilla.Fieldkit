"""Remove people's names from the file properties of Word/Excel/PowerPoint files.

Ported from a document harness's metadata scrubber (2026-09-22), where Word
kept writing the author's name back into "Last saved by" on every save. Made
general: the words to hunt come from the caller or from fieldkit.local.json
("privacy": {"terms": [...]}), never from the code.

Removed: Author, Last saved by, Manager, Company; title/subject/keywords/
description/category only when they contain a hunted term; custom properties
that contain one; review-mark (comment / tracked change) author names, which
become "Author" - the same thing Word's own "Remove Personal Information" does.
Never touched: the document's own text.

Word writes the name back on every save, so scrub is the LAST step.
"""
import os
import re
import shutil
import tempfile
import zipfile
from pathlib import Path

from ..core import privacy

OFFICE_SUFFIXES = (".docx", ".pptx", ".xlsx", ".docm", ".pptm", ".xlsm")
ALWAYS_CLEAR = ("dc:creator", "cp:lastModifiedBy")
APP_CLEAR = ("Company", "Manager")
CLEAR_IF_NAMED = ("dc:title", "dc:subject", "cp:keywords", "dc:description", "cp:category")
ANON_AUTHOR = "Author"
# Where review-mark authors live: Word attributes, Excel comment <author>
# elements and threaded-comment persons, PowerPoint comment authors.
_WORD_AUTHOR = re.compile(r'\bw:author="([^"]*)"')
_WORD_INITIALS = re.compile(r'\bw:initials="[^"]*"')
_XL_AUTHOR = re.compile(r"<author>([^<]*)</author>")
_DISPLAY_NAME = re.compile(r'\bdisplayName="([^"]*)"')
_PPT_AUTHOR = re.compile(r'(<p:cmAuthor\b[^>]*?\bname=")([^"]*)(")')


def _authors(x):
    out = set(_WORD_AUTHOR.findall(x)) | set(_XL_AUTHOR.findall(x)) | set(_DISPLAY_NAME.findall(x))
    out |= {m.group(2) for m in _PPT_AUTHOR.finditer(x)}
    return {a.strip() for a in out if a.strip() and a.strip() != ANON_AUTHOR}


def _anonymise(x):
    x = _WORD_AUTHOR.sub(f'w:author="{ANON_AUTHOR}"', x)
    x = _WORD_INITIALS.sub('w:initials="A"', x)
    x = _XL_AUTHOR.sub(f"<author>{ANON_AUTHOR}</author>", x)
    x = _DISPLAY_NAME.sub(f'displayName="{ANON_AUTHOR}"', x)
    return _PPT_AUTHOR.sub(lambda m: m.group(1) + ANON_AUTHOR + m.group(3), x)


def _tag_value(xml, tag):
    m = re.search(r"<%s\b[^>]*>(.*?)</%s>" % (re.escape(tag), re.escape(tag)), xml, flags=re.S)
    return m.group(1).strip() if m else ""


def _blank_tag(xml, tag):
    return re.sub(r"(<%s\b[^>]*>)(.*?)(</%s>)" % (re.escape(tag), re.escape(tag)),
                  lambda m: m.group(1) + m.group(3), xml, flags=re.S)


def _has(value, terms):
    low = (value or "").lower()
    return any(t.lower() in low for t in terms if t)


def inspect(path, terms):
    """Everything in the properties that identifies someone -> list of (where, field, value)."""
    found = []
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        if "docProps/core.xml" in names:
            core = z.read("docProps/core.xml").decode("utf-8", "replace")
            for tag in ALWAYS_CLEAR + CLEAR_IF_NAMED:
                v = _tag_value(core, tag)
                if v and (tag in ALWAYS_CLEAR or _has(v, terms)):
                    found.append(("properties", tag.split(":")[-1], v))
        if "docProps/app.xml" in names:
            app = z.read("docProps/app.xml").decode("utf-8", "replace")
            for tag in APP_CLEAR:
                v = _tag_value(app, tag)
                if v:
                    found.append(("properties", tag, v))
        if "docProps/custom.xml" in names:
            custom = z.read("docProps/custom.xml").decode("utf-8", "replace")
            for m in re.finditer(r'<property\b[^>]*name="([^"]+)"[^>]*>(.*?)</property>', custom, re.S):
                v = re.sub(r"<[^>]+>", "", m.group(2)).strip()
                if v and _has(v, terms):
                    found.append(("custom property", m.group(1), v))
        authors = set()
        for n in names:
            if n.endswith(".xml") and n.startswith(("word/", "ppt/", "xl/")):
                authors |= _authors(z.read(n).decode("utf-8", "replace"))
        found += [("review marks", "author", a) for a in sorted(authors)]
    return found


def scrub(path, terms=None, backup=True):
    """Clean in place. Returns {'removed': [...], 'backup': path|None}."""
    path = Path(path)
    if path.suffix.lower() not in OFFICE_SUFFIXES:
        raise ValueError(f"{path.name}: only {', '.join(OFFICE_SUFFIXES)}")
    terms = privacy.private_terms() if terms is None else terms
    before = inspect(path, terms)
    if not before:
        return {"removed": [], "backup": None}
    bak = None
    if backup:
        bak = str(path) + ".bak"
        shutil.copy2(path, bak)
    fd, tmp = tempfile.mkstemp(suffix=path.suffix, dir=path.parent)
    os.close(fd)
    try:
        with zipfile.ZipFile(path) as src, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as dst:
            for item in src.infolist():
                data = src.read(item.filename)
                n = item.filename
                if n in ("docProps/core.xml", "docProps/app.xml", "docProps/custom.xml") or (
                        n.endswith(".xml") and n.startswith(("word/", "ppt/", "xl/"))):
                    x = data.decode("utf-8", "replace")
                    if n == "docProps/core.xml":
                        for tag in ALWAYS_CLEAR:
                            x = _blank_tag(x, tag)
                        for tag in CLEAR_IF_NAMED:
                            if _has(_tag_value(x, tag), terms):
                                x = _blank_tag(x, tag)
                    elif n == "docProps/app.xml":
                        for tag in APP_CLEAR:
                            x = _blank_tag(x, tag)
                    elif n == "docProps/custom.xml":
                        x = re.sub(r"<property\b.*?</property>", lambda m: "" if _has(m.group(0), terms) else m.group(0),
                                   x, flags=re.S)
                    else:
                        x = _anonymise(x)
                    data = x.encode("utf-8")
                dst.writestr(item, data)
        shutil.move(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return {"removed": before, "backup": bak}
