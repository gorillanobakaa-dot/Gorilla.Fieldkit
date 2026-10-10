"""Is the laptop fit for a long run? On mains, and the run's owner told that a closed lid stops it.

Born 2026-10-10. Build 30 was started at 23:50 on battery. At 00:02 the lid closed; on this Modern Standby laptop a
closed lid is standby whatever a program asks (the window's stay-awake request holds only while the lid is open),
and the build froze until 06:20 with the battery at 19 %. The day before, an "unexpected shutdown" at 13:19 during a
package step came four minutes after Windows logged the battery at 18 %: very likely the battery running out
(Kernel-Power 506 records the charge on every standby). Nothing is changed on the machine: this only reads
[System.Windows.Forms.SystemInformation]::PowerStatus, and build-run refuses to start a compile on battery.
Changed the same day by the owner: "Screw the battery warnings ... the gate must work even when the laptop is on
battery but let the user know": nothing refuses on battery any more; the row says so, and the herald
(toolbox/herald/herald.ps1, started by every window) speaks at 20, 10 and 5 per cent.
On Linux (2026-10-10) the same answer comes from the kernel's /sys/class/power_supply, read only; herald.py speaks.
"""
import json
import subprocess
import sys
from pathlib import Path

PS = ("Add-Type -AssemblyName System.Windows.Forms; $p = [System.Windows.Forms.SystemInformation]::PowerStatus; "
      "@{line=[int]$p.PowerLineStatus; percent=[double]$p.BatteryLifePercent; flag=[int]$p.BatteryChargeStatus} | "
      "ConvertTo-Json -Compress")
LID = "keep the lid open: on this laptop a closed lid is standby and the run freezes (2026-10-10, six hours lost)"


SYSFS = Path("/sys/class/power_supply")


def _linux_status(root=SYSFS):
    """The kernel's own view (/sys/class/power_supply, read only): a Mains supply that is online, a Battery's capacity.
    No battery at all is a desktop or a server on mains; a folder that cannot be read is "cannot be asked"."""
    def val(d, name):
        try:
            return (d / name).read_text(encoding="ascii", errors="replace").strip()
        except OSError:
            return None
    try:
        supplies = [d for d in Path(root).iterdir()]
    except OSError:
        return {"mains": None, "percent": None, "battery": True}
    bats = [d for d in supplies if val(d, "type") == "Battery" and val(d, "present") != "0"]
    mains = [d for d in supplies if val(d, "type") in ("Mains", "USB", "USB_C", "USB_PD")]
    if not bats:
        return {"mains": True, "percent": None, "battery": False}
    caps = [int(c) for c in (val(d, "capacity") for d in bats) if c and c.isdigit()]
    online = any(val(d, "online") == "1" for d in mains)
    # no Mains entry but a battery that says it is charging or full: the charger is in
    online = online or any(val(d, "status") in ("Charging", "Full") for d in bats)
    return {"mains": online, "percent": min(caps) if caps else None, "battery": True}


def status(run=subprocess.run, platform=None, root=SYSFS):
    """-> {"mains": bool|None, "percent": int|None, "battery": bool}; None when the system cannot be asked."""
    if (platform or sys.platform) != "win32":
        return _linux_status(root)
    try:
        out = run(["powershell", "-NoProfile", "-Command", PS], capture_output=True, text=True, timeout=60).stdout
        d = json.loads(out)
    except (OSError, ValueError, subprocess.SubprocessError):
        return {"mains": None, "percent": None, "battery": True}
    no_battery = d.get("flag") == 128                        # BatteryChargeStatus.NoSystemBattery
    pct = None if no_battery or d.get("percent", 255) > 1 else round(d["percent"] * 100)
    return {"mains": True if no_battery else d.get("line") == 1, "percent": pct, "battery": not no_battery}


def row(st=None):
    """What the power is, said before a long run; never a refusal (the owner, 2026-10-10)."""
    st = st or status()
    charge = f"battery {st['percent']} %" if st.get("percent") is not None else "no battery"
    if st["mains"] is None:
        ev = f"the system did not say whether it is on mains; {LID}"
    elif st["mains"]:
        ev = f"on mains, {charge}; {LID}"
    else:
        ev = (f"ON BATTERY, {charge}: the run goes on; the herald says so aloud at 20, 10 and 5 per cent "
              f"(2026-10-09 13:19: a package step died at 18 %); {LID}")
    return {"check": "power", "ok": True, "evidence": ev, "on_battery": st["mains"] is False}
