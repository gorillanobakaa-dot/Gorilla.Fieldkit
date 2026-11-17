"""Age selection and metadata-only inspection inside the P40 scratch allowlist."""

import hashlib
import os
import re
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path

from .. import scratch
from . import store


_UNITS = {
    unit: seconds
    for names, seconds in (
        ("s sec secs second seconds", 1),
        ("m min mins minute minutes", 60),
        ("h hr hrs hour hours", 3600),
        ("d day days", 86400),
        ("w wk wks week weeks", 7 * 86400),
        ("mo month months", 30 * 86400),
    )
    for unit in names.split()
}


def duration(value):
    value = value.strip().lower()
    if re.fullmatch(r"half (?:a|an) hour", value):
        return 1800
    match = re.fullmatch(r"(\d+)\s*([a-z]+)", value, flags=re.ASCII)
    if match and match[2] in _UNITS and 0 < int(match[1]) <= 10**12 // _UNITS[match[2]]:
        return int(match[1]) * _UNITS[match[2]]
    raise ValueError("use a positive age such as 90s, 30m, 'half an hour', 2w, or '1 month'")


def cutoff(seconds, now=None):
    if seconds is None:
        return None
    now = now or datetime.now(timezone.utc)
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    delta = now - epoch
    return ((delta.days * 86400 + delta.seconds - seconds) * 10**9 + delta.microseconds * 1000)


def normalized(path):
    return os.path.normcase(os.path.abspath(path))


def within(path, root):
    candidate, base = normalized(path), normalized(root)
    try:
        return os.path.commonpath((candidate, base)) == base
    except ValueError:
        return False


def link(info):
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


def check_root(root):
    """Check ancestors before looking inside a configured root."""
    for path in (*reversed(root.parents), root):
        info = path.lstat()
        if link(info) or not stat.S_ISDIR(info.st_mode):
            raise OSError(f"unsafe directory: {path}")
    return info


def mount_paths():
    if sys.platform != "linux":
        return set()
    # ismount() cannot see a same-device bind mount.
    lines = Path("/proc/self/mountinfo").read_text(encoding="utf-8", errors="surrogateescape").splitlines()
    return {re.sub(r"\\([0-7]{3})", lambda match: chr(int(match[1], 8)), line.split()[4]) for line in lines}


def active_paths(roots):
    import psutil

    active = set()
    for process in psutil.process_iter(["cwd"]):
        cwd = process.info.get("cwd")
        if not cwd:
            continue
        for root in roots:
            if within(cwd, root):
                relative = Path(normalized(cwd)).relative_to(normalized(root))
                active.add(root / relative.parts[0] if relative.parts else root)
    return active


def protected_directory(path, root, names):
    markers = {name.lower() for name in names}
    if markers & {".git", ".hg", ".svn"}:
        return "checkout or worktree"
    if scratch.MARKER in markers:
        try:
            job = scratch.marker(path, r=root)
            if job.get("keep") or scratch.alive(job.get("owner")):
                return "active or kept scratch job"
        except scratch.Refused:
            return "unverified scratch owner"
    return None


def inspect(path, root, before, kept, protected, device, mounts=()):
    """Return a tree snapshot, stopping at directories that must stay intact."""
    info = path.lstat()
    stamp = (info.st_dev, info.st_ino, info.st_mode, info.st_size,
             info.st_mtime_ns, info.st_ctime_ns, getattr(info, "st_file_attributes", 0))
    row = {"path": str(path), "root": str(root), "name": path.name,
           "kind": "folder" if stat.S_ISDIR(info.st_mode) else "file", "files": 0,
           "status": "undigested"}
    reason = None
    children = []
    if link(info) or info.st_dev != device or str(path) in mounts or os.path.ismount(path):
        reason = "link or mount"
    elif kept(path, root) or any(within(path, item) for item in protected):
        reason = "protected path"
    elif row["kind"] == "folder":
        with os.scandir(path) as entries:
            names = sorted(entry.name for entry in entries)
        reason = protected_directory(path, root, names)
        if reason is None:
            children = [inspect(path / name, root, before, kept, protected, device, mounts) for name in names]
            row["files"] = sum(child["files"] for child in children)
    elif stat.S_ISREG(info.st_mode):
        row["files"] = 1
    else:
        reason = "special file"
    if reason is None and before is not None and info.st_mtime_ns >= before:
        reason = "recent"
    if reason is None and any(child["reason"] for child in children):
        reason = "contains retained entries"
    row["reason"] = reason
    row["identity"] = hashlib.sha256(
        store.canon([row["name"], stamp, reason, [child["identity"] for child in children]]).encode("ascii", "backslashreplace")
    ).hexdigest()
    row["children"] = children
    return row


def rows_to_check(row):
    children = row["children"]
    yield {k: v for k, v in row.items() if k != "children"}
    if row["reason"] in ("recent", "contains retained entries"):
        for child in children:
            yield from rows_to_check(child)
