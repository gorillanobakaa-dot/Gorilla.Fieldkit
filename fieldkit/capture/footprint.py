"""Metadata-only clean-slate preview of shared temp and native session stores."""

import fnmatch
import os
import re
import stat
import sys
from pathlib import Path

from . import retention


def _machine_temp(environ):
    import winreg

    key = r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as registry:
        for name in ("TEMP", "TMP"):
            try:
                value, kind = winreg.QueryValueEx(registry, name)
            except FileNotFoundError:
                continue
            if kind in (winreg.REG_SZ, winreg.REG_EXPAND_SZ):
                yield "machine " + name, re.sub(
                    r"%([^%]+)%", lambda m: environ.get(m[1].upper(), m[0]), value
                )


def _temp_roots(home, environ):
    for name in ("TEMP", "TMP", "TMPDIR"):
        if environ.get(name):
            yield name, environ[name]
    if sys.platform == "win32":
        yield "user temp", Path(environ.get("LOCALAPPDATA") or home / "AppData/Local") / "Temp"
        yield "system temp", Path(environ.get("SystemRoot") or r"C:\Windows") / "Temp"
        yield "system account temp", environ.get("SystemTemp") or str(
            Path(environ.get("SystemRoot") or r"C:\Windows") / "SystemTemp"
        )
    elif sys.platform == "linux":
        yield "system temp", "/tmp"
        yield "persistent system temp", "/var/tmp"
        yield "user runtime temp", environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"


def _session_location(source, home, environ):
    if source.get("env") and environ.get(source["env"]):
        base = Path(environ[source["env"]])
    elif source.get("home_parent_env") and environ.get(source["home_parent_env"]):
        base = Path(environ[source["home_parent_env"]]) / ".gemini"
    elif source.get("xdg") and environ.get("XDG_DATA_HOME"):
        base = Path(environ["XDG_DATA_HOME"]) / source["xdg"]
    else:
        base = home / source["home"].removeprefix("~/")
    if source.get("file_env") and environ.get(source["file_env"]):
        configured = base / environ[source["file_env"]]
        literal = re.sub(r"([*?\[])", r"[\1]", configured.name)
        return configured.parent, literal + "*"
    return base / source["sub"], source["match"]


def local_root(value):
    path = Path(value)
    if (not path.is_absolute() or ".." in path.parts or
            (os.name == "nt" and not re.fullmatch(r"[A-Za-z]:", path.drive))):
        raise ValueError("configured root must be an absolute local path")
    if os.name == "nt":
        from ctypes import windll

        if windll.kernel32.GetDriveTypeW(str(path.anchor)) not in (2, 3, 6):
            raise ValueError("configured root is not on a local drive")
    return path


def _walk(root, device, mounts, pattern, row):
    """Stream metadata, pruning paths that cannot match and never crossing links or mounts."""
    stack = [(root, 0)]
    while stack:
        folder, depth = stack.pop()
        try:
            with os.scandir(folder) as entries:
                for entry in entries:
                    token = pattern[min(depth, len(pattern) - 1)]
                    recursive = token == "**"
                    leaf = recursive or depth == len(pattern) - 1
                    if not recursive and not fnmatch.fnmatchcase(entry.name, token):
                        continue
                    path = Path(entry.path)
                    try:
                        # Windows DirEntry.stat reports zero for the device, inode and link count.
                        info = path.lstat()
                    except OSError:
                        row["unreadable"] += 1
                        continue
                    if retention.link(info) or info.st_dev != device or str(path) in mounts:
                        row["boundaries"] += 1
                    elif stat.S_ISDIR(info.st_mode) and (recursive or not leaf):
                        stack.append((path, depth if recursive else depth + 1))
                    elif stat.S_ISREG(info.st_mode) and leaf and (
                        not recursive or fnmatch.fnmatchcase(entry.name, pattern[-1])
                    ):
                        yield path, info
        except OSError:
            row["unreadable"] += 1


def _row(value, pattern="**/*"):
    return {"root": str(value), "pattern": pattern, "status": "found", "files": 0, "older": 0,
            "recent": 0, "bytes": 0, "oldest_mtime_ns": None, "newest_mtime_ns": None,
            "boundaries": 0, "unreadable": 0, "candidate_count": 0}


def _measure(row, before, mounts, candidates, adapter):
    try:
        root = local_root(row["root"])
        info = retention.check_root(root)
    except FileNotFoundError:
        row["status"] = "missing"
        return
    except (ValueError, OSError) as exc:
        row.update(status="unverified", detail=str(exc))
        return
    for path, entry in _walk(root, info.st_dev, mounts, row["pattern"].split("/"), row):
        stamp = entry.st_mtime_ns
        old = before is not None and stamp < before
        row["files"] += 1
        row["bytes"] += entry.st_size
        if before is not None:
            row["older" if old else "recent"] += 1
        row["oldest_mtime_ns"] = min(stamp, row["oldest_mtime_ns"] if row["oldest_mtime_ns"] is not None else stamp)
        row["newest_mtime_ns"] = max(stamp, row["newest_mtime_ns"] if row["newest_mtime_ns"] is not None else stamp)
        if adapter and (before is None or old) and entry.st_nlink == 1:
            from . import session_history

            if session_history.candidate_id(adapter, path, root):
                candidates.append({"agent": adapter, "root": str(root), "path": str(path),
                                   "mtime_ns": stamp, "fingerprint": session_history.fingerprint(entry),
                                   "reason": "native file and modification age match; closure and event window still unverified"})
                row["candidate_count"] += 1
    if row["unreadable"]:
        row["status"] = "partial"
    elif not row["files"]:
        row["status"] = "no matching files"


def inventory(policy, before, *, home=None, environ=None, temp=True):
    home = Path(home) if home is not None else Path.home()
    environ = os.environ if environ is None else environ
    mounts = retention.mount_paths()
    result = {"cutoff_ns": before, "temp": [], "sessions": [], "session_candidates": [], "errors": []}
    sources = list(_temp_roots(home, environ)) if temp else []
    if temp and sys.platform == "win32" and environ is os.environ:
        try:
            sources.extend(_machine_temp(environ))
        except OSError as exc:
            result["errors"].append(f"machine TEMP/TMP values unavailable: {exc}")
    roots = {}
    for name, path in sources:
        key = os.path.normcase(os.path.normpath(path))
        if key not in roots:
            roots[key] = _row(path)
            roots[key].update(sources=[], policy="shared temp; only separately proven scratch items may move")
            _measure(roots[key], before, mounts, [], None)
        roots[key]["sources"].append(name)
    result["temp"] = list(roots.values())
    for source in policy["session_sources"]:
        root, pattern = _session_location(source, home, environ)
        row = _row(root, pattern)
        row.update(agent=source["agent"], policy=source["policy"])
        _measure(row, before, mounts, result["session_candidates"], source.get("adapter"))
        row["retained"] = row["files"] - row["candidate_count"]
        result["sessions"].append(row)
    return result
