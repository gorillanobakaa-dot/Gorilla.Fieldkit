"""Discover every tool in toolbox/ without hand-writing a registry entry for each.

For each script in toolbox/<source>/ it derives, from the file alone:
  - title: the first line of the docstring (Python) or the first comment line
  - platforms: .ps1/.cs/.cmd -> windows; .sh -> linux; Python -> from what it
    touches (winreg, ctypes.windll, powershell -> windows; /proc, /sys, apt,
    dpkg, pactl -> linux; otherwise both)
  - probe safety (registry.probe_safety) for Python
  - portable: False when it hard-codes a user's home (C:\\Users\\<name> or
    /home/<name>), with the count - the first thing to fix before it can run
    on another machine
  - test: the source's test command from imports.yaml, or test_*.py beside it
Nothing is executed.
"""
import ast
import re
from pathlib import Path

from .. import gather
from .registry import probe_safety

SCRIPT_EXT = {".py", ".sh", ".ps1", ".js", ".cs", ".cmd"}
WIN_HINTS = re.compile(r"\bwinreg\b|ctypes\.windll|powershell|\bwmic\b|HKEY_|\.exe\b|win32com|os\.startfile")
LINUX_HINTS = re.compile(r"/proc/|/sys/|\bapt(-get)?\b|\bdpkg\b|\bpactl\b|\bsystemctl\b|/etc/")
_HOME_RX = re.compile(r"[A-Za-z]:[\\/]+Users[\\/]+(?!Public|Default|<)([^\\/\s\"'`]+)|/home/([a-z_][a-z0-9_-]*)")


class _HomePaths:
    """Hard-coded home folders, ignoring documentation placeholders (C:\\Users\\.., /home/you),
    with the same placeholder list the privacy scan uses."""

    def findall(self, text):
        from ..core.privacy import PLACEHOLDER_USERS
        return [m.group(0) for m in _HOME_RX.finditer(text)
                if (m.group(1) or m.group(2) or "").strip(".,;:()[]").lower() not in PLACEHOLDER_USERS]

    def search(self, text):
        return bool(self.findall(text))


HOME_PATH = _HomePaths()


def _title(path, text):
    if path.suffix == ".py":
        try:
            doc = ast.get_docstring(ast.parse(text))
        except SyntaxError:
            doc = None
        if doc:
            return doc.strip().splitlines()[0].strip()
    for line in text.splitlines()[:15]:
        if line.startswith("#!"):                       # shebang, not a description
            continue
        s = line.strip().lstrip("#<!-/*").strip()
        if s and not s.startswith(("!", "=", "requires", "-*-")) and len(s) > 8:
            return s[:120]
    return ""


def _platforms(path, text):
    if path.suffix in (".ps1", ".cs", ".cmd"):
        return ["windows"]
    if path.suffix == ".sh":
        return ["linux"]
    w, l = bool(WIN_HINTS.search(text)), bool(LINUX_HINTS.search(text))
    if w and not l:
        return ["windows"]
    if l and not w:
        return ["linux"]
    return ["windows", "linux"]


def discover(toolbox=None):
    toolbox = Path(toolbox or gather.TOOLBOX)
    sources = {s["name"]: s for s in gather.load_manifest()}
    out = []
    for folder in sorted(p for p in toolbox.iterdir() if p.is_dir()):
        src = sources.get(folder.name, {})
        scripts = sorted(p for p in folder.rglob("*") if p.is_file() and p.suffix in SCRIPT_EXT
                         and "__pycache__" not in p.parts)
        tests = [p for p in scripts if p.name.startswith("test_") or "tests" in p.relative_to(folder).parts]
        for p in scripts:
            if p in tests:
                continue
            text = p.read_text(encoding="utf-8-sig", errors="replace")
            homes = HOME_PATH.findall(text)
            entry = {"id": f"{folder.name}/{p.relative_to(folder).as_posix()}", "source": folder.name,
                     "title": _title(p, text), "path": str(p), "platforms": _platforms(p, text),
                     "portable": not homes, "hardcoded_home_paths": len(homes),
                     "tests": src.get("tests"), "discovered": True}
            if p.suffix == ".py":
                entry["probe_safe"], entry["probe_reason"] = probe_safety(p)
            out.append(entry)
        if tests and not src.get("tests"):
            for e in out:
                if e["source"] == folder.name:
                    e["tests_present"] = [t.name for t in tests]
    return out
