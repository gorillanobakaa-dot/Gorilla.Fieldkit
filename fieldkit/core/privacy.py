"""Find what must never be published: secrets, personal paths, names, emails.

Generalised from Gorilla.firefox's publish_gate / scan_public_patchset and a
document harness's name hunt. Deterministic regexes plus a private word list.

The private word list (real names, ID numbers, your email) is
read from fieldkit.local.json -> "privacy": {"terms": [...]} so the list
itself is never committed. Values are never echoed in full: findings show a
masked excerpt, so the scan report is itself safe to paste.

    fieldkit privacy scan PATH [PATH...] [--json]
Exit code 0 = clean, 3 = findings.
"""
import re
import zipfile
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


def _scan_zip(f, terms, **kw):
    """Findings for each member, keyed 'archive.zip!member/path'."""
    out = {}
    with zipfile.ZipFile(f) as z:
        for info in z.infolist():
            if info.is_dir() or info.file_size > TEXT_LIMIT:
                continue
            hits = scan_text(_as_text(z.read(info)), terms, **kw)
            for t in terms:
                if t.lower() in info.filename.lower():
                    hits.append({"kind": "private-term-in-filename", "line": 0, "excerpt": _mask(t)})
            if hits:
                out[f"{f}!{info.filename}"] = hits
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
    else:
        files = [path] if path.is_file() else [
            p for p in path.rglob("*") if p.is_file() and not (set(p.relative_to(path).parts) & SKIP_DIRS)]
    report = {}
    for f in files:
        # Archives (handover zips, .docx/.xlsx/.pptx) are compressed: scanning
        # their bytes proves nothing, so scan every member instead.
        if f.suffix.lower() in ZIP_LIKE and zipfile.is_zipfile(f):
            report.update(_scan_zip(f, terms, **kw))
            continue
        try:
            if f.stat().st_size > TEXT_LIMIT:
                continue
            data = f.read_bytes()
        except OSError:
            continue
        hits = scan_text(_as_text(data), terms, **kw)
        # names inside the path count too - but only the part inside the scanned
        # folder: the folder's own location (C:\Users\<login>) is not published
        rel = str(f.relative_to(path)) if path.is_dir() else f.name
        for t in terms:
            if t.lower() in rel.lower():
                hits.append({"kind": "private-term-in-filename", "line": 0, "excerpt": _mask(t)})
        if hits:
            report[str(f)] = hits
    return report
