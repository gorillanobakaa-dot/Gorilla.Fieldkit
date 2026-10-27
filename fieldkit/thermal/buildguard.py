"""Every compile job on this laptop runs guarded: the fan control must be cooling, and the thermal governor keeps the
CPU near thermal.build_target (67 -> about 70 C at most). Owner, 2026-10-13: "every time we start a compiling job,
coupled with the fan, that should keep this ultrathin Lenovo from cooking itself."

    fieldkit guard -- CMD ...        any build command (cargo build, go build, make, ...) under the guard
    guard(say, label)                the same for Fieldkit's own build stages (a context manager)

The fan check FAILS CLOSED on Windows: a build does not start while Gorilla TPFanControl (gorilla.4 or later) is not
running, its status file is not updating, or it is in a mode that does not cool for builds (BIOS, Smart, Manual).
The message says the one click that fixes it. On Linux the kernel's thermald does the governor's job; the fan check
is skipped there (no TPFanControl).
"""
import os
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

STATUS = Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "TPFanControl" / "status.txt"
FRESH_S = 20.0                       # the engine writes every 5 s
COOLING_MODES = {4: "Gorilla", 5: "Compile"}
MODE_NAMES = {1: "BIOS", 2: "Smart", 3: "Manual", 4: "Gorilla", 5: "Compile"}


class BuildRefused(RuntimeError):
    pass


def read_status(path=STATUS):
    try:
        text = Path(path).read_text(encoding="ascii", errors="replace")
    except OSError:
        return None
    out = {}
    for kv in text.strip().split(";"):
        if "=" in kv:
            k, v = kv.split("=", 1)
            try:
                out[k] = int(v, 16) if v.lower().startswith("0x") else int(v)
            except ValueError:
                out[k] = v
    return out or None


def fan_check(path=STATUS, now=None, platform=sys.platform):
    """-> (ok, message). Pure apart from reading the status file."""
    if platform != "win32":
        return True, "fan check: not Windows (the kernel's thermald cools Linux builds)"
    p = Path(path)
    if not p.is_file():
        return False, ("fan check: Gorilla TPFanControl is not running, or is older than gorilla.4 (no status file "
                       f"at {p}). Start it (Start menu: Gorilla TPFanControl), then build again.")
    age = (now or time.time()) - p.stat().st_mtime
    if age > FRESH_S:
        return False, (f"fan check: the fan control's status has not changed for {age:.0f} s: it is not running. "
                       "Start Gorilla TPFanControl, then build again.")
    s = read_status(p) or {}
    mode = s.get("mode")
    if mode not in COOLING_MODES:
        return False, (f"fan check: the fan control is in {MODE_NAMES.get(mode, mode)} mode, which does not cool for "
                       "builds. In its window pick Compile (Firefox & kernel), then build again.")
    return True, (f"fan check: {COOLING_MODES[mode]} mode, fan 0x{s.get('fan', 0):02x}, CPU {s.get('cpu')} C")


def kill_children(pid=None):
    """Stop every process this one started (the build), never this process or anything else."""
    pid = pid or os.getpid()
    if sys.platform == "win32":
        ps = (f"function k($p) {{ Get-CimInstance Win32_Process -Filter \"ParentProcessId=$p\" | ForEach-Object "
              f"{{ k $_.ProcessId; Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }} }}; k {pid}")
        subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True)
    else:
        subprocess.run(["pkill", "-TERM", "-P", str(pid)], capture_output=True)


@contextmanager
def guard(say=print, label="build", csv_dir=None, check_fan=True, sensor=None):
    """Fan check (fail closed), then the governor around the block. Yields the governor (verdict, peak)."""
    from ..core import preflight
    from .. import admission
    # 2026-10-26: the ONE run guard around every guarded build: the preflight first (idle models unloaded, orphans
    # stopped, the free-RAM floor), the RAM/temperature sampler during it, one end line after it. Stage O4: a slot of
    # the machine-wide gate first (fieldkit/admission.py), so a build waits its turn beside a suite or a mutate
    try:
        with admission.admitted("build", label, say=say), preflight.guard(label, "build", say) as rg:
            with _fan_and_governor(say, label, csv_dir, check_fan, sensor, rg) as gov:
                yield gov
    except preflight.Refused as e:
        raise BuildRefused(f"preflight: {e}") from e


@contextmanager
def _fan_and_governor(say, label, csv_dir, check_fan, sensor, rg):
    from . import governor as gv
    if check_fan:
        ok, msg = fan_check()
        say("  " + msg)
        if not ok:
            raise BuildRefused(msg)
    if sys.platform != "win32":
        yield None
        return
    if sensor is None:
        from ..buildh import buildrun
        name, fn, surface = buildrun.thermal_sensor(say)
        if fn is None:
            raise BuildRefused("no CPU temperature source responds to load (fieldkit thermal prove): not building blind")
    else:
        fn = sensor
    csv_path = Path(csv_dir) / f"thermal-{label}-{time.strftime('%Y%m%d-%H%M%S')}.csv" if csv_dir else None
    target = gv.build_target()
    say(f"  governor: CPU cap comes down above {target + 3:.0f} C (thermal.build_target {target:.0f})")
    gov = gv.Governor(fn, target_c=target, interval=3.0, kill=lambda why: kill_children(), on_event=say,
                      csv_path=csv_path)
    gov.start()
    if rg is not None:
        rg.governor = gov                             # the end line counts the seconds the cap was under 100%
    try:
        yield gov
    finally:
        gov.stop()
        gov.join(timeout=15)
        say(f"  governor stopped: peak {gov.peak} C" + (f"; {gov.verdict}" if gov.verdict else ""))


def run(cmd, cwd=None, say=print, check_fan=True):
    """fieldkit guard -- CMD: run CMD under the guard. -> exit code (2 when refused)."""
    try:
        with guard(say, label=Path(cmd[0]).stem, check_fan=check_fan) as gov:
            rc = subprocess.run(cmd, cwd=cwd).returncode
            if gov is not None and gov.verdict:
                say(f"  STOPPED by the governor: {gov.verdict}")
                return rc or 3
            return rc
    except BuildRefused as e:
        say(f"REFUSED: {e}")
        return 2
