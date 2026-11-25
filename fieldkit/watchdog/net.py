"""The two things the watchdog needs from the network, on either system.

Windows behaviour is UNCHANGED from the original script
(local/kits/OnlineWatchdog/online_watchdog.py): same netsh calls, same
PowerShell radio incantation, same return values. Only the Linux half is new.
That is deliberate, on the owner's instruction of 2026-10-13: Windows works,
leave it alone.
"""
import os
import subprocess
from fieldkit import proc as fkproc
import sys
from pathlib import Path

NO_WINDOW = 0x08000000          # CREATE_NO_WINDOW: no console flashing up on Windows

# The radio incantation, kept VERBATIM from the Windows script. Do not tidy it.
RADIO_ON = r"""
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$opT = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
$null = [Windows.Devices.Radios.Radio,Windows.System.Devices,ContentType=WindowsRuntime]
function Await($o, [Type]$t) { $x = $opT.MakeGenericMethod($t).Invoke($null, @($o)); $null = $x.Wait(15000); $x.Result }
$null = Await ([Windows.Devices.Radios.Radio]::RequestAccessAsync()) ([Windows.Devices.Radios.RadioAccessStatus])
$r = (Await ([Windows.Devices.Radios.Radio]::GetRadiosAsync()) ([System.Collections.Generic.IReadOnlyList[Windows.Devices.Radios.Radio]])) | Where-Object { $_.Kind -eq 'WiFi' } | Select-Object -First 1
if ($r -and $r.State -ne 'On') { "radio was $($r.State): " + (Await ($r.SetStateAsync('On')) ([Windows.Devices.Radios.RadioAccessStatus])) } else { 'radio on' }
"""


def _plat(platform=None):
    p = (platform or sys.platform).lower()
    if p in ("win32", "windows"):
        return "win32"
    if p in ("darwin", "macos"):
        return "darwin"
    return "linux"


def state_dir(platform=None):
    """Where the log and the pause file live. %LOCALAPPDATA% on Windows, Library/Logs on macOS, XDG on Linux."""
    p = _plat(platform)
    if p == "win32":
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "PortableNetworkHub"
    if p == "darwin":
        return Path.home() / "Library" / "Logs" / "gorilla-watchdog"
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local/state")
    return Path(base) / "gorilla-watchdog"


def remembered_ssid_path(platform=None):
    return state_dir(platform) / "last-ssid.txt"


def manual_switch_path(platform=None):
    return state_dir(platform) / "manual-switch.until"


def read_remembered_ssid(platform=None):
    try:
        return remembered_ssid_path(platform).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def write_remembered_ssid(ssid, platform=None):
    if not ssid:
        return
    try:
        d = state_dir(platform)
        d.mkdir(parents=True, exist_ok=True)
        (d / "last-ssid.txt").write_text(str(ssid).strip() + "\n", encoding="utf-8")
    except OSError:
        pass


def mark_manual_switch(seconds=90, platform=None):
    """Give an explicit manual Wi-Fi switch a grace window so the watchdog never yanks the link back."""
    import time
    try:
        d = state_dir(platform)
        d.mkdir(parents=True, exist_ok=True)
        (d / "manual-switch.until").write_text(f"{time.time() + float(seconds)}\n", encoding="utf-8")
    except OSError:
        pass


def manual_switch_active(platform=None, now=None):
    import time
    t = time.time() if now is None else now
    try:
        return t < float(manual_switch_path(platform).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return False


def fallback_saved_ssid(exclude=(), platform=None):
    """Pick the strongest saved Wi-Fi network currently in range (100% offline, no passwords read)."""
    try:
        from ..network import radios as R
        sysname = {"win32": "windows", "darwin": "darwin", "linux": "linux"}[_plat(platform)]
        saved = {p["ssid"] for p in R.list_profiles(with_details=False, sysname=sysname) if p.get("ssid")}
        saved -= {x for x in (exclude or ()) if x}
        if not saved:
            return ""
        sc = R.scan_networks(rescan_now=False, sysname=sysname)
        candidates = [n for n in (sc.get("networks") or []) if n.get("ssid") in saved]
        if not candidates:
            return ""
        candidates.sort(key=lambda n: -int(n.get("signal") or 0))
        return candidates[0]["ssid"]
    except Exception:  # noqa: BLE001
        return ""


def _darwin_wifi_dev(runner):
    port = ""
    for line in (runner(["networksetup", "-listallhardwareports"]) or "").splitlines():
        s = line.strip()
        if s.startswith("Hardware Port:"):
            port = s.partition(":")[2].strip()
        elif s.startswith("Device:") and ("wi-fi" in port.lower() or "airport" in port.lower()):
            return s.partition(":")[2].strip()
    return "en0"


def run(args, timeout=30):
    try:
        out = fkproc.run(args, capture_output=True, text=True, timeout=timeout,
                             creationflags=NO_WINDOW if os.name == "nt" else 0)
        return out.stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def current_ssid(platform=None, runner=None):
    """The wifi network this machine is on, or ''."""
    r = runner or run
    p = _plat(platform)
    if p == "win32":
        for line in r(["netsh", "wlan", "show", "interfaces"]).splitlines():
            key, _, value = line.strip().partition(":")
            if key.strip() == "SSID":                 # not "AP BSSID"
                return value.strip()
        return ""
    if p == "darwin":
        dev = _darwin_wifi_dev(r)
        for line in r(["ipconfig", "getsummary", dev]).splitlines():
            k, _, v = line.strip().partition(" : ")
            if k.strip() == "SSID":
                return v.strip()
        out = r(["networksetup", "-getairportnetwork", dev]).strip()
        if "Current Wi-Fi Network:" in out:
            return out.partition("Current Wi-Fi Network:")[2].strip()
        return ""
    # Linux: nmcli marks the active connection with yes. Asking for the ACTIVE
    # one matters: `nmcli -t -f SSID dev wifi` lists every network in range, and
    # taking the first would cheerfully try to join a neighbour's.
    for line in r(["nmcli", "-t", "-f", "ACTIVE,SSID", "dev", "wifi"]).splitlines():
        active, _, ssid = line.partition(":")
        if active == "yes" and ssid:
            # nmcli -t ESCAPES a colon inside a value as "\\:", so an SSID of
            # ":(" arrives as "\\:(" and handing that straight to
            # `nmcli connection up` asks for a network that does not exist. The
            # watchdog would then fail to rejoin, silently, at the one moment it
            # is needed. Found 2026-10-13 on a network deliberately named ":("
            # to trip exactly this.
            return unescape_nmcli(ssid)
    return ""


def unescape_nmcli(value):
    """Undo nmcli -t's escaping. It escapes ":" and "\\" with a backslash."""
    out, i = [], 0
    while i < len(value):
        if value[i] == "\\" and i + 1 < len(value):
            out.append(value[i + 1])
            i += 2
        else:
            out.append(value[i])
            i += 1
    return "".join(out)


def reconnect(ssid, platform=None, runner=None, log=lambda s: None):
    """Switch the radio on and rejoin `ssid`. Needs no admin on any of the three systems."""
    r = runner or run
    p = _plat(platform)
    if p == "win32":
        said = r(["powershell", "-NoProfile", "-NonInteractive", "-Command", RADIO_ON]).strip()
        log(f"reconnect: {said or 'radio check gave no answer'}")
        if ssid:
            out = r(["netsh", "wlan", "connect", f"name={ssid}"]).strip()
            log(f"reconnect: netsh wlan connect name={ssid}: {out}")
        else:
            log("reconnect: no remembered network yet, so nothing to join")
        return
    if p == "darwin":
        dev = _darwin_wifi_dev(r)
        out = r(["networksetup", "-setairportpower", dev, "on"]).strip()
        log(f"reconnect: networksetup -setairportpower {dev} on: {out or 'ok'}")
        if ssid:
            out = r(["networksetup", "-setairportnetwork", dev, ssid]).strip()
            log(f"reconnect: networksetup -setairportnetwork {dev} {ssid}: {out or 'ok'}")
        else:
            log("reconnect: no remembered network yet, so nothing to join")
        return
    # Linux: nmcli does both halves, and neither needs root for the user's own
    # connections.
    out = r(["nmcli", "radio", "wifi", "on"]).strip()
    log(f"reconnect: nmcli radio wifi on: {out or 'ok'}")
    if ssid:
        out = r(["nmcli", "connection", "up", ssid]).strip()
        log(f"reconnect: nmcli connection up {ssid}: {out.splitlines()[0] if out else 'no output'}")
    else:
        log("reconnect: no remembered network yet, so nothing to join")


def tools_present(platform=None, which=None):
    """What this machine is missing for the watchdog to work. [] means ready."""
    import shutil
    w = which or shutil.which
    p = _plat(platform)
    need = ["netsh", "powershell"] if p == "win32" else (["networksetup"] if p == "darwin" else ["nmcli"])
    return [t for t in need if not w(t)]
