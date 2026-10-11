"""The processor guard: is the processor locked slow by the firmware, and if so, unlock it and prove it.

    fieldkit laptop cpu check        measure under a short load and say: full speed, throttled, or cannot measure
    fieldkit laptop cpu guard        check; when throttled, start the unlock tool (if one is set), put the Windows
                                     "Maximum processor state" back to 100%, and measure again
    fieldkit laptop cpu install      Windows: run `guard` at every logon (a scheduled task, highest privileges)
    fieldkit laptop cpu uninstall    remove that task
    fieldkit laptop cpu status       the last recorded run and the task; measures nothing

Born from the academic harness this generalises (cpu_guard.py, 2026-10-08): at every boot a laptop's BIOS asserted
BD PROCHOT, a "too hot" signal from a sensor that was not hot, and the processor stayed locked at 0.80 GHz; Windows
logs it as Kernel-Processor-Power event 37 ("limited by system firmware"). An unlock tool such as ThrottleStop clears
it, but only while it runs. Nothing here is that laptop's: the unlock tool is whatever the person names in
fieldkit.local.json ({"laptop": {"unlock_tool": "C:\\\\...\\\\ThrottleStop.exe"}}), and "throttled" means slower than
THROTTLED_RATIO of the processor's own base speed, not a number from one machine.

Deterministic: the same machine state gives the same verdict; every run is written to state/cpu-guard.json, every
action is measured afterwards, and the exit code says the result (0 full speed, 1 throttled or the tool would not
start, 2 cannot measure). On Linux it measures (the cpufreq files and the kernel's throttle counters) and says what
it sees; unlocking BD PROCHOT there needs root and msr-tools, which it names rather than runs.
"""
import datetime as _dt
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from ..core import settings

THROTTLED_RATIO = 0.55          # BD PROCHOT locks at about 0.8 of 2.9 GHz (0.28); a cooling cap of 70% is about 0.7
SAMPLES = 3
TASK = "FieldkitCpuGuard"
PERF = r"\Processor Information(_Total)\% Processor Performance"
FREQ = r"\Processor Information(_Total)\Processor Frequency"
NOWIN = 0x08000000


def _state_file():
    return Path(settings.ROOT) / "state" / "cpu-guard.json"


def _local():
    return settings.local_settings().get("laptop") or {}


# -- pure logic -------------------------------------------------------------------------------------------------------
def parse_typeperf(text):
    """(performance %, base MHz) for every sample line of typeperf's CSV output."""
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith('"') or "PDH-CSV" in line:
            continue
        cells = [c.strip().strip('"') for c in line.split('","')]
        if len(cells) >= 3:
            try:
                out.append((float(cells[1]), float(cells[2])))
            except ValueError:
                continue
    return out


def speed(samples):
    """(best MHz seen, base MHz): the largest performance % times its base frequency."""
    best, base = None, None
    for perf, b in samples:
        if b > 0 and (best is None or perf * b / 100.0 > best):
            best, base = perf * b / 100.0, b
    return (None if best is None else int(round(best))), (None if base is None else int(round(base)))


def verdict(mhz, base, tool_running=True, ratio=THROTTLED_RATIO):
    """'ok', 'throttled', 'no-tool' (an unlock tool is set and is not running) or 'unknown'."""
    if not tool_running:
        return "no-tool"
    if mhz is None or not base:
        return "unknown"
    return "ok" if mhz >= ratio * base else "throttled"


# -- Windows ----------------------------------------------------------------------------------------------------------
def _spin(stop):
    x = 0
    while not stop.is_set():
        x = (x * 31 + 7) % 1000003


def measure_windows(run=subprocess.run, samples=SAMPLES):
    stop = threading.Event()
    workers = [threading.Thread(target=_spin, args=(stop,), daemon=True) for _ in range(2)]
    for w in workers:
        w.start()
    try:
        time.sleep(0.5)                                  # let the clocks ramp before the first sample
        r = run(["typeperf", PERF, FREQ, "-sc", str(samples), "-si", "1"], capture_output=True, text=True,
                timeout=samples + 20, creationflags=NOWIN)
        return speed(parse_typeperf(r.stdout or ""))
    except (OSError, subprocess.SubprocessError):
        return None, None
    finally:
        stop.set()


def firmware_limits_since_boot(run=subprocess.run):
    """How often the firmware limited the processor since this boot (System log, Kernel-Processor-Power event 37)."""
    ps = ("$b=(Get-CimInstance Win32_OperatingSystem).LastBootUpTime; (Get-WinEvent -FilterHashtable @{LogName="
          "'System'; ProviderName='Microsoft-Windows-Kernel-Processor-Power'; Id=37; StartTime=$b} -ErrorAction "
          "SilentlyContinue | Measure-Object).Count")
    try:
        r = run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=60,
                creationflags=NOWIN)
        return int((r.stdout or "").strip().splitlines()[-1])
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return None


def tool_running(tool, run=subprocess.run):
    if not tool:
        return True                                      # none set: nothing that has to run
    name = Path(tool).name
    try:
        r = run(["tasklist", "/FI", f"IMAGENAME eq {name}", "/NH"], capture_output=True, text=True,
                creationflags=NOWIN)
        return name.lower() in (r.stdout or "").lower()
    except OSError:
        return False


def start_tool(tool, run=subprocess.run, wait=15, sleep=time.sleep):
    """Start the unlock tool as administrator (a logon task already is; a manual run asks Windows) and wait for it."""
    if not tool or not Path(tool).is_file():
        return False
    if tool_running(tool, run):
        return True
    folder = str(Path(tool).parent)
    q = lambda s: "'" + str(s).replace("'", "''") + "'"
    run(["powershell", "-NoProfile", "-Command",
         f"Start-Process -FilePath {q(tool)} -WorkingDirectory {q(folder)} -Verb RunAs"],
        capture_output=True, creationflags=NOWIN)
    for _ in range(wait * 2):
        sleep(0.5)
        if tool_running(tool, run):
            sleep(3)                                      # a moment to clear BD PROCHOT
            return True
    return False


def stop_tool(tool, run=subprocess.run):
    run(["taskkill", "/IM", Path(tool).name, "/F"], capture_output=True, creationflags=NOWIN)


# -- Linux -------------------------------------------------------------------------------------------------------------
def measure_linux(root="/sys/devices/system/cpu", cpuinfo="/proc/cpuinfo"):
    """(MHz under load, base MHz, throttle events) from cpufreq and the kernel's thermal_throttle counters."""
    stop = threading.Event()
    workers = [threading.Thread(target=_spin, args=(stop,), daemon=True) for _ in range(2)]
    for w in workers:
        w.start()
    try:
        time.sleep(1.0)
        cur = []
        for p in Path(root).glob("cpu[0-9]*/cpufreq/scaling_cur_freq"):
            try:
                cur.append(int(p.read_text().strip()) // 1000)
            except (OSError, ValueError):
                continue
        if not cur:
            try:
                cur = [int(float(l.split(":")[1])) for l in Path(cpuinfo).read_text().splitlines()
                       if l.lower().startswith("cpu mhz")]
            except (OSError, ValueError, IndexError):
                cur = []
    finally:
        stop.set()
    base = None
    for name in ("base_frequency", "cpuinfo_max_freq"):
        p = Path(root) / "cpu0" / "cpufreq" / name
        if p.is_file():
            try:
                base = int(p.read_text().strip()) // 1000
                break
            except ValueError:
                continue
    events = 0
    for p in Path(root).glob("cpu[0-9]*/thermal_throttle/package_throttle_count"):
        try:
            events = max(events, int(p.read_text().strip()))
        except (OSError, ValueError):
            continue
    return (max(cur) if cur else None), base, events


# -- the runs ---------------------------------------------------------------------------------------------------------
def check(fix=False, platform=None, run=subprocess.run, sleep=time.sleep, tool=None, now=None):
    win = (platform or sys.platform) == "win32"
    tool = tool if tool is not None else _local().get("unlock_tool")
    ratio = float(_local().get("throttled_ratio") or THROTTLED_RATIO)
    st = {"when": (now or _dt.datetime.now()).strftime("%Y-%m-%d %H:%M:%S"), "platform": "windows" if win else "linux",
          "unlock_tool": tool or None, "actions": [], "fixed_from": None}
    if win:
        running = tool_running(tool, run)
        if not running and fix:
            running = start_tool(tool, run, sleep=sleep)
            st["actions"].append("started the unlock tool" if running else "the unlock tool would not start")
        mhz, base = measure_windows(run)
        v = verdict(mhz, base, running, ratio)
        if v == "throttled" and fix:
            before = mhz
            if tool:
                stop_tool(tool, run)
                if start_tool(tool, run, wait=10, sleep=sleep):
                    st["actions"].append("restarted the unlock tool")
            from ..thermal import governor
            governor.set_cap(100)
            st["actions"].append("maximum processor state back to 100%")
            mhz, base = measure_windows(run)
            v = verdict(mhz, base, tool_running(tool, run), ratio)
            if v == "ok":
                st["fixed_from"] = before
        st["firmware_limits_since_boot"] = firmware_limits_since_boot(run)
    else:
        mhz, base, events = measure_linux()
        v = verdict(mhz, base, True, ratio)
        st["throttle_events"] = events
        if v == "throttled":
            st["actions"].append("not changed: BD PROCHOT is cleared on Linux with root and msr-tools "
                                 "(sudo wrmsr 0x1FC with bit 0 off), which this does not run for you")
    st.update(mhz=mhz, base_mhz=base, verdict=v)
    p = _state_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(st, indent=1), encoding="utf-8")
    return st


def exit_code(st):
    return {"ok": 0, "throttled": 1, "no-tool": 1}.get(st.get("verdict"), 2)


def status(run=subprocess.run, platform=None):
    try:
        st = json.loads(_state_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        st = {}
    task = None
    if (platform or sys.platform) == "win32":
        r = run(["powershell", "-NoProfile", "-Command", f"(Get-ScheduledTask -TaskName '{TASK}' -ErrorAction Stop).State"],
                capture_output=True, text=True, creationflags=NOWIN)
        task = (r.stdout or "").strip() or None
    return {"last": st, "logon_task": task}


def task_script(python, fieldkit_root, remove=False):
    """The PowerShell that registers (or removes) the logon task. It runs elevated: the unlock tool needs it."""
    if remove:
        return f"Unregister-ScheduledTask -TaskName '{TASK}' -Confirm:$false -ErrorAction SilentlyContinue"
    pyw = str(Path(python).with_name("pythonw.exe")) if Path(python).with_name("pythonw.exe").is_file() else python
    q = lambda s: "'" + str(s).replace("'", "''") + "'"
    return (f"$a = New-ScheduledTaskAction -Execute {q(pyw)} -Argument '-m fieldkit laptop cpu guard' "
            f"-WorkingDirectory {q(fieldkit_root)}\n"
            "$t = New-ScheduledTaskTrigger -AtLogOn -User \"$env:USERDOMAIN\\$env:USERNAME\"\n"
            "$s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries "
            "-ExecutionTimeLimit (New-TimeSpan -Minutes 10) -MultipleInstances IgnoreNew\n"
            "$p = New-ScheduledTaskPrincipal -UserId \"$env:USERDOMAIN\\$env:USERNAME\" -LogonType Interactive "
            "-RunLevel Highest\n"
            f"Register-ScheduledTask -TaskName '{TASK}' -Action $a -Trigger $t -Settings $s -Principal $p "
            "-Description 'Fieldkit: the processor is not locked slow at logon' -Force | Out-Null")


def install(remove=False, run=subprocess.run, platform=None):
    if (platform or sys.platform) != "win32":
        return {"ok": False, "status": "windows-only",
                "next": "on Linux, run fieldkit laptop cpu check from your session's autostart if you want it at login"}
    import tempfile
    from ..win11.run import elevated_wrapper
    script = Path(tempfile.mkdtemp()) / "cpu-guard-task.ps1"
    script.write_text(task_script(sys.executable, settings.ROOT, remove), encoding="utf-8")
    out = script.with_suffix(".log")
    r = run(elevated_wrapper(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)], str(out)),
            capture_output=True, text=True)
    state = status(run, platform)["logon_task"]
    ok = (state is None) if remove else (state in ("Ready", "Running"))
    return {"ok": ok, "status": "removed" if remove else "installed", "task": state, "exit": r.returncode,
            "next": "done" if ok else "Windows did not register the task: run this again and click Yes when asked"}


def lines(st):
    v = st.get("verdict")
    say = {"ok": f"full speed: {st.get('mhz')} MHz under load (base {st.get('base_mhz')} MHz)",
           "throttled": f"THROTTLED: {st.get('mhz')} MHz under load, base {st.get('base_mhz')} MHz",
           "no-tool": f"the unlock tool is not running and would not start: {st.get('unlock_tool')}",
           "unknown": "cannot measure the processor speed here"}.get(v, str(v))
    out = [say]
    if st.get("fixed_from"):
        out.append(f"it was locked at {st['fixed_from']} MHz; unlocked")
    if st.get("firmware_limits_since_boot") is not None:
        out.append(f"the firmware limited the processor {st['firmware_limits_since_boot']} time(s) since boot (event 37)")
    if st.get("throttle_events"):
        out.append(f"the kernel counted {st['throttle_events']} package throttle event(s)")
    out += [f"  {a}" for a in st.get("actions", [])]
    return out
