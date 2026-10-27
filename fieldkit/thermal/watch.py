"""The always-on machine-wide governor: `fieldkit thermal watch --forever --target 67`, started at logon.

Crash 2026-10-27 ~17:10: the only governors were per command (`fieldkit guard`), so a privacy scan, Codex or a git
hook outside the guard heated the laptop with nothing braking, and between commands the cap went back to 100%. The
fan window said it in words ("also run fieldkit thermal watch --target 67"); nothing started it.

One per computer (the lease in state/thermal/machine-governor.json). It owns PROCTHROTTLEMAX: the cap comes down above
target + 3 on the proven sensor, and while a guarded job holds (brake.holds) the ceiling is buildrun.SURFACE_CAP when
that sensor is only a surface reading. It never exits on its own: past the kill line it holds the floor and keeps
watching. Events go to state/thermal/watch-YYYY-MM-DD.log (pythonw has no console).
"""
import time

from . import brake, governor as gv


def _logger(folder=None):
    def say(msg):
        d = brake._dir() if folder is None else folder
        try:
            d.mkdir(parents=True, exist_ok=True)
            with open(d / f"watch-{time.strftime('%Y-%m-%d')}.log", "a", encoding="utf-8") as fh:
                fh.write(f"{time.strftime('%H:%M:%S')} {str(msg).strip()}\n")
        except OSError:
            pass
        try:
            print(msg, flush=True)
        except (OSError, ValueError, AttributeError):
            pass                                          # pythonw: no stdout
    return say


def forever(target=None, say=None, sensor=None, ticks=None, sleep=time.sleep, folder=None):
    """-> exit code. `sensor` = (fn, surface) for tests; `ticks` ends the loop after that many lease writes."""
    say = say or _logger(folder)
    from ..buildh import buildrun
    if brake.machine_alive(folder):
        say("thermal watch: a machine governor already runs; not starting a second one")
        return 0
    target = gv.build_target() if target is None else float(target)
    if sensor is None:
        name, fn, surface = buildrun.thermal_sensor(say)
        if fn is None:
            say("thermal watch: no CPU temperature source responds to load; nothing to govern with")
            return 3
    else:
        (fn, surface), name = sensor, "test"
    ceiling = (lambda: buildrun.SURFACE_CAP if brake.holds(folder) else 100) if surface else None
    gov = gv.Governor(fn, target_c=target, interval=3.0, kill=None, on_event=say, ceiling=ceiling,
                      persistent=True, dead_check=not surface, set_cap=gv.set_cap, read_cap=gv.read_cap)
    brake.lease_write(folder)
    say(f"thermal watch: machine governor on {name}, cap comes down above {target + 3:.0f} C"
        + (f"; ceiling {buildrun.SURFACE_CAP}% while a guarded job runs (surface sensor)" if surface else ""))
    gov.start()
    n = 0
    try:
        while gov.is_alive() and (ticks is None or n < ticks):
            brake.lease_write(folder)
            brake.restore_stale_boost(folder, say=say)
            n += 1
            sleep(3)
    finally:
        gov.stop()
        gov.join(timeout=15)
        brake.lease_clear(folder)
        say(f"thermal watch: stopped, peak {gov.peak} C")
    return 0
