"""Everyday cooling for a Windows laptop: a profile, a background monitor, and a voice when it gets hot.

    fieldkit thermal profile [quiet|balanced|performance|off]   show or choose the profile
    fieldkit thermal monitor                                      the monitor itself (started at login; runs until
                                                                  `thermal monitor --stop`)
    fieldkit thermal autostart on|off                            start it at login (the Startup folder), or not

Many laptops cannot have their fans set from Windows (the embedded controller ignores it), but the heat can be cut
at the source: the power plan's "Maximum processor state" (the governor's cap). A profile says at which CPU
temperature the cap comes down:

    mode 1  normal           cap 100%
    mode 2  turbo off        cap 99%   much less heat, a little slower
    mode 3  strong cooling   cap 70%

Going up is quick (the average of the last WINDOW readings reaches a threshold); coming down is slow on purpose (the
average must stay DOWN_MARGIN degrees below for DOWN_HOLD seconds), so the processor is not switched back and forth.
At DANGER it always speaks; a profile that never slows the processor still speaks at HOT. No reading for
NO_DATA_AFTER seconds: the cap goes back to 100% and it says so. The cap is 100% again whenever the monitor stops.

Born from the academic harness this generalises (thermal_control.py, 2026-10-05), on Fieldkit's rule that a sensor
is believed only after it has been PROVEN to move under load (sensors.best). The start at login is a Startup-folder
shortcut, not a scheduled task: a process Task Scheduler starts sees a different HKEY_CURRENT_USER, without the
HWiNFO values a desktop process sees (measured 2026-10-09). Linux needs none of this: the kernel and thermald do it.
"""
import collections
import json
import os
import sys
import time
from pathlib import Path

from ..core import settings

MODES = {1: 100, 2: 99, 3: 70}
PROFILES = {"quiet": {"mode2": 65, "mode3": 80, "about": "studying, writing, the browser: less heat, less noise"},
            "balanced": {"mode2": 75, "mode3": 85, "about": "right for almost everything"},
            "performance": {"mode2": 95, "mode3": None, "about": "rendering, video, heavy programs: full speed"},
            "off": {"mode2": None, "mode3": None, "about": "changes nothing; only speaks when it is hot"}}
DEFAULT = "balanced"
POLL, WINDOW, DOWN_MARGIN, DOWN_HOLD = 3, 3, 7, 60
DANGER, HOT, REPEAT_HOT, REPEAT_DANGER, NO_DATA_AFTER = 95, 85, 600, 300, 60


def _dir():
    d = Path(settings.ROOT) / "state" / "thermal"
    d.mkdir(parents=True, exist_ok=True)
    return d


def chosen():
    try:
        return json.loads((_dir() / "profile.json").read_text(encoding="utf-8")).get("profile", DEFAULT)
    except (OSError, ValueError):
        return DEFAULT


def choose(name):
    if name not in PROFILES:
        raise ValueError(f"profile {name!r}: {', '.join(PROFILES)}")
    (_dir() / "profile.json").write_text(json.dumps({"profile": name}), encoding="utf-8")
    return {"ok": True, "profile": name, "about": PROFILES[name]["about"],
            "next": "a running monitor picks it up within seconds"}


def mode_for(profile, temp):
    p, mode = PROFILES[profile], 1
    for m in (2, 3):
        if p[f"mode{m}"] is not None and temp >= p[f"mode{m}"]:
            mode = m
    return mode


class Controller:
    """Decides the mode from a stream of readings -> events [{kind: up|down|hot|danger, mode, temp}]."""

    def __init__(self, profile=DEFAULT, mode=1):
        self.profile, self.mode = profile, mode
        self.samples = collections.deque(maxlen=WINDOW)
        self.cool_since = None
        self.last_hot = self.last_danger = -1e9

    def set_profile(self, profile):
        self.profile, self.cool_since = profile, None

    def update(self, temp, now):
        events = []
        self.samples.append(temp)
        avg = sum(self.samples) / len(self.samples)
        wanted = mode_for(self.profile, avg)
        if wanted > self.mode:
            self.mode, self.cool_since = wanted, None
            events.append({"kind": "up", "mode": wanted, "temp": avg})
        elif self.mode > 1:
            limit = PROFILES[self.profile][f"mode{self.mode}"]
            if limit is None or avg < limit - DOWN_MARGIN:
                self.cool_since = self.cool_since if self.cool_since is not None else now
                if limit is None or now - self.cool_since >= DOWN_HOLD:
                    target = mode_for(self.profile, avg + DOWN_MARGIN)
                    if target < self.mode:
                        self.mode, self.cool_since = target, None
                        events.append({"kind": "down", "mode": target, "temp": avg})
            else:
                self.cool_since = None
        if avg >= DANGER:                                  # danger is said on its own schedule, never as "hot"
            if now - self.last_danger >= REPEAT_DANGER:
                self.last_danger = now
                events.append({"kind": "danger", "temp": avg})
        elif avg >= HOT and PROFILES[self.profile]["mode3"] is None and now - self.last_hot >= REPEAT_HOT:
            self.last_hot = now
            events.append({"kind": "hot", "temp": avg})
        return events


def words(ev):
    """What to say. SHORT: state, then action. See the note in watchdog/__init__.

    Measured 2026-10-13: the danger line went from 8.58 s to 3.39 s with no
    change of pace, purely by dropping words that carried no information.
    "The processor is at" is four words of preamble before the number that
    matters, and the listener has already been told what the subject is by the
    fact that the laptop just spoke to them.
    """
    t = round(ev["temp"])
    mode = ev.get("mode")
    return {"up": f"{t} degrees. Cooling mode {mode}."
                  + (" Maximum cooling." if mode == 3 else ""),
            "down": f"Down to {t} degrees. Cooling mode {mode}.",
            "hot": f"{t} degrees. Close heavy programs if you can.",
            "danger": f"{t} degrees. Save your work now.",
            }[ev["kind"]]


def monitor(sensor=None, set_cap=None, say=None, clock=time.time, sleep=time.sleep, ticks=None):
    """The loop. -> the events it acted on (for tests: give `sensor`, `set_cap`, `say`, `ticks`)."""
    from . import governor, sensors
    if sensor is None:
        name, sensor, detail = sensors.best(prove=True)
        if sensor is None:
            raise RuntimeError(f"no CPU temperature source moves under load here: {detail}")
    set_cap = set_cap or governor.set_cap
    if say is None:
        from ..buildh import herald
        say = herald.say
    stop = _dir() / "monitor.stop"
    stop.unlink(missing_ok=True)
    ctl = Controller(chosen())
    set_cap(MODES[1])
    acted, last_data, told, n = [], clock(), False, 0
    try:
        while (ticks is None or n < ticks) and not stop.exists():
            n += 1
            now = clock()
            if chosen() != ctl.profile:
                ctl.set_profile(chosen())
            t = sensor()
            if t is None:
                if now - last_data > NO_DATA_AFTER and not told:
                    told = True
                    say("I cannot read the processor temperature. Cooling is back to normal.")
                    if ctl.mode != 1:
                        ctl.mode = 1
                        set_cap(MODES[1])
            else:
                last_data, told = now, False
                for ev in ctl.update(t, now):
                    if ev["kind"] in ("up", "down"):
                        set_cap(MODES[ev["mode"]])
                    say(words(ev))
                    acted.append(ev)
            (_dir() / "monitor.json").write_text(json.dumps({"pid": os.getpid(), "profile": ctl.profile, "mode": ctl.mode,
                                                             "cpu": t, "updated": now}), encoding="utf-8")
            sleep(POLL)
    finally:
        set_cap(MODES[1])
    return acted


def stop_monitor():
    (_dir() / "monitor.stop").write_text("stop", encoding="utf-8")
    return {"ok": True, "next": "the monitor stops within a few seconds and puts the cap back to 100%"}


def autostart(on, platform=None, home=None, run=None):
    import subprocess
    run = run or subprocess.run
    if (platform or sys.platform) != "win32":
        return {"ok": False, "next": "on Linux the kernel and thermald cool the processor; nothing to start"}
    home = Path(home or Path.home())
    link = Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming") / "Microsoft" / "Windows" / \
        "Start Menu" / "Programs" / "Startup" / "Fieldkit cooling.lnk"
    if not on:
        link.unlink(missing_ok=True)
        return {"ok": not link.exists(), "removed": str(link), "next": "done"}
    py = Path(sys.executable)
    pyw = py.with_name("pythonw.exe") if py.with_name("pythonw.exe").is_file() else py
    q = lambda s: "'" + str(s).replace("'", "''") + "'"
    link.parent.mkdir(parents=True, exist_ok=True)
    run(["powershell", "-NoProfile", "-Command",
         f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut({q(link)}); $s.TargetPath={q(pyw)}; "
         f"$s.Arguments='-m fieldkit thermal monitor'; $s.WorkingDirectory={q(settings.ROOT)}; $s.WindowStyle=7; "
         "$s.Save()"], capture_output=True)
    return {"ok": link.is_file(), "link": str(link), "next": "starts at the next login"}
