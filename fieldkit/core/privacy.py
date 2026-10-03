"""Find what must never be published: secrets, personal paths, names, emails.

Generalised from Gorilla.firefox's publish_gate / scan_public_patchset and a
document harness's name hunt. Deterministic regexes plus a private word list.

The private word list (real names, ID numbers, your email) is
read from fieldkit.local.json -> "privacy": {"terms": [...]} so the list
itself is never committed. Values are never echoed in full: findings show a
masked excerpt, so the scan report is itself safe to paste.

    fieldkit privacy scan PATH [PATH...] [--json]
Exit code 0 = clean, 3 = findings.

Fail closed: anything that could not be read - a file or archive member over
TEXT_LIMIT, an unreadable file or folder, an encrypted or corrupt member, a PDF
stream whose filter cannot be undone - is itself a finding ("not-scanned"),
never a silent skip. PDF streams are decompressed (FlateDecode, ASCIIHex,
ASCII85; stdlib only) and the text layer is read with pypdfium2.
"""
import base64
import binascii
import bisect
import io
import os
import re
import zipfile
import zlib
from pathlib import Path

from . import settings

SECRETS = [
    ("github-token", r"\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}\b"),
    ("github-fine-token", r"\bgithub_pat_[A-Za-z0-9_]{40,}\b"),
    ("openai-key", r"\bsk-(proj-)?[A-Za-z0-9_-]{20,}\b"),
    ("anthropic-key", r"\bsk-ant-[A-Za-z0-9_-]{20,}\b"),
    ("google-api-key", r"\bAIza[0-9A-Za-z_-]{35}\b"),
    ("aws-access-key", r"\bAKIA[0-9A-Z]{16}\b"),
    ("private-key", r"-----BEGIN (RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    ("url-credentials", r"https?://[^/\s@\"']+@[^/\s]+"),   # user:pass@ or token@ in a URL
]
PERSONAL = [
    ("windows-user-path", r"[A-Za-z]:\\+Users\\+(?!Public\\|Default\\|All Users\\|<)([^\\\s\"'<>]+)"),
    ("windows-user-path", r"[A-Za-z]:/Users/(?!Public/|Default/)([^/\s\"']+)"),
    ("linux-home-path", r"/home/([a-z_][a-z0-9_-]*)"),
    ("email", r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
]
# Home-folder names used as documentation placeholders, not people's accounts.
PLACEHOLDER_USERS = {"", "you", "me", "user", "username", "name", "yourname", "your-name", "example",
                     "foo", "bar"}                 # kept short: when in doubt, report it
ALLOW_MARK = "privacy-scan: allow"          # put on a line holding a deliberate fake (tests)
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".pytest_cache"}
TEXT_LIMIT = 5_000_000
# Emails that are fine to publish (placeholders, bots).
EMAIL_OK = re.compile(r"(@example\.(com|org)|noreply|no-reply|@users\.noreply\.github\.com|@localhost)$", re.I)


def _mask(s):
    s = s.strip()
    return s if len(s) <= 6 else s[:4] + "…" + s[-2:]


def private_terms(local=None):
    local = settings.local_settings() if local is None else local
    return [t for t in (local.get("privacy", {}) or {}).get("terms", []) if t and len(t) > 2]


def scan_text(text, terms=(), allow_paths=False, allow_emails=False):
    """Return a list of findings {kind, line, excerpt} for one text."""
    out = []
    rules = list(SECRETS)
    if not allow_paths:
        rules += [r for r in PERSONAL if r[0] != "email"]
    if not allow_emails:
        rules += [r for r in PERSONAL if r[0] == "email"]
    for lineno, line in enumerate(text.splitlines(), 1):
        if ALLOW_MARK in line:
            continue
        for kind, rx in rules:
            for m in re.finditer(rx, line):
                if kind == "email" and EMAIL_OK.search(m.group(0)):
                    continue
                if kind in ("windows-user-path", "linux-home-path") and m.groups() and \
                        m.group(1).strip(".,;:()[]").lower() in PLACEHOLDER_USERS:
                    continue
                out.append({"kind": kind, "line": lineno, "excerpt": _mask(m.group(0))})
        low = line.lower()
        for t in terms:
            if t.lower() in low:
                out.append({"kind": "private-term", "line": lineno, "excerpt": _mask(t)})
    return out


ZIP_LIKE = {".zip", ".docx", ".xlsx", ".pptx", ".docm", ".xlsm", ".pptm", ".jar", ".whl", ".xpi"}


def _as_text(data):
    # binary: still look for secrets and terms in the raw bytes
    return data.decode("latin-1") if b"\0" in data[:4096] else data.decode("utf-8", "replace")


def not_scanned(why):
    """A finding for something that could not be looked at. Fail closed: unread is never 'clean'."""
    return {"kind": "not-scanned", "line": 0, "excerpt": f"not scanned: {why}"}


ZIP_DEPTH = 3          # archives inside archives inside archives; deeper is reported, not skipped


def _scan_zip(f, terms, label=None, depth=0, **kw):
    """Findings for each member, keyed 'archive.zip!member/path'.

    `f` is a path or a file-like object; `label` is how it is named in the report.
    Members over TEXT_LIMIT, unreadable members (encrypted, corrupt, unknown
    compression) and archives nested too deep are findings, not silent skips.
    """
    label = str(f) if label is None else label
    out = {}
    try:
        z = zipfile.ZipFile(f)
    except (zipfile.BadZipFile, OSError, ValueError, NotImplementedError, EOFError) as e:
        return {label: [not_scanned(f"unreadable archive ({type(e).__name__}: {e})")]}
    with z:
        for info in z.infolist():
            if info.is_dir():
                continue
            key = f"{label}!{info.filename}"
            hits = [{"kind": "private-term-in-filename", "line": 0, "excerpt": _mask(t)}
                    for t in terms if t.lower() in info.filename.lower()]
            if info.file_size > TEXT_LIMIT:
                hits.append(not_scanned(f"too large ({info.file_size:,} bytes > {TEXT_LIMIT:,})"))
                out[key] = hits
                continue
            try:
                data = z.read(info)
            except (zipfile.BadZipFile, RuntimeError, NotImplementedError, zlib.error, EOFError, OSError,
                    ValueError) as e:
                hits.append(not_scanned(f"unreadable ({type(e).__name__}: {e})"))
                out[key] = hits
                continue
            suffix = Path(info.filename).suffix.lower()
            if suffix in ZIP_LIKE and data[:4] == b"PK\x03\x04":
                if depth + 1 >= ZIP_DEPTH:
                    hits.append(not_scanned(f"archive nested more than {ZIP_DEPTH} deep"))
                else:
                    out.update(_scan_zip(io.BytesIO(data), terms, label=key, depth=depth + 1, **kw))
                if hits:
                    out.setdefault(key, []).extend(hits)
                continue
            hits = scan_text(_as_text(data), terms, **kw) + hits
            if hits:
                out[key] = hits
            if data[:5] == b"%PDF-":
                out.update(_scan_pdf(data, terms, key, **kw))
    return out


# -- PDF: text hides in compressed streams; read them, or say they were not read ----------------
_PDF_OBJ = re.compile(rb"\d+\s+\d+\s+obj\b")
_PDF_STREAM = re.compile(rb"\bstream(?:\r\n|\n|\r)")
_PDF_FILTER = re.compile(rb"/Filter\s*(\[[^\]]*\]|/[A-Za-z0-9]+)")
_PDF_LENGTH = re.compile(rb"/Length\s+(\d+)(?!\s+\d+\s+R)")
_PDF_IMAGE = re.compile(rb"/Subtype\s*/Image\b")
_PDF_IMAGE_FILTERS = {"DCTDecode", "DCT", "JPXDecode", "CCITTFaxDecode", "CCF", "JBIG2Decode"}


class _NotScanned(Exception):
    pass


def _inflate(raw):
    d = zlib.decompressobj()
    try:
        out = d.decompress(raw, TEXT_LIMIT + 1)
    except zlib.error as e:
        raise _NotScanned(f"FlateDecode stream could not be decoded ({e})") from None
    if len(out) > TEXT_LIMIT or d.unconsumed_tail:
        raise _NotScanned(f"FlateDecode stream decodes to more than {TEXT_LIMIT:,} bytes")
    if not d.eof:
        raise _NotScanned("FlateDecode stream is cut short (truncated or encrypted)")
    return out


def _unhex(raw):
    s = re.sub(rb"\s", b"", raw.split(b">", 1)[0])
    try:
        return binascii.unhexlify(s + b"0" * (len(s) % 2))
    except (binascii.Error, ValueError) as e:
        raise _NotScanned(f"ASCIIHexDecode stream could not be decoded ({e})") from None


def _un85(raw):
    s = raw.strip()
    s = s[2:] if s.startswith(b"<~") else s
    s = s.split(b"~>", 1)[0]
    try:
        return base64.a85decode(re.sub(rb"\s", b"", s))
    except ValueError as e:
        raise _NotScanned(f"ASCII85Decode stream could not be decoded ({e})") from None


_PDF_DECODERS = {"FlateDecode": _inflate, "Fl": _inflate, "ASCIIHexDecode": _unhex, "AHx": _unhex,
                 "ASCII85Decode": _un85, "A85": _un85}


def pdf_streams(data):
    """Yield (offset, decoded bytes | None, why-not) for every stream in a PDF.

    Stdlib only. Image sample data is not decoded (pixels hold no text; the raw
    bytes, where EXIF/XMP would sit, are scanned with the rest of the file).
    A stream with a filter we cannot undo is yielded with decoded=None and the
    reason, so the caller can report it instead of trusting it.
    """
    objs = [m.start() for m in _PDF_OBJ.finditer(data)]
    pos = 0
    while True:
        m = _PDF_STREAM.search(data, pos)
        if not m:
            return
        start = m.end()
        i = bisect.bisect_right(objs, m.start()) - 1
        head = data[objs[i]:m.start()] if i >= 0 else data[max(0, m.start() - 2048):m.start()]
        end = -1
        lm = _PDF_LENGTH.search(head)
        if lm:
            n = int(lm.group(1))
            if data[start + n:start + n + 32].lstrip().startswith(b"endstream"):
                end = start + n
        if end < 0:
            end = data.find(b"endstream", start)
        if end < 0:
            yield m.start(), None, "stream has no endstream (truncated file)"
            return
        pos = end + len(b"endstream")
        fm = _PDF_FILTER.search(head)
        filters = [x.decode("latin-1") for x in re.findall(rb"/([A-Za-z0-9]+)", fm.group(1))] if fm else []
        if not filters or _PDF_IMAGE.search(head):
            continue                                # plain stream: already in the raw scan
        raw = data[start:end]
        try:
            for name in filters:
                if name in _PDF_IMAGE_FILTERS:
                    break
                if name not in _PDF_DECODERS:
                    raise _NotScanned(f"{name} stream: no decoder for this filter")
                raw = _PDF_DECODERS[name](raw)
        except _NotScanned as e:
            yield m.start(), None, str(e)
            continue
        yield m.start(), raw, ""


def _pdf_text_layer(data):
    """The text a reader shows (pypdfium2), which catches fonts whose codes are not plain text."""
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(data)
    try:
        return "\n".join(doc[i].get_textpage().get_text_range() for i in range(len(doc)))
    finally:
        doc.close()


def _scan_pdf(data, terms, label, **kw):
    """Findings in a PDF's decoded streams and its text layer, keyed 'file.pdf!stream@OFFSET'."""
    out = {}
    for off, decoded, why in pdf_streams(data):
        key = f"{label}!stream@{off}"
        hits = [not_scanned(why)] if decoded is None else scan_text(_as_text(decoded), terms, **kw)
        if hits:
            out[key] = hits
    try:
        text = _pdf_text_layer(data)
    except Exception as e:  # noqa: BLE001 - any failure to read the text layer is a finding
        out[f"{label}!text"] = [not_scanned(f"text layer could not be read ({type(e).__name__}: {e})")]
    else:
        hits = scan_text(text, terms, **kw)
        if hits:
            out[f"{label}!text"] = hits
    return out


def git_publishable(folder):
    """Files git would publish from `folder`: tracked + untracked-but-not-ignored. None if not a repo."""
    import subprocess
    r = subprocess.run(["git", "-C", str(folder), "ls-files", "-co", "--exclude-standard", "-z"],
                       capture_output=True)
    if r.returncode != 0:
        return None
    return [Path(folder) / f for f in r.stdout.decode("utf-8", "replace").split("\0") if f]


def scan_path(path, terms=None, git_only=False, **kw):
    """Scan a file or folder tree. Returns {file: [findings]} (only files with findings).

    git_only=True scans exactly what git would publish (respects .gitignore);
    it refuses to guess if the folder is not a git repository.
    """
    terms = private_terms() if terms is None else terms
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"{path} does not exist: nothing scanned is not the same as nothing found")
    if git_only and path.is_dir():
        files = git_publishable(path)
        if files is None:
            raise ValueError(f"{path} is not a git repository; drop --git to scan every file")
        files = [f for f in files if f.is_file()]
    report = {}
    if not git_only or not path.is_dir():
        if path.is_file():
            files = [path]
        else:
            files = []

            def unreadable_dir(err):                # fail closed: a folder we cannot list is a finding
                report[str(err.filename or path)] = [not_scanned(f"folder unreadable ({err.strerror or err})")]
            for root, dirs, names in os.walk(path, onerror=unreadable_dir):
                dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
                files += [Path(root) / n for n in sorted(names) if n not in SKIP_DIRS]
    for f in files:
        # Archives (handover zips, .docx/.xlsx/.pptx) are compressed: scanning
        # their bytes proves nothing, so scan every member instead.
        try:
            is_zip = f.suffix.lower() in ZIP_LIKE and zipfile.is_zipfile(f)
        except OSError:
            is_zip = False
        if is_zip:
            report.update(_scan_zip(f, terms, **kw))
            continue
        try:
            size = f.stat().st_size
            if size > TEXT_LIMIT:
                report[str(f)] = [not_scanned(f"too large ({size:,} bytes > {TEXT_LIMIT:,})")]
                continue
            data = f.read_bytes()
        except OSError as e:
            report[str(f)] = [not_scanned(f"unreadable ({e.strerror or type(e).__name__})")]
            continue
        hits = scan_text(_as_text(data), terms, **kw)
        if data[:5] == b"%PDF-" or f.suffix.lower() == ".pdf":
            report.update(_scan_pdf(data, terms, str(f), **kw))
        # names inside the path count too - but only the part inside the scanned
        # folder: the folder's own location (C:\Users\<login>) is not published
        rel = str(f.relative_to(path)) if path.is_dir() else f.name
        for t in terms:
            if t.lower() in rel.lower():
                hits.append({"kind": "private-term-in-filename", "line": 0, "excerpt": _mask(t)})
        if hits:
            report[str(f)] = hits
    return report
