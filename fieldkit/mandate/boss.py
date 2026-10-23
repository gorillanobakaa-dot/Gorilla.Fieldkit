# Version: 1.0.0 · updated 26-10-23-19-43
"""Choosing the boss: which device orchestrates a project, proposed by a fixed rule, approved by signing the mandate.

    fieldkit mandate boss          the devices this one sees (itself, and the paired one over the link) and the proposal

Owner, 2026-10-23 (plan section 17.1): at project start the harness lists the devices it sees and proposes a boss by a
fixed rule, always on > most resources > on cable; the owner confirms or picks another, and that choice is approved by
signing the mandate, which names it:
    orchestrator: {device: <host name>, key: <its result key fingerprint>}
    backup:       {device: ..., key: ...}            # optional: takes over if the boss dies
Workers learn the boss from the sealed mandate and ask nobody; a job from any other device is refused (jobs.py). With no
backup and the boss gone, workers finish the job they have and take no new one.
"""
import os
import socket
import subprocess
import sys
from pathlib import Path


def _mem_gb():
    try:
        if sys.platform == "win32":
            import ctypes

            class MS(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            m = MS()
            m.dwLength = ctypes.sizeof(MS)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            return round(m.ullTotalPhys / 2**30, 1)
        if sys.platform == "darwin":
            out = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True).stdout.strip()
            return round(int(out) / 2**30, 1)
        return round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30, 1)
    except (OSError, ValueError, AttributeError):
        return None


def _power(root=Path("/sys/class/power_supply")):
    """-> (has_battery, on_mains): None where this system does not say."""
    try:
        if sys.platform == "win32":
            import ctypes

            class PS(ctypes.Structure):
                _fields_ = [("ACLineStatus", ctypes.c_byte), ("BatteryFlag", ctypes.c_byte),
                            ("BatteryLifePercent", ctypes.c_byte), ("SystemStatusFlag", ctypes.c_byte),
                            ("BatteryLifeTime", ctypes.c_ulong), ("BatteryFullLifeTime", ctypes.c_ulong)]
            s = PS()
            ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(s))
            return (s.BatteryFlag & 128) == 0, s.ACLineStatus == 1
        if sys.platform == "darwin":
            out = subprocess.run(["pmset", "-g", "batt"], capture_output=True, text=True).stdout
            return "InternalBattery" in out, "AC Power" in out
        return power_sysfs(root)
    except (OSError, ValueError, AttributeError):
        return None, None


def power_sysfs(root):
    """Linux's /sys/class/power_supply as (has_battery, on_mains): its own function so every system can test it
    (2026-10-23: the test went through _power(), which on Windows asks Windows and never read the test's folder)."""
    kinds = {p.name: (p / "type").read_text().strip() for p in Path(root).iterdir() if (p / "type").is_file()}
    battery = any(k == "Battery" for k in kinds.values())
    mains = [p for p, k in kinds.items() if k == "Mains"]
    on = any((Path(root) / p / "online").read_text().strip() == "1" for p in mains) if mains else (not battery)
    return battery, on


def device_facts(cable=None):
    """What the boss rule weighs, for this device. `always_on`: no battery, or on mains power now."""
    battery, mains = _power()
    try:                                                  # read only: a hello never makes a key
        from . import mandate as M
        pub = M._review_paths()[1]
        key = M.fingerprint(bytes.fromhex(pub.read_text(encoding="utf-8").strip())) if pub.is_file() else None
    except Exception:  # noqa: BLE001 - no key yet: the proposal still works, the mandate line says so
        key = None
    return {"host": socket.gethostname(), "os": sys.platform, "mem_gb": _mem_gb(), "battery": battery,
            "on_mains": mains, "always_on": (battery is False) or bool(mains), "cable": cable, "key": key}


def propose(devices):
    """The fixed rule: always on > most memory > on the cable. -> the device chosen, or None."""
    rank = lambda d: (bool(d.get("always_on")), float(d.get("mem_gb") or 0), bool(d.get("cable")))
    return max(devices, key=rank) if devices else None


def devices(call=None):
    """This device and the paired one (its facts come back in the link's hello answer)."""
    out = [dict(device_facts(cable=None), here=True)]
    try:
        if call is None:
            from ..peers import link
            call = link.call
        r = call({"verb": "hello"})
        if r.get("ok") and isinstance(r.get("device"), dict):
            out[0]["cable"] = True
            out.append(dict(r["device"], cable=True, here=False))
    except Exception:  # noqa: BLE001 - no link: this device alone
        pass
    return out


def lines(devs, chosen):
    out = ["  {:<20} {:<7} {:>6} {:<9} {:<6} {}".format("device", "system", "memory", "always on", "cable", "")]
    for d in devs:
        out.append("  {:<20} {:<7} {:>6} {:<9} {:<6} {}".format(
            str(d.get("host"))[:20], str(d.get("os"))[:7], f"{d.get('mem_gb') or '?'} GB",
            "yes" if d.get("always_on") else "no", "yes" if d.get("cable") else "no",
            "(this one)" if d.get("here") else ""))
    if chosen:
        out.append(f"PROPOSED BOSS: {chosen['host']} (rule: always on > most memory > on the cable). To approve it, "
                   "put this in the mandate and sign it (fieldkit mandate approve):")
        out.append(f"  orchestrator: {{device: {chosen['host']}, key: {chosen.get('key') or '<its fieldkit mandate review-key>'}}}")
    return out
