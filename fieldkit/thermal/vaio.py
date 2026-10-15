# Version: 1.0.0 · updated 26-10-15-01-10
"""The Sony VAIO's sensors for the harness, read through the owner's own tool, sensors.gorilla.

Owner, 2026-10-15: "i used to have sensors. gorilla ... the reason i wired it in, is because is way more complex than
normal sensors". sensors.gorilla (toolbox/sensors-gorilla, also on GitHub as sensors-gorilla) reads what plain
`sensors` does not: the Sony thermal profile and whether thermal-profile-daemon is stepping it, the fan, RAPL package
power, CPU and GPU frequency, and thermal throttling since boot. The harness reuses its functions instead of
re-reading sysfs: one place knows this laptop.

    snapshot()        -> dict of the readings (None for what cannot be read)
    line(snap)        -> one log line
    log(path, ...)    -> append one line per interval until a pid ends or a time is up (a build's thermal record)

Windows keeps fieldkit/thermal/sensors.py (TPFanControl, HWiNFO, PowerShell).
"""
import importlib.machinery
import importlib.util
import os
import subprocess
import time
from pathlib import Path

from ..core import settings

TOOL = settings.ROOT / "toolbox" / "sensors-gorilla" / "sensors.gorilla"
SONY = Path("/sys/devices/platform/sony-laptop")


def load(path=None):
    """sensors.gorilla as a module, or None when it is not on this machine (never invented)."""
    p = Path(path or TOOL)
    if not p.is_file():
        return None
    spec = importlib.util.spec_from_loader("sensors_gorilla", importlib.machinery.SourceFileLoader("sensors_gorilla", str(p)))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _read(p):
    try:
        return Path(p).read_text().strip()
    except OSError:
        return None


def _daemon():
    try:
        return subprocess.run(["systemctl", "is-active", "thermal-profile-daemon"], capture_output=True, text=True,
                              timeout=5).stdout.strip() or None
    except (OSError, subprocess.TimeoutExpired):
        return None


def snapshot(mod=None, sony=SONY):
    m = mod if mod is not None else load()
    if m is None:
        return {"tool": None}
    temps = m.get_cpu_temps() or {}
    pkg = next((v for k, v in temps.items() if "Package" in k), None)
    cores = [v for k, v in temps.items() if "Core" in k]
    rapl = m.get_rapl_power() or {}
    ghz = (m.get_frequencies() or (None,))[0]
    thr = m.get_throttle_stats() or {}
    fan = _read(Path(sony) / "fanspeed")
    return {"tool": str(TOOL), "package_c": pkg, "core_max_c": max(cores) if cores else None,
            "fan_pct": int(fan) if fan and fan.isdigit() else None,
            "profile": _read(Path(sony) / "thermal_control"), "daemon": _daemon(),
            "package_w": round(rapl["package"], 1) if rapl.get("package") is not None else None,
            "cpu_ghz": round(ghz, 2) if isinstance(ghz, (int, float)) else None,
            "throttle_events": thr.get("pkg"), "throttle_ms": thr.get("pkg_ms")}


def line(s):
    if not s.get("tool"):
        return f"{time.strftime('%Y-%m-%d %H:%M:%S')} sensors.gorilla not found"
    return (f"{time.strftime('%Y-%m-%d %H:%M:%S')} package {s['package_c']} C, hottest core {s['core_max_c']} C, "
            f"fan {s['fan_pct']}%, profile {s['profile']} (daemon {s['daemon']}), {s['package_w']} W, "
            f"{s['cpu_ghz']} GHz, throttling since boot: {s['throttle_events']} events / {s['throttle_ms']} ms")


def log(path, interval=60, until_pid=None, seconds=None, mod=None, say=None):
    """Append one line per interval to `path` until `until_pid` ends or `seconds` pass. -> lines written."""
    m = mod if mod is not None else load()
    end = time.monotonic() + seconds if seconds else None
    n = 0
    with open(path, "a", encoding="utf-8") as fh:
        while True:
            text = line(snapshot(m))
            fh.write(text + "\n")
            fh.flush()
            say and say(text)
            n += 1
            if end is not None and time.monotonic() >= end:
                break
            if until_pid is not None:
                try:
                    os.kill(int(until_pid), 0)
                except (OSError, ValueError):
                    break
            if until_pid is None and end is None:
                break
            time.sleep(interval)
    return n
