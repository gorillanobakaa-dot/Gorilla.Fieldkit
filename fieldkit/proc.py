"""The one place fieldkit starts a child process (rules/CORE.md, owner order 2026-11-22).

On Windows every console program started by a process that has no console (a hook, a watcher, a scheduled task)
gets its own conhost.exe: 60,000 of them piled up. `CREATE_NO_WINDOW` still makes a hidden conhost; `DETACHED_PROCESS`
with redirected standard streams makes none. This module adds that flag (only when the caller already redirects
stdout and stderr, so a streaming build or an interactive tool still inherits its console) and fills stdin with
DEVNULL. On Linux and macOS every function is the plain `subprocess` call.

The enforcing test is `tests/test_no_console_spawn.py`: a raw `subprocess.run/Popen/...` outside this module fails CI
unless `fieldkit/proc_allow.yaml` lists it with a reason. Audit the live machine with `fieldkit hog consoles`.
"""
from __future__ import annotations

import subprocess
import sys

DETACHED_PROCESS = 0x00000008
CREATE_NEW_CONSOLE = 0x00000010


def _prepare(kw: dict) -> dict:
    """kw plus the no-console flags on Windows; unchanged elsewhere or when the streams are inherited."""
    if sys.platform != "win32":
        return kw
    out, err = kw.get("stdout"), kw.get("stderr")
    if not kw.get("capture_output") and not (out is not None and (err is not None)):
        return kw
    flags = kw.get("creationflags", 0) or 0
    if flags & CREATE_NEW_CONSOLE:
        return kw
    kw = dict(kw)
    kw["creationflags"] = flags | DETACHED_PROCESS
    if kw.get("stdin") is None and "input" not in kw:
        kw["stdin"] = subprocess.DEVNULL
    return kw


def run(*a, **kw):
    return subprocess.run(*a, **_prepare(kw))


def Popen(*a, **kw):  # noqa: N802 - mirrors subprocess.Popen
    return subprocess.Popen(*a, **_prepare(kw))


def check_output(*a, **kw):
    kw.pop("stdout", None)
    if sys.platform == "win32" and kw.get("stderr") is None and not _has_console():
        kw["stderr"] = subprocess.DEVNULL  # nobody can read an inherited stderr here anyway
    return subprocess.run(*a, **_prepare({**kw, "stdout": subprocess.PIPE}), check=True).stdout


def _has_console() -> bool:
    try:
        import ctypes
        return bool(ctypes.windll.kernel32.GetConsoleWindow())
    except Exception:
        return True


def check_call(*a, **kw):
    return subprocess.check_call(*a, **_prepare(kw))


def call(*a, **kw):
    return subprocess.call(*a, **_prepare(kw))
