"""The build environment a recipe asks for, worked out on this machine (Linux and macOS; Windows: nothing).

A recipe says it in data (`toolenv:`); this reads it:

    toolenv:
      path_if_missing: {cargo: ["~/.cargo/bin"]}     # a tool not on PATH is looked for in these folders
      tmpdir: {min_free_gb: 2, fallback: "~/.cache/fieldkit-scratch/tmp"}   # /tmp too small or noexec

-> the variables to add to the environment of every command stage and toolchain probe ({} when nothing is needed).
A build that needs a hand-set PATH or TMPDIR (job T-20261101-161350-424c2c, plan W6) now needs neither."""
import os
import shutil
import sys
import tempfile
from pathlib import Path


def _which(tool, path):
    return shutil.which(tool, path=path)


def _can_exec(folder):
    """True when a program in `folder` may run (the folder is not mounted noexec)."""
    try:
        with tempfile.NamedTemporaryFile("w", dir=folder, suffix=".sh", delete=False) as f:
            f.write("#!/bin/sh\nexit 0\n")
            name = f.name
        try:
            os.chmod(name, 0o700)
            return os.system(f"'{name}' >/dev/null 2>&1") == 0
        finally:
            os.unlink(name)
    except OSError:
        return False


def tmp_ok(folder, min_free_gb, free=None, can_exec=None):
    folder = str(folder)
    try:
        left = (free or (lambda p: shutil.disk_usage(p).free))(folder)
    except OSError:
        return False
    return left >= min_free_gb * (1 << 30) and (can_exec or _can_exec)(folder)


def resolve(spec, environ=None, platform=None, free=None, can_exec=None):
    """-> {VAR: value} to add; `free`, `can_exec`, `environ` and `platform` are for tests."""
    if not spec or (platform or sys.platform) == "win32":
        return {}
    environ = os.environ if environ is None else environ
    out = {}
    path = environ.get("PATH", os.defpath)
    add = []
    for tool, folders in (spec.get("path_if_missing") or {}).items():
        if _which(tool, path):
            continue
        for d in folders:
            d = os.path.expanduser(d)
            if os.path.isdir(d) and d not in add and _which(tool, d):
                add.append(d)
                break
    if add:
        out["PATH"] = os.pathsep.join(add + ([path] if path else []))
    t = spec.get("tmpdir")
    if t:
        cur = environ.get("TMPDIR") or "/tmp"
        need = float(t.get("min_free_gb", 2))
        if not (os.path.isdir(cur) and tmp_ok(cur, need, free, can_exec)):
            fb = Path(os.path.expanduser(t["fallback"]))
            fb.mkdir(parents=True, exist_ok=True)
            out["TMPDIR"] = str(fb)
    return out
