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
    ("openai-key", r"(?<![\w.-])sk-(proj-)?[A-Za-z0-9_-]{20,}\b"),   # not glued to a word: imx53-sk-imx53-... is a file
    ("anthropic-key", r"\bsk-ant-[A-Za-z0-9_-]{20,}\b"),
    ("google-api-key", r"\bAIza[0-9A-Za-z_-]{35}\b"),
    ("aws-access-key", r"\bAKIA[0-9A-Z]{16}\b"),
    ("private-key", r"-----BEGIN (RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    ("url-credentials", r"https?://[^/\s@\"']+@[^/\s]+"),   # user:pass@ or token@ in a URL
]
PERSONAL = [
    ("windows-user-path", r"[A-Za-z]:\\+Users\\+(?!Public\\|Default\\|Default User\\|All Users\\|<)([^\\\s\"'<>]+)"),
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
EMAIL_OK = re.compile(r"(@example\.(com|org)|noreply|no-reply|@users\.noreply\.github\.com|@localhost"
                      # Mozilla's public add-on and theme ids (default-theme@mozilla.org), and picture files named
                      # name@2x.png, which the address pattern reads as an address (2026-10-16 push of the 158 set)
                      r"|@mozilla\.(org|com)|\.(png|jpe?g|svg|ico|webp|gif|avif|bmp))$", re.I)

# A browser profile: the person's bookmarks and history (places.sqlite), cookies, saved passwords (logins.json +
# key4.db), form entries, open tabs and sessions (.jsonlz4), certificates, site storage; Chromium's equivalents.
# Owner 2026-10-16: "make sure you don't upload my personal data like my bookmarks and history search ... those are
# mine, private". Never allowed by any flag or allow mark: a finding of these kinds always fails the scan.
PROFILE_FILE = re.compile(
    r"(^|/)("
    r"(places|cookies|formhistory|favicons|permissions|webappsstore|content-prefs|storage|protections|cert9|key4|"
    r"cert8|key3|signons|history|bookmarks|downloads|chromeappsstore)\.(sqlite|db)(-wal|-shm|-journal)?"
    r"|logins(-backup)?\.json|signedInUser\.json|sessionCheckpoints\.json|handlers\.json|extension-preferences\.json"
    r"|[^/]*\.(mozlz4|jsonlz4|baklz4)"
    r"|(bookmarkbackups|sessionstore-backups|datareporting|storage/default)/[^/]+"
    r"|Login Data|Web Data|History|Bookmarks|Cookies|Top Sites|Visited Links|Favicons"
    r")$", re.I)
PROFILE_KINDS = ("browser-profile-data", "browser-profile-data-in-patch", "sqlite-database", "bookmarks-export")
_PATCH_PATH = re.compile(r"^(?:diff --git a/(\S+) b/(\S+)|\+\+\+ b/(\S+)|--- a/(\S+))")


def profile_findings(rel, data):
    """Findings for browser profile data in one file: by its path (rel, '/' separated), by its first bytes (an SQLite
    database, Mozilla's mozLz4 compression, a bookmarks export) and, in a patch, by the paths the patch writes."""
    out = []
    rel = str(rel).replace("\\", "/")
    if PROFILE_FILE.search(rel):
        out.append({"kind": "browser-profile-data", "line": 0, "excerpt": _mask(rel.rsplit("/", 1)[-1])})
    head = data[:64] if isinstance(data, (bytes, bytearray)) else b""
    if head.startswith(b"SQLite format 3\x00"):
        out.append({"kind": "sqlite-database", "line": 0, "excerpt": "SQLite format 3"})
    if head.startswith(b"mozLz40\x00"):
        out.append({"kind": "browser-profile-data", "line": 0, "excerpt": "mozLz40 (session or backup)"})
    text = _as_text(data) if isinstance(data, (bytes, bytearray)) else str(data)
    if text.lstrip("\ufeff \r\n\t")[:40].upper().startswith("<!DOCTYPE NETSCAPE-BOOKMARK-FILE-1>"):   # how an export STARTS
        out.append({"kind": "bookmarks-export", "line": 1, "excerpt": "NETSCAPE-Bookmark-file-1"})
    if rel.endswith((".patch", ".diff")):
        for n, line in enumerate(text.splitlines(), 1):
            m = _PATCH_PATH.match(line)
            p = m and next((g for g in m.groups() if g), None)
            if p and PROFILE_FILE.search(p):
                out.append({"kind": "browser-profile-data-in-patch", "line": n, "excerpt": _mask(p.rsplit("/", 1)[-1])})
    return out


def _mask(s):
    s = s.strip()
    return s if len(s) <= 6 else s[:4] + "…" + s[-2:]


def private_terms(local=None):
    local = settings.local_settings() if local is None else local
    return [t for t in (local.get("privacy", {}) or {}).get("terms", []) if t and len(t) > 2]



def term_pattern(term):
    """A private term matches as a whole word, not inside another one. 2026-10-14: a five-letter name
    matched inside 36 unrelated domain names in uBlock's third-party filter lists on the public
    Firefox tree, so the scan could not gate that repository. Letters and digits on either side
    mean 'inside a word'; punctuation, spaces and line ends do not."""
    return re.compile(r"(?<![a-z0-9])" + re.escape(term.lower()) + r"(?![a-z0-9])")

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
            if term_pattern(t).search(low):
                out.append({"kind": "private-term", "line": lineno, "excerpt": _mask(t)})
    return out


ZIP_LIKE = {".zip", ".docx", ".xlsx", ".pptx", ".docm", ".xlsm", ".pptm", ".jar", ".whl", ".xpi"}


def _as_text(data):
    # binary: still look for secrets and terms in the raw bytes
    return data.decode("latin-1") if b"\0" in data[:4096] else data.decode("utf-8", "replace")


def not_scanned(why):
    """A finding for something that could not be looked at. Fail closed: unread is never 'clean'."""
    return {"kind": "not-scanned", "line": 0, "excerpt": f"not scanned: {why}"}


LARGE_LIMIT = 1_000_000_000     # a text file up to this size is scanned in pieces; larger is reported, not skipped
CHUNK_LINES = 50_000


def _scan_large(f, size, terms, **kw):
    """A file over TEXT_LIMIT, line by line in pieces, with the same rules and true line numbers (2026-10-10: an 11 MB
    System.map and a source tree listing in the kernel repository were reported as not scanned, and blocked)."""
    if size > LARGE_LIMIT:
        return [not_scanned(f"too large ({size:,} bytes > {LARGE_LIMIT:,})")]
    if f.suffix.lower() == ".pdf":
        return [not_scanned(f"too large for a PDF scan ({size:,} bytes > {TEXT_LIMIT:,})")]
    hits, buf, start = [], [], 1
    try:
        with open(f, "rb") as fh:
            binary = b"\0" in fh.read(4096)
            fh.seek(0)
            for n, raw in enumerate(fh, 1):
                buf.append(raw.decode("latin-1") if binary else raw.decode("utf-8", "replace"))
                if len(buf) >= CHUNK_LINES:
                    for h in scan_text("".join(buf), terms, **kw):
                        hits.append(dict(h, line=h["line"] + start - 1))
                    buf, start = [], n + 1
            if buf:
                for h in scan_text("".join(buf), terms, **kw):
                    hits.append(dict(h, line=h["line"] + start - 1))
    except OSError as e:
        return [not_scanned(f"unreadable ({e.strerror or type(e).__name__})")]
    return hits


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


def scan_path(path, terms=None, git_only=False, examined=None, **kw):
    """Scan a file or folder tree. Returns {file: [findings]} (only files with findings).

    git_only=True scans exactly what git would publish (respects .gitignore);
    it refuses to guess if the folder is not a git repository.

    examined: a list, if given, has the number of files examined appended to it.
    The caller cannot count them itself without repeating the walk, and a
    privacy report that cannot say how many files it read is indistinguishable
    from one that read none (2026-10-13: the count printed was the number of
    PATH ARGUMENTS, so a folder of five files reported "out of 1 file scanned").
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
    if examined is not None:
        examined.append(len(files))
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
                hits = _scan_large(f, size, terms, **kw)
                if hits:
                    report[str(f)] = hits
                continue
            data = f.read_bytes()
        except OSError as e:
            report[str(f)] = [not_scanned(f"unreadable ({e.strerror or type(e).__name__})")]
            continue
        hits = scan_text(_as_text(data), terms, **kw)
        hits += profile_findings(f.relative_to(path).as_posix() if path.is_dir() else f.name, data)
        if data[:5] == b"%PDF-" or f.suffix.lower() == ".pdf":
            report.update(_scan_pdf(data, terms, str(f), **kw))
        # names inside the path count too - but only the part inside the scanned
        # folder: the folder's own location (C:\Users\<login>) is not published
        rel = str(f.relative_to(path)) if path.is_dir() else f.name
        for t in terms:
            if term_pattern(t).search(rel.lower()):
                hits.append({"kind": "private-term-in-filename", "line": 0, "excerpt": _mask(t)})
        if hits:
            report[str(f)] = hits
    return report


def push_files(repo, rng):
    """Files a push publishes: added, copied, modified or renamed in the commit range `rng` (A..B). Fails closed."""
    import subprocess
    r = subprocess.run(["git", "-C", str(repo), "diff", "--name-only", "-z", "--no-renames", "--diff-filter=ACMR", rng],
                       capture_output=True)
    if r.returncode != 0:
        raise ValueError(f"git could not list the range {rng}: {r.stderr.decode('utf-8', 'replace').strip()[:200]}")
    return [f for f in r.stdout.decode("utf-8", "replace").split("\0") if f]


def push_check(repo, rng, terms=None):
    """-> {"files": n, "report": {path: [findings]}, "profile": [paths with browser profile data]}
    Scans each file AS IT IS IN THE PUSHED COMMIT (git show B:path), not the working copy, so what is checked is
    exactly what reaches the remote. Profile data is matched on the repository path as well."""
    import subprocess
    terms = private_terms() if terms is None else terms
    tip = rng.split("..")[-1] or "HEAD"
    files = push_files(repo, rng)
    report = {}
    for rel in files:
        r = subprocess.run(["git", "-C", str(repo), "show", f"{tip}:{rel}"], capture_output=True)
        if r.returncode != 0:
            report[rel] = [not_scanned("not readable from the pushed commit")]
            continue
        data = r.stdout
        hits = profile_findings(rel, data)
        hits += scan_text(_as_text(data), terms) if len(data) <= LARGE_LIMIT else [not_scanned("over the size limit")]
        if hits:
            report[rel] = hits
    profile = [p for p, h in report.items() if any(x["kind"] in PROFILE_KINDS for x in h)]
    return {"files": len(files), "report": report, "profile": profile}


PRE_PUSH = """#!/bin/sh
# Installed by: fieldkit privacy install-hook (owner 2026-10-16: nothing private reaches GitHub).
# Every push is scanned AS PUSHED: secrets, personal paths and names, emails, and browser profile data
# (bookmarks, history, cookies, passwords, sessions). Any finding refuses the push.
z=0000000000000000000000000000000000000000
while read lref lsha rref rsha; do
  [ "$lsha" = "$z" ] && continue
  if [ "$rsha" = "$z" ]; then
    base=$(git merge-base "$lsha" refs/remotes/origin/HEAD 2>/dev/null || git rev-list --max-parents=0 "$lsha" | tail -n 1)
    rng="$base..$lsha"
  else
    rng="$rsha..$lsha"
  fi
  {fieldkit} privacy push-check . --range "$rng" || { echo "pre-push: refused, see the findings above"; exit 1; }
done
exit 0
"""


def install_hook(repo, fieldkit_cmd):
    """Write .git/hooks/pre-push (the repository's own hooks folder, never committed). -> its path."""
    import subprocess
    r = subprocess.run(["git", "-C", str(repo), "rev-parse", "--git-path", "hooks"], capture_output=True, text=True)
    if r.returncode != 0:
        raise ValueError(f"{repo} is not a git repository")
    hooks = Path(r.stdout.strip())
    hooks = hooks if hooks.is_absolute() else Path(repo) / hooks
    hooks.mkdir(parents=True, exist_ok=True)
    p = hooks / "pre-push"
    if p.exists() and "fieldkit privacy" not in p.read_text(encoding="utf-8", errors="replace"):
        raise ValueError(f"{p} exists and is not ours: merge it by hand")
    p.write_text(PRE_PUSH.replace("{fieldkit}", fieldkit_cmd), encoding="utf-8", newline="\n")
    p.chmod(0o755)
    return p
