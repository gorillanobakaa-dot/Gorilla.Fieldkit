"""Long paths by default, and the standard way to delete a tree, for every Fieldkit tool that walks or deletes files.

    from fieldkit.core import longpath
    longpath.extended(p)            \\\\?\\C:\\... on Windows (up to 32,767 UTF-16 characters), p unchanged elsewhere
    longpath.remove_tree(p)         the platform's standard direct delete, links never followed (see below)
    longpath.limits()               this platform's path and name limits
    longpath.machine()              LongPathsEnabled, git core.longpaths, Python's long-path awareness
    python -m fieldkit.core.longpath [--json]     the doctor: the machine's long-path state, exit 0 OK / 1 to fix

Owner, 2026-10-26: "dealing with long paths. This is ADMIN 101. Our harness should be able to deal with long paths
(the maximum windows, osx, and linux can allow) by default." Two test copies (956,000 files) could not go to the
Recycle Bin because their paths inside it passed 259 characters.

THE LIMITS (references cited in the card `longpath`):
  Windows  32,767 UTF-16 characters through the extended-length prefix \\\\?\\ (Microsoft Learn, "Maximum Path Length
           Limitation", https://learn.microsoft.com/en-us/windows/win32/fileio/maximum-file-path-limitation); without
           it MAX_PATH = 260 unless HKLM\\SYSTEM\\CurrentControlSet\\Control\\FileSystem LongPathsEnabled = 1 AND the
           program's manifest says longPathAware (python.exe 3.6+ does). A name is at most 255 characters. git needs
           core.longpaths = true on Windows. The Recycle Bin and Explorer stay at MAX_PATH whatever is set: long trees
           are deleted directly, never recycled.
  Linux    PATH_MAX 4096 bytes per system call, NAME_MAX 255 (POSIX <limits.h>, Linux include/uapi/linux/limits.h);
           deeper trees are reached relative to a directory descriptor (openat/unlinkat), which has no total limit.
  macOS    PATH_MAX 1024, NAME_MAX 255 (sys/syslimits.h); the same *at() calls.

THE DELETE (remove_tree), the established method on each platform, never the Recycle Bin:
  Windows  DeleteFileW / RemoveDirectoryW on \\\\?\\ paths, what `rmdir /s /q` and `robocopy EMPTY X /MIR` do; a
           junction, symlink or any reparse point is removed as itself (RemoveDirectoryW on the link), never entered;
           read-only files are made writable first; folders in parallel (deletes are I/O-bound system calls).
  POSIX    shutil.rmtree, which walks with directory descriptors and unlinkat (the `rm -rf` method; its
           avoids_symlink_attacks is True on Linux and macOS: a symlink is unlinked, never followed), after a check
           that nothing below is another file system (`rm -rf --one-file-system`: a mount point inside refuses the
           whole delete, nothing touched).
"""
import argparse
import json
import os
import shutil
import stat
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
LIMITS = {"windows": {"path": 32767, "name": 255, "legacy_path": 260},
          "linux": {"path": 4096, "name": 255},
          "darwin": {"path": 1024, "name": 255}}


class Refused(OSError):
    pass


def system(platform=None):
    p = platform or sys.platform
    return "windows" if p.startswith("win") else "darwin" if p == "darwin" else "linux"


def limits(platform=None):
    return dict(LIMITS[system(platform)])


def extended(p, nt=None):
    """The extended-length form on Windows (absolute, \\\\?\\ or \\\\?\\UNC\\), the path as a string elsewhere."""
    nt = (os.name == "nt") if nt is None else nt
    s = str(p)
    if not nt:
        return s
    import ntpath                   # Windows rules even when a test asks for them on Linux
    s = ntpath.abspath(s) if not s.startswith("\\\\?\\") else s
    if s.startswith("\\\\?\\"):
        return s
    return "\\\\?\\UNC\\" + s[2:] if s.startswith("\\\\") else "\\\\?\\" + s


def plain(p):
    """The path without the \\\\?\\ prefix, for messages and comparisons."""
    s = str(p)
    if s.startswith("\\\\?\\UNC\\"):
        return "\\\\" + s[8:]
    return s[4:] if s.startswith("\\\\?\\") else s


def is_link(p):
    """A symlink, a junction or any other reparse point: never followed."""
    try:
        st = os.lstat(extended(p))
    except OSError:
        return False
    return stat.S_ISLNK(st.st_mode) or bool(getattr(st, "st_file_attributes", 0) & REPARSE)


# ---- the delete ----------------------------------------------------------------------------------------------------
def _unlink(p):
    try:
        os.unlink(p)
    except PermissionError:
        os.chmod(p, stat.S_IWRITE | stat.S_IREAD)
        os.unlink(p)


def _drop_link(p):
    try:
        os.unlink(p)            # Python's unlink removes a directory symlink or junction as the link itself
    except OSError:
        os.rmdir(p)


def _rmdir(p):
    try:
        os.rmdir(p)
    except PermissionError:
        os.chmod(p, stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)
        os.rmdir(p)


def remove_tree(d, keep_top=(), workers=None, nt=None):
    """Delete everything inside folder d (d itself stays; names in keep_top at its top stay too).
    -> {files, dirs, links, bytes, errors, error_count}. A link is removed, never followed. Raises Refused when d is
    a link, or (POSIX) when another file system is mounted below it."""
    nt = (os.name == "nt") if nt is None else nt
    if is_link(d):
        raise Refused(f"{d} is a link or junction: never deleted through")
    if nt:
        return _remove_nt(extended(d, nt=True), keep_top, workers)
    return _remove_posix(str(d), keep_top)


def _remove_nt(top, keep_top, workers):
    workers = workers or min(32, (os.cpu_count() or 4) * 2)
    lock = threading.Lock()
    tot = {"files": 0, "dirs": 0, "links": 0, "bytes": 0, "errors": [], "error_count": 0}

    def err(p, e):
        with lock:
            if len(tot["errors"]) < 50:
                tot["errors"].append(f"{plain(p)}: {e}")
            tot["error_count"] += 1

    def one(path):
        subs, f, b, ln = [], 0, 0, 0
        try:
            it = os.scandir(path)
        except OSError as e:
            err(path, e)
            return subs
        with it:
            for e in it:
                if path == top and e.name in keep_top:
                    continue
                try:
                    st = e.stat(follow_symlinks=False)
                    if e.is_symlink() or getattr(st, "st_file_attributes", 0) & REPARSE:
                        _drop_link(e.path)
                        ln += 1
                    elif stat.S_ISDIR(st.st_mode):
                        subs.append(e.path)
                    else:
                        _unlink(e.path)
                        f += 1
                        b += st.st_size
                except OSError as x:
                    err(e.path, x)
        with lock:
            tot["files"] += f
            tot["bytes"] += b
            tot["links"] += ln
        return subs

    def rm(p):
        try:
            _rmdir(p)
            return 1
        except OSError as x:
            err(p, x)
            return 0

    levels, level = [], [top]
    with ThreadPoolExecutor(workers) as pool:
        while level:
            level = [s for subs in pool.map(one, level) for s in subs]
            if level:
                levels.append(level)
        for lv in reversed(levels):                 # the deepest folders first, each level in parallel
            tot["dirs"] += sum(pool.map(rm, lv))
    return tot


def _remove_posix(top, keep_top):
    tot = {"files": 0, "dirs": 0, "links": 0, "bytes": 0, "errors": [], "error_count": 0}
    dev = os.lstat(top).st_dev
    for root, dirs, files in os.walk(top, followlinks=False):     # count, and the --one-file-system check
        for n in dirs + files:
            if root == top and n in keep_top:
                continue
            st = os.lstat(os.path.join(root, n))
            if stat.S_ISLNK(st.st_mode):
                tot["links"] += 1
            elif stat.S_ISDIR(st.st_mode):
                if st.st_dev != dev:
                    raise Refused(f"{os.path.join(root, n)} is another file system: nothing deleted")
                tot["dirs"] += 1
            else:
                tot["files"] += 1
                tot["bytes"] += st.st_size
        if root == top:
            dirs[:] = [x for x in dirs if x not in keep_top]

    def onexc(_fn, p, e):
        tot["error_count"] += 1
        if len(tot["errors"]) < 50:
            tot["errors"].append(f"{p}: {e}")
    for e in os.scandir(top):
        if e.name in keep_top:
            continue
        if e.is_dir(follow_symlinks=False):
            if sys.version_info >= (3, 12):
                shutil.rmtree(e.path, onexc=onexc)
            else:
                shutil.rmtree(e.path, onerror=lambda f, p, ei: onexc(f, p, ei[1]))
        else:
            try:
                os.unlink(e.path)
            except OSError as x:
                onexc(os.unlink, e.path, x)
    return tot


# ---- the machine ---------------------------------------------------------------------------------------------------
def long_paths_enabled(reg=None):
    """HKLM ... FileSystem LongPathsEnabled (readable without admin) -> True | False | None (not Windows/unreadable)."""
    if os.name != "nt" and reg is None:
        return None
    try:
        if reg is not None:
            return bool(reg())
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\FileSystem") as k:
            return winreg.QueryValueEx(k, "LongPathsEnabled")[0] == 1
    except FileNotFoundError:
        return False
    except OSError:
        return None


def git_longpaths(run=subprocess.run):
    """git config --global core.longpaths -> True | False | None (no git)."""
    git = shutil.which("git")
    if not git:
        return None
    try:
        r = run([git, "config", "--global", "--get", "core.longpaths"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip().lower() == "true"


def machine(reg=None, run=subprocess.run):
    """-> rows (name, ok, detail). On Linux and macOS there is nothing to switch on."""
    sysname = system()
    lim = limits()
    rows = [("limits", True, f"{sysname}: path {lim['path']}, name {lim['name']}"
             + (f"; legacy MAX_PATH {lim['legacy_path']} without \\\\?\\" if "legacy_path" in lim else ""))]
    if sysname == "windows":
        lp = long_paths_enabled(reg)
        rows.append(("LongPathsEnabled", lp is True, {True: "1 (plain paths over 260 work in long-path-aware "
                     "programs)", False: "0: plain paths stop at 260; Fieldkit still uses \\\\?\\; fix: the mandate's "
                     "machine prerequisite longpaths-enabled", None: "unreadable"}[lp]))
        g = git_longpaths(run)
        rows.append(("git core.longpaths", g is not False, {True: "true", False: "not set: git fails on deep trees; "
                     "fix: the mandate's machine prerequisite git-longpaths", None: "git not found"}[g]))
        rows.append(("python", True, f"{sys.version.split()[0]} (python.exe is longPathAware since 3.6)"))
    return rows


def probe(base, depth=12, seg=30):
    """Build and delete a tree deeper than the legacy limit under base. -> (ok, deepest path length, detail)."""
    d = os.path.join(extended(base), ".fk-longpath-probe-%d" % os.getpid())
    deep = d
    for _ in range(depth):
        deep = os.path.join(deep, "p" * seg)
    try:
        os.makedirs(deep)
        with open(os.path.join(deep, "f.txt"), "wb") as f:
            f.write(b"x")
        n = len(plain(os.path.join(deep, "f.txt")))
        remove_tree(d)
        os.rmdir(d)
        return True, n, f"made and deleted a {n}-character path"
    except OSError as e:
        return False, 0, str(e)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m fieldkit.core.longpath", description=__doc__.split("\n\n")[0])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--probe", default=None, help="also build and delete a tree deeper than 260 characters here")
    a = ap.parse_args(argv)
    rows = machine()
    if a.probe:
        ok, _n, why = probe(a.probe)
        rows.append(("probe", ok, why))
    if a.json:
        print(json.dumps([{"check": n, "ok": ok, "detail": d} for n, ok, d in rows], indent=1))
    else:
        for n, ok, d in rows:
            print(f"  {'OK  ' if ok else 'FIX '} {n:<19} {d}")
        print(f"VERDICT  long paths: {'OK' if all(r[1] for r in rows) else 'something to fix'}")
    return 0 if all(r[1] for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
