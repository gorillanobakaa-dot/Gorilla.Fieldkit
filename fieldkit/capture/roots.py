"""Where agents leave work: the table of scan roots for each system, and what is never read.

A root is {name, path, mode, agent}: mode "files" = loose scripts under it, "transcripts" = an agent's session records.
Overrides for tests and for unusual machines (all optional): FIELDKIT_SCAN_HOME, FIELDKIT_SCAN_DOCUMENTS,
FIELDKIT_SCAN_LOCAL (the Fieldkit/local folder), FIELDKIT_SCRATCH_ROOT (the scratch tool's own), and the system's
TMP/TEMP/TMPDIR. A root that does not exist is skipped by the scanner, never an error.
"""
import os
import re
import sys
import tempfile
from pathlib import Path

SCRIPT_EXT = {".py": "python", ".sh": "bash", ".bash": "bash", ".zsh": "bash", ".ps1": "powershell", ".psm1": "powershell",
              ".cmd": "batch", ".bat": "batch", ".rs": "rust", ".go": "go", ".js": "javascript", ".mjs": "javascript",
              ".cjs": "javascript", ".ts": "typescript", ".java": "java", ".rb": "ruby", ".pl": "perl", ".c": "c",
              ".cpp": "cpp", ".cs": "csharp", ".lua": "lua", ".php": "php"}
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".pytest_cache", ".venv", "venv", "site-packages", "target",
             ".cache", "cache", "Cache", "CachedData", "GPUCache", "Code Cache", "Crashpad", "blob_storage", "dist",
             ".mypy_cache", ".tox", "plugins", "extensions", "browser-profile", "Service Worker"}
#: names whose content is never opened (credentials, keys, tokens, auth caches). Matched on the file name.
_SECRET_NAME = re.compile(r"(cred|secret|token|passw|oauth|\.env|\.pem$|\.key$|\.pfx$|\.p12$|id_rsa|id_ed25519|"
                          r"\.netrc|\.npmrc|\.pypirc|auth|cookie|keychain|\.kdbx$|\.gpg$|vault)", re.I)
#: our own test sandboxes and runner copies: made by the harness, not by an agent solving a problem
SKIP_PREFIX = ("fkcap-", "pytest-of-", "pytest-", "fk-test", "fkscan-")
MAX_FILE = 1_000_000           # a script bigger than this is a data dump; kept by name only, not read
MAX_DEPTH = 8


def is_secret_name(name):
    return bool(_SECRET_NAME.search(name or ""))


def _env_path(name):
    v = os.environ.get(name)
    return Path(v) if v else None


def _scratch():
    try:
        from ..scratch import root
        return Path(root())
    except Exception:                                    # noqa: BLE001
        return Path(r"C:\fk-scratch") if sys.platform == "win32" else Path.home() / ".cache" / "fieldkit-scratch"


def _documents(home):
    for cand in (home / "Documents", home / "documents"):
        if cand.is_dir():
            return cand
    return home / "Documents"


def roots(platform=None, home=None, fieldkit=None):
    """-> list of root dicts for `platform` (win32 | linux | darwin; default this system)."""
    platform = platform or sys.platform
    win = platform.startswith("win")
    home = Path(home) if home else (_env_path("FIELDKIT_SCAN_HOME") or Path.home())
    docs = _env_path("FIELDKIT_SCAN_DOCUMENTS") or _documents(home)
    if fieldkit is None:
        fieldkit = _env_path("FIELDKIT_SCAN_LOCAL") or Path(__file__).resolve().parents[2] / "local"
    scratch = _scratch()
    tmp = Path(tempfile.gettempdir())
    out = []

    def add(name, path, mode="files", agent=None):
        out.append({"name": name, "path": str(path), "mode": mode, "agent": agent})

    add("documents-scripts", docs / "Scripts")
    add("fieldkit-local", fieldkit)
    add("scratch-root", scratch)
    add("scratch-scripts", scratch / "scripts")
    add("system-temp", tmp)
    if not win:
        add("system-temp-tmp", "/tmp")
    # agents' own scratch folders: Claude's %TEMP%/claude/**/scratchpad, its home, Gemini's home, OpenCode's data dirs
    add("claude-scratchpad", tmp / "claude")
    add("claude-home", home / ".claude")
    add("gemini-home", home / ".gemini")
    opencode = [home / ".local" / "share" / "opencode", home / ".local" / "share" / "gorilla-opencode",
                home / ".config" / "opencode", home / ".config" / "gorilla-opencode"]
    if win:
        for var in ("APPDATA", "LOCALAPPDATA"):
            base = os.environ.get(var)
            if base:
                opencode += [Path(base) / "opencode", Path(base) / "gorilla-opencode"]
    elif platform == "darwin":
        opencode.append(home / "Library" / "Application Support" / "opencode")
    for i, p in enumerate(opencode):
        add(f"opencode-data-{i}" if i else "opencode-data", p)
    add("linux-config", home / ".config" / "gemini")          # ~/.config also exists on Windows ports of Linux tools
    add("linux-config-claude", home / ".config" / "claude")
    # transcripts
    add("claude-transcripts", home / ".claude" / "projects", "transcripts", "claude")
    add("gemini-transcripts", home / ".gemini" / "tmp", "transcripts", "gemini")
    add("gemini-history", home / ".gemini" / "history", "transcripts", "gemini")
    for i, p in enumerate(opencode):
        add(f"opencode-sessions-{i}" if i else "opencode-sessions", p, "transcripts", "opencode")
    return out
