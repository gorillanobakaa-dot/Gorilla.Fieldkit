"""pins check: every helper source URL of a Gorilla OpenCode tree is immutable and hash-pinned."""
import re
from pathlib import Path

PIN_FILES = ["third_party/PINS.md", "internal/toolbox/SHA256SUMS", "scripts/build-toolbox.ps1",
             "scripts/build-toolbox.sh", "scripts/fetch-toolbox-extras.sh"]
SCRIPTS = {"scripts/build-toolbox.ps1", "scripts/build-toolbox.sh", "scripts/fetch-toolbox-extras.sh"}

_URL = re.compile(r"https?://[^\s\"'<>)\]|`,;]+")
_HEX = re.compile(r"(?<![0-9A-Za-z])[0-9a-fA-F]{64}(?![0-9A-Za-z])")
_VAR = re.compile(r"\$\{(\w+)\}|\$(\w+)")
_ASSIGN = re.compile(r"""(?:^|[\s;(])\$?([A-Za-z_]\w*)\s*=\s*["']?([^"'\s)]+)""")
_ARRAY = re.compile(r"\[([^\]\s]+)\]=(\S+)")
_ARCHIVE = re.compile(r"\.(zip|7z|tar\.(gz|xz|bz2|zst)|tgz|txz|tbz2|gz|xz|exe|msi|dmg|pkg|deb|rpm|whl)$", re.I)
_VERSION = re.compile(r"\d+\.\d+|\d{4,}|(?<![0-9a-f])[0-9a-f]{40}(?![0-9a-f])", re.I)

# (kind, regex on the URL, why). First match names the finding.
ROLLING = [
    ("latest", re.compile(r"(?<![a-z0-9])latest(?![a-z0-9])", re.I), "'latest' moves with every upstream release"),
    ("nightly", re.compile(r"nightly", re.I), "a nightly build is replaced every night"),
    ("rolling", re.compile(r"rolling", re.I), "a rolling build is replaced in place"),
    ("autobuild", re.compile(r"autobuild", re.I), "an autobuild is deleted by its maker (BtbN, 2026-11-01)"),
    ("refs-heads", re.compile(r"refs/heads/", re.I), "a branch moves with every commit"),
    ("branch-archive", re.compile(r"(/archive/|/raw/|raw\.githubusercontent\.com/[^/]+/[^/]+/|/blob/|/tarball/|/zipball/)"
                                  r"(refs/heads/)?(master|main|HEAD|trunk|develop)(?![\w.-]*\d)(?:[/.]|$)", re.I),
     "a master/main/HEAD archive changes with every commit"),
]


def _strip(u):
    return u.rstrip(".:!?}")


def _expand(text, env):
    for _ in range(4):
        new = _VAR.sub(lambda m: env.get(m.group(1) or m.group(2), m.group(0)), text)
        if new == text:
            break
        text = new
    return text


def _env(lines):
    env = {}
    for l in lines:
        if l.lstrip().startswith("#"):
            continue
        for m in _ASSIGN.finditer(l):
            env[m.group(1)] = m.group(2)
    for k in list(env):
        env[k] = _expand(env[k], env)
    return env


def rolling_reason(url):
    """-> (kind, why) when the URL is not immutable, else None."""
    for kind, rx, why in ROLLING:
        if rx.search(url):
            return kind, why
    if _ARCHIVE.search(url.split("?")[0].split("#")[0]) and not _VERSION.search(url):
        return "unversioned", "a download path with no version in it points at whatever is there today"
    return None


def _has_name(line, name):
    return bool(re.search(r"(?<![\w.-])" + re.escape(name) + r"(?![\w-])", line))


def scan(root):
    """-> (findings, urls_seen). A finding is {kind, url, file, line, why}."""
    root = Path(root)
    findings, seen = [], []
    texts = {}
    for rel in PIN_FILES:
        p = root / rel
        if p.is_file():
            texts[rel] = p.read_text(encoding="utf-8", errors="replace").splitlines()
        else:
            findings.append({"kind": "missing-file", "url": "", "file": rel, "line": 0,
                             "why": "a file the pins are read from is not in the tree"})
    # the pin lines: every line holding a 64-hex digest, variables expanded
    pinned = []
    for rel, lines in texts.items():
        env = _env(lines) if rel in SCRIPTS else {}
        for l in lines:
            if _HEX.search(l):
                pinned.append(_expand(l, env))
    for l in texts.get("internal/toolbox/SHA256SUMS", []):
        s = l.strip()
        if s and not s.startswith("#") and not re.fullmatch(r"[0-9a-fA-F]{64}\s+\S+", s):
            findings.append({"kind": "unpinned", "url": s[:80], "file": "internal/toolbox/SHA256SUMS",
                             "line": texts["internal/toolbox/SHA256SUMS"].index(l) + 1,
                             "why": "a line that is not '<sha256>  <platform>/<file>'"})
    for rel, lines in texts.items():
        if rel == "internal/toolbox/SHA256SUMS":
            continue
        env = _env(lines) if rel in SCRIPTS else {}
        for n, raw in enumerate(lines, 1):
            if rel in SCRIPTS and raw.lstrip().startswith("#"):
                continue
            line = _expand(raw, env)
            keys = []
            am = _ARRAY.search(raw)
            if am:
                keys.append(_expand(am.group(1), env))
            urls = {_strip(u) for u in _URL.findall(line)}
            for u in sorted(urls):
                seen.append(u)
                why = rolling_reason(u)
                if why:
                    findings.append({"kind": why[0], "url": u, "file": rel, "line": n, "why": why[1]})
                    continue
                path = u.split("?")[0].split("#")[0]
                if not _ARCHIVE.search(path):
                    continue                                   # a project page or a base, not a download
                names = keys + [path.rsplit("/", 1)[-1]]
                if _HEX.search(line) or any(_has_name(p, k) for p in pinned for k in names):
                    continue
                findings.append({"kind": "unpinned", "url": u, "file": rel, "line": n,
                                 "why": "no sha256 pin names this download"})
    return findings, seen
