"""A thermald for Windows builds: watch a PROVEN CPU sensor, lower the processor cap before the silicon gets hot,
raise it back when it cools, and kill the build when the sensor dies or the die passes the ceiling.

Linux does this in the kernel (intel_pstate + thermald). Windows leaves it to the firmware trip, which is what reset
the laptop on 2026-10-02 at 05:28. The lever is the same one the owner's governor sets once at build start -
PROCTHROTTLEMAX on the active power scheme (powercfg, no elevation needed) - moved every few seconds instead.

Rules (deterministic, hysteretic, logged to a CSV and through `on_event`):
  * temp > target + 3           -> cap -= STEP_DOWN  (floor FLOOR)              every interval while hot
  * temp >= HOT                 -> cap = FLOOR at once
  * temp < target - 6 for RELAX -> cap += STEP_UP   (ceiling = the cap the build started with)
  * temp >= KILL                -> kill the build, cap = FLOOR, stop
  * DEAD identical readings while perf > BUSY (3 min) -> the sensor died: kill the build, stop
  * no reading for STALE_S      -> same
The original cap is restored on stop, whatever happened.
"""
import csv
import subprocess
import threading
import time
from pathlib import Path

from . import sensors

STEP_DOWN, STEP_UP, FLOOR = 10, 5, 30
HOT, KILL = 90.0, 95.0
RELAX_S, STALE_S = 45.0, 30.0
DEAD, BUSY = 60, 50.0        # 3 minutes at 3 s: a 1-degree EC sensor sits flat for a minute at steady load, not three


def _powercfg(*args):
    r = subprocess.run(["powercfg", *args], capture_output=True, text=True, errors="replace")
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def read_cap():
    rc, out = _powercfg("/query", "scheme_current", "sub_processor", "PROCTHROTTLEMAX")
    for line in out.splitlines():
        if "Current AC Power Setting Index" in line:
            return int(line.split(":")[-1].strip(), 16)
    return None


def _marker():
    from ..core import settings
    return settings.ROOT / "state" / "cap-original.json"


def set_cap(pct):
    """Set PROCTHROTTLEMAX. The first lowering writes the original value to a marker, so a crash that skips the
    restore is undone by `restore_stale_cap()` on the next run (2026-10-03: a reset left the laptop at 40%)."""
    import json
    m = _marker()
    if not m.exists():
        orig = read_cap()
        if orig is not None and int(pct) < orig:
            try:
                m.parent.mkdir(parents=True, exist_ok=True)
                m.write_text(json.dumps({"original": orig}), encoding="utf-8")
            except OSError:
                pass
    for rail in ("/setacvalueindex", "/setdcvalueindex"):
        _powercfg(rail, "scheme_current", "sub_processor", "PROCTHROTTLEMAX", str(int(pct)))
    _powercfg("/setactive", "scheme_current")          # apply
    if m.exists():
        try:
            import json as _j
            if int(pct) >= _j.loads(m.read_text(encoding="utf-8")).get("original", 101):
                m.unlink()                              # back at (or above) the original: nothing to undo
        except (OSError, ValueError):
            pass


def restore_stale_cap(say=print):
    """A marker left by a crashed run means the cap was never put back: restore the recorded original."""
    import json
    m = _marker()
    if not m.exists():
        return None
    try:
        orig = int(json.loads(m.read_text(encoding="utf-8"))["original"])
    except (OSError, ValueError, KeyError):
        orig = 100
    set_cap(orig)
    try:
        m.unlink()
    except OSError:
        pass
    say(f"  power cap: a crashed run had left it lowered; restored to {orig}%")
    return orig


class Governor(threading.Thread):
    def __init__(self, sensor, target_c=75.0, interval=3.0, csv_path=None, kill=None, on_event=print,
                 set_cap=set_cap, read_cap=read_cap, perf=sensors.perf_percent, clock=time.time, sleep=None, max_cap=None,
                 dead_check=True):
        super().__init__(daemon=True)
        self.sensor, self.target, self.interval = sensor, target_c, interval
        self.csv_path = Path(csv_path) if csv_path else None
        self.kill, self.say = kill, on_event
        self._set_cap, self._read_cap, self._perf, self._clock = set_cap, read_cap, perf, clock
        self._halt = threading.Event()
        self._sleep = sleep or (lambda s: self._halt.wait(s))
        self.start_cap = None
        self.cap = None
        self.max_cap = max_cap       # a ceiling below the scheme's own cap: 80 while only a surface sensor exists
        self.dead_check = dead_check # the never-moved kill: for die sensors; a skin sensor plateaus for minutes
        self._orig_cap = None
        self.peak = 0.0
        self.samples = 0
        self.verdict = None                                   # why the build was killed, if it was
        self._cool_since = None
        self._flat = []
        self._seen = set()
        self._last_reading = None

    def stop(self):
        self._halt.set()

    def _event(self, kind, detail):
        self.say(f"  thermal: {kind}: {detail}")

    def _apply(self, cap, why):
        cap = max(FLOOR, min(self.start_cap, int(cap)))
        if cap != self.cap:
            self._set_cap(cap)
            self.cap = cap
            self._event("cap", f"{cap}% ({why})")

    def _die(self, why):
        self.verdict = why
        self._event("KILL", why)
        self._apply(FLOOR, "kill")
        if self.kill:
            self.kill(why)

    def run(self):
        self._orig_cap = self._read_cap() or 100
        self.start_cap = min(self._orig_cap, self.max_cap) if self.max_cap else self._orig_cap
        self.cap = self._orig_cap
        if self.start_cap != self.cap:
            self._apply(self.start_cap, "ceiling while only a surface sensor exists")
        fh = w = None
        if self.csv_path:
            self.csv_path.parent.mkdir(parents=True, exist_ok=True)
            fh = open(self.csv_path, "w", newline="", encoding="utf-8")
            w = csv.writer(fh)
            w.writerow(["elapsed_s", "temp_c", "perf_pct", "cap_pct"])
        t0 = self._clock()
        self._last_reading = t0
        try:
            while not self._halt.is_set():
                now = self._clock()
                t = self.sensor()
                p = self._perf()
                self.samples += 1
                if w:
                    w.writerow([f"{now - t0:.1f}", "" if t is None else f"{t:.2f}", "" if p is None else f"{p:.1f}", self.cap])
                    fh.flush()
                if t is None:
                    if now - self._last_reading >= STALE_S:
                        self._die(f"no temperature reading for {STALE_S:.0f} s")
                        return
                    self._sleep(self.interval)
                    continue
                self._last_reading = now
                self.peak = max(self.peak, t)
                busy = p is not None and p > BUSY
                self._flat = (self._flat + [t])[-DEAD:] if busy else []
                self._seen.add(t)
                if self.dead_check and len(self._flat) == DEAD and len(self._seen) == 1:   # die sensor that never moved
                    self._die(f"sensor stuck at {t:.2f} C since the start, {DEAD} busy samples")
                    return
                if t >= KILL:
                    self._die(f"{t:.1f} C >= {KILL:.0f} C ceiling")
                    return
                if t >= HOT:
                    self._apply(FLOOR, f"{t:.1f} C >= {HOT:.0f} C")
                    self._cool_since = None
                elif t > self.target + 3:
                    self._apply(self.cap - STEP_DOWN, f"{t:.1f} C over target {self.target:.0f} C")
                    self._cool_since = None
                elif t < self.target - 6:
                    self._cool_since = self._cool_since or now
                    if now - self._cool_since >= RELAX_S and self.cap < self.start_cap:
                        self._apply(self.cap + STEP_UP, f"{t:.1f} C, cool for {RELAX_S:.0f} s")
                        self._cool_since = now
                else:
                    self._cool_since = None
                self._sleep(self.interval)
        finally:
            if fh:
                fh.close()
            if self._orig_cap is not None and self.cap != self._orig_cap:
                self._set_cap(self._orig_cap)
                self._event("cap", f"restored to {self._orig_cap}%")


def kill_tree(pid):
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
