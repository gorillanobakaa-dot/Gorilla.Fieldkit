"""Start a program the way the operating system itself does, detached, and check after 3 s that it runs.

Owner, 2026-10-31. Windows: the program itself (CreateProcess; an app that demands administrator fails with 740 and is
reported, never raised through UAC), a shortcut through ShellExecute (os.startfile, as the .lnk defines it), a Store app
through shell:AppsFolder. Linux: `gtk-launch` / `gio launch` for a .desktop file, else the Exec line; flatpak run;
snap run. macOS: `open -a`. Never elevated (no UAC, no sudo; a process that is already elevated refuses). Arguments
come only from the person or script typing them after `--`; the cable verb passes none. `stop` is not here: stop only
what we started, by PID (fieldkit hog / liveness).
"""
from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
import time

from ..peers import agentfind
from . import find as F

VERIFY_SECONDS = 3


def elevated(osname=None):
    osname = osname or sys.platform
    try:
        if osname == "win32":
            import ctypes
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        return os.geteuid() == 0
    except Exception:  # noqa: BLE001
        return False


def exec_argv(exec_line, args):
    """A .desktop Exec= line without its field codes (%f %U ...), plus the given arguments."""
    argv = shlex.split(re.sub(r"%[a-zA-Z%]", lambda m: "%" if m.group(0) == "%%" else "", exec_line))
    return argv + list(args)


def _detached_kw(osname):
    if osname == "win32":
        return {"creationflags": getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
                "close_fds": True}
    return {"start_new_session": True}


def plan(match, args, osname, which=shutil.which):
    """-> (method, argv or None, direct). direct: the PID of the process we make is the program's own."""
    k, t = match["launch"]["kind"], match["launch"]["target"]
    if k == "exe":
        return "CreateProcess" if osname == "win32" else "exec", [t, *args], True
    if k == "lnk":
        return "ShellExecute (the shortcut)", None, False
    if k == "appsfolder":
        return "explorer shell:AppsFolder", ["explorer.exe", "shell:AppsFolder\\" + t], False
    if k == "desktop":
        did = match["launch"].get("id", "")
        if which("gtk-launch") and not args:
            return "gtk-launch", ["gtk-launch", did], False
        if which("gio") and not args:
            return "gio launch", ["gio", "launch", t], False
        return "Exec line", exec_argv(match.get("exec", ""), args), True
    if k == "flatpak":
        return "flatpak run", ["flatpak", "run", t, *args], False
    if k == "snap":
        return "snap run", ["snap", "run", t, *args], False
    if k == "bundle":
        return "open -a", ["open", "-a", t, *(["--args", *args] if args else [])], False
    if k == "folder":
        return "", None, False
    raise ValueError(f"unknown launch kind {k!r}")


def start(match, args=(), osname=None, popen=subprocess.Popen, startfile=None, procs=agentfind.process_list,
          sleep=time.sleep, is_elevated=elevated, which=shutil.which, wait=VERIFY_SECONDS):
    """-> {ok, name, path, method, pid, pids, verified, why}. Verified = running `wait` seconds later."""
    osname = osname or sys.platform
    base = {"ok": False, "name": match["name"], "path": match["path"], "method": "", "pid": None, "pids": [],
            "verified": False}
    if is_elevated(osname):
        return {**base, "why": "refused: this process is elevated (administrator/root); a program is started only "
                               "unelevated, never through UAC or sudo"}
    method, argv, direct = plan(match, list(args), osname, which)
    if not method:
        return {**base, "why": f"refused: {match['path']} is an install folder, not a program"}
    base["method"] = method
    before = {p[0] for p in procs()}
    pid = None
    try:
        if argv is None:                                        # a .lnk: ShellExecute, as the shortcut defines it
            if args:
                return {**base, "why": "refused: a shortcut takes no arguments (it defines its own)"}
            (startfile or getattr(os, "startfile"))(match["launch"]["target"])
        else:
            p = popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                      **_detached_kw(osname))
            pid = p.pid
    except OSError as e:
        why = "needs administrator rights (not started; no UAC prompt)" if getattr(e, "winerror", 0) == 740 else \
            f"{type(e).__name__}: {e}"
        return {**base, "why": f"not started: {why}"}
    sleep(wait)
    now = procs()
    alive = {x[0] for x in now}
    stems = {F.norm(match["name"]), F.norm(os.path.splitext(os.path.basename(match["path"] or ""))[0])} - {""}
    new = [(pid_, n, e) for pid_, n, e in now if pid_ not in before and
           (F.norm(os.path.splitext(n or "")[0]) in stems or (e and match["launch"]["kind"] == "exe"
                                                              and os.path.normcase(e) == os.path.normcase(match["path"])))]
    pids = [x[0] for x in new]
    if direct and pid in alive and pid not in pids:
        pids.insert(0, pid)
    if pids:
        return {**base, "ok": True, "pid": pids[0], "pids": pids, "verified": True,
                "why": f"running after {wait} s (PID {', '.join(map(str, pids[:4]))})"}
    if direct and pid is not None:
        return {**base, "pid": pid, "why": f"started as PID {pid} but not running after {wait} s (it exited)"}
    # a launcher (gtk-launch, open, a shortcut) hands over and exits: an already running program may have taken it
    again = F.find(match["name"], env=_env_procs(match, now))
    old = [r for m in again for r in m["running"] if m["name"] == match["name"]]
    if old:
        return {**base, "ok": True, "pid": old[0], "pids": old, "verified": True,
                "why": f"running after {wait} s (PID {', '.join(map(str, old[:4]))}); it was already running or took the request"}
    return {**base, "why": f"asked the system to start it, but no matching process appeared in {wait} s"}


def _env_procs(match, procs):
    """A tiny find env over `procs` only: the process source for this match's name."""
    return {"osname": "none", "procs": procs, "environ": {}, "home": "", "listdir": lambda d: [], "isfile": lambda p: False,
            "isexec": lambda p: False, "lines": lambda a: [], "read": lambda p: "", "read_bytes": lambda p: b"",
            "registry": lambda: [], "lnk_dirs": [], "walk_lnk": lambda d: [], "lnk_target": lambda p: "",
            "start_apps": lambda: [], "glob": lambda p: [], "which": lambda s: None, "run": None}
