# Version: 1.0.0 · updated 26-11-12
"""fieldkit task stop ID: stop a started task AND its worker (idea I-07, 2026-11-11).

Killing the runner's PID alone left the reparented `claude -p` worker running and editing (twice on 2026-11-11). The
runner is started in its own session, so its PID is also its process group: the stop signals that group, and every other
process whose working folder is the task's worktree (a worker that left the group), then force-kills what is still alive
after a short grace. Only processes of THIS task are touched: the group of its recorded PID and the processes working
inside its own worktree; never by name. Linux/macOS read /proc or `ps`; on Windows `taskkill /T` takes the PID's tree.
"""
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

GRACE_S = 5


def _proc_rows():
    """-> [(pid, pgid, cwd)] of every process this account can read (Linux /proc); [] elsewhere."""
    rows = []
    proc = Path("/proc")
    if not proc.is_dir():
        return rows
    for d in proc.iterdir():
        if not d.name.isdigit():
            continue
        try:
            stat = (d / "stat").read_text(encoding="utf-8", errors="replace")
            pgid = int(stat.rsplit(")", 1)[1].split()[2])
            rows.append((int(d.name), pgid, os.path.realpath(os.readlink(d / "cwd"))))
        except (OSError, ValueError, IndexError):
            continue
    return rows


def targets(pid, worktree, rows=None, me=None):
    """-> sorted pids belonging to the task: its PID, its process group, and whatever works inside its worktree."""
    me = me or {os.getpid(), os.getppid()}
    wt = os.path.realpath(worktree) if worktree else ""
    out = set()
    for p, pgid, cwd in (_proc_rows() if rows is None else rows):
        if p in me:
            continue
        if (pid and (p == int(pid) or pgid == int(pid))) or (wt and (cwd == wt or cwd.startswith(wt + os.sep))):
            out.add(p)
    return sorted(out)


def stop(t, rows=None, kill=os.kill, alive=None, sleep=time.sleep, grace=GRACE_S, run=subprocess.run):
    """Stop the task record `t`. -> {"signalled": [pids], "killed": [pids], "left": [pids]}."""
    if alive is None:
        from ..peers.peers import pid_alive as alive
    pid, wt = t.get("pid"), t.get("worktree")
    if sys.platform == "win32":
        if pid:
            run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, text=True, timeout=60)
        return {"signalled": [int(pid)] if pid else [], "killed": [], "left": []}
    found = targets(pid, wt, rows)
    for p in found:
        try:
            kill(p, signal.SIGTERM)
        except OSError:
            pass
    left = [p for p in found if alive(p)]
    for _ in range(int(grace * 4)):
        if not left:
            break
        sleep(0.25)
        left = [p for p in left if alive(p)]
    killed = []
    for p in left:
        try:
            kill(p, getattr(signal, "SIGKILL", signal.SIGTERM))   # Windows has no SIGKILL
            killed.append(p)
        except OSError:
            pass
    return {"signalled": found, "killed": killed, "left": [p for p in killed if alive(p)]}
