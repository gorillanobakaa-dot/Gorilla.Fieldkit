"""Cross-platform interface, routing, per-interface internet/DNS probes, and read-only router triage.

Works across Windows, Linux, and macOS using only the Python standard library.
"""
from __future__ import annotations

import ctypes
from dataclasses import asdict, dataclass, field
import datetime as dt
import email.utils
import json
import os
import re
import socket
import ssl
import time
import urllib.error
import urllib.request

from .radios import find_tool, run_cmd, run_ps, system_name

PROBES = [("1.1.1.1", 443), ("9.9.9.9", 443), ("8.8.8.8", 53)]
PROBE_DNS_NAME = "cloudflare.com"

KNOWN_ROUTERS = {
    "ee-smart-5g-hub": {
        "match": ["smart5g.hub", "mbbstatus", "getltestatus"],
        "label": "Smart 5G Hub (mobile broadband)",
        "paths": {
            "lte": "cgi-bin/GetLTEStatus",
            "internet": "cgi-bin/GetStatusInternet",
            "wanport": "cgi-bin/GetWanportStatus",
        },
    },
    "arris-docsis-hub": {
        "match": ["skins/lgi/", "getrouterstatus", "mib.js"],
        "label": "DOCSIS Cable Hub (ARRIS / LGI)",
        "paths": {
            "status": "getRouterStatus",
            "cm": "walk?oids=1.3.6.1.4.1.4491.2.1.20.1.1.1;",
            "prelogin": "getPreLoginData",
        },
    },
}

DOCSIS_CM_STATE = {
    1: "other",
    2: "not ready",
    3: "not synchronised (no downstream lock)",
    4: "PHY synchronised",
    5: "upstream parameters acquired",
    6: "ranging complete",
    7: "DHCPv4 complete",
    8: "DHCPv6 complete",
    9: "time of day established",
    10: "security established",
    11: "config file downloaded",
    12: "registration complete (online)",
    13: "operational (online)",
    14: "ACCESS DENIED by the network",
}
DOCSIS_VERSION = {1: "1.0", 2: "1.1", 3: "2.0", 4: "3.0", 5: "3.1"}
OID_DS = "1.3.6.1.2.1.10.127.1.1.1.1"
OID_US = "1.3.6.1.2.1.10.127.1.1.2.1"
OID_SIGQ = "1.3.6.1.2.1.10.127.1.1.4.1"
OID_US3 = "1.3.6.1.4.1.4491.2.1.20.1.2.1"
OID_EVENTS = "1.3.6.1.2.1.69.1.5.8.1"
OID_DOCSIS_VER = "1.3.6.1.2.1.10.127.1.1.5.0"
OID_CM_STATE = "1.3.6.1.4.1.4491.2.1.20.1.1.1.1."

VENDOR_HINTS = [
    {"match": ["smart5g.hub", "mbbstatus"], "label": "5G Mobile Hub", "hint": "status shown before login"},
    {"match": ["bt smart hub", "bthomehub", "home hub"], "label": "BT Smart Hub", "hint": "Advanced settings > Technical log"},
    {"match": ["skins/lgi/", "superhub", "arris"], "label": "DOCSIS Cable Hub", "hint": "Router status > Network status"},
    {"match": ["sky hub", "sky broadband"], "label": "Sky Hub", "hint": "Maintenance > Router status"},
    {"match": ["huawei", "mobilewifi", "hilink", "cpe pro"], "label": "Huawei HiLink router", "hint": "front page shows signal and SIM"},
    {"match": ["zte", "ztemobile", "mc801", "mc888", "mu5001"], "label": "ZTE mobile router", "hint": "front page shows SIM and signal"},
    {"match": ["nr5103", "nr7101", "zyxel"], "label": "Zyxel router", "hint": "Status page shows SIM, signal, and WAN"},
    {"match": ["tp-link", "tplink", "archer", "deco"], "label": "TP-Link router", "hint": "Network map > Internet"},
    {"match": ["netgear", "nighthawk", "orbi", "routerlogin"], "label": "Netgear router", "hint": "Advanced > Internet port status"},
    {"match": ["asus", "asuswrt", "zenwifi"], "label": "ASUS router", "hint": "Network map > Internet status"},
    {"match": ["fritz!box", "fritzbox", "avm"], "label": "AVM FRITZ!Box", "hint": "Overview > Connections"},
    {"match": ["mikrotik", "routeros", "webfig"], "label": "MikroTik RouterOS", "hint": "Interfaces / IP > DHCP Client"},
    {"match": ["openwrt", "luci"], "label": "OpenWrt (LuCI)", "hint": "Status > Overview > Network"},
    {"match": ["unifi", "ubiquiti", "udm"], "label": "Ubiquiti gateway", "hint": "check UniFi controller"},
    {"match": ["starlink"], "label": "Starlink router", "hint": "Starlink app or 192.168.100.1"},
]
MOBILE_WORDS = ("sim", "puk", "apn", "signal", "lte", "4g", "5g", "imei", "mobile broadband", "cellular")
SIM_STATE = {
    0: "unknown SIM",
    1: "connected",
    2: "connected",
    3: "SIM PIN required",
    4: "SIM PUK required",
    5: "NO SIM",
    6: "SIM error",
    7: "invalid SIM",
    8: "SIM locked",
    9: "SIM permanently blocked",
}


@dataclass
class Iface:
    index: int
    alias: str
    description: str = ""
    kind: str = ""
    status: str = ""
    speed: str = ""
    mac: str = ""
    ipv4: str = ""
    ipv4_origin: str = ""
    ipv4_state: str = ""
    gateway: str = ""
    metric: int | None = None
    automatic_metric: bool | None = None
    dhcp: bool | None = None
    dns: list[str] = field(default_factory=list)
    has_default_route: bool = False
    gateway_ping_ms: int | None = None
    gateway_mac: str = ""
    internet: bool | None = None
    internet_detail: str = ""
    dns_ok: bool | None = None


def is_private_ip(ip: str) -> bool:
    try:
        a = [int(x) for x in ip.split(".")]
    except ValueError:
        return True
    if len(a) != 4:
        return True
    return (
        a[0] in (0, 10, 127)
        or (a[0] == 172 and 16 <= a[1] <= 31)
        or (a[0] == 192 and a[1] == 168)
        or (a[0] == 169 and a[1] == 254)
    )


def tcp_from(source_ip: str, host: str, port: int, timeout: float = 2.8) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        if source_ip:
            s.bind((source_ip, 0))
        s.connect((host, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def dns_answer_is_real(data: bytes, server: str) -> bool:
    if len(data) < 12 or data[:2] != b"\x12\x34" or (data[3] & 0x0F) != 0:
        return False
    qd, an = int.from_bytes(data[4:6], "big"), int.from_bytes(data[6:8], "big")
    if an == 0:
        return False
    pos = 12
    for _ in range(qd):
        while pos < len(data) and 0 < data[pos] < 0xC0:
            pos += data[pos] + 1
        pos += 1 if pos < len(data) and data[pos] == 0 else 2
        pos += 4
    for _ in range(an):
        if pos >= len(data):
            return False
        pos += 2 if data[pos] >= 0xC0 else 1
        if pos + 10 > len(data):
            return False
        rtype = int.from_bytes(data[pos : pos + 2], "big")
        rdlen = int.from_bytes(data[pos + 8 : pos + 10], "big")
        rdata = data[pos + 10 : pos + 10 + rdlen]
        pos += 10 + rdlen
        if rtype == 1 and rdlen == 4:
            ip = ".".join(str(b) for b in rdata)
            if ip != server and not is_private_ip(ip):
                return True
    return False


def dns_from(source_ip: str, server: str, name: str = PROBE_DNS_NAME, timeout: float = 2.8) -> bool:
    if not server or ":" in server:
        return False
    labels = b"".join(bytes([len(p)]) + p.encode("ascii") for p in name.split("."))
    q = b"\x12\x34\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00" + labels + b"\x00\x00\x01\x00\x01"
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    try:
        if source_ip:
            s.bind((source_ip, 0))
        s.sendto(q, (server, 53))
        data, _ = s.recvfrom(512)
    except OSError:
        return False
    finally:
        s.close()
    return dns_answer_is_real(data, server)


def ping_host(host: str, source_ip: str = "", sysname: str | None = None) -> int | None:
    s = system_name(sysname)
    if not find_tool("ping"):
        return None
    if s == "windows":
        args = ["ping", "-n", "1", "-w", "1500"] + (["-S", source_ip] if source_ip else []) + [host]
    elif s == "darwin":
        args = ["ping", "-c", "1", "-W", "1500"] + (["-S", source_ip] if source_ip else []) + [host]
    else:
        args = ["ping", "-c", "1", "-W", "2"] + (["-I", source_ip] if source_ip else []) + [host]
    _, out, _ = run_cmd(args, timeout=8)
    m = re.search(r"time[=<]([0-9.]+)\s*ms", out, re.I)
    if m and "unreachable" not in out.lower():
        return int(round(float(m.group(1))))
    return None


def resolve_neighbor_mac(ip: str, iface_index: int | None = None, iface_name: str = "", sysname: str | None = None) -> str:
    if not ip:
        return ""
    s = system_name(sysname)
    if s == "windows":
        idx_filter = f"-InterfaceIndex {iface_index}" if iface_index else ""
        script = (
            f"Test-Connection -ComputerName '{ip}' -Count 1 -Quiet -ErrorAction SilentlyContinue | Out-Null; "
            f"$n = Get-NetNeighbor -IPAddress '{ip}' {idx_filter} -ErrorAction SilentlyContinue | "
            "Where-Object { $_.LinkLayerAddress -and $_.LinkLayerAddress -notmatch '^(00-){5}00$' } | Select-Object -First 1; "
            "[string]$n.LinkLayerAddress"
        )
        mac = run_ps(script, timeout=12).strip()
        if mac:
            return mac.lower().replace("-", ":")
    if s == "linux" and find_tool("ip"):
        cmd = [find_tool("ip"), "neigh", "show", ip] + (["dev", iface_name] if iface_name else [])
        _, out, _ = run_cmd(cmd, timeout=8)
        m = re.search(r"lladdr\s+([0-9a-f:]{17})", out, re.I)
        if m:
            return m.group(1).lower()
    if find_tool("arp"):
        args = ["arp", "-a", ip] if s == "windows" else ["arp", "-n", ip]
        _, out, _ = run_cmd(args, timeout=8)
        m = re.search(r"([0-9a-f]{1,2}(?:[:-][0-9a-f]{1,2}){5})", out, re.I)
        if m:
            mac = ":".join(p.zfill(2) for p in re.split(r"[:-]", m.group(1))).lower()
            if mac != "00:00:00:00:00:00":
                return mac
    return ""


# ---------------------------------------------------------------------------
# Interface & route collection per OS
# ---------------------------------------------------------------------------

def _windows_interfaces() -> list[Iface]:
    script = r"""
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$ErrorActionPreference = 'SilentlyContinue'
$ads = Get-NetAdapter | Select-Object ifIndex,Name,InterfaceDescription,Status,LinkSpeed,MacAddress
$ips = Get-NetIPAddress -AddressFamily IPv4 | Select-Object InterfaceIndex,IPAddress,@{n='Origin';e={$_.PrefixOrigin.ToString()}},@{n='State';e={$_.AddressState.ToString()}}
$ifs = Get-NetIPInterface -AddressFamily IPv4 | Select-Object InterfaceIndex,InterfaceMetric,AutomaticMetric,Dhcp
$rts = Get-NetRoute -DestinationPrefix 0.0.0.0/0 | Select-Object ifIndex,NextHop,RouteMetric
$dns = Get-DnsClientServerAddress -AddressFamily IPv4 | Select-Object InterfaceIndex,ServerAddresses
[ordered]@{ adapters=$ads; ips=$ips; ifs=$ifs; routes=$rts; dns=$dns } | ConvertTo-Json -Depth 4 -Compress
"""
    raw = run_ps(script, timeout=40).strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except ValueError:
        return []
    as_list = lambda v: [] if v is None else (v if isinstance(v, list) else [v])
    by: dict[int, Iface] = {}
    for r in as_list(data.get("adapters")):
        idx = int(r.get("ifIndex") or 0)
        desc = str(r.get("InterfaceDescription") or "")
        kind = "wifi" if re.search(r"wi-?fi|wireless|802\.11|wlan", desc, re.I) else "wired"
        by[idx] = Iface(
            index=idx,
            alias=str(r.get("Name") or ""),
            description=desc,
            kind=kind,
            status=str(r.get("Status") or ""),
            speed=str(r.get("LinkSpeed") or ""),
            mac=str(r.get("MacAddress") or ""),
        )
    for r in as_list(data.get("ips")):
        i = by.get(int(r.get("InterfaceIndex") or -1))
        ip = str(r.get("IPAddress") or "")
        if i and ip and not ip.startswith(("127.", "169.254.")):
            i.ipv4, i.ipv4_origin, i.ipv4_state = ip, str(r.get("Origin") or ""), str(r.get("State") or "")
    for r in as_list(data.get("ifs")):
        i = by.get(int(r.get("InterfaceIndex") or -1))
        if i:
            i.metric = int(r["InterfaceMetric"]) if r.get("InterfaceMetric") is not None else None
            i.automatic_metric = str(r.get("AutomaticMetric")) in ("1", "Enabled", "True")
            i.dhcp = str(r.get("Dhcp")) in ("1", "Enabled", "True")
    for r in as_list(data.get("routes")):
        i = by.get(int(r.get("ifIndex") or -1))
        if i and r.get("NextHop"):
            i.has_default_route = True
            i.gateway = str(r.get("NextHop") or "")
    for r in as_list(data.get("dns")):
        i = by.get(int(r.get("InterfaceIndex") or -1))
        if i:
            sv = r.get("ServerAddresses") or []
            i.dns = [str(x) for x in (sv if isinstance(sv, list) else [sv]) if x]
    return list(by.values())


def _resolv_conf_dns() -> list[str]:
    try:
        txt = open("/etc/resolv.conf", encoding="utf-8", errors="replace").read()
        return [m.group(1) for m in re.finditer(r"^\s*nameserver\s+(\d+\.\d+\.\d+\.\d+)", txt, re.M)]
    except OSError:
        return []


def _linux_interfaces() -> list[Iface]:
    out: list[Iface] = []
    ip_bin = find_tool("ip")
    try:
        names = sorted(os.listdir("/sys/class/net"))
    except OSError:
        names = []
    routes: dict[str, tuple[str, int]] = {}
    if ip_bin:
        _, rtxt, _ = run_cmd([ip_bin, "route"])
        for line in rtxt.splitlines():
            if not line.startswith("default"):
                continue
            dev_m = re.search(r"\bdev\s+(\S+)", line)
            gw_m = re.search(r"\bvia\s+(\S+)", line)
            met_m = re.search(r"\bmetric\s+(\d+)", line)
            if dev_m:
                dev = dev_m.group(1)
                met = int(met_m.group(1)) if met_m else 100
                if dev not in routes or met < routes[dev][1]:
                    routes[dev] = (gw_m.group(1) if gw_m else "", met)
    addrs: dict[str, tuple[str, bool]] = {}
    if ip_bin:
        _, atxt, _ = run_cmd([ip_bin, "-4", "addr", "show"])
        cur = ""
        for line in atxt.splitlines():
            m_if = re.match(r"^\d+:\s+([^:@]+)", line)
            if m_if:
                cur = m_if.group(1)
                continue
            m_ip = re.search(r"inet\s+(\d+\.\d+\.\d+\.\d+)/\d+.*?(dynamic)?", line)
            if cur and m_ip and not m_ip.group(1).startswith(("127.", "169.254.")):
                addrs[cur] = (m_ip.group(1), bool(m_ip.group(2)))
    dns_servers = _resolv_conf_dns()
    for idx, name in enumerate(names, 1):
        if name == "lo" or name.startswith(("docker", "veth", "br-", "virbr", "sit")):
            continue
        rd = lambda f, _n=name: open(f"/sys/class/net/{_n}/{f}", encoding="utf-8", errors="replace").read().strip() if os.path.isfile(f"/sys/class/net/{_n}/{f}") else ""
        wireless = os.path.isdir(f"/sys/class/net/{name}/wireless") or os.path.exists(f"/sys/class/net/{name}/phy80211")
        st_raw = rd("operstate")
        status = "Up" if st_raw == "up" else ("Disconnected" if st_raw in ("down", "dormant", "unknown") else st_raw)
        spd = rd("speed")
        speed = f"{spd} Mbps" if spd and spd.lstrip("-").isdigit() and int(spd) > 0 else ""
        ip, is_dyn = addrs.get(name, ("", False))
        gw, met = routes.get(name, ("", None))
        if ip and status != "Up":
            status = "Up"
        out.append(
            Iface(
                index=idx,
                alias=name,
                description="Wi-Fi" if wireless else "Ethernet",
                kind="wifi" if wireless else "wired",
                status=status,
                speed=speed,
                mac=rd("address").upper().replace(":", "-"),
                ipv4=ip,
                ipv4_origin="Dhcp" if is_dyn else ("Manual" if ip else ""),
                ipv4_state="Preferred" if ip else "",
                gateway=gw,
                metric=met,
                dhcp=is_dyn if ip else None,
                dns=dns_servers if ip else [],
                has_default_route=bool(gw),
            )
        )
    return out


def _macos_interfaces() -> list[Iface]:
    ns = find_tool("networksetup")
    ports: dict[str, tuple[str, str]] = {}
    if ns:
        txt = run_cmd([ns, "-listallhardwareports"])[1]
        cur_p, cur_d, cur_m = "", "", ""
        for line in txt.splitlines():
            s = line.strip()
            if not s:
                if cur_d:
                    ports[cur_d] = (cur_p, cur_m)
                cur_p = cur_d = cur_m = ""
            elif s.startswith("Hardware Port:"):
                cur_p = s.split(":", 1)[1].strip()
            elif s.startswith("Device:"):
                cur_d = s.split(":", 1)[1].strip()
            elif s.startswith("Ethernet Address:"):
                cur_m = s.split(":", 1)[1].strip().upper().replace(":", "-")
        if cur_d:
            ports[cur_d] = (cur_p, cur_m)

    routes: dict[str, tuple[str, int]] = {}
    if find_tool("netstat"):
        _, rtxt, _ = run_cmd(["netstat", "-rn", "-f", "inet"])
        rank = 10
        for line in rtxt.splitlines():
            parts = line.split()
            if len(parts) >= 4 and parts[0] == "default":
                gw, dev = parts[1], parts[-1]
                if dev not in routes:
                    routes[dev] = (gw, rank)
                    rank += 10

    dns_servers = _resolv_conf_dns()
    if not dns_servers and find_tool("scutil"):
        _, stxt, _ = run_cmd(["scutil", "--dns"])
        dns_servers = list(dict.fromkeys(re.findall(r"nameserver\[\d+\]\s*:\s*(\d+\.\d+\.\d+\.\d+)", stxt)))

    ifc = find_tool("ifconfig")
    _, itxt, _ = run_cmd([ifc]) if ifc else (1, "", "")
    blocks: dict[str, str] = {}
    cur = ""
    for line in itxt.splitlines():
        m = re.match(r"^([a-z0-9]+):\s+flags=", line)
        if m:
            cur = m.group(1)
            blocks[cur] = line + "\n"
        elif cur:
            blocks[cur] += line + "\n"

    out: list[Iface] = []
    for idx, (dev, body) in enumerate(sorted(blocks.items()), 1):
        if dev.startswith(("lo", "gif", "stf", "awdl", "llw", "utun", "bridge")):
            continue
        if ports and dev not in ports and not dev.startswith("en"):
            continue
        label, mac = ports.get(dev, (dev, ""))
        if not mac:
            m_mac = re.search(r"ether\s+([0-9a-f:]{17})", body, re.I)
            mac = m_mac.group(1).upper().replace(":", "-") if m_mac else ""
        m_ip = re.search(r"\binet\s+(\d+\.\d+\.\d+\.\d+)\b", body)
        ip = m_ip.group(1) if m_ip and not m_ip.group(1).startswith(("127.", "169.254.")) else ""
        active = "status: active" in body or bool(ip)
        gw, met = routes.get(dev, ("", None))
        kind = "wifi" if re.search(r"wi-?fi|airport", label, re.I) else "wired"
        out.append(
            Iface(
                index=idx,
                alias=dev,
                description=label,
                kind=kind,
                status="Up" if active else "Disconnected",
                mac=mac,
                ipv4=ip,
                ipv4_state="Preferred" if ip else "",
                gateway=gw,
                metric=met,
                dns=dns_servers if ip else [],
                has_default_route=bool(gw),
            )
        )
    return out


def gather_interfaces(test_links: bool = True, sysname: str | None = None) -> list[Iface]:
    s = system_name(sysname)
    ifaces = (
        _windows_interfaces()
        if s == "windows"
        else _linux_interfaces()
        if s == "linux"
        else _macos_interfaces()
        if s == "darwin"
        else []
    )
    if test_links:
        for i in ifaces:
            if not i.ipv4 or i.status != "Up":
                continue
            if i.gateway:
                i.gateway_ping_ms = ping_host(i.gateway, source_ip=i.ipv4, sysname=s)
                i.gateway_mac = resolve_neighbor_mac(i.gateway, iface_index=i.index, iface_name=i.alias, sysname=s)
            hits = [f"{h}:{p}" for h, p in PROBES if tcp_from(i.ipv4, h, p)]
            i.internet = bool(hits)
            i.internet_detail = ", ".join(hits) if hits else "no outbound TCP reply"
            if i.dns:
                i.dns_ok = any(dns_from(i.ipv4, d) for d in i.dns[:2])
    return ifaces


def winning_route(sysname: str | None = None) -> dict:
    s = system_name(sysname)
    ifaces = gather_interfaces(test_links=False, sysname=s)
    cands = [i for i in ifaces if i.status == "Up" and i.has_default_route]
    cands.sort(key=lambda i: (i.metric is None, i.metric if i.metric is not None else 9999))
    win = cands[0] if cands else None
    gw_mac = resolve_neighbor_mac(win.gateway, iface_index=win.index, iface_name=win.alias, sysname=s) if win and win.gateway else ""
    return {
        "iface": win.alias if win else None,
        "source_ip": win.ipv4 if win else None,
        "gateway": win.gateway if win else None,
        "gateway_mac": gw_mac,
        "metric": win.metric if win else None,
        "defaults": [{"iface": c.alias, "gateway": c.gateway, "metric": c.metric} for c in cands],
    }


# ---------------------------------------------------------------------------
# Router inspection (GET-only, zero lockout risk)
# ---------------------------------------------------------------------------

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def http_get(url: str, timeout: float = 5.0) -> tuple[int, dict, str]:
    ctx = ssl.create_default_context()
    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
    req = urllib.request.Request(url, headers={"User-Agent": "fieldkit-network/1.0", "Accept": "*/*"})
    opener = urllib.request.build_opener(_NoRedirect(), urllib.request.HTTPSHandler(context=ctx))
    try:
        with opener.open(req, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read(250_000).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), ""
    except Exception:  # noqa: BLE001
        return 0, {}, ""


def _snmp_table(status: dict, prefix: str) -> dict[str, dict[int, str]]:
    out: dict[str, dict[int, str]] = {}
    for k, v in status.items():
        if k.startswith(prefix + "."):
            col, _, idx = k[len(prefix) + 1 :].partition(".")
            if col.isdigit():
                out.setdefault(idx, {})[int(col)] = str(v)
    return out


def _fnum(val, scale: float = 1.0) -> float | None:
    try:
        return float(val) / scale
    except (TypeError, ValueError):
        return None


def _decode_mobile_hub(status: dict) -> list[str]:
    f = []
    lte = status.get("lte") or {}
    inet = status.get("internet") or {}
    if not (lte or inet):
        return ["hub recognised, but status endpoints did not answer"]
    try:
        sim = int(str(inet.get("SIMState", lte.get("SIMState", "0"))))
    except ValueError:
        sim = 0
    conn = str(inet.get("Enable_DataConn", lte.get("Enable_DataConn", "0"))) == "1"
    op = inet.get("operator") or lte.get("operator") or ""
    rsrp = inet.get("RSRP_4G") or ""
    f.append(f"SIM state {sim}: {SIM_STATE.get(sim, 'unknown')}")
    if sim == 5:
        f.append("ROUTER HAS NO SIM: seat the SIM card, reboot the hub, and wait 2-3 minutes")
    elif sim == 3:
        f.append("SIM requires its PIN on the router's admin page")
    elif sim in (1, 2) and not conn:
        f.append(f"SIM present ({op or 'unknown operator'}), but mobile data session is down")
    elif sim in (1, 2) and conn:
        f.append(f"mobile data active ({op}, {inet.get('net_type', '')} band {inet.get('Band', '')})".strip())
    if rsrp and rsrp not in ("0", ""):
        v = _fnum(rsrp)
        if v is not None:
            grade = "good" if v > -90 else "fair" if v > -100 else "weak"
            f.append(f"4G RSRP {int(v)} dBm ({grade})")
    return f


def _decode_docsis_hub(status: dict) -> list[str]:
    f = []
    st = status.get("status") or {}
    cm = status.get("cm") or {}
    if not isinstance(st, dict) or not any(k.startswith("1.3.6.1") for k in st):
        return ["cable hub recognised, but unauthenticated DOCSIS table did not answer"]
    for k, v in (cm if isinstance(cm, dict) else {}).items():
        if k.startswith(OID_CM_STATE) and str(v).isdigit():
            code = int(v)
            f.append(f"cable modem state {code}: {DOCSIS_CM_STATE.get(code, 'unknown')}")
            break
    ver = _fnum(st.get(OID_DOCSIS_VER))
    if ver:
        f.append(f"DOCSIS {DOCSIS_VERSION.get(int(ver), '?')}")
    ds, sq = _snmp_table(st, OID_DS), _snmp_table(st, OID_SIGQ)
    if ds:
        powers = [p for p in (_fnum(r.get(6), 10) for r in ds.values()) if p is not None]
        snrs = [s for s in (_fnum(r.get(5), 10) for r in sq.values()) if s is not None]
        uncorr = sum(int(_fnum(r.get(4)) or 0) for r in sq.values())
        corr = sum(int(_fnum(r.get(3)) or 0) for r in sq.values())
        line = f"downstream {len(ds)} ch"
        if powers:
            line += f", power {min(powers):+.1f}..{max(powers):+.1f} dBmV"
        if snrs:
            line += f", SNR {min(snrs):.1f}..{max(snrs):.1f} dB"
        line += f", corrected {corr}, uncorrectable {uncorr}"
        f.append(line)
        if uncorr > 10_000:
            f.append("high uncorrectable codeword errors since boot (line noise / coax issue)")
    us, us3 = _snmp_table(st, OID_US), _snmp_table(st, OID_US3)
    if us or us3:
        tx = [p for p in (_fnum(r.get(1), 10) for r in us3.values()) if p is not None]
        t3 = sum(int(_fnum(r.get(2)) or 0) for r in us3.values())
        t4 = sum(int(_fnum(r.get(3)) or 0) for r in us3.values())
        line = f"upstream {len(us) or len(us3)} ch"
        if tx:
            line += f", tx power {min(tx):.1f}..{max(tx):.1f} dBmV"
        line += f", T3 timeouts {t3}, T4 timeouts {t4}"
        f.append(line)
    ev = _snmp_table(st, OID_EVENTS)
    if ev:
        rows = sorted(ev.values(), key=lambda r: r.get(1, "0"))
        last = rows[-1]
        f.append(f"modem log ({len(rows)} entries), last: {last.get(2, '')} {last.get(7, '').split(';')[0]}")
    return f


def probe_router(ip: str | None = None, sysname: str | None = None) -> dict:
    s = system_name(sysname)
    if not ip:
        ip = winning_route(s).get("gateway") or ""
    res: dict = {
        "ip": ip,
        "mac": resolve_neighbor_mac(ip, sysname=s) if ip else "",
        "http": 0,
        "url": "",
        "title": "",
        "server": "",
        "kind": "",
        "label": "",
        "vendor": "",
        "mobile": False,
        "clock": "",
        "findings": [],
        "status": {},
    }
    if not ip:
        res["findings"].append("no default gateway IP found")
        return res

    base_url, code, headers, body = "", 0, {}, ""
    for cand in (f"http://{ip}/", f"https://{ip}/"):
        c, h, b = http_get(cand)
        if c in (301, 302, 303, 307, 308) and h.get("Location"):
            loc = h["Location"]
            if loc.startswith("/"):
                loc = cand.rstrip("/") + loc
            c2, h2, b2 = http_get(loc)
            if c2:
                parts = loc.split("/", 3)
                base_url, code, headers, body = "/".join(parts[:3]) + "/", c2, h2, b2
                break
        if c:
            base_url, code, headers, body = cand, c, h, b
            break

    res["http"], res["url"] = code, base_url or f"http://{ip}/"
    res["server"] = headers.get("Server", "")
    res["clock"] = headers.get("Date", "")
    m = re.search(r"<title>(.*?)</title>", body, re.S | re.I)
    res["title"] = re.sub(r"\s+", " ", m.group(1)).strip() if m else ""

    low = " ".join([res["title"], res["server"], body]).lower()
    for kind, spec in KNOWN_ROUTERS.items():
        if any(k in low for k in spec["match"]):
            res["kind"], res["label"] = kind, spec["label"]
            for name, path in spec["paths"].items():
                c, _, b = http_get(res["url"] + path)
                try:
                    res["status"][name] = json.loads(b) if c == 200 and b.strip() else {"http": c}
                except ValueError:
                    res["status"][name] = {"http": c, "raw": b[:200]}
            break

    vhit = next((v for v in VENDOR_HINTS if any(k in low for k in v["match"])), None)
    if vhit:
        res["vendor"] = vhit["label"]
    res["mobile"] = sum(1 for w in MOBILE_WORDS if re.search(r"\b" + re.escape(w) + r"\b", low)) >= 3

    if res["kind"] == "ee-smart-5g-hub":
        res["findings"] += _decode_mobile_hub(res["status"])
    elif res["kind"] == "arris-docsis-hub":
        res["findings"] += _decode_docsis_hub(res["status"])
    elif code:
        if vhit:
            res["findings"].append(f"detected {vhit['label']} ({vhit['hint']})")
        if res["mobile"]:
            res["findings"].append("mobile/SIM router vocabulary detected: throughput naturally varies with cell load; check SIM/signal if offline")
    else:
        res["findings"].append(f"gateway {ip} did not serve an HTTP page (may be offline, booting, or admin web UI disabled)")

    if res["clock"]:
        try:
            t = email.utils.parsedate_to_datetime(res["clock"])
            if abs((t.astimezone(dt.timezone.utc) - dt.datetime.now(dt.timezone.utc)).days) > 30:
                res["findings"].append(f"router clock ({res['clock']}) is unsynchronised: box has likely not reached NTP since boot")
        except Exception:  # noqa: BLE001
            pass
    return res


def router_login_risk(ip: str | None = None, sysname: str | None = None) -> dict:
    s = system_name(sysname)
    if not ip:
        ip = winning_route(s).get("gateway") or ""
    if not ip:
        return {"ip": "", "error": "no default gateway IP found", "lockout_findings": [], "client_side_crypto": [], "safe_to_automate_login": False}
    _, _, body = http_get(f"http://{ip}/")
    scripts = re.findall(r'src="([^"]+\.js)"', body or "")
    findings, crypto = [], []
    for sc in scripts[:12]:
        url = sc if sc.startswith("http") else f"http://{ip}/{sc.lstrip('/')}"
        _, _, js = http_get(url)
        if not js:
            continue
        if re.search(r"lock.{0,20}out|fail_\d+_time|FailTime|WaitSeconds", js, re.I):
            findings.append({"script": sc, "finding": "login lockout detected after repeated failures"})
        m = re.search(r"should lock out for (\d+) seconds", js, re.I)
        if m:
            findings.append({"script": sc, "finding": f"lockout duration is {m.group(1)} seconds"})
        for cname in ("bcrypt", "sjcl", "CryptoJS", "AES", "RSA"):
            if cname.lower() in js.lower() and cname not in crypto:
                crypto.append(cname)
    return {
        "ip": ip,
        "lockout_findings": findings,
        "client_side_crypto": crypto,
        "safe_to_automate_login": not crypto,
        "advice": (
            "client-side hashing/encryption detected: log in manually in a browser rather than scripting credentials"
            if crypto
            else "no client-side login crypto detected; still allow only one careful attempt"
        ),
    }


# ---------------------------------------------------------------------------
# Route verdict & reversible route steering
# ---------------------------------------------------------------------------

def routes_verdict(ifaces: list[Iface]) -> tuple[int, list[str], list[str]]:
    v, nxt = [], []
    up = [i for i in ifaces if i.status == "Up" and i.ipv4]
    with_route = [i for i in up if i.has_default_route]
    with_route.sort(key=lambda i: (i.metric is None, i.metric if i.metric is not None else 9999))
    if not up:
        return 2, ["NO ACTIVE INTERFACE with an IPv4 address"], ["fieldkit connect   (join a saved Wi-Fi network) or plug in Ethernet"]
    winner = with_route[0] if with_route else None
    if not winner:
        return 2, ["NO DEFAULT ROUTE: interfaces have an IP, but DHCP supplied no gateway"], ["reconnect Wi-Fi or renew DHCP"]
    v.append(f"Traffic leaves via '{winner.alias}' (metric {winner.metric}, gateway {winner.gateway}, IP {winner.ipv4})")
    for i in with_route:
        inet = "internet YES" if i.internet else ("internet NO" if i.internet is False else "internet untested")
        gw = f"gw {i.gateway_ping_ms} ms" if i.gateway_ping_ms is not None else "gw no ping"
        dns = f"DNS {'ok' if i.dns_ok else 'FAIL'}" if i.dns_ok is not None else "DNS -"
        v.append(f"  {i.alias:<16} {inet}; {gw}; {dns}; metric {i.metric}")
    if winner.internet is False:
        good = [i for i in with_route[1:] if i.internet]
        if good:
            v.append(f"WRONG WINNER: '{winner.alias}' has no internet while '{good[0].alias}' works (lower metric won)")
            nxt.append(f"fieldkit network demote {json.dumps(winner.alias)}")
        else:
            nxt.append(f"fieldkit network router {winner.gateway}   (check router WAN/SIM status)")
        return 3, v, nxt
    if winner.dns_ok is False:
        v.append(f"DNS FAIL on '{winner.alias}' although raw TCP works")
        nxt.append("check router DNS settings or configure 1.1.1.1 / 9.9.9.9")
        return 3, v, nxt
    return 0, v, nxt


def steer_route(iface: str, mode: str = "demote", metric: int | None = None, apply_it: bool = True, sysname: str | None = None) -> dict:
    """mode: 'prefer' (low metric 20), 'demote' (high metric 150), or 'restore' (automatic)."""
    s = system_name(sysname)
    ifaces = gather_interfaces(test_links=False, sysname=s)
    hit = next((i for i in ifaces if i.alias.lower() == iface.lower() or str(i.index) == iface), None)
    if not hit:
        known = ", ".join(i.alias for i in ifaces if i.status != "Not Present")
        return {"ok": False, "exit": 2, "verdict": f"no interface named {iface!r} (have: {known})"}

    target_metric = metric if metric is not None else (20 if mode == "prefer" else 150)

    if s == "windows":
        if mode == "restore":
            ps_cmd = (
                f"Set-NetIPInterface -InterfaceIndex {hit.index} -AddressFamily IPv4 -AutomaticMetric Enabled; "
                f"Set-NetIPInterface -InterfaceIndex {hit.index} -AddressFamily IPv6 -AutomaticMetric Enabled -ErrorAction SilentlyContinue"
            )
            desc = f"Restoring '{hit.alias}' (ifIndex {hit.index}) to automatic route metric"
        else:
            ps_cmd = (
                f"Set-NetIPInterface -InterfaceIndex {hit.index} -AddressFamily IPv4 -InterfaceMetric {target_metric}; "
                f"Set-NetIPInterface -InterfaceIndex {hit.index} -AddressFamily IPv6 -InterfaceMetric {target_metric} -ErrorAction SilentlyContinue"
            )
            desc = f"Setting '{hit.alias}' (ifIndex {hit.index}) metric to {target_metric} ({mode})"
        if not apply_it:
            return {"ok": True, "exit": 0, "status": "plan", "command": ps_cmd, "verdict": desc}
        try:
            admin = bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:  # noqa: BLE001
            admin = False
        if admin:
            run_ps(ps_cmd)
            return {"ok": True, "exit": 0, "verdict": f"APPLIED: {desc}"}
        rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", "powershell.exe", f'-NoProfile -Command "{ps_cmd}"', None, 1)
        if rc <= 32:
            return {"ok": False, "exit": 2, "verdict": f"UAC declined or failed (code {rc}); nothing changed"}
        return {"ok": True, "exit": 0, "verdict": f"OPENED ELEVATED POWERSHELL: {desc} (undo: fieldkit network restore-route {json.dumps(hit.alias)})"}

    if s == "linux":
        nm = find_tool("nmcli")
        if not nm:
            return {"ok": False, "exit": 3, "verdict": "nmcli not found on Linux"}
        _, dtxt, _ = run_cmd([nm, "-t", "-f", "DEVICE,CONNECTION", "dev", "status"])
        conn = next((l.split(":", 1)[1] for l in dtxt.splitlines() if l.startswith(hit.alias + ":") and l.split(":", 1)[1] not in ("", "--")), "")
        if not conn:
            return {"ok": False, "exit": 2, "verdict": f"{hit.alias} has no active NetworkManager connection"}
        val = "-1" if mode == "restore" else str(target_metric)
        cmds = [[nm, "connection", "modify", conn, "ipv4.route-metric", val], [nm, "connection", "up", conn]]
        if not apply_it:
            return {"ok": True, "exit": 0, "status": "plan", "commands": cmds, "verdict": f"PLAN: set {conn} ipv4.route-metric={val}"}
        for c in cmds:
            rc, out, err = run_cmd(c, timeout=40)
            if rc != 0:
                return {"ok": False, "exit": 3, "verdict": f"FAILED {' '.join(c)}: {(err or out).strip()[:200]}"}
        return {"ok": True, "exit": 0, "verdict": f"APPLIED: {hit.alias} ({conn}) ipv4.route-metric={val}"}

    if s == "darwin":
        ns = find_tool("networksetup")
        if not ns:
            return {"ok": False, "exit": 3, "verdict": "networksetup not found on macOS"}
        _, stxt, _ = run_cmd([ns, "-listallnetworkservices"])
        services = [l.strip().lstrip("*").strip() for l in stxt.splitlines()[1:] if l.strip()]
        svc = hit.description if hit.description in services else next((x for x in services if hit.description.lower() in x.lower()), "")
        if not svc or mode == "restore":
            return {"ok": True, "exit": 0, "verdict": f"macOS service order: use `networksetup -ordernetworkservices` (services: {', '.join(services)})"}
        ordered = ([svc] + [x for x in services if x != svc]) if mode == "prefer" else ([x for x in services if x != svc] + [svc])
        if not apply_it:
            return {"ok": True, "exit": 0, "status": "plan", "command": [ns, "-ordernetworkservices", *ordered], "verdict": f"PLAN: reorder macOS services -> {', '.join(ordered)}"}
        rc, out, err = run_cmd([ns, "-ordernetworkservices", *ordered])
        return {
            "ok": rc == 0,
            "exit": 0 if rc == 0 else 3,
            "verdict": f"APPLIED macOS service order: {', '.join(ordered)}" if rc == 0 else f"FAILED: {(err or out).strip()[:200]}",
        }

    return {"ok": False, "exit": 3, "verdict": f"unsupported platform {s}"}


def format_routes(ifaces: list[Iface]) -> list[str]:
    code, v, nxt = routes_verdict(ifaces)
    lines = ["INTERFACES"]
    for i in ifaces:
        if i.status == "Not Present":
            continue
        head = f"  [{i.index:>2}] {i.alias:<16} {i.status:<12} {i.speed:<10}"
        if i.ipv4:
            head += f" {i.ipv4:<15}"
            if i.has_default_route:
                pin = "" if i.automatic_metric in (True, None) else " (pinned)"
                head += f" gw {i.gateway} metric {i.metric}{pin}"
        lines.append(head.rstrip())
        if i.internet is not None:
            lines.append(
                f"       internet: {'YES (' + i.internet_detail + ')' if i.internet else 'NO (' + i.internet_detail + ')'}"
                f"; gw ping {str(i.gateway_ping_ms) + ' ms' if i.gateway_ping_ms is not None else 'none'}"
                f"{' mac ' + i.gateway_mac if i.gateway_mac else ''}"
                f"; DNS {'ok' if i.dns_ok else 'FAIL' if i.dns_ok is False else '-'}"
            )
    lines.append("")
    lines.append("VERDICT")
    for l in v:
        lines.append("  " + l)
    for n in nxt:
        lines.append("NEXT: " + n)
    if code == 0 and not nxt:
        lines.append("NEXT: nothing; internet works via the winning route")
    return lines
