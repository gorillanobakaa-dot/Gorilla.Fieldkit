"""No console window flashes (open item 38, 2026-10-28).

On Windows every console program started by a process with no console of its own gets a fresh black window. Hooks run
once per tool call, so the owner saw one flash per call. Two parts:
- hooks and background workers run under pythonw.exe (`windowless`), which has no console;
- a process with no console starts every child with CREATE_NO_WINDOW (`install`, which wraps subprocess.Popen), so
  git, gh and the helpers it starts stay hidden too.
pythonw has no stdout unless the parent passes pipes: `ensure_stdio` gives a hook a real stdout (fd 1, else the null
device), so a deny printed as JSON still reaches the caller; the exit code never depended on it.
"""
import os
import subprocess
import sys
from pathlib import Path, PureWindowsPath

NO_WINDOW = 0x08000000                 # CREATE_NO_WINDOW


def windowless(python, platform=None, exists=None):
    """-> the pythonw.exe beside a python.exe on Windows when it exists, else `python` unchanged."""
    if (platform or sys.platform) != "win32":
        return python
    p = PureWindowsPath(str(python))
    if p.name.lower() != "python.exe":
        return python
    w = str(p.with_name("pythonw.exe"))
    return w if (exists or os.path.isfile)(w) else python


def has_console(platform=None):
    """True when this process owns a console (False on pythonw, and always off Windows: nothing to hide)."""
    if (platform or sys.platform) != "win32":
        return True
    try:
        import ctypes
        return bool(ctypes.windll.kernel32.GetConsoleWindow())
    except (AttributeError, OSError):
        return True


def flags(creationflags=0, platform=None):
    """-> creationflags with CREATE_NO_WINDOW added on Windows."""
    return creationflags | NO_WINDOW if (platform or sys.platform) == "win32" else creationflags


_installed = False


def install(force=False, platform=None):
    """Wrap subprocess.Popen so a child of a console-less Windows process starts with CREATE_NO_WINDOW. A child that
    asked for its own console (CREATE_NEW_CONSOLE) or a detached one is left as asked. -> True when installed."""
    global _installed
    if _installed or (platform or sys.platform) != "win32" or not (force or not has_console(platform)):
        return False
    real = subprocess.Popen.__init__

    def init(self, *a, **kw):
        cf = kw.get("creationflags") or 0
        if not cf & (0x00000010 | 0x00000008):        # CREATE_NEW_CONSOLE, DETACHED_PROCESS
            kw["creationflags"] = cf | NO_WINDOW
        real(self, *a, **kw)

    subprocess.Popen.__init__ = init
    _installed = True
    return True


def ensure_stdio():
    """A pythonw process may have no stdout/stderr: use fd 1/2 when the parent gave pipes, else the null device."""
    for name, fd in (("stdout", 1), ("stderr", 2)):
        if getattr(sys, name) is None:
            try:
                os.fstat(fd)
                setattr(sys, name, os.fdopen(fd, "w", encoding="utf-8", closefd=False))
            except OSError:
                setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))
    if sys.stdin is None:
        sys.stdin = open(os.devnull, "r", encoding="utf-8")


def prepare():
    """What every hook / worker entry point calls first."""
    ensure_stdio()
    return install()
