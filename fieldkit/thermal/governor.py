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

Linux (2026-10-20): the same cap is the kernel's intel_pstate max_perf_pct, or cpufreq scaling_max_freq where the
driver is not intel_pstate. Written directly when the file is writable, else through `sudo -n tee` (never a password
prompt). When neither works, set_cap returns False and the caller says so: the cap is never claimed when it was not
set. read_cap/set_cap pick the system's own lever; no Windows command is started on Linux, nor the reverse.
"""
import csv
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import sensors

STEP_DOWN, STEP_UP, FLOOR = 10, 5, 30
HOT, KILL = 90.0, 95.0
RELAX_S, STALE_S = 45.0, 30.0
DEAD, BUSY = 60, 50.0        # 3 minutes at 3 s: a 1-degree EC sensor sits flat for a minute at steady load, not three
# The target every build-harness build uses. The cap comes down at target + 3, so 67 keeps a build at about 70 C at
# most (owner, 2026-10-13: "keep it to MAXIMUM 70 C"; it was 75, so up to ~78). fieldkit.local.json thermal.build_target
# overrides it; a value outside 50-90 is ignored. The fan's Compile mode only decides when the fan spins up.
BUILD_TARGET_DEFAULT = 67.0


def build_target(local=None):
    from ..core import settings
    local = settings.local_settings() if local is None else local
    try:
        t = float((local.get("thermal") or {}).get("build_target"))
    except (TypeError, ValueError):
        return BUILD_TARGET_DEFAULT
    return t if 50.0 <= t <= 90.0 else BUILD_TARGET_DEFAULT


def _powercfg(*args):
    r = subprocess.run(["powercfg", *args], capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), text=True, errors="replace")
    return r.returncode, (r.stdout or "") + (r.stderr or "")


PSTATE = Path("/sys/devices/system/cpu/intel_pstate/max_perf_pct")
CPUFREQ = Path("/sys/devices/system/cpu")


def _cpufreq_dirs(root=None):
    return sorted(Path(root or CPUFREQ).glob("cpu[0-9]*/cpufreq"))


def _linux_read_cap(pstate=None, cpufreq=None):
    """The cap in percent on Linux: max_perf_pct, else the first CPU's scaling_max_freq / cpuinfo_max_freq."""
    pstate = Path(pstate or PSTATE)
    try:
        return int(pstate.read_text().strip())
    except (OSError, ValueError):
        pass
    for d in _cpufreq_dirs(cpufreq):
        try:
            return round(100 * int((d / "scaling_max_freq").read_text()) / int((d / "cpuinfo_max_freq").read_text()))
        except (OSError, ValueError, ZeroDivisionError):
            continue
    return None


def _write(path, value, run=subprocess.run):
    """Write one sysfs value: directly when allowed, else `sudo -n tee` (fails at once instead of asking)."""
    try:
        Path(path).write_text(str(value))
        return True
    except OSError:
        pass
    try:
        r = run(["sudo", "-n", "tee", str(path)], input=str(value), capture_output=True, text=True, timeout=10)
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _linux_set_cap(pct, pstate=None, cpufreq=None, run=subprocess.run):
    pct = max(1, min(100, int(pct)))
    pstate = Path(pstate or PSTATE)
    if pstate.exists():
        return _write(pstate, pct, run)
    dirs = _cpufreq_dirs(cpufreq)
    ok = bool(dirs)
    for d in dirs:
        try:
            top = int((d / "cpuinfo_max_freq").read_text())
        except (OSError, ValueError):
            ok = False
            continue
        ok = _write(d / "scaling_max_freq", top * pct // 100, run) and ok
    return ok


def can_cap():
    """-> (True, how) when this system's cap can be moved by this user now, else (False, why). Nothing is changed:
    the current value is written back to itself."""
    if sys.platform == "win32":
        return (read_cap() is not None, "power plan PROCTHROTTLEMAX (powercfg)")
    if sys.platform.startswith("linux"):
        now = _linux_read_cap()
        if now is None:
            return False, "no intel_pstate or cpufreq cap file on this machine"
        how = "intel_pstate max_perf_pct" if PSTATE.exists() else "cpufreq scaling_max_freq"
        if _linux_set_cap(now):
            return True, how
        return False, f"{how} is root-only and sudo asks for a password; the monitor can only speak, not cool"
    return False, f"no processor cap is known on this system ({sys.platform})"


def read_cap():
    if sys.platform.startswith("linux"):
        return _linux_read_cap()
    if sys.platform != "win32":
        return None
    rc, out = _powercfg("/query", "scheme_current", "sub_processor", "PROCTHROTTLEMAX")
    for line in out.splitlines():
        if "Current AC Power Setting Index" in line:
            return int(line.split(":")[-1].strip(), 16)
    return None


def _marker():
    from ..core import settings
    return settings.state_root() / "state" / "cap-original.json"


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
    if sys.platform == "win32":
        for rail in ("/setacvalueindex", "/setdcvalueindex"):
            _powercfg(rail, "scheme_current", "sub_processor", "PROCTHROTTLEMAX", str(int(pct)))
        _powercfg("/setactive", "scheme_current")      # apply
        done = True
    elif sys.platform.startswith("linux"):
        done = _linux_set_cap(pct)
    else:
        done = False
    if m.exists():
        try:
            import json as _j
            if int(pct) >= _j.loads(m.read_text(encoding="utf-8")).get("original", 101):
                m.unlink()                              # back at (or above) the original: nothing to undo
        except (OSError, ValueError):
            pass
    return done


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
                 set_cap=set_cap, read_cap=read_cap, perf=None, clock=time.time, sleep=None, max_cap=None,
                 dead_check=True, ceiling=None, persistent=False):
        super().__init__(daemon=True)
        self.sensor, self.target, self.interval = sensor, target_c, interval
        self.csv_path = Path(csv_path) if csv_path else None
        self.kill, self.say = kill, on_event
        self._set_cap, self._read_cap, self._perf, self._clock = set_cap, read_cap, perf or sensors.perf_percent, clock
        self._halt = threading.Event()
        self._sleep = sleep or (lambda s: self._halt.wait(s))
        self.start_cap = None
        self.cap = None
        self.max_cap = max_cap       # a ceiling below the scheme's own cap: 80 while only a surface sensor exists
        self.dead_check = dead_check # the never-moved kill: for die sensors; a skin sensor plateaus for minutes
        # 2026-10-27 (crash ~17:10): the machine-wide governor (`thermal watch --forever`) lowers its ceiling while a
        # guarded job holds (brake.holds) and never exits: at the kill line it holds FLOOR and keeps watching
        self.ceiling, self.persistent = ceiling, persistent
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

    def _top(self):
        """The highest cap allowed now: the start cap, lowered by the live ceiling when one is given."""
        if self.ceiling is None:
            return self.start_cap
        try:
            return max(FLOOR, min(self.start_cap, int(self.ceiling())))
        except Exception:
            return self.start_cap

    def _die(self, why):
        if self.persistent:                                   # the machine governor never leaves the cap unwatched
            if self.verdict != why:
                self._event("HOLD", why)
            self.verdict = why
            self._apply(FLOOR, "hold at the floor")
            return
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
                        if not self.persistent:
                            return
                    self._sleep(self.interval)
                    continue
                self._last_reading = now
                if self.persistent and self.verdict and t < HOT:
                    self.verdict = None                       # readings back and below HOT: the cool rule climbs again
                self.peak = max(self.peak, t)
                busy = p is not None and p > BUSY
                self._flat = (self._flat + [t])[-DEAD:] if busy else []
                self._seen.add(t)
                if self.dead_check and len(self._flat) == DEAD and len(self._seen) == 1:   # die sensor that never moved
                    self._die(f"sensor stuck at {t:.2f} C since the start, {DEAD} busy samples")
                    return
                top = self._top()
                if self.cap > top:
                    self._apply(top, "a guarded job holds: ceiling for a surface sensor")
                if t >= KILL:
                    self._die(f"{t:.1f} C >= {KILL:.0f} C ceiling")
                    if not self.persistent:
                        return
                elif t >= HOT:
                    self._apply(FLOOR, f"{t:.1f} C >= {HOT:.0f} C")
                    self._cool_since = None
                elif t > self.target + 3:
                    self._apply(self.cap - STEP_DOWN, f"{t:.1f} C over target {self.target:.0f} C")
                    self._cool_since = None
                elif t < self.target - 6:
                    self._cool_since = self._cool_since or now
                    if now - self._cool_since >= RELAX_S and self.cap < top:
                        self._apply(min(top, self.cap + STEP_UP), f"{t:.1f} C, cool for {RELAX_S:.0f} s")
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
