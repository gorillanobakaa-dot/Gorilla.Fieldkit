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
"""
import json
import subprocess

PS = ("Add-Type -AssemblyName System.Windows.Forms; $p = [System.Windows.Forms.SystemInformation]::PowerStatus; "
      "@{line=[int]$p.PowerLineStatus; percent=[double]$p.BatteryLifePercent; flag=[int]$p.BatteryChargeStatus} | "
      "ConvertTo-Json -Compress")
LID = "keep the lid open: on this laptop a closed lid is standby and the run freezes (2026-10-10, six hours lost)"


def status(run=subprocess.run):
    """-> {"mains": bool|None, "percent": int|None, "battery": bool}; None when Windows cannot be asked."""
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
        ev = f"Windows did not say whether it is on mains; {LID}"
    elif st["mains"]:
        ev = f"on mains, {charge}; {LID}"
    else:
        ev = (f"ON BATTERY, {charge}: the run goes on; the herald says so aloud at 20, 10 and 5 per cent "
              f"(2026-10-09 13:19: a package step died at 18 %); {LID}")
    return {"check": "power", "ok": True, "evidence": ev, "on_battery": st["mains"] is False}
