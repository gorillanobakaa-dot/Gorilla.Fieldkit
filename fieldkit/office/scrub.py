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

The backup is kept in Fieldkit's state folder (state/office-backups/<time>/), NOT
next to the file: a .bak beside the cleaned file still holds every removed name.
restore(backup) puts it back. The cleaned file is re-checked before and after it
replaces the original; on failure the original is put back.

PDF: inspect() reads the Info dictionary and XMP; scrub() refuses (PDF_CANNOT_SCRUB).

Word writes the name back on every save, so scrub is the LAST step.
"""
import html
import json
import os
import re
import shutil
import tempfile
import time
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
    """Everything in the properties that identifies someone -> list of (where, field, value).

    PDFs: the Info dictionary and XMP (see inspect_pdf); scrub cannot clean those.
    """
    if Path(path).suffix.lower() == ".pdf":
        return inspect_pdf(path, terms)
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
    """Clean in place. Returns {'removed': [...], 'backup': path|None}.

    The cleaned copy is written beside the file under a temporary name, checked
    (still sound, no names left) and only then swapped in; the file in place is
    checked once more and put back as it was if that fails. The backup goes to
    Fieldkit's own state folder (backup_root()), never next to the file: a .bak
    beside a cleaned file still holds every name that was removed.
    """
    path = Path(path)
    if path.suffix.lower() == ".pdf":
        raise ValueError(PDF_CANNOT_SCRUB)
    if path.suffix.lower() not in OFFICE_SUFFIXES:
        raise ValueError(f"{path.name}: only {', '.join(OFFICE_SUFFIXES)}")
    terms = privacy.private_terms() if terms is None else terms
    before = inspect(path, terms)
    if not before:
        return {"removed": [], "backup": None}
    original = path.read_bytes()
    bak = None
    fd, tmp = tempfile.mkstemp(prefix=".fieldkit-scrub-", suffix=path.suffix, dir=path.parent)
    os.close(fd)
    try:
        _rewrite(path, tmp, terms)
        bad = _left_over(tmp, terms)
        if bad:
            raise RuntimeError(f"{path.name}: the cleaned copy failed its check, the file was not changed: "
                               + "; ".join(bad[:3]))
        if backup:
            bak = str(_backup(path))
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    bad = _left_over(path, terms)                   # the file actually in place, not the copy we checked
    if bad:
        path.write_bytes(original)
        raise RuntimeError(f"{path.name}: failed its check after cleaning ({'; '.join(bad[:3])}); "
                           f"the original was put back" + (f" (backup also at {bak})" if bak else ""))
    return {"removed": before, "backup": bak}


def _left_over(f, terms):
    """Problems with a cleaned file: broken structure, or names still in its properties."""
    from . import check as chk
    try:
        probs = list(chk.check(f)["problems"])
    except Exception as e:  # noqa: BLE001 - a check that cannot run is a failed check
        probs = [f"check could not run: {type(e).__name__}: {e}"]
    try:
        probs += [f"{w} {k} still set" for w, k, _ in inspect(f, terms)]
    except Exception as e:  # noqa: BLE001
        probs.append(f"could not re-read properties: {type(e).__name__}: {e}")
    return probs


def _rewrite(path, tmp, terms):
    """Write the cleaned package to `tmp`."""
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


# -- backups live in Fieldkit's state folder, never next to the document ----------------------
def backup_root():
    """Where scrub keeps backups. FIELDKIT_OFFICE_BACKUPS overrides (tests); default ROOT/state/office-backups."""
    env = os.environ.get("FIELDKIT_OFFICE_BACKUPS")
    if env:
        return Path(env)
    from ..core import settings
    return settings.ROOT / "state" / "office-backups"


def _backup(path):
    """Copy `path` to backup_root()/<timestamp>/<name>, with original.json saying where it came from."""
    path = Path(path)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    for n in range(1000):
        d = backup_root() / (stamp if n == 0 else f"{stamp}-{n}")
        try:
            d.mkdir(parents=True, exist_ok=False)
            break
        except FileExistsError:
            continue
    else:
        raise RuntimeError(f"could not make a backup folder under {backup_root()}")
    dst = d / path.name
    shutil.copy2(path, dst)
    (d / "original.json").write_text(json.dumps({"original": str(path.resolve())}, indent=1), encoding="utf-8")
    return dst


def restore(backup, target=None):
    """Put a scrub backup back over the original (or over `target`). Returns the path written."""
    backup = Path(backup)
    if target is None:
        meta = backup.parent / "original.json"
        if not meta.is_file():
            raise FileNotFoundError(f"{meta} missing: say where to restore {backup.name} to")
        target = json.loads(meta.read_text(encoding="utf-8"))["original"]
    target = Path(target)
    fd, tmp = tempfile.mkstemp(prefix=".fieldkit-restore-", suffix=target.suffix, dir=target.parent)
    os.close(fd)
    try:
        shutil.copy2(backup, tmp)
        os.replace(tmp, target)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return target


def stale_backups(path):
    """Copies of `path` lying beside it that would leak what scrub removed.

    report.docx.bak, report.docx.bak-1, report.bak, report.wbk, "Backup of report.wbk",
    Word/Excel lock files (~$port.docx, which hold the editor's name) and leftover
    .fieldkit-scrub-* temporaries.
    """
    path = Path(path)
    name, stem = path.name.lower(), path.stem.lower()
    out = []
    try:
        entries = sorted(path.parent.iterdir())
    except OSError:
        return out
    for p in entries:
        x = p.name.lower()
        if x == name or not p.is_file():
            continue
        if (x.startswith(name + ".bak") or x in (stem + ".bak", stem + ".wbk") or x.startswith(stem + ".bak")
                or x.startswith("backup of " + stem) or x.startswith(".fieldkit-scrub-")
                or (x.startswith("~$") and len(x) > 2 and name.endswith(x[2:]))):
            out.append(p)
    return out


# -- PDF: properties are inspected, not cleaned ---------------------------------------------------
PDF_CANNOT_SCRUB = (
    "PDF properties cannot be cleaned by Fieldkit: with the libraries it uses (pypdfium2, reportlab) the only "
    "way to change them is an incremental update, which leaves the old values in the file. Clean the PDF "
    "elsewhere - re-export it from the source with the author and title fields blank (for a PDF made by "
    "'fieldkit office create', leave 'author' out of the spec) - then run deliver again.")
PDF_ALWAYS = ("Author",)                                   # a name by definition: always reported
PDF_IF_NAMED = ("Title", "Subject", "Keywords", "Creator", "Producer")
_XMP_ALWAYS = ("dc:creator", "pdf:Author")
_XMP_IF_NAMED = ("dc:title", "dc:subject", "dc:description", "pdf:Keywords", "pdf:Producer", "xmp:CreatorTool",
                 "dc:rights", "xmpRights:Owner", "photoshop:AuthorsPosition")


def _xmp_values(xmp, tag):
    vals = []
    for m in re.finditer(r"<%s\b[^>]*>(.*?)</%s>" % (re.escape(tag), re.escape(tag)), xmp, re.S):
        v = re.sub(r"<[^>]+>", " ", m.group(1))
        vals.append(" ".join(v.split()))
    vals += re.findall(r"\b%s\s*=\s*\"([^\"]*)\"" % re.escape(tag), xmp)
    return [html.unescape(v).strip() for v in vals if v.strip()]


def inspect_pdf(path, terms):
    """Names in a PDF's document properties (Info dictionary and XMP) -> list of (where, field, value)."""
    found = []
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(str(path))
    try:
        meta = doc.get_metadata_dict(skip_empty=True)
    finally:
        doc.close()
    for k in PDF_ALWAYS + PDF_IF_NAMED:
        v = (meta.get(k) or "").strip()
        if v and (k in PDF_ALWAYS or _has(v, terms)):
            found.append(("pdf properties", k, v))
    data = Path(path).read_bytes()
    texts = [data.decode("latin-1")]
    texts += [d.decode("utf-8", "replace") for _, d, _ in privacy.pdf_streams(data) if d]
    seen = set()
    for t in texts:
        for m in re.finditer(r"<x:xmpmeta\b.*?</x:xmpmeta>", t, re.S):
            xmp = m.group(0)
            for tag in _XMP_ALWAYS + _XMP_IF_NAMED:
                for v in _xmp_values(xmp, tag):
                    if (tag in _XMP_ALWAYS or _has(v, terms)) and (tag, v) not in seen:
                        seen.add((tag, v))
                        found.append(("pdf xmp", tag, v))
    return found
