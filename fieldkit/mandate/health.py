# Version: 1.0.0 · updated 26-10-23-16-17
"""A worker's health before and during a job (plan section 17.2 item 10): free temp space, the clock, the network to
origin with retries, and the device kept awake while the job runs.

All four bit on 2026-10-23: /tmp sat at the videokit doctor's 5 GB limit and failed commits twice (measured in this
session); the other sessions reported Wi-Fi drops, a laptop that slept mid-run and a clock 64 s off. What is wrong is
reported as a CONCERN with the fix (the owner: workers report, offer a fix, carry on); a job is not run only when it
cannot be (no space to check the commit out, no way to reach origin).
"""
import contextlib
import getpass
import os
import shutil
import subprocess
import sys
import tempfile
import time

MIN_FREE_GB = 5
HOME_TEMP = os.path.join(os.path.expanduser("~"), ".cache", "fktemp")
# only fieldkit's own leftovers are ever moved: a job's temporary worktrees, and pytest's old run folders
OWN_SCRATCH = ("mandate-verdict-*", "mandate-base-*", "fk-hook-*")


def repair_temp(min_gb=MIN_FREE_GB, where=None, age_s=6 * 3600, now=None, home_temp=HOME_TEMP):
    """Repair before refusing (recovery loop 5): move fieldkit's OWN old scratch from the temp folder to the home disk
    (never deleted, never another program's), then, if space is still short, point this process's temp folder at the
    home disk. -> what was done, in words (empty when nothing was needed)."""
    import glob
    import tempfile as tf
    d = where or tf.gettempdir()
    if free_temp(min_gb, d)[0]:
        return []
    now = time.time() if now is None else now
    done, moved = [], 0
    dest = os.path.join(home_temp, "moved-from-temp", time.strftime("%y-%m-%d-%H-%M"))
    pats = list(OWN_SCRATCH) + [os.path.join(f"pytest-of-{getpass.getuser()}", "pytest-*")]
    for pat in pats:
        for p in glob.glob(os.path.join(d, pat)):
            try:
                if now - os.path.getmtime(p) < age_s:
                    continue                              # young: maybe in use
                target = os.path.join(dest, os.path.relpath(p, d))
                os.makedirs(os.path.dirname(target), exist_ok=True)
                shutil.move(p, target)
                moved += 1
            except OSError:
                continue
    if moved:
        done.append(f"moved {moved} old fieldkit scratch folder(s) from {d} to {dest}")
    if not free_temp(min_gb, d)[0]:
        os.makedirs(home_temp, exist_ok=True)
        for k in ("TMPDIR", "TEMP", "TMP"):
            os.environ[k] = home_temp
        tf.tempdir = None                                 # tempfile reads the variables again
        done.append(f"the temp folder for this job is {home_temp} (the home disk)")
    return done


def repair_clock(run=subprocess.run):
    """Ask the system to synchronise its clock, without a password prompt (none is answered at night)."""
    try:
        if sys.platform.startswith("linux"):
            r = run(["timedatectl", "set-ntp", "true", "--no-ask-password"], capture_output=True, text=True, timeout=20)
            return ["asked timedatectl to synchronise the clock"] if r.returncode == 0 else []
    except (OSError, subprocess.TimeoutExpired):
        pass
    return []


def free_temp(min_gb=MIN_FREE_GB, where=None):
    d = where or tempfile.gettempdir()
    try:
        free = shutil.disk_usage(d).free / 1e9
    except OSError as e:
        return False, f"temp folder {d} unreadable: {e}", "set TMPDIR to a folder on a disk with room"
    ok = free >= min_gb
    return ok, f"{free:.1f} GB free in {d}", ("" if ok else
                                              f"free space, or point TMPDIR at a disk with at least {min_gb} GB")


def clock(run=subprocess.run):
    """-> (ok | None when this system cannot say, detail, fix)."""
    try:
        if sys.platform.startswith("linux"):
            out = run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"], capture_output=True,
                      text=True, timeout=10).stdout.strip()
            if out in ("yes", "no"):
                return out == "yes", f"NTP synchronised: {out}", ("" if out == "yes" else
                                                                  "sudo timedatectl set-ntp true")
        elif sys.platform == "win32":
            out = run(["w32tm", "/query", "/status"], capture_output=True, text=True, timeout=10).stdout
            if out:
                ok = "Leap Indicator: 0" in out or "Source:" in out and "Local CMOS Clock" not in out
                return ok, out.strip().splitlines()[0][:120], ("" if ok else "w32tm /resync (elevated)")
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None, "this system does not say whether its clock is synchronised", ""


def reach_origin(repo, tries=3, sleep=time.sleep, run=subprocess.run):
    """git can talk to origin; Wi-Fi drops are retried (2, 4 s) before it counts as down."""
    delay, last = 2, ""
    for i in range(tries):
        r = run(["git", "-C", str(repo), "ls-remote", "--heads", "origin"], capture_output=True, text=True,
                timeout=60)
        if r.returncode == 0:
            return True, f"origin answered (try {i + 1})", ""
        last = (r.stderr or r.stdout).strip()[-160:]
        if i < tries - 1:
            sleep(delay)
            delay *= 2
    return False, f"origin did not answer after {tries} tries: {last}", "check the Wi-Fi or cable, then resend the job"


def check(repo, min_gb=MIN_FREE_GB, run=subprocess.run, sleep=time.sleep, repair=True):
    """-> (can_run, concerns [{text, fix_offered}], evidence lines). What can be repaired safely is repaired first
    (temp space, the clock); what was repaired is reported too."""
    repaired = []
    if repair:
        repaired += repair_temp(min_gb)
        if clock(run)[0] is False:
            repaired += repair_clock(run)
    rows = {"temp space": free_temp(min_gb), "clock": clock(run), "network": reach_origin(repo, run=run, sleep=sleep)}
    concerns = [{"text": f"{name}: {detail}", "fix_offered": fix} for name, (ok, detail, fix) in rows.items()
                if ok is False]
    can_run = rows["network"][0] is not False and shutil.disk_usage(tempfile.gettempdir()).free > 1e9
    concerns += [{"text": f"repaired before the job: {x}", "fix_offered": ""} for x in repaired]
    return can_run, concerns, [f"{n}: {'ok' if ok else 'unknown' if ok is None else 'NOT OK'} ({d})"
                               for n, (ok, d, _) in rows.items()] + [f"repaired: {x}" for x in repaired]


@contextlib.contextmanager
def awake(why="a fieldkit job is running", popen=subprocess.Popen):
    """Keep the device from sleeping while the job runs: systemd-inhibit (Linux), caffeinate (macOS),
    SetThreadExecutionState (Windows). Nothing is changed after it ends; where none exists, the job runs anyway."""
    held = None
    try:
        if sys.platform == "win32":
            import ctypes
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)   # CONTINUOUS | SYSTEM_REQUIRED
            held = "windows"
        elif shutil.which("systemd-inhibit"):
            held = popen(["systemd-inhibit", "--what=sleep:idle", "--who=fieldkit", f"--why={why}", "--mode=block",
                          "sleep", "infinity"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
        elif shutil.which("caffeinate"):
            held = popen(["caffeinate", "-i"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    except (OSError, AttributeError):
        held = None
    try:
        yield held
    finally:
        if held == "windows":
            import ctypes
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
        elif held is not None and hasattr(held, "terminate"):
            held.terminate()                              # the inhibitor this function started, nothing else
