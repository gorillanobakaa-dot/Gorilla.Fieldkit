"""CPU temperature sources on Windows, ranked by PROOF, never by readability.

2026-10-02 05:28: the laptop reset mid-compile. The build governor read `\\_TZ.THM0`, a chassis ACPI zone with
1-kelvin steps, which sat at 41.85 C for 386 samples under a 12-thread load and then reported 401 K. Readable,
plausible, useless. Linux reads the die through coretemp/hwmon; Windows has no such API, so every source here must
be made to move under load before it is believed (prove_live), every build.

Sources, best first:
  tpfan_window  - TPFanControl (the owner's fork) reads the EC `cpu` sensor through PawnIO and shows it in its window;
                  the Status edit control is read with WM_GETTEXT, no elevation, live every call.
  tpfan_csv     - TPFanControl_csv.txt when Log2csv=1 in its ini (admin to enable).
  thermalzone   - the 'Thermal Zone Information' performance counter (chassis zones).
  msacpi        - MSAcpi_ThermalZoneTemperature (static on this ThinkPad).
"""
import csv
import re
import subprocess
import sys
import time
from pathlib import Path

STATUS = re.compile(r"Switch:\s*(-?\d+)\s*.?\s*C\s*\(([^)]*)\)")
DUMMY_SENSOR = 140.0          # the EC publishes 148 on an unpopulated register; TPFanControl ignores it, so do we
TPFAN_DIR = Path(r"C:\Program Files\Gorilla TPFanControl")


def parse_status(text):
    """'Fan: 0x04 / Switch: 25° C (25; 0; 25; 0; 0; 25; 0; 148; 0; 1; 0; 0;)' -> (25.0, [25, 0, 25, ...]).
    The first number is the max TPFanControl itself acts on; the vector is every EC sensor."""
    m = STATUS.search(text or "")
    if not m:
        return None, []
    vec = [float(x) for x in re.findall(r"-?\d+", m.group(2))]
    return float(m.group(1)), vec


def _tpfan_window_text():
    """Every Edit control's text in the TPFanControl window, or [] when it is not running (user session only)."""
    if sys.platform != "win32":
        return []
    import ctypes
    import ctypes.wintypes as w
    u = ctypes.windll.user32
    hwnd = None
    cb = ctypes.WINFUNCTYPE(ctypes.c_bool, w.HWND, w.LPARAM)

    def top(h, _):
        nonlocal hwnd
        n = u.GetWindowTextLengthW(h)
        b = ctypes.create_unicode_buffer(n + 1)
        u.GetWindowTextW(h, b, n + 1)
        if b.value.startswith("TPFanControl"):
            hwnd = h
            return False
        return True
    u.EnumWindows(cb(top), 0)
    if not hwnd:
        return []
    texts = []

    def child(c, _):
        n = u.SendMessageW(c, 0x000E, 0, 0)                 # WM_GETTEXTLENGTH
        b = ctypes.create_unicode_buffer(n + 1)
        u.SendMessageW(c, 0x000D, n + 1, b)                 # WM_GETTEXT
        if b.value:
            texts.append(b.value)
        return True
    u.EnumChildWindows(hwnd, cb(child), 0)
    return texts


def tpfan_window():
    """-> the EC cpu temperature TPFanControl acts on, or None."""
    for t in _tpfan_window_text():
        if t.startswith("Fan:") and "Switch:" in t:         # the Status edit, not the Log edit (which quotes old lines)
            temp, vec = parse_status(t)
            if temp is not None:
                # the Switch value is TPFanControl's own maximum over the sensors it does NOT ignore; the raw vector
                # carries constant placeholders (148 on an unpopulated register, a steady 66 on another) that must
                # not win. vec[0] is the EC `cpu` sensor: take the hotter of the two, never the placeholders.
                # ONLY the EC `cpu` sensor (vec[0]). TPFanControl's own Switch value is the max over sensors it does
                # not ignore, and sensor 11 (`pwr`) publishes a constant 66 whenever it reads at all - at 07:38 the
                # Switch said 66 with the cpu at 40, and the stage was fed 66 flat until it stopped the build.
                cpu = vec[0] if vec and 0 < vec[0] < DUMMY_SENSOR else None
                return cpu
    return None


STATUS_FILE = Path(r"C:\ProgramData\TPFanControl\status.txt")


def tpfan_status(path=None, max_age_s=30):
    """TPFanControl 2.5.1-gorilla.4's status file (engine side, every sample, atomic): `cpu=` from
    ts=..;mode=..;cpu=..;max=..;load=..;fan=0x..;rpm=.. - no window needed. None when absent, stale or unreadable."""
    import time as _t
    p = Path(path) if path else STATUS_FILE
    try:
        if _t.time() - p.stat().st_mtime > max_age_s:
            return None
        fields = dict(kv.split("=", 1) for kv in p.read_text(encoding="utf-8", errors="replace").strip().split(";") if "=" in kv)
        cpu = float(fields.get("cpu", -1))
        return cpu if 0 < cpu < DUMMY_SENSOR else None
    except (OSError, ValueError):
        return None


def tpfan_csv(path=None):
    """Last row of TPFanControl_csv.txt (Log2csv=1): the max of the sensor columns below DUMMY_SENSOR, or None."""
    p = Path(path) if path else TPFAN_DIR / "TPFanControl_csv.txt"
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    rows = [l for l in lines if l.strip()]
    if len(rows) < 2:
        return None
    vals = [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", rows[-1].split(",", 1)[-1])]
    real = [v for v in vals if 0 < v < DUMMY_SENSOR]
    return max(real) if real else None


def _ps(cmd, timeout=60):
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    return (r.stdout or "").strip()


def thermalzone():
    try:
        return float(_ps("(Get-Counter '\\Thermal Zone Information(*)\\Temperature' -ErrorAction Stop).CounterSamples | "
                         "ForEach-Object { $_.CookedValue - 273.15 } | Measure-Object -Maximum | "
                         "ForEach-Object { [math]::Round($_.Maximum,2) }"))
    except ValueError:
        return None


def msacpi():
    try:
        return float(_ps("$z = Get-CimInstance -Namespace root/wmi -ClassName MSAcpi_ThermalZoneTemperature -ErrorAction Stop | "
                         "ForEach-Object { ($_.CurrentTemperature/10)-273.15 } | Measure-Object -Maximum; [math]::Round($z.Maximum,1)"))
    except ValueError:
        return None


PROVIDERS = (("tpfan_status", tpfan_status), ("tpfan_window", tpfan_window), ("tpfan_csv", tpfan_csv),
             ("thermalzone", thermalzone), ("msacpi", msacpi))


def perf_percent():
    try:
        return float(_ps("(Get-Counter '\\Processor Information(_Total)\\% Processor Performance' -ErrorAction Stop)"
                         ".CounterSamples[0].CookedValue"))
    except ValueError:
        return None


def _load(n):
    return [subprocess.Popen([sys.executable, "-c", "while True: pass"]) for _ in range(n)]


DIE_RISE = 10.0       # a package sensor climbs this much within a minute of twelve busy threads; a skin sensor does not


def grade(idle, hi):
    """'die' when the load test rose >= DIE_RISE, else 'surface' (moves, but not the silicon: this L15 Gen 3's EC
    `cpu` value is replicated on three sensors and read 27 C during a compile)."""
    return "die" if hi - idle >= DIE_RISE else "surface"


PROOF_CEILING = 80.0  # the proof stops loading at this reading (2026-10-03 14:24: an unguarded proof reset the laptop)


def prove_live(fn, settle=8, samples=6, interval=2.0, rise=2.0, threads=None, load=_load, ceiling=PROOF_CEILING):
    """Saturate the cores and require the reading to RISE by `rise` degrees or move by that much. -> (ok, detail).
    A source that does not move while twelve threads spin is not watching the silicon. `detail` ends with the grade."""
    import os
    idle = fn()
    if idle is None:
        return False, "no reading"
    procs = load(threads or max(1, (os.cpu_count() or 4) // 2))      # half the threads: enough to move a live sensor
    seen = []
    try:
        waited = 0.0
        while waited < settle:                                     # watch the ceiling while settling too
            time.sleep(1.0)
            waited += 1.0
            t = fn()
            if t is not None and t >= ceiling:
                seen.append(t)
                break
        for _ in range(samples):
            if seen and seen[-1] >= ceiling:
                break                                              # hot enough: stop loading NOW
            t = fn()
            if t is not None:
                seen.append(t)
            time.sleep(interval)
    finally:
        for p in procs:
            p.kill()
    if not seen:
        return False, "no reading under load"
    hi, lo = max(seen), min(seen)
    detail = f"idle {idle:.1f} C, under load {lo:.1f}-{hi:.1f} C [{grade(idle, hi)} sensor]"
    if hi - idle >= rise or hi - lo >= rise:
        return True, detail
    return False, detail + f" - moved less than {rise:.0f} C: not a live CPU sensor"


def best(prove=True, **kw):
    """The first provider that reads and (when `prove`) moves under load. -> (name, fn, detail) or (None, None, why)."""
    tried = []
    for name, fn in PROVIDERS:
        if fn() is None:
            tried.append(f"{name}: no reading")
            continue
        if not prove:
            return name, fn, "not proven"
        ok, detail = prove_live(fn, **kw)
        if ok and "[surface sensor]" in detail:
            # a SLOW die-tracking sensor looks like skin in a 20-second test: the EC cpu value climbed only
            # 32 -> 38 C in that test yet reached 82 C in a real compile (2026-10-02 07:50). Give it a minute.
            ok2, detail2 = prove_live(fn, settle=45 if kw.get("settle", 8) else 0, samples=8, interval=kw.get("interval", 2.0),
                                      rise=kw.get("rise", 2.0), threads=kw.get("threads"), load=kw.get("load", _load))
            if ok2:
                detail = detail2 + " (after a 60 s load)"
        tried.append(f"{name}: {detail}")
        if ok:
            return name, fn, detail
    return None, None, "; ".join(tried)
