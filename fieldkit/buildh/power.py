"""Is the laptop fit for a long run? On mains, and the run's owner told that a closed lid stops it.

Born 2026-10-10. Build 30 was started at 23:50 on battery. At 00:02 the lid closed; on this Modern Standby laptop a
closed lid is standby whatever a program asks (the window's stay-awake request holds only while the lid is open),
and the build froze until 06:20 with the battery at 19 %. The day before, an "unexpected shutdown" at 13:19 during a
package step came four minutes after Windows logged the battery at 18 %: very likely the battery running out
(Kernel-Power 506 records the charge on every standby). Nothing is changed on the machine: this only reads
[System.Windows.Forms.SystemInformation]::PowerStatus, and build-run refuses to start a compile on battery.
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
    """The run may start only on mains (a desktop without a battery always may)."""
    st = st or status()
    if st["mains"] is None:
        return {"check": "power: the laptop is on mains", "ok": False,
                "evidence": "Windows did not say whether it is on mains: plug it in and try again"}
    ok = bool(st["mains"])
    charge = f"battery {st['percent']} %" if st.get("percent") is not None else "no battery"
    return {"check": "power: the laptop is on mains", "ok": ok,
            "evidence": (f"on mains, {charge}; {LID}" if ok else
                         f"ON BATTERY, {charge}: a compile drains it (2026-10-09 13:19: shut down mid-package at 18 %); "
                         "plug the charger in, then start again")}
