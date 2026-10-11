"""Would the Windows 11 service profiles be right on THIS computer? (read-only)

The profiles in 06-services/profiles.json were measured on one ThinkPad. Some entries rest on facts about that
machine - no SIM, no WSL, no shared folders. fit checks each such fact here, and lists every entry of the chosen
profile (and the profiles under it: they are cumulative) whose fact does not hold on this computer. The repository is
not changed: the ThinkPad keeps its profiles; another computer gets told what to leave out.
"""
import json
import subprocess
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
ORDER = ["light", "moderate", "super"]


def _ps(command, timeout=60):
    """One read-only PowerShell query -> stdout text, or None when it could not run."""
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
                           capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def _count(command):
    out = _ps(f"@({command}).Count")
    try:
        return int(out) if out is not None else None
    except ValueError:
        return None


def _reg(path, name):
    """A registry value, or None."""
    try:
        import winreg
    except ImportError:
        return None
    hive, _, sub = path.partition("\\")
    try:
        with winreg.OpenKey(getattr(winreg, hive), sub) as k:
            return winreg.QueryValueEx(k, name)[0]
    except OSError:
        return None


def _key_exists(path):
    try:
        import winreg
    except ImportError:
        return None
    hive, _, sub = path.partition("\\")
    try:
        winreg.CloseKey(winreg.OpenKey(getattr(winreg, hive), sub))
        return True
    except OSError:
        return False


# Each probe -> True (the profile's assumption holds here), False (it does not), None (could not tell).
def _wwan_unused():
    out = _ps("netsh mbn show interfaces")
    if out is None:
        return None
    return "There is no Mobile Broadband interface" in out or "Not connected" in out or not out.strip()


def _file_history_off():
    import os
    cfg = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Windows" / "FileHistory" / "Configuration"
    return not (cfg.is_dir() and any(cfg.iterdir()))


def _rdp_disabled():
    v = _reg(r"HKEY_LOCAL_MACHINE\SYSTEM\CurrentControlSet\Control\Terminal Server", "fDenyTSConnections")
    return None if v is None else v == 1


def _not_domain_joined():
    out = _ps("(Get-CimInstance Win32_ComputerSystem).PartOfDomain")
    return None if out is None else out.strip().lower() == "false"


def _no_file_shares():
    n = _count("Get-SmbShare -ErrorAction Stop | Where-Object { -not $_.Special -and $_.Name -notmatch '\\$$' }")
    return None if n is None else n == 0


def _no_refs():
    n = _count("Get-Volume -ErrorAction Stop | Where-Object FileSystem -eq 'ReFS'")
    return None if n is None else n == 0


def _only_virtual_printers():
    n = _count("Get-Printer -ErrorAction Stop | Where-Object { $_.Name -notmatch "
               "'Microsoft Print to PDF|Microsoft XPS Document Writer|OneNote|Fax' }")
    return None if n is None else n == 0


def _hyperv_absent():
    out = _ps("[bool](Get-Service vmms -ErrorAction SilentlyContinue)")
    return None if out is None else out.strip().lower() == "false"


def _wsl_absent():
    lxss = _key_exists(r"HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Lxss")
    if lxss is None:
        return None
    if not lxss:
        return True
    n = _count(r"Get-ChildItem HKCU:\Software\Microsoft\Windows\CurrentVersion\Lxss -ErrorAction Stop")
    return None if n is None else n == 0


def _work_folders_off():
    v = _key_exists(r"HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\WorkFolders")
    return None if v is None else not v


def _tz_auto_off():
    v = _reg(r"HKEY_LOCAL_MACHINE\SYSTEM\CurrentControlSet\Services\tzautoupdate", "Start")
    return None if v is None else v == 4


def _ask_parental_controls():
    return None             # no read-only probe can tell whether Microsoft Family is in use: the owner decides


PROBES = {"ask_parental_controls": _ask_parental_controls, "tz_auto_off": _tz_auto_off, "wwan_unused": _wwan_unused, "file_history_off": _file_history_off, "rdp_disabled": _rdp_disabled,
          "not_domain_joined": _not_domain_joined, "no_file_shares": _no_file_shares, "no_refs": _no_refs,
          "only_virtual_printers": _only_virtual_printers, "hyperv_absent": _hyperv_absent,
          "wsl_absent": _wsl_absent, "work_folders_off": _work_folders_off}


def assumptions():
    return yaml.safe_load((HERE / "fit.yaml").read_text(encoding="utf-8"))["services"]


def profile_services(profiles_json, profile):
    """The services a profile disables, its lower profiles included (they are cumulative)."""
    data = json.loads(Path(profiles_json).read_text(encoding="utf-8-sig"))
    out = []
    for name in ORDER[:ORDER.index(profile) + 1]:
        for s in data["profiles"][name]["services"]:
            out.append((name, s["service"]))
    return out


def fit(profiles_json, profile, probes=None):
    probes = PROBES if probes is None else probes
    facts = assumptions()
    results, cache = {"findings": [], "unknown": [], "checked": 0, "profile": profile}, {}
    for tier, svc in profile_services(profiles_json, profile):
        if svc not in facts:
            continue
        probe, assumed = facts[svc]
        if probe not in cache:
            fn = probes.get(probe)
            try:
                cache[probe] = fn() if fn else None
            except Exception:                    # a probe that breaks is "could not tell", never "fine"
                cache[probe] = None
        results["checked"] += 1
        row = {"service": svc, "tier": tier, "assumes": assumed, "probe": probe}
        if cache[probe] is False:
            results["findings"].append(row)
        elif cache[probe] is None:
            results["unknown"].append(row)
    return results


def unmapped(profiles_json):
    """Profile entries whose reason talks about 'this machine' but which fit.yaml does not cover: a test guards it."""
    import re
    data = json.loads(Path(profiles_json).read_text(encoding="utf-8-sig"))
    facts = assumptions()
    general = set(yaml.safe_load((HERE / "fit.yaml").read_text(encoding="utf-8")).get("general") or [])
    return sorted({s["service"] for p in data["profiles"].values() for s in p["services"]
                   if re.search(r"(?i)this machine|not installed here|no sim|not configured here|here\.|no refs",
                                s.get("reason", "")) and s["service"] not in facts and s["service"] not in general})
