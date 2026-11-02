"""The machine governor is never down: `fieldkit thermal ensure` starts it when it is not running, and a keeper the
system runs (Task Scheduler on Windows, a systemd user timer on Linux) calls `ensure` at logon, every 5 minutes and
on resume from sleep. Heavy work refuses to start while the governor is down (fail closed): `fieldkit guard` and the
goal supervisor call `ensure` first.

    fieldkit thermal ensure                 up? else start it and wait for its lease. Exit 0 up, 3 down
    fieldkit thermal ensure --install       also install the keeper (no admin; user task / user timer)

The brake (owner, 2026-11-02 03:3x: "the LLMs WILL COOK MY LAPTOP"): since 2026-10-28 the governor never writes the
power plan (three cutoffs in 28 h followed powercfg scheme resets). It kept its rules (cap down 10 per 3 s above
target + 3, the floor at HOT, back up when cool) but its lever did nothing. The lever is now the guarded jobs
themselves: at or under PAUSE_AT the children of every guarded job (brake.holds) are paused; once the cap climbs back
to RESUME_AT they run again. Nothing outside a guarded job is touched, and the power plan is never changed.

Found 2026-11-02 03:40: the logon script had the governor commented out since 2026-10-28, so nothing ran it; the
fan window still said "fieldkit thermal watch (started at logon) lowers the CPU cap". Words, no check.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from . import brake

TASK = "Fieldkit thermal keeper"
UNIT = "fieldkit-thermal-keeper"
EVERY_MIN = 5
START_WAIT_S = 25
PAUSE_AT, RESUME_AT = 50, 80        # governor cap %: 50 = two steps down (sustained heat) or the floor at HOT


def state(folder=None, now=None, alive=None):
    """-> {"up": bool, "pid": int|None, "age_s": float|None} from the governor's lease."""
    f = Path(folder or brake._dir()) / "machine-governor.json"
    try:
        m = json.loads(f.read_text(encoding="utf-8"))
        age = (now or time.time()) - float(m["t"])
        return {"up": brake.machine_alive(folder, now=now, alive=alive), "pid": int(m["pid"]), "age_s": round(age, 1)}
    except (OSError, ValueError, KeyError, TypeError):
        return {"up": False, "pid": None, "age_s": None}


def _python():
    py = Path(sys.executable)
    w = py.with_name("pythonw.exe")
    return str(w if sys.platform == "win32" and w.is_file() else py)


def command(target=None):
    from . import governor as gv
    t = gv.build_target() if target is None else float(target)
    return [_python(), "-m", "fieldkit", "thermal", "watch", "--forever", "--target", f"{t:g}"]


def spawn(cmd, cwd=None):
    """Start the governor detached from this process (it outlives the caller). -> pid."""
    from ..core import settings
    kw = {"cwd": str(cwd or settings.ROOT), "stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
          "stderr": subprocess.DEVNULL, "close_fds": True}
    if sys.platform == "win32":
        kw["creationflags"] = 0x00000008 | 0x00000200 | 0x08000000   # DETACHED, NEW_PROCESS_GROUP, NO_WINDOW
    else:
        kw["start_new_session"] = True
    return subprocess.Popen(cmd, **kw).pid


def ensure(folder=None, start=None, sleep=time.sleep, now=time.time, alive=None, wait_s=START_WAIT_S, say=print):
    """-> {"ok", "state": up|started|down, ...}. Starts the governor when its lease is missing, stale or its process
    is gone, then waits for a fresh lease from the new process (the proof it runs, never the spawn's exit code)."""
    st = state(folder, now(), alive)
    if st["up"]:
        return dict(st, ok=True, state="up")
    start = start or (lambda: spawn(command()))
    pid = start()
    end = now() + wait_s
    while now() < end:
        sleep(1)
        st = state(folder, now(), alive)
        if st["up"]:
            say(f"thermal ensure: the governor was down; started (pid {st['pid']})")
            return dict(st, ok=True, state="started", spawned=pid)
    return dict(st, ok=False, state="down", spawned=pid,
                error=f"the governor did not take its lease within {wait_s} s (see state/thermal/watch-*.log)")


# --- the brake: pause guarded jobs instead of moving the power plan -------------------------------------------

def _children(pid):
    import psutil
    try:
        return psutil.Process(int(pid)).children(recursive=True)
    except (psutil.Error, ValueError):
        return []


class Brake:
    """The governor's lever (set_cap). cap <= PAUSE_AT: pause the children of every guarded job; cap >= RESUME_AT:
    run them again. Idempotent; remembers what it paused so it resumes exactly those."""

    def __init__(self, folder=None, holds=None, children=None, say=print):
        self.folder, self.say = folder, say
        self._holds = holds or (lambda: brake.holds(self.folder))
        self._children = children or _children
        self.paused = {}                                   # pid -> process handle

    def __call__(self, pct):
        if pct <= PAUSE_AT:
            self.pause(pct)
        elif pct >= RESUME_AT:
            self.resume(pct)
        return True

    def pause(self, pct):
        n = 0
        for h in self._holds():
            for p in self._children(h["pid"]):
                if p.pid in self.paused:
                    continue
                try:
                    p.suspend()
                    self.paused[p.pid] = p
                    n += 1
                except Exception:          # gone or not ours: nothing to pause
                    pass
        if n:
            self.say(f"thermal brake: cap {pct}% - paused {n} process(es) of guarded jobs")
        return n

    def resume(self, pct=100):
        n = 0
        for pid, p in list(self.paused.items()):
            try:
                p.resume()
                n += 1
            except Exception:
                pass
            self.paused.pop(pid, None)
        if n:
            self.say(f"thermal brake: cap {pct}% - resumed {n} process(es)")
        return n


# --- the keeper: the system runs `ensure` at logon, every 5 minutes and on resume ----------------------------

def task_xml(python, root):
    """A user task, no admin: logon, every EVERY_MIN minutes, resume from sleep (Kernel-Power 107 and 507)."""
    q = lambda s: str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    sub = ("&lt;QueryList&gt;&lt;Query Id='0' Path='System'&gt;&lt;Select Path='System'&gt;*[System[Provider"
           "[@Name='Microsoft-Windows-Kernel-Power'] and (EventID=107 or EventID=507)]]&lt;/Select&gt;&lt;/Query&gt;"
           "&lt;/QueryList&gt;")
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>Fieldkit: keep the machine thermal governor running (fieldkit thermal ensure)</Description></RegistrationInfo>
  <Triggers>
    <LogonTrigger><Enabled>true</Enabled></LogonTrigger>
    <TimeTrigger><Repetition><Interval>PT{EVERY_MIN}M</Interval><StopAtDurationEnd>false</StopAtDurationEnd></Repetition><StartBoundary>2026-01-01T00:00:00</StartBoundary><Enabled>true</Enabled></TimeTrigger>
    <EventTrigger><Enabled>true</Enabled><Subscription>{sub}</Subscription></EventTrigger>
  </Triggers>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT2M</ExecutionTimeLimit>
    <StartWhenAvailable>true</StartWhenAvailable>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author">
    <Exec><Command>{q(python)}</Command><Arguments>-m fieldkit thermal ensure</Arguments><WorkingDirectory>{q(root)}</WorkingDirectory></Exec>
  </Actions>
</Task>
"""


def systemd_units(python, root):
    service = (f"[Unit]\nDescription=Fieldkit: keep the machine thermal governor running\n\n[Service]\nType=oneshot\n"
               f"WorkingDirectory={root}\nExecStart={python} -m fieldkit thermal ensure\n")
    timer = (f"[Unit]\nDescription=Fieldkit thermal keeper every {EVERY_MIN} minutes\n\n[Timer]\nOnBootSec=1min\n"
             f"OnUnitActiveSec={EVERY_MIN}min\nPersistent=true\n\n[Install]\nWantedBy=timers.target\n")
    return {f"{UNIT}.service": service, f"{UNIT}.timer": timer}


def install(run=subprocess.run, platform=None, home=None, scratch=None):
    """-> {"ok", ...}. Windows: schtasks /Create /XML (user task). Linux: systemd --user timer, enabled now."""
    from ..core import settings
    plat = platform or sys.platform
    if plat == "win32":
        d = Path(scratch or (settings.state_root() / "state" / "thermal"))
        d.mkdir(parents=True, exist_ok=True)
        x = d / "keeper-task.xml"
        x.write_text(task_xml(_python(), settings.ROOT), encoding="utf-16")
        r = run(["schtasks", "/Create", "/F", "/TN", TASK, "/XML", str(x)], capture_output=True, text=True)
        return {"ok": r.returncode == 0, "task": TASK, "detail": (r.stdout or r.stderr or "").strip()[-200:]}
    if plat.startswith("linux"):
        d = Path(home or Path.home()) / ".config" / "systemd" / "user"
        d.mkdir(parents=True, exist_ok=True)
        for name, text in systemd_units(_python(), settings.ROOT).items():
            (d / name).write_text(text, encoding="utf-8")
        run(["systemctl", "--user", "daemon-reload"], capture_output=True, text=True)
        r = run(["systemctl", "--user", "enable", "--now", f"{UNIT}.timer"], capture_output=True, text=True)
        return {"ok": r.returncode == 0, "timer": f"{UNIT}.timer", "detail": (r.stdout or r.stderr or "").strip()[-200:]}
    return {"ok": False, "error": f"{plat}: no keeper (the MacBook Air runs no heavy work; macOS cools in firmware)"}


def installed(run=subprocess.run, platform=None):
    plat = platform or sys.platform
    try:
        if plat == "win32":
            return run(["schtasks", "/Query", "/TN", TASK], capture_output=True, text=True).returncode == 0
        if plat.startswith("linux"):
            return run(["systemctl", "--user", "is-enabled", f"{UNIT}.timer"], capture_output=True,
                       text=True).returncode == 0
    except OSError:
        return False
    return False


def line(r, keeper=None):
    s = {"up": "UP", "started": "STARTED", "down": "DOWN"}[r["state"]]
    out = f"GOVERNOR {s}" + (f": pid {r['pid']}, lease {r['age_s']} s old" if r.get("pid") else "")
    if r.get("error"):
        out += f" - {r['error']}"
    if keeper is not None:
        out += "; keeper " + ("installed" if keeper else "NOT installed (fieldkit thermal ensure --install)")
    return out


def refusal(platform=None, ensure_fn=None):
    """-> "" when heavy work may start, else why not (the guard and the goal supervisor call it; it starts the
    governor itself when it can). Enforced on Windows, where the laptop has cooked (crashes 2026-10-02, 10-27); on
    Linux the VAIO's kernel and thermald brake and `watch --forever` there is not yet proven (ZERO-OPEN-PROBLEMS)."""
    if (platform or sys.platform) != "win32":
        return ""
    r = (ensure_fn or ensure)(say=lambda *_: None)
    return "" if r["ok"] else ("the machine thermal governor is down and could not be started: " + r.get("error", ""))
