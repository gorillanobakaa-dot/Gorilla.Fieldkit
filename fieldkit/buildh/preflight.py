"""Preflight: what must be true BEFORE a job is allowed to start. Each row is PASS or FAIL with evidence.

Written 2026-10-01 after the overnight run: a stale git lock crashed eight jobs in a row and the driver
kept feeding the model new ones; nobody had checked that the machine and the working copy were fit to start.
"""
import ctypes
import json
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

from . import task, vault

MIN_FREE_GB_JOB = 20          # a patch/port job: the working copy plus checkpoints
MIN_FREE_GB_BUILD = 120       # a Firefox build: object directory, caches, packaging
MIN_RAM_GB = 16
LM_STUDIO = "http://127.0.0.1:1234/v1/models"


def _running(image):
    """Names of running processes matching an image name, via the OS (no extra packages)."""
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {image}", "/FO", "CSV", "/NH"],
                             capture_output=True, text=True, errors="replace").stdout
        return [l.split('","')[1] for l in out.splitlines() if l.lower().startswith(f'"{image.lower()}"')]
    out = subprocess.run(["pgrep", "-x", image.replace(".exe", "")], capture_output=True, text=True).stdout
    return out.split()


def _ram_gb():
    if os.name == "nt":
        class MS(ctypes.Structure):
            _fields_ = [("l", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong),
                        ("avail", ctypes.c_ulonglong), ("pt", ctypes.c_ulonglong), ("pa", ctypes.c_ulonglong),
                        ("vt", ctypes.c_ulonglong), ("va", ctypes.c_ulonglong), ("x", ctypes.c_ulonglong)]
        m = MS()
        m.l = ctypes.sizeof(MS)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
        return m.total / 2 ** 30
    for line in open("/proc/meminfo"):
        if line.startswith("MemTotal"):
            return int(line.split()[1]) / 2 ** 20
    return 0


def stale_locks(workdir):
    """index.lock files in the working copy that no git process can own. -> (locks, git_running)"""
    locks = [p for p in Path(workdir, ".git").glob("*.lock")]
    return locks, bool(_running("git.exe" if os.name == "nt" else "git"))


def run(task_id, build=False, model=False, fix_locks=False, lm_url=LM_STUDIO, fan_required=False):
    """-> [{check, ok, evidence}]. Nothing here changes anything except removing a PROVEN stale git lock
    when fix_locks is set (no git process running at all)."""
    t = task.load(task_id)
    rows = []

    def row(name, ok, evidence):
        rows.append({"check": name, "ok": bool(ok), "evidence": evidence})

    wd = Path(t["workdir"])
    drive = wd if wd.exists() else wd.parent
    while not drive.exists() and drive != drive.parent:
        drive = drive.parent
    free = shutil.disk_usage(drive).free / 2 ** 30
    need = MIN_FREE_GB_BUILD if build else MIN_FREE_GB_JOB
    row("disk space", free >= need, f"{free:.0f} GB free on {drive.anchor or drive}, need {need} GB")
    ram = _ram_gb()
    row("memory", ram >= MIN_RAM_GB, f"{ram:.0f} GB installed, need {MIN_RAM_GB} GB")
    for tool in ("git", "patch"):
        found = shutil.which(tool)
        row(f"{tool} available", found, found or f"{tool} not found on PATH")
    v = vault.verify("firefox", t["meta"]["upstream"]["version"])
    row("vault copy intact", v["intact"], "; ".join(v["problems"]) or "unchanged")
    row("working copy exists", (wd / ".git").is_dir(), str(wd))
    if (wd / ".git").is_dir():
        locks, git_up = stale_locks(wd)
        if locks and not git_up and fix_locks:
            for p in locks:
                p.unlink()
            row("git locks", True, f"removed {len(locks)} stale lock(s); no git process was running")
        elif locks:
            row("git locks", False, ("a git process is running, so these may be in use: " if git_up else
                                     "stale (no git process running); rerun with --fix-locks: ") + ", ".join(p.name for p in locks))
        else:
            row("git locks", True, "none")
        dirty = subprocess.run(["git", "-C", str(wd), "status", "--porcelain"], capture_output=True, text=True,
                               errors="replace").stdout.splitlines()
        row("working copy clean", not dirty, "clean" if not dirty else f"{len(dirty)} uncommitted file(s): {dirty[:3]}")
    if os.name == "nt":
        fan = _running("TPFanControl.exe")
        row("fan control running (keeps the laptop at 70 C)", fan or not fan_required,
            "running" if fan else ("NOT running - a long build will run hot" if fan_required else "not running (needed for builds)"))
    if model:
        try:
            ids = [m["id"] for m in json.load(urllib.request.urlopen(lm_url, timeout=5))["data"]]
            row("model server answers", True, f"{len(ids)} model(s): {', '.join(ids[:3])}")
            row("a Gemma model is available", any("gemma" in i.lower() for i in ids), ", ".join(ids[:5]) or "none")
        except Exception as e:  # noqa: BLE001 - any failure means the model is not reachable
            row("model server answers", False, f"{lm_url}: {type(e).__name__}")
    return rows


def lines(rows):
    out = [f"  {'PASS' if r['ok'] else 'FAIL'}  {r['check']}: {r['evidence']}" for r in rows]
    bad = [r for r in rows if not r["ok"]]
    return out + [f"PREFLIGHT: {'READY' if not bad else 'NOT READY - ' + str(len(bad)) + ' check(s) failed'}"]
