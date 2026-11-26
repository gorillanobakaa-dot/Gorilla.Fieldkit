"""Cross-platform Wi-Fi radio inventory, fresh scanning, saved profiles, switching, and deterministic join.

Supports Windows (PowerShell, netsh, WinRT), Linux (sysfs, rfkill, nmcli, iw), and macOS
(networksetup, system_profiler, ipconfig, airport) using only the Python standard library.
Never reads, asks for, or prints Wi-Fi passwords/keys: joins only saved OS profiles.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import os
from pathlib import Path
import platform
import re
import shutil
import socket
import sys
import time

from fieldkit import proc as fkproc

NO_WINDOW = 0x08000000 if os.name == "nt" else 0
ON_STATES = ("Up", "Disconnected")
SBIN_DIRS = (
    "/usr/sbin",
    "/sbin",
    "/usr/local/sbin",
    "/System/Library/PrivateFrameworks/Apple80211.framework/Versions/Current/Resources",
)

# Generic hardware chip tables (USB VID:PID and PCI VEN:DEV) -> (chipset, bands, streams, note).
USB_CHIPS = {
    "148F:3070": ("Ralink RT3070", "2.4 GHz", "1x1", "802.11n, 150 Mbit/s; common high-power 2.4 GHz long-range chip"),
    "148F:3072": ("Ralink RT3072", "2.4 GHz", "2x2", "802.11n, 300 Mbit/s"),
    "148F:5370": ("Ralink RT5370", "2.4 GHz", "1x1", "802.11n, 150 Mbit/s; compact dongle"),
    "148F:5372": ("Ralink RT5372", "2.4 GHz", "2x2", "802.11n, 300 Mbit/s"),
    "148F:3572": ("Ralink RT3572", "2.4 + 5 GHz", "2x2", "802.11n dual band"),
    "148F:7601": ("MediaTek MT7601U", "2.4 GHz", "1x1", "802.11n, 150 Mbit/s"),
    "0E8D:7610": ("MediaTek MT7610U", "2.4 + 5 GHz", "1x1", "802.11ac, 433 Mbit/s"),
    "0E8D:7612": ("MediaTek MT7612U", "2.4 + 5 GHz", "2x2", "802.11ac, 867 Mbit/s"),
    "0CF3:9271": ("Atheros AR9271", "2.4 GHz", "1x1", "802.11n, 150 Mbit/s"),
    "0BDA:8187": ("Realtek RTL8187", "2.4 GHz", "1x1", "802.11g, 54 Mbit/s"),
    "0BDA:8176": ("Realtek RTL8188CUS", "2.4 GHz", "1x1", "802.11n, 150 Mbit/s"),
    "0BDA:8178": ("Realtek RTL8192CU", "2.4 GHz", "2x2", "802.11n, 300 Mbit/s"),
    "0BDA:8812": ("Realtek RTL8812AU", "2.4 + 5 GHz", "2x2", "802.11ac, 867 Mbit/s"),
    "0BDA:8813": ("Realtek RTL8814AU", "2.4 + 5 GHz", "4x4", "802.11ac, 1733 Mbit/s"),
    "0BDA:B812": ("Realtek RTL8812BU", "2.4 + 5 GHz", "2x2", "802.11ac, 867 Mbit/s"),
    "0BDA:C811": ("Realtek RTL8821CU", "2.4 + 5 GHz", "1x1", "802.11ac, 433 Mbit/s"),
    "0BDA:F192": ("Realtek RTL8188FTV", "2.4 GHz", "1x1", "802.11n, 150 Mbit/s"),
    "2357:010C": ("Realtek RTL8192EU", "2.4 GHz", "2x2", "802.11n, 300 Mbit/s"),
    "2357:0115": ("Realtek RTL8812AU", "2.4 + 5 GHz", "2x2", "802.11ac"),
}

PCI_CHIPS = {
    "8086:51F0": ("Intel Wi-Fi 6 AX201", "2.4 + 5 GHz", "2x2", "802.11ax, 160 MHz; built-in"),
    "8086:A0F0": ("Intel Wi-Fi 6 AX201", "2.4 + 5 GHz", "2x2", "802.11ax; built-in"),
    "8086:2723": ("Intel Wi-Fi 6 AX200", "2.4 + 5 GHz", "2x2", "802.11ax; built-in"),
    "8086:2725": ("Intel Wi-Fi 6E AX210", "2.4 + 5 + 6 GHz", "2x2", "802.11ax; built-in"),
    "8086:7AF0": ("Intel Wi-Fi 6E AX211", "2.4 + 5 + 6 GHz", "2x2", "802.11ax; built-in"),
    "8086:51F1": ("Intel Wi-Fi 6E AX211", "2.4 + 5 + 6 GHz", "2x2", "802.11ax; built-in"),
    "8086:272B": ("Intel Wi-Fi 7 BE200", "2.4 + 5 + 6 GHz", "2x2", "802.11be; built-in"),
    "8086:24FD": ("Intel Wireless-AC 8265", "2.4 + 5 GHz", "2x2", "802.11ac; built-in"),
    "8086:095A": ("Intel Wireless-AC 7265", "2.4 + 5 GHz", "2x2", "802.11ac; built-in"),
    "168C:0032": ("Qualcomm Atheros AR9485", "2.4 GHz", "1x1", "802.11n; built-in"),
    "14E4:43A0": ("Broadcom BCM4360", "2.4 + 5 GHz", "3x3", "802.11ac; built-in"),
    "14E4:43BA": ("Broadcom BCM43602", "2.4 + 5 GHz", "3x3", "802.11ac; built-in"),
    "10EC:8821": ("Realtek RTL8821AE", "2.4 + 5 GHz", "1x1", "802.11ac; built-in"),
    "10EC:C821": ("Realtek RTL8821CE", "2.4 + 5 GHz", "1x1", "802.11ac; built-in"),
    "10EC:B822": ("Realtek RTL8822BE", "2.4 + 5 GHz", "2x2", "802.11ac; built-in"),
    "14C3:7961": ("MediaTek MT7921", "2.4 + 5 + 6 GHz", "2x2", "802.11ax; built-in"),
}

OUI = {
    "00:C0:CA": "ALFA Network",
    "00:25:22": "ASRock",
    "00:0C:43": "Ralink",
    "00:E0:4C": "Realtek",
    "50:C7:BF": "TP-Link",
    "C4:E9:84": "TP-Link",
    "E8:DE:27": "TP-Link",
    "30:B5:C2": "TP-Link",
    "00:1F:1F": "Edimax",
    "80:1F:02": "Edimax",
    "9C:EF:D5": "Panda Wireless",
    "8C:88:2B": "Intel",
    "78:AF:08": "Intel",
    "A4:BB:6D": "Dell",
}

PRODUCTS = {
    ("ALFA Network", "Ralink RT3070"): (
        "ALFA AWUS036NH",
        "long range",
        "high-power 2.4 GHz adapter with external RP-SMA antenna; 1x1 150 Mbit/s, 2.4 GHz only",
    ),
    ("ALFA Network", "Atheros AR9271"): (
        "ALFA AWUS036NHA",
        "long range",
        "high-power 2.4 GHz adapter with external antenna; 1x1 150 Mbit/s, 2.4 GHz only",
    ),
    ("ALFA Network", "Realtek RTL8187"): (
        "ALFA AWUS036H",
        "long range",
        "high-power 802.11g adapter; 54 Mbit/s, 2.4 GHz only, external antenna",
    ),
    ("ALFA Network", "Realtek RTL8812AU"): (
        "ALFA AWUS036ACH",
        "long range, dual band",
        "high-power 802.11ac adapter with two external antennas, 2.4 and 5 GHz",
    ),
    ("ALFA Network", "MediaTek MT7612U"): (
        "ALFA AWUS036ACM",
        "dual band",
        "802.11ac adapter with two external antennas, 2.4 and 5 GHz",
    ),
    ("ALFA Network", "Realtek RTL8814AU"): (
        "ALFA AWUS1900",
        "long range, dual band",
        "four-antenna 802.11ac adapter",
    ),
    ("Panda Wireless", "Ralink RT5372"): (
        "Panda PAU06",
        "small",
        "300 Mbit/s 2.4 GHz adapter with external antenna",
    ),
}

DFS_CHANNELS_5 = set(range(52, 145))
UK_EU_CHANNELS_24 = {12, 13}


@dataclass
class Radio:
    name: str
    os_label: str
    status: str  # Up, Disconnected, Disabled, Not Present, Hard blocked, Software off
    mac: str = ""
    pnp: str = ""
    bus: str = ""
    hw_id: str = ""
    maker: str = ""
    chipset: str = ""
    bands: str = ""
    streams: str = ""
    chip_note: str = ""
    product: str = ""
    product_kind: str = ""
    product_note: str = ""
    driver: str = ""
    driver_date: str = ""
    driver_provider: str = ""
    hotspot_capable: str = ""
    driver_claims_bands: str = ""
    link: str = ""
    connected_ssid: str = ""
    carries_hotspot: bool = False
    virtual_children: list[str] = field(default_factory=list)
    identity_confidence: str = ""
    rfkill: str = ""

    def identity(self) -> str:
        if self.product and self.product != self.chipset:
            return f"{self.product} ({self.chipset})"
        if self.product:
            return self.product
        if self.chipset and self.maker:
            return f"{self.maker} radio, {self.chipset}"
        if self.chipset:
            return self.chipset
        return self.os_label or self.name


def system_name(sysname: str | None = None) -> str:
    s = (sysname or sys.platform).lower()
    if s.startswith("win") or s == "nt":
        return "windows"
    if s.startswith("darwin") or s == "macos":
        return "darwin"
    if s.startswith("linux"):
        return "linux"
    return s


def on_windows(sysname: str | None = None) -> bool:
    return system_name(sysname) == "windows"


def find_tool(name: str) -> str | None:
    p = shutil.which(name)
    if p:
        return p
    for d in SBIN_DIRS:
        cand = os.path.join(d, name)
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return None


def run_cmd(args: list[str], timeout: int = 30) -> tuple[int, str, str]:
    try:
        r = fkproc.run(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=NO_WINDOW,
        )
        return r.returncode, r.stdout or "", r.stderr or ""
    except (OSError, ValueError) as e:
        return 1, "", str(e)
    except Exception as e:  # noqa: BLE001
        return 1, "", str(e)


def run_ps(script: str, timeout: int = 45) -> str:
    if not on_windows():
        return ""
    rc, out, _ = run_cmd(
        ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
        timeout=timeout,
    )
    return out if rc == 0 or out else ""


def freq_for_channel(ch: int, band: str = "") -> int:
    if ch == 14:
        return 2484
    if 1 <= ch <= 13 or band.startswith("2"):
        return 2407 + 5 * ch
    if 32 <= ch <= 177 or band.startswith("5"):
        return 5000 + 5 * ch
    if ch >= 1 and band.startswith("6"):
        return 5950 + 5 * ch
    return 0


def band_from_freq_or_ch(mhz: int = 0, ch: int = 0) -> str:
    if 2300 <= mhz <= 2600:
        return "2.4 GHz"
    if 4900 <= mhz <= 5900:
        return "5 GHz"
    if 5925 <= mhz <= 7125:
        return "6 GHz"
    if 1 <= ch <= 14:
        return "2.4 GHz"
    if 32 <= ch <= 177:
        return "5 GHz"
    return "unknown"


def hw_id_of(pnp: str) -> tuple[str, str]:
    m = re.search(r"USB\\VID_([0-9A-F]{4})&PID_([0-9A-F]{4})", pnp or "", re.I)
    if m:
        return "USB", f"{m.group(1).upper()}:{m.group(2).upper()}"
    m = re.search(r"PCI\\VEN_([0-9A-F]{4})&DEV_([0-9A-F]{4})", pnp or "", re.I)
    if m:
        return "PCI", f"{m.group(1).upper()}:{m.group(2).upper()}"
    return "", ""


def maker_of(mac: str) -> str:
    oui = (mac or "").upper().replace("-", ":")[:8]
    return OUI.get(oui, "")


def identify_radio(r: Radio) -> Radio:
    r.bus, r.hw_id = hw_id_of(r.pnp)
    r.maker = r.maker or maker_of(r.mac)
    table = USB_CHIPS if r.bus == "USB" else PCI_CHIPS if r.bus == "PCI" else {}
    hit = table.get(r.hw_id)
    if hit:
        r.chipset, r.bands, r.streams, r.chip_note = hit
    if r.bus == "PCI" and r.chipset:
        r.product, r.product_kind, r.product_note = r.chipset, "built-in", "the machine's built-in card"
        if not r.maker:
            r.maker = r.chipset.split()[0]
    prod = PRODUCTS.get((r.maker, r.chipset))
    if prod:
        r.product, r.product_kind, r.product_note = prod
    r.identity_confidence = "product" if r.product else "chipset" if r.chipset else "driver only"
    return r


def nmcli_fields(line: str) -> list[str]:
    """Split one `nmcli -t` line while unescaping `\\:` and `\\\\` so SSIDs like `:(` stay intact."""
    out, cur, i = [], [], 0
    while i < len(line):
        c = line[i]
        if c == "\\" and i + 1 < len(line):
            cur.append(line[i + 1])
            i += 2
            continue
        if c == ":":
            out.append("".join(cur))
            cur = []
        else:
            cur.append(c)
        i += 1
    out.append("".join(cur))
    return out


# ---------------------------------------------------------------------------
# Windows readers & parsers
# ---------------------------------------------------------------------------

PS_ADAPTERS = r"""
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$ErrorActionPreference = 'SilentlyContinue'
$out = @()
foreach ($a in Get-NetAdapter -IncludeHidden) {
  if ($a.InterfaceDescription -notmatch 'Wi-Fi|Wireless|802\.11|WLAN') { continue }
  $o = [ordered]@{
    Name=$a.Name; Description=$a.InterfaceDescription; Status=[string]$a.Status; Mac=$a.MacAddress;
    PnP=$a.PnPDeviceID; Driver=$a.DriverFileName; DriverDate=[string]$a.DriverDate;
    DriverProvider=$a.DriverProvider; DriverVersion=$a.DriverVersion; Parent=''
  }
  if ($a.PnPDeviceID) {
    $o.Parent = (Get-PnpDeviceProperty -InstanceId $a.PnPDeviceID -KeyName DEVPKEY_Device_Parent).Data
  }
  $out += [pscustomobject]$o
}
$out | ConvertTo-Json -Depth 3 -Compress
"""

PS_RESCAN = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Devices.WiFi.WiFiAdapter, Windows.Devices.WiFi, ContentType = WindowsRuntime]
$ops = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 }
$opGeneric = $ops | Where-Object { $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' } | Select-Object -First 1
$actPlain  = $ops | Where-Object { $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncAction' } | Select-Object -First 1
$t = $opGeneric.MakeGenericMethod([System.Collections.Generic.IReadOnlyList[Windows.Devices.WiFi.WiFiAdapter]]).Invoke($null, @([Windows.Devices.WiFi.WiFiAdapter]::FindAllAdaptersAsync()))
$t.Wait(10000) | Out-Null
$n = 0; $when = ''
foreach ($a in $t.Result) { $s = $actPlain.Invoke($null, @($a.ScanAsync())); $s.Wait(15000) | Out-Null; $n++; $when = [string]$a.NetworkReport.Timestamp }
"rescanned $n radio(s), listened at $when"
"""

PS_RADIO_SET = r"""
param([string]$TargetState = 'On')
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$opT = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
$null = [Windows.Devices.Radios.Radio,Windows.System.Devices,ContentType=WindowsRuntime]
function Await($o, [Type]$t) { $x = $opT.MakeGenericMethod($t).Invoke($null, @($o)); $null = $x.Wait(15000); $x.Result }
$null = Await ([Windows.Devices.Radios.Radio]::RequestAccessAsync()) ([Windows.Devices.Radios.RadioAccessStatus])
$radios = (Await ([Windows.Devices.Radios.Radio]::GetRadiosAsync()) ([System.Collections.Generic.IReadOnlyList[Windows.Devices.Radios.Radio]])) | Where-Object { $_.Kind -eq 'WiFi' }
foreach ($r in $radios) {
  if ([string]$r.State -ne $TargetState) {
    $res = Await ($r.SetStateAsync($TargetState)) ([Windows.Devices.Radios.RadioAccessStatus])
    "radio $($r.Name): $($r.State) -> $TargetState ($res)"
  } else {
    "radio $($r.Name): already $TargetState"
  }
}
"""


def parse_adapters_json(text: str) -> list[dict]:
    text = (text or "").strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except ValueError:
        return []
    return [data] if isinstance(data, dict) else list(data)


def build_windows_radios(adapters: list[dict]) -> list[Radio]:
    physical: dict[str, Radio] = {}
    virtual: list[dict] = []
    for a in adapters:
        pnp = a.get("PnP") or ""
        if "vwifimp" in pnp.lower() or "Virtual" in (a.get("Description") or ""):
            virtual.append(a)
            continue
        r = Radio(
            name=a.get("Name") or "",
            os_label=a.get("Description") or "",
            status=a.get("Status") or "",
            mac=a.get("Mac") or "",
            pnp=pnp,
            driver=a.get("Driver") or "",
            driver_date=(a.get("DriverDate") or "")[:10],
            driver_provider=a.get("DriverProvider") or "",
        )
        physical[pnp.upper() or r.name.upper()] = identify_radio(r)
    for v in virtual:
        parent = (v.get("Parent") or "").upper()
        if parent in physical:
            physical[parent].virtual_children.append(v.get("Name") or "")
            physical[parent].carries_hotspot = True
    return list(physical.values())


def parse_netsh_drivers(text: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    cur = None
    for line in (text or "").splitlines():
        s = line.strip()
        m = re.match(r"Interface name:\s*(.+)$", s)
        if m:
            cur = m.group(1).strip()
            out[cur] = {"bands": []}
            continue
        if cur is None:
            continue
        for key, label in (
            ("hosted", "Hosted network supported"),
            ("radio_types", "Radio types supported"),
            ("date", "Date"),
            ("driver", "Driver"),
            ("provider", "Provider"),
        ):
            if s.startswith(label) and ":" in s:
                out[cur][key] = s.split(":", 1)[1].strip()
        m = re.match(r"(2\.4|5|6) GHz", s)
        if m:
            out[cur]["bands"].append(m.group(1) + " GHz")
    return out


def parse_netsh_interfaces(text: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    cur = None
    for line in (text or "").splitlines():
        s = line.strip()
        if s.startswith("Name") and ":" in s and not s.startswith("Network"):
            cur = s.split(":", 1)[1].strip()
            out[cur] = {}
            continue
        if cur is None or ":" not in s:
            continue
        k, v = s.split(":", 1)
        out[cur][k.strip()] = v.strip()
    return out


def parse_netsh_networks(text: str) -> list[dict]:
    nets: list[dict] = []
    ssid = None
    cur = None
    auth = ""
    cipher = ""
    for line in (text or "").splitlines():
        s = line.strip()
        m = re.match(r"SSID \d+\s*:\s*(.*)$", s)
        if m:
            ssid = m.group(1).strip() or "<hidden>"
            cur, auth, cipher = None, "", ""
            continue
        m = re.match(r"BSSID \d+\s*:\s*(.+)$", s)
        if m:
            cur = {
                "ssid": ssid,
                "bssid": m.group(1).strip().lower(),
                "signal": 0,
                "radio": "",
                "band": "",
                "channel": 0,
                "mhz": 0,
                "auth": auth,
                "cipher": cipher,
            }
            nets.append(cur)
            continue
        if ":" not in s:
            continue
        key, val = (x.strip() for x in s.split(":", 1))
        if key == "Authentication":
            auth = val
            continue
        if key == "Encryption":
            cipher = val
            continue
        if cur is None:
            continue
        if key == "Signal":
            cur["signal"] = int(re.sub(r"\D", "", val) or 0)
        elif key == "Radio type":
            cur["radio"] = val
        elif key == "Band":
            cur["band"] = val
        elif key == "Channel":
            ch = int(re.sub(r"\D", "", val) or 0)
            cur["channel"] = ch
            cur["mhz"] = freq_for_channel(ch, cur.get("band") or "")
            if not cur["band"]:
                cur["band"] = band_from_freq_or_ch(cur["mhz"], ch)
            cur["dfs"] = ch in DFS_CHANNELS_5
            cur["uk_eu_ch"] = ch in UK_EU_CHANNELS_24
    return nets


def windows_radios() -> list[Radio]:
    if not on_windows():
        return []
    radios = build_windows_radios(parse_adapters_json(run_ps(PS_ADAPTERS)))
    _, drv_txt, _ = run_cmd(["netsh", "wlan", "show", "drivers"])
    _, if_txt, _ = run_cmd(["netsh", "wlan", "show", "interfaces"])
    drivers = parse_netsh_drivers(drv_txt)
    interfaces = parse_netsh_interfaces(if_txt)
    for r in radios:
        d = drivers.get(r.name, {})
        r.hotspot_capable = d.get("hosted", "")
        r.driver_claims_bands = " + ".join(d.get("bands", []))
        i = interfaces.get(r.name, {})
        st = i.get("State", "")
        if st.lower() == "connected":
            r.connected_ssid = i.get("SSID", "")
            r.link = (
                f"{r.connected_ssid}, {i.get('Band', '?')} channel {i.get('Channel', '?')}, "
                f"{i.get('Radio type', '?')}, signal {i.get('Signal', '?')}, "
                f"tx {i.get('Transmit rate (Mbps)', '?')} / rx {i.get('Receive rate (Mbps)', '?')} Mbit/s"
            )
        elif i:
            sw = i.get("Radio status", "")
            if "software off" in sw.lower() or "hardware off" in sw.lower():
                r.status = "Software off" if "software off" in sw.lower() else "Hard blocked"
            r.link = st
    return radios


# ---------------------------------------------------------------------------
# Linux & macOS readers & parsers
# ---------------------------------------------------------------------------

def linux_sysfs_root() -> Path:
    return Path(os.environ.get("WIFIRADIOS_SYSFS") or "/sys")


def linux_radios(sysfs: Path | None = None) -> list[Radio]:
    root = Path(sysfs) if sysfs else linux_sysfs_root()
    out: list[Radio] = []
    net = root / "class" / "net"
    rfk: dict[str, dict] = {}
    if (root / "class" / "rfkill").is_dir():
        for k in sorted((root / "class" / "rfkill").glob("rfkill*")):
            rd = lambda f, _k=k: (_k / f).read_text(encoding="utf-8", errors="replace").strip() if (_k / f).is_file() else ""
            if rd("type") == "wlan":
                rfk[rd("name")] = {"idx": k.name.replace("rfkill", ""), "soft": rd("soft") == "1", "hard": rd("hard") == "1"}
    active_by_dev: dict[str, str] = {}
    nm = find_tool("nmcli")
    if nm and sysfs is None:
        _, txt, _ = run_cmd([nm, "-t", "-f", "ACTIVE,DEVICE,SSID,CHAN,FREQ,SIGNAL", "dev", "wifi"])
        for line in txt.splitlines():
            f = nmcli_fields(line)
            if len(f) >= 6 and f[0] == "yes":
                mhz = int(re.sub(r"\D", "", f[4]) or 0)
                active_by_dev[f[1]] = f"{f[2]}, {band_from_freq_or_ch(mhz)} channel {f[3]}, signal {f[5]}%|{f[2]}"
    if net.is_dir():
        for d in sorted(net.iterdir()):
            if not (d / "wireless").is_dir() and not (d / "phy80211").exists():
                continue
            rd = lambda f, _d=d: (_d / f).read_text(encoding="utf-8", errors="replace").strip() if (_d / f).is_file() else ""
            uevent = (d / "device" / "uevent").read_text(encoding="utf-8", errors="replace") if (d / "device" / "uevent").is_file() else ""
            pnp = ""
            m = re.search(r"PRODUCT=([0-9a-f]+)/([0-9a-f]+)/", uevent, re.I)
            if m:
                pnp = f"USB\\VID_{int(m.group(1), 16):04X}&PID_{int(m.group(2), 16):04X}"
            m = re.search(r"PCI_ID=([0-9A-F]{4}):([0-9A-F]{4})", uevent, re.I)
            if m:
                pnp = f"PCI\\VEN_{m.group(1).upper()}&DEV_{m.group(2).upper()}"
            drv = re.search(r"DRIVER=(\S+)", uevent)
            phy = (d / "phy80211" / "name").read_text(encoding="utf-8", errors="replace").strip() if (d / "phy80211" / "name").is_file() else ""
            kill = rfk.get(phy, {})
            state = rd("operstate")
            status = (
                "Hard blocked"
                if kill.get("hard")
                else "Disabled"
                if kill.get("soft")
                else "Up"
                if state == "up"
                else "Disconnected"
                if state in ("dormant", "down", "unknown")
                else state
            )
            r = Radio(
                name=d.name,
                os_label=d.name,
                status=status,
                mac=rd("address").upper().replace(":", "-"),
                pnp=pnp,
                driver=drv.group(1) if drv else "",
                driver_provider="linux",
                rfkill=kill.get("idx", ""),
            )
            if d.name in active_by_dev:
                link_info, _, ssid = active_by_dev[d.name].partition("|")
                r.link = link_info
                r.connected_ssid = ssid
            out.append(identify_radio(r))
    return out


def macos_wifi_ports(ports_text: str) -> list[dict]:
    """Parse `networksetup -listallhardwareports` for Wi-Fi / AirPort interfaces."""
    out = []
    cur: dict[str, str] = {}
    for line in (ports_text or "").splitlines():
        s = line.strip()
        if not s:
            if cur.get("device") and re.search(r"wi-?fi|airport", cur.get("port", ""), re.I):
                out.append(cur)
            cur = {}
            continue
        if s.startswith("Hardware Port:"):
            cur["port"] = s.split(":", 1)[1].strip()
        elif s.startswith("Device:"):
            cur["device"] = s.split(":", 1)[1].strip()
        elif s.startswith("Ethernet Address:"):
            cur["mac"] = s.split(":", 1)[1].strip().upper().replace(":", "-")
    if cur.get("device") and re.search(r"wi-?fi|airport", cur.get("port", ""), re.I):
        out.append(cur)
    return out


def macos_radios() -> list[Radio]:
    ns = find_tool("networksetup")
    ports_txt = run_cmd([ns, "-listallhardwareports"])[1] if ns else ""
    ports = macos_wifi_ports(ports_txt)
    sp = find_tool("system_profiler")
    sp_info: dict[str, dict] = {}
    if sp:
        _, txt, _ = run_cmd([sp, "-json", "SPAirPortDataType"], timeout=35)
        try:
            data = json.loads(txt)
            for item in data.get("SPAirPortDataType") or []:
                for iface in item.get("spairport_airport_interfaces") or []:
                    dev = iface.get("_name") or ""
                    sp_info[dev] = iface
        except Exception:  # noqa: BLE001
            pass
    out: list[Radio] = []
    for p in ports:
        dev = p["device"]
        pwr_txt = run_cmd([ns, "-getairportpower", dev])[1] if ns else ""
        power_on = "off" not in pwr_txt.lower()
        ipcfg = find_tool("ipconfig")
        sum_txt = run_cmd([ipcfg, "getsummary", dev])[1] if ipcfg else ""
        m_ssid = re.search(r"^\s*SSID\s*:\s*(.+)$", sum_txt, re.M)
        ssid = m_ssid.group(1).strip() if m_ssid else ""
        sp_i = sp_info.get(dev, {})
        card_type = sp_i.get("spairport_wireless_card_type") or p.get("port") or "AirPort Wi-Fi"
        fw = sp_i.get("spairport_wireless_firmware_version") or ""
        modes = sp_i.get("spairport_supported_phymodes") or ""
        bands = " + ".join(b for b in ("2.4 GHz", "5 GHz", "6 GHz") if b.split()[0] in str(sp_i.get("spairport_supported_channels") or modes))
        if not bands and modes:
            bands = "2.4 + 5 GHz" if ("ac" in modes or "ax" in modes or "a/" in modes) else "2.4 GHz"
        if not ssid and isinstance(sp_i.get("spairport_current_network_information"), dict):
            cur_net = sp_i["spairport_current_network_information"]
            ssid = cur_net.get("_name") or ""
        status = "Disabled" if not power_on else ("Up" if ssid else "Disconnected")
        r = Radio(
            name=dev,
            os_label=card_type,
            status=status,
            mac=p.get("mac") or "",
            chipset=card_type,
            bands=bands or "2.4 + 5 GHz",
            chip_note=modes,
            product=card_type,
            product_kind="built-in",
            driver=fw,
            driver_provider="Apple",
            connected_ssid=ssid,
            link=f"{ssid} (connected)" if ssid else ("power off" if not power_on else "disconnected"),
            identity_confidence="product",
        )
        out.append(r)
    return out


def list_radios(sysname: str | None = None) -> list[Radio]:
    s = system_name(sysname)
    if on_windows(s):
        return windows_radios()
    if s == "linux":
        return linux_radios()
    if s == "darwin":
        return macos_radios()
    return []


# ---------------------------------------------------------------------------
# Cross-platform fresh Wi-Fi scan
# ---------------------------------------------------------------------------

def scan_networks(radio_name: str | None = None, rescan_now: bool = True, sysname: str | None = None) -> dict:
    """Fresh Wi-Fi scan on Windows, Linux, or macOS -> {rescan, radios: {name: {identity, summary, all}}, networks}."""
    s = system_name(sysname)
    if on_windows(s):
        radios = [r for r in windows_radios() if r.status in ON_STATES and (not radio_name or r.name == radio_name)]
        if not radios:
            return {"rescan": "no active Wi-Fi radio", "radios": {}, "networks": [], "ok": False}
        rescan_msg = run_ps(PS_RESCAN, timeout=45).strip() if rescan_now else "cached"
        by_radio = {}
        all_nets: list[dict] = []
        for r in radios:
            _, txt, _ = run_cmd(["netsh", "wlan", "show", "networks", "mode=bssid", f"interface={r.name}"], timeout=45)
            nets = parse_netsh_networks(txt)
            by_radio[r.name] = {"identity": r.identity(), "summary": summarize_scan(nets), "all": nets}
            all_nets.extend(nets)
        return {"rescan": rescan_msg or "ok", "radios": by_radio, "networks": all_nets, "ok": True}

    if s == "linux":
        nm = find_tool("nmcli")
        nets = []
        if nm:
            cmd = [nm, "-t", "-f", "SSID,BSSID,CHAN,FREQ,SIGNAL,SECURITY,RATE", "device", "wifi", "list"]
            if rescan_now:
                cmd += ["--rescan", "yes"]
            if radio_name:
                cmd += ["ifname", radio_name]
            _, txt, _ = run_cmd(cmd, timeout=45)
            for line in txt.splitlines():
                f = nmcli_fields(line)
                if len(f) < 6:
                    continue
                ssid = f[0] or "<hidden>"
                bssid = f[1].lower()
                ch = int(re.sub(r"\D", "", f[2]) or 0)
                mhz = int(re.sub(r"\D", "", f[3]) or 0) or freq_for_channel(ch)
                sig = int(re.sub(r"\D", "", f[4]) or 0)
                band = band_from_freq_or_ch(mhz, ch)
                nets.append({
                    "ssid": ssid,
                    "bssid": bssid,
                    "signal": sig,
                    "radio": f[6] if len(f) > 6 else "",
                    "band": band,
                    "channel": ch,
                    "mhz": mhz,
                    "auth": f[5],
                    "cipher": "",
                    "dfs": ch in DFS_CHANNELS_5,
                    "uk_eu_ch": ch in UK_EU_CHANNELS_24,
                })
        label = radio_name or "wlan"
        return {
            "rescan": "nmcli rescan" if nm else "nmcli not installed",
            "radios": {label: {"identity": label, "summary": summarize_scan(nets), "all": nets}} if nets else {},
            "networks": nets,
            "ok": bool(nm),
        }

    if s == "darwin":
        nets = []
        ap = find_tool("airport")
        if ap:
            _, txt, _ = run_cmd([ap, "-s"], timeout=40)
            for line in txt.splitlines()[1:]:
                m = re.match(r"\s*(.+?)\s+([0-9a-f:]{17})\s+(-?\d+)\s+(\d+)\s+\S+\s+\S+\s+(.*)$", line, re.I)
                if not m:
                    continue
                ssid, bssid, dbm, ch, sec = m.group(1).strip(), m.group(2).lower(), int(m.group(3)), int(m.group(4)), m.group(5).strip()
                mhz = freq_for_channel(ch)
                band = band_from_freq_or_ch(mhz, ch)
                nets.append({
                    "ssid": ssid or "<hidden>",
                    "bssid": bssid,
                    "signal": max(0, min(100, 2 * (dbm + 100))),
                    "radio": "",
                    "band": band,
                    "channel": ch,
                    "mhz": mhz,
                    "auth": sec,
                    "cipher": "",
                    "dfs": ch in DFS_CHANNELS_5,
                    "uk_eu_ch": ch in UK_EU_CHANNELS_24,
                })
        else:
            sp = find_tool("system_profiler")
            if sp:
                _, txt, _ = run_cmd([sp, "-json", "SPAirPortDataType"], timeout=45)
                try:
                    data = json.loads(txt)
                    for item in data.get("SPAirPortDataType") or []:
                        for iface in item.get("spairport_airport_interfaces") or []:
                            for n in iface.get("spairport_airport_other_local_wireless_networks") or []:
                                m_ch = re.search(r"(\d+)", str(n.get("spairport_network_channel") or ""))
                                ch = int(m_ch.group(1)) if m_ch else 0
                                m_sig = re.search(r"(-?\d+)", str(n.get("spairport_signal_noise") or "-70"))
                                dbm = int(m_sig.group(1)) if m_sig else -70
                                mhz = freq_for_channel(ch)
                                band = band_from_freq_or_ch(mhz, ch)
                                nets.append({
                                    "ssid": n.get("_name") or "<hidden>",
                                    "bssid": "",
                                    "signal": max(0, min(100, 2 * (dbm + 100))),
                                    "radio": str(n.get("spairport_network_phymode") or ""),
                                    "band": band,
                                    "channel": ch,
                                    "mhz": mhz,
                                    "auth": str(n.get("spairport_security_mode") or ""),
                                    "cipher": "",
                                    "dfs": ch in DFS_CHANNELS_5,
                                    "uk_eu_ch": ch in UK_EU_CHANNELS_24,
                                })
                except Exception:  # noqa: BLE001
                    pass
        label = radio_name or "en0"
        return {
            "rescan": "macOS scan",
            "radios": {label: {"identity": label, "summary": summarize_scan(nets), "all": nets}} if nets else {},
            "networks": nets,
            "ok": True,
        }

    return {"rescan": f"unsupported platform {s}", "radios": {}, "networks": [], "ok": False}


def summarize_scan(nets: list[dict]) -> dict:
    ssids = {n["ssid"] for n in nets}
    by_band: dict[str, int] = {}
    chans: dict[int, int] = {}
    for n in nets:
        b = n.get("band") or "unknown"
        by_band[b] = by_band.get(b, 0) + 1
        ch = int(n.get("channel") or 0)
        if ch:
            chans[ch] = chans.get(ch, 0) + 1
    strongest = sorted(nets, key=lambda n: -int(n.get("signal") or 0))[:4]
    weakest = sorted(nets, key=lambda n: int(n.get("signal") or 0))[:3]
    return {
        "networks": len(ssids),
        "access_points": len(nets),
        "by_band": by_band,
        "busiest_channels": sorted(chans.items(), key=lambda kv: -kv[1])[:4],
        "strongest": strongest,
        "weakest": weakest,
    }


# ---------------------------------------------------------------------------
# Saved Wi-Fi profiles (NEVER reads keys/passwords)
# ---------------------------------------------------------------------------

def parse_windows_profile_detail(ssid: str, text: str) -> dict:
    mode_m = re.search(r"Connection mode\s*:\s*(.+)", text)
    rand_m = re.search(r"MAC Randomization\s*:\s*(.+)", text)
    auths = [m.group(1).strip() for m in re.finditer(r"Authentication\s*:\s*(.+)", text)]
    ciphers = [m.group(1).strip() for m in re.finditer(r"Cipher\s*:\s*(.+)", text)]
    key_m = re.search(r"Security key\s*:\s*(.+)", text)
    mode = mode_m.group(1).strip() if mode_m else "unknown"
    auto = "auto" in mode.lower()
    return {
        "ssid": ssid,
        "saved": bool(auths or mode_m),
        "connection_mode": mode,
        "autoconnect": auto,
        "auths": auths,
        "ciphers": ciphers,
        "security_key": key_m.group(1).strip() if key_m else "unknown",
        "mac_randomization": rand_m.group(1).strip() if rand_m else "unknown",
    }


def list_profiles(sysname: str | None = None, with_details: bool = True) -> list[dict]:
    s = system_name(sysname)
    if on_windows(s):
        _, txt, _ = run_cmd(["netsh", "wlan", "show", "profiles"])
        names = [m.group(1).strip() for m in re.finditer(r"All User Profile\s*:\s*(.+)", txt)]
        if not with_details:
            return [{"ssid": n, "saved": True, "autoconnect": None} for n in names]
        out = []
        for n in names:
            # Never pass key=clear: secrets are never read.
            _, dtxt, _ = run_cmd(["netsh", "wlan", "show", "profile", f"name={n}"])
            out.append(parse_windows_profile_detail(n, dtxt))
        return out

    if s == "linux":
        nm = find_tool("nmcli")
        if not nm:
            return []
        _, txt, _ = run_cmd([nm, "-t", "-f", "NAME,TYPE,AUTOCONNECT", "connection", "show"])
        out = []
        for line in txt.splitlines():
            f = nmcli_fields(line)
            if len(f) >= 2 and f[1] == "802-11-wireless":
                auto = (f[2].lower() != "no") if len(f) > 2 else True
                out.append({"ssid": f[0], "saved": True, "connection_mode": "auto" if auto else "manual", "autoconnect": auto})
        return out

    if s == "darwin":
        ns = find_tool("networksetup")
        if not ns:
            return []
        ports = macos_wifi_ports(run_cmd([ns, "-listallhardwareports"])[1])
        dev = ports[0]["device"] if ports else "en0"
        _, txt, _ = run_cmd([ns, "-listpreferredwirelessnetworks", dev])
        out = []
        for line in txt.splitlines():
            if line.startswith("\t") or line.startswith("    "):
                name = line.strip()
                if name:
                    out.append({"ssid": name, "saved": True, "connection_mode": "auto", "autoconnect": True})
        return out

    return []


def inspect_profile(ssid: str, sysname: str | None = None) -> dict:
    s = system_name(sysname)
    if on_windows(s):
        _, txt, _ = run_cmd(["netsh", "wlan", "show", "profiles"])
        names = [m.group(1).strip() for m in re.finditer(r"All User Profile\s*:\s*(.+)", txt)]
        if ssid not in names:
            return {"ssid": ssid, "saved": False, "autoconnect": False, "all_saved": names}
        _, dtxt, _ = run_cmd(["netsh", "wlan", "show", "profile", f"name={ssid}"])
        d = parse_windows_profile_detail(ssid, dtxt)
        d["all_saved"] = names
        return d
    profs = list_profiles(s, with_details=True)
    hit = next((p for p in profs if p["ssid"] == ssid), None)
    if hit:
        return {**hit, "all_saved": [p["ssid"] for p in profs]}
    return {"ssid": ssid, "saved": False, "autoconnect": False, "all_saved": [p["ssid"] for p in profs]}


def set_autoconnect(ssid: str, auto: bool = True, sysname: str | None = None) -> tuple[bool, str]:
    s = system_name(sysname)
    if on_windows(s):
        mode = "auto" if auto else "manual"
        rc, out, err = run_cmd(["netsh", "wlan", "set", "profileparameter", f"name={ssid}", f"connectionmode={mode}"])
        msg = (out or err).strip()
        return rc == 0, msg or f"set connectionmode={mode}"
    if s == "linux":
        nm = find_tool("nmcli")
        if not nm:
            return False, "nmcli not installed"
        val = "yes" if auto else "no"
        rc, out, err = run_cmd([nm, "connection", "modify", ssid, "connection.autoconnect", val])
        return rc == 0, (out or err).strip() or f"set connection.autoconnect={val}"
    if s == "darwin":
        return True, "macOS preferred networks auto-join when Wi-Fi is on"
    return False, f"unsupported platform {s}"


# ---------------------------------------------------------------------------
# Radio switching & deterministic saved-network connection
# ---------------------------------------------------------------------------

def mark_manual_switch_grace(seconds: float = 45.0) -> None:
    """Tell the offline watchdog that a deliberate network switch is underway so it does not yank us back."""
    try:
        from ..watchdog import net as wdnet
        d = wdnet.state_dir()
        d.mkdir(parents=True, exist_ok=True)
        (d / "manual-switch.until").write_text(f"{time.time() + seconds:.1f}", encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def save_last_good_ssid(ssid: str) -> None:
    if not ssid:
        return
    try:
        from ..watchdog import net as wdnet
        d = wdnet.state_dir()
        d.mkdir(parents=True, exist_ok=True)
        (d / "last-ssid.txt").write_text(ssid, encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def choose_saved_profile(saved: list[str], visible: list[dict], want: str | None = None, exclude: set[str] | None = None) -> tuple[str | None, str]:
    excl = exclude or set()
    if want:
        return (want, "named") if want in saved else (None, f"no saved profile named {want!r} (saved: {', '.join(saved) or 'none'})")
    heard: dict[str, int] = {}
    for n in visible:
        name = n.get("ssid")
        if name and name not in excl:
            heard[name] = max(heard.get(name, 0), int(n.get("signal") or 0))
    cands = sorted((p for p in saved if p in heard and p not in excl), key=lambda p: -heard[p])
    if cands:
        return cands[0], f"strongest saved network in range ({heard[cands[0]]}%)"
    return None, (
        "no saved Wi-Fi network is currently in range"
        if saved
        else "no saved Wi-Fi profiles on this machine: join once in the system Wi-Fi menu so the OS stores the password"
    )


def _elevated_windows_switch(plans: dict[str, list[str]], on: bool) -> tuple[int, str]:
    import ctypes
    from ..core import settings

    word = "ENABLED" if on else "DISABLED"
    lines = ["$ErrorActionPreference = 'Stop'"]
    for name, cmds in plans.items():
        body = "; ".join(cmds)
        lines.append(f"try {{ {body}; Write-Host 'NETWORK: {word} {name}' }} catch {{ Write-Host \"NETWORK: FAILED {name}: $_\" }}")
    lines.append("Write-Host 'ELEVATE: script finished'")
    script_text = "\n".join(lines) + "\n"

    out_dir = settings.state_root() / "state" / "network"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    sp, log = out_dir / f"switch-{stamp}.ps1", out_dir / f"switch-{stamp}.log"
    sp.write_text(script_text, encoding="utf-8-sig")

    elevate_py = settings.ROOT / "local" / "kits" / "Elevate" / "elevate.py"
    if elevate_py.is_file():
        rc, out, err = run_cmd([sys.executable, str(elevate_py), "run", str(sp), "--log", str(log), "--close"], timeout=60)
        if rc != 0:
            return 2, (out + err).strip()
        wrc, wout, _ = run_cmd(
            [sys.executable, str(elevate_py), "wait", str(log), "--marker", "ELEVATE: script finished", "--timeout", "5"],
            timeout=320,
        )
        txt = log.read_text(encoding="utf-8", errors="replace") if log.is_file() else wout
        return (0 if wrc == 0 else 3), txt

    try:
        is_admin = bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # noqa: BLE001
        is_admin = False
    if is_admin:
        out = run_ps(script_text, timeout=45)
        return 0, out
    ps_arg = f"-NoProfile -ExecutionPolicy Bypass -Command \"& '{sp}' *> '{log}'\""
    rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", "powershell.exe", ps_arg, None, 1)
    if rc <= 32:
        return 2, f"UAC prompt declined or failed (code {rc})"
    deadline = time.time() + 60
    while time.time() < deadline:
        if log.is_file():
            txt = log.read_text(encoding="utf-8", errors="replace")
            if "ELEVATE: script finished" in txt:
                return 0, txt
        time.sleep(1.5)
    return 3, (log.read_text(encoding="utf-8", errors="replace") if log.is_file() else "timed out waiting for elevated PowerShell")


def switch_radio(name: str | None = None, on: bool = True, sysname: str | None = None) -> dict:
    """Switch one or all Wi-Fi radios on/off across Windows, Linux, and macOS."""
    s = system_name(sysname)
    radios = list_radios(s)
    if name:
        radios = [r for r in radios if r.name.lower() == name.lower()]
    if not radios:
        return {"ok": False, "exit": 2, "verdict": f"no Wi-Fi radio {name!r} found" if name else "no Wi-Fi radio found", "radios": []}

    if on_windows(s):
        # Software-only radio toggle needs NO admin rights on Windows.
        if on and all(r.status in (*ON_STATES, "Software off") for r in radios):
            msg = run_ps(f"& {{ {PS_RADIO_SET} }} -TargetState 'On'", timeout=25).strip()
            after = {r.name: r.status for r in list_radios(s)}
            if all(after.get(r.name) in ON_STATES for r in radios):
                return {"ok": True, "exit": 0, "verdict": f"ON: {', '.join(r.name for r in radios)}", "detail": msg}
        plans: dict[str, list[str]] = {}
        for r in radios:
            q = "'" + r.name.replace("'", "''") + "'"
            pnp = "'" + r.pnp.replace("'", "''") + "'"
            if on:
                if r.status in ON_STATES:
                    continue
                if r.status == "Not Present" and r.pnp:
                    plans[r.name] = [f"Enable-PnpDevice -InstanceId {pnp} -Confirm:$false"]
                else:
                    plans[r.name] = [f"Enable-NetAdapter -Name {q} -Confirm:$false"]
            else:
                if r.status in ("Disabled", "Not Present"):
                    continue
                plans[r.name] = [f"Disable-NetAdapter -Name {q} -Confirm:$false"]
        if not plans:
            return {"ok": True, "exit": 0, "verdict": f"NOTHING TO DO: {', '.join(f'{r.name} ({r.status})' for r in radios)}"}
        rc, log = _elevated_windows_switch(plans, on)
        if on:
            run_ps(f"& {{ {PS_RADIO_SET} }} -TargetState 'On'", timeout=20)
        deadline = time.time() + 30
        now: dict[str, str] = {}
        while time.time() < deadline:
            now = {r.name: r.status for r in list_radios(s)}
            if all((now.get(n) in ON_STATES) == on for n in plans):
                break
            time.sleep(2)
        ok = rc == 0 and all((now.get(n) in ON_STATES) == on for n in plans)
        return {
            "ok": ok,
            "exit": 0 if ok else 3,
            "verdict": (f"{'ON' if on else 'OFF'}: " + ", ".join(f"{n} ({now.get(n, '?')})" for n in plans))
            if ok
            else f"FAILED switching {', '.join(plans)}: {log[-240:].strip()}",
        }

    if s == "linux":
        for r in radios:
            if on and r.status == "Hard blocked":
                return {"ok": False, "exit": 2, "verdict": f"REFUSED: {r.name} hardware Wi-Fi switch is OFF (rfkill hard block); flip the physical switch"}
            cmds = []
            if on:
                if r.rfkill and r.status == "Disabled":
                    cmds.append(["rfkill", "unblock", r.rfkill])
                cmds.append(["nmcli", "radio", "wifi", "on"])
            else:
                cmds.append(["nmcli", "device", "disconnect", r.name])
                cmds.append(["rfkill", "block", r.rfkill or "wifi"])
            for c in cmds:
                rc, out, err = run_cmd(c)
                if rc != 0 and ("not permitted" in err.lower() or "denied" in err.lower() or "authorized" in err.lower()):
                    rc, out, err = run_cmd(["pkexec", *c], timeout=60)
                if rc != 0:
                    return {"ok": False, "exit": 3, "verdict": f"FAILED {' '.join(c)}: {(err or out).strip()[:200]}"}
        return {"ok": True, "exit": 0, "verdict": f"{'ON' if on else 'OFF'}: {', '.join(r.name for r in radios)}"}

    if s == "darwin":
        ns = find_tool("networksetup")
        if not ns:
            return {"ok": False, "exit": 3, "verdict": "networksetup not found on macOS"}
        for r in radios:
            rc, out, err = run_cmd([ns, "-setairportpower", r.name, "on" if on else "off"])
            if rc != 0:
                return {"ok": False, "exit": 3, "verdict": f"FAILED on {r.name}: {(err or out).strip()[:200]}"}
        return {"ok": True, "exit": 0, "verdict": f"{'ON' if on else 'OFF'}: {', '.join(r.name for r in radios)}"}

    return {"ok": False, "exit": 3, "verdict": f"unsupported platform {s}"}


def prefer_hotspot_radio(name: str, sysname: str | None = None) -> dict:
    s = system_name(sysname)
    if s != "windows":
        return {"ok": True, "exit": 0, "status": "not-this-platform", "verdict": "hotspot radio selection is for Windows Mobile Hotspot; on Linux/macOS bind the AP interface directly"}
    radios = list_radios(s)
    keep = [r for r in radios if r.name.lower() == name.lower()]
    if not keep:
        return {"ok": False, "exit": 2, "verdict": f"no Wi-Fi radio called {name!r}; available: {', '.join(r.name for r in radios)}"}
    others = [r for r in radios if r.name.lower() != name.lower() and r.status not in ("Disabled", "Not Present")]
    if not others:
        return {"ok": True, "exit": 0, "verdict": f"{keep[0].name} is already the only active Wi-Fi radio"}
    plans = {o.name: [f"Disable-NetAdapter -Name '{o.name.replace(chr(39), chr(39)*2)}' -Confirm:$false"] for o in others}
    rc, log = _elevated_windows_switch(plans, on=False)
    ok = rc == 0 and "NETWORK: FAILED" not in log
    return {
        "ok": ok,
        "exit": 0 if ok else 3,
        "verdict": f"HOTSPOT PREFERRED ON {keep[0].name}: switched off {', '.join(o.name for o in others)} (undo: fieldkit network on)"
        if ok
        else f"FAILED: {log[-240:].strip()}",
    }


def probe_internet_quick(timeout: float = 3.5) -> bool:
    for host, port in (("1.1.1.1", 443), ("9.9.9.9", 443), ("8.8.8.8", 53)):
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            continue
    return False


def join_saved(
    ssid: str | None = None,
    radio_name: str | None = None,
    fix_auto: bool = False,
    expect_gateway_mac: str | None = None,
    sysname: str | None = None,
    wait_seconds: int = 30,
) -> dict:
    """Connect to a saved Wi-Fi network (named, or strongest in range) across Windows, Linux, and macOS."""
    s = system_name(sysname)
    mark_manual_switch_grace(60.0)

    # Step 1: ensure a radio is on
    radios = list_radios(s)
    if radio_name:
        radios = [r for r in radios if r.name.lower() == radio_name.lower()]
    if not radios:
        return {"ok": False, "exit": 2, "step": "radio", "verdict": f"NO WIFI RADIO FOUND ({radio_name or 'any'})", "next": "check hardware / adapter"}
    target_radio = next((r for r in radios if r.product_kind == "built-in"), radios[0])
    if target_radio.status not in ON_STATES:
        sw = switch_radio(target_radio.name, on=True, sysname=s)
        if not sw.get("ok"):
            return {"ok": False, "exit": 3, "step": "radio", "verdict": f"RADIO OFF: {sw.get('verdict')}", "next": f"fieldkit network on {target_radio.name}"}

    # Step 2: scan & match saved profiles
    sc = scan_networks(target_radio.name, rescan_now=True, sysname=s)
    visible = sc.get("networks") or []
    profs = list_profiles(s, with_details=False)
    saved_names = [p["ssid"] for p in profs]
    chosen, why = choose_saved_profile(saved_names, visible, want=ssid)
    if not chosen:
        heard_names = sorted({n["ssid"] for n in visible if n.get("ssid") and n["ssid"] != "<hidden>"})[:6]
        return {
            "ok": False,
            "exit": 2,
            "step": "profile",
            "ssid": ssid,
            "heard": heard_names,
            "saved": saved_names,
            "verdict": f"CANNOT JOIN: {why}",
            "next": "join once in the OS Wi-Fi menu so the OS saves the password, then run again",
        }

    if ssid and not any(n.get("ssid") == chosen for n in visible):
        near = ", ".join(repr(x) for x in sorted({n["ssid"] for n in visible if n.get("ssid")})[:6]) or "none"
        return {
            "ok": False,
            "exit": 2,
            "step": "range",
            "ssid": chosen,
            "verdict": f"NOT IN RANGE: {chosen!r} is saved, but not currently heard in the air (heard: {near})",
            "next": "switch the router/hotspot on or move closer, then run again",
        }

    if fix_auto:
        set_autoconnect(chosen, auto=True, sysname=s)

    # Step 3: connect using OS native saved profile
    connected = False
    if on_windows(s):
        run_cmd(["netsh", "wlan", "connect", f"name={chosen}", f"interface={target_radio.name}"])
        deadline = time.time() + wait_seconds
        while time.time() < deadline:
            _, if_txt, _ = run_cmd(["netsh", "wlan", "show", "interfaces"])
            st = parse_netsh_interfaces(if_txt).get(target_radio.name, {})
            if (st.get("State") or "").lower() == "connected" and st.get("SSID") == chosen:
                connected = True
                break
            time.sleep(2)
    elif s == "linux":
        nm = find_tool("nmcli")
        args = [nm, "connection", "up", chosen] + (["ifname", target_radio.name] if target_radio.name else [])
        rc, _, _ = run_cmd(args, timeout=wait_seconds)
        connected = rc == 0
    elif s == "darwin":
        ns = find_tool("networksetup")
        if ns:
            # For a network saved in Keychain, macOS joins without a password argument.
            rc, out, _ = run_cmd([ns, "-setairportnetwork", target_radio.name, chosen], timeout=wait_seconds)
            connected = rc == 0 and "failed" not in out.lower() and "could not find" not in out.lower()

    if not connected:
        return {
            "ok": False,
            "exit": 3,
            "step": "connect",
            "ssid": chosen,
            "radio": target_radio.name,
            "verdict": f"FAILED TO CONNECT to {chosen!r} on {target_radio.name}",
            "next": f"fieldkit network diagnose {json.dumps(chosen)}",
        }

    # Step 4: verify internet & winning route
    inet = False
    for _ in range(3):
        if probe_internet_quick(timeout=3.0):
            inet = True
            break
        time.sleep(1.5)

    from . import triage
    rt = triage.winning_route(sysname=s)
    gw_mac = (rt.get("gateway_mac") or "").lower().replace("-", ":")
    want_mac = (expect_gateway_mac or "").lower().replace("-", ":").strip()

    if inet:
        save_last_good_ssid(chosen)
        try:
            from ..watchdog import net as wdnet
            wdnet.write_remembered_ssid(chosen)
        except Exception:  # noqa: BLE001
            pass

    if not inet:
        return {
            "ok": False,
            "exit": 3,
            "step": "internet",
            "ssid": chosen,
            "radio": target_radio.name,
            "internet": False,
            "route": rt,
            "verdict": f"CONNECTED WITHOUT INTERNET: joined {chosen!r} on {target_radio.name}, but upstream internet did not answer",
            "next": f"fieldkit network diagnose {json.dumps(chosen)}",
        }

    if want_mac and (not gw_mac or not gw_mac.startswith(want_mac)):
        return {
            "ok": False,
            "exit": 3,
            "step": "route",
            "ssid": chosen,
            "radio": target_radio.name,
            "internet": True,
            "route": rt,
            "verdict": f"WRONG ROUTE: internet leaves via {rt.get('iface')} -> {rt.get('gateway')} ({gw_mac or 'unknown MAC'}), not {want_mac}*",
            "next": f"fieldkit network demote {rt.get('iface')}",
        }

    return {
        "ok": True,
        "exit": 0,
        "step": "done",
        "ssid": chosen,
        "why_chosen": why,
        "radio": target_radio.name,
        "internet": True,
        "route": rt,
        "verdict": f"CONNECTED: {target_radio.name} on {chosen!r} ({why}); internet OK via {rt.get('iface') or target_radio.name} -> {rt.get('gateway') or '?'}",
        "next": "nothing",
    }


def radios_verdict(radios: list[Radio]) -> tuple[str, list[str], int]:
    if not radios:
        return "NO WIFI RADIO FOUND", ["plug in a Wi-Fi adapter or check hardware settings"], 2
    up = [r for r in radios if r.status == "Up"]
    on_any = [r for r in radios if r.status in ON_STATES]
    carrier = [r for r in radios if r.carries_hotspot]
    nxt: list[str] = []
    if carrier:
        v = f"UP (hotspot carried by {carrier[0].name}: {carrier[0].identity()})"
    elif up:
        v = "UP: " + ", ".join(f"{r.name} ({r.identity()})" for r in up)
    elif on_any:
        v = "ON (not connected): " + ", ".join(f"{r.name} ({r.identity()})" for r in on_any)
        nxt.append("fieldkit connect   (join the strongest saved Wi-Fi network)")
    else:
        v = "ALL WIFI RADIOS ARE OFF"
        nxt.append("fieldkit network on   (switch Wi-Fi radio on)")
    for r in radios:
        if r.bands and r.driver_claims_bands and "5 GHz" in r.driver_claims_bands and "5 GHz" not in r.bands:
            nxt.append(f"{r.name}: generic driver claims 5 GHz, but {r.chipset} physically supports {r.bands} only")
    return v, nxt, (0 if on_any else 2)


def format_radios(radios: list[Radio]) -> list[str]:
    v, nxt, _ = radios_verdict(radios)
    lines: list[str] = []
    for r in radios:
        lines.append(f"{r.name}  [{r.status}]  {r.identity()}")
        if r.bands:
            s_part = f", {r.streams} streams" if r.streams else ""
            note_part = f" ({r.chip_note})" if r.chip_note else ""
            lines.append(f"  capabilities : {r.bands}{s_part}{note_part}")
        if r.mac or r.hw_id:
            lines.append(f"  hardware     : {r.maker or 'unknown OUI'}  MAC {r.mac or '-'}  {r.bus} {r.hw_id}".rstrip())
        if r.driver:
            lines.append(f"  driver       : {r.driver} {r.driver_date} ({r.driver_provider})".rstrip())
        if r.link:
            lines.append(f"  link now     : {r.link}")
        if r.carries_hotspot:
            lines.append(f"  hotspot      : carried by this radio ({', '.join(r.virtual_children)})")
    lines.append(f"VERDICT: {v}")
    for n in nxt:
        lines.append(f"NEXT: {n}")
    return lines
