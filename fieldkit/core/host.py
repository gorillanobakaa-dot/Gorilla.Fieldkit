"""Which machine are we on, and where are the tools?

Every difference between Windows and Debian lives here or in a tool's own
small adapter, never scattered through the harness code. Callers ask
`host()` once and branch on facts, not on guesses.
"""
import os
import platform
import shutil
import sys
from functools import lru_cache
from pathlib import Path

# Places tools hide on Windows that are not on PATH by default.
WINDOWS_EXTRA = {
    "soffice": [r"C:\Program Files\LibreOffice\program\soffice.exe",
                r"C:\Program Files (x86)\LibreOffice\program\soffice.exe"],
    "pdftotext": [r"C:\Program Files\Git\mingw64\bin\pdftotext.exe"],
    "pwsh": [r"C:\Program Files\PowerShell\7\pwsh.exe"],
    "bash": [r"C:\Program Files\Git\bin\bash.exe"],
}


def _os_release():
    data = {}
    try:
        for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                data[k] = v.strip().strip('"')
    except OSError:
        pass
    return data


@lru_cache(maxsize=1)
def host():
    """Facts about this machine, as a plain dict (JSON-safe)."""
    system = platform.system()
    info = {
        "system": system,                     # Windows | Linux | Darwin
        "is_windows": system == "Windows",
        "is_linux": system == "Linux",
        "release": platform.release(),
        "machine": platform.machine(),
        "python": sys.version.split()[0],
        "distro": None,
        "distro_like": [],
        "cpus": os.cpu_count() or 1,
    }
    if info["is_linux"]:
        rel = _os_release()
        info["distro"] = rel.get("ID")
        info["distro_like"] = rel.get("ID_LIKE", "").split()
    return info


def is_debian_family():
    h = host()
    return h["is_linux"] and (h["distro"] in ("debian", "ubuntu") or "debian" in h["distro_like"])


def find_tool(name, extra=None):
    """Full path to a program, or None. Looks on PATH, then known locations."""
    found = shutil.which(name)
    if found:
        return found
    candidates = list(extra or [])
    if host()["is_windows"]:
        candidates += WINDOWS_EXTRA.get(name, [])
    for c in candidates:
        if Path(c).is_file():
            return str(c)
    return None


def platform_ok(platforms):
    """True if this host is in a stage/tool's platform list.

    platforms: None/[] = anywhere; entries are 'windows', 'linux', 'debian'.
    """
    if not platforms:
        return True
    h = host()
    for p in platforms:
        if p == "windows" and h["is_windows"]:
            return True
        if p == "linux" and h["is_linux"]:
            return True
        if p == "debian" and is_debian_family():
            return True
    return False
