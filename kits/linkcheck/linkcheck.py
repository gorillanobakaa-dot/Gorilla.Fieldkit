"""linkcheck: a direct cable between two computers - set it up, then measure it. Windows and Linux, standard library only.

WHY THIS EXISTS
On 2026-10-22 two machines (a Windows laptop and a Linux box) were joined by one Cat8 cable, no router, so that one
long job could be shared between them. Whether that is worth it is a number, not a feeling: how fast the cable really
carries data each way, how long a round trip takes, how much that wobbles, and whether power saving on the network
card (Energy Efficient Ethernet) costs latency. This tool measures those, the same way every time, with its own
sender and receiver (no iperf3 download: a door and a dependency avoided).

COMMANDS
  setup --ip ADDR/PREFIX --iface NAME [--undo]
            Put a private address on the cable's network card: no gateway, no DNS, no internet sharing, so nothing on
            the cable can reach the internet and the cable adds no route for other traffic. Idempotent: an address
            already in place is NOTHING TO DO. Windows: one UAC prompt through Fieldkit's elevate kit, the result read
            back from Windows. Linux: one NetworkManager profile (ipv4.never-default yes), root through pkexec only
            when refused. --undo takes it off again (Windows: DHCP back on; Linux: the profile deleted).
  serve --bind ADDR [--allow PEER] [--port N] [--idle S] [--once]
            The other end: a TCP sink/source and a UDP echo, bound to ONE address (never 0.0.0.0), answering only the
            allowed peer, stopping after --idle seconds with no client (default 600). Run it in a terminal; Ctrl-C
            stops it. Nothing is installed and nothing starts at boot.
  check PEER [--count N] [--seconds S] [--iface NAME] [--port N]
            Negotiated speed and duplex, Energy Efficient Ethernet state, round trip (p50, p99, jitter, loss) over
            --count UDP echoes, TCP throughput up and down (--seconds each), and the largest packet that passes
            unfragmented (MTU, by ping with don't-fragment). One line: LINK: ... or LINK: FAIL <why>, then VERDICT
            and NEXT. The measurement is kept as JSON under the results folder.
  status [PEER] [--iface NAME]   the card's addresses, speed, duplex, EEE now (read-only)
  doctor                  what this machine has for the tool

Exit codes: 0 fine, 1 error, 2 bad usage or a person must act, 3 findings (a failed link, a failed setup).
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
import shutil
import socket
import statistics
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

VERSION = "linkcheck 1.0.0"
PORT = 47077
CHUNK = 1 << 20                                   # 1 MiB per send: enough to fill a gigabit link from Python
MAX_SECONDS = 60                                  # a throughput leg never runs longer than this
ECHO_FMT = "!IQ"                                  # sequence number, sender's perf_counter_ns
ROOT = Path(__file__).resolve().parents[2]        # the Fieldkit folder (kits/linkcheck/linkcheck.py)
ELEVATE = os.environ.get("LINKCHECK_ELEVATE")     # default: <Fieldkit>/local/kits/Elevate/elevate.py
RESULTS = os.environ.get("LINKCHECK_RESULTS")     # default: <Fieldkit>/state/linkcheck


def _paths():
    return (Path(ELEVATE) if ELEVATE else ROOT / "local" / "kits" / "Elevate" / "elevate.py",
            Path(RESULTS) if RESULTS else ROOT / "state" / "linkcheck")


# ---- reading the network card --------------------------------------------------------------------------------------
def speed_mbps(text) -> int | None:
    """'1 Gbps' / '100 Mbps' / '2.5 Gbps' / '1000' (Linux sysfs, Mb/s) / '1000Mb/s' (ethtool) -> Mbit/s."""
    if text is None:
        return None
    s = str(text).strip().lower().replace(" ", "")
    for unit, mult in (("gbps", 1000), ("mbps", 1), ("mb/s", 1), ("kbps", 0.001)):
        if s.endswith(unit):
            try:
                return int(float(s[: -len(unit)]) * mult)
            except ValueError:
                return None
    try:
        v = int(s)
    except ValueError:
        return None
    return v if v > 0 else None                    # sysfs says -1 when the link is down


def parse_ethtool(text: str) -> dict:
    """`ethtool IFACE` -> {speed_mbps, duplex, link}."""
    out = {"speed_mbps": None, "duplex": None, "link": None}
    for line in text.splitlines():
        k, _, v = line.strip().partition(":")
        k, v = k.strip(), v.strip()
        if k == "Speed":
            out["speed_mbps"] = speed_mbps(v)
        elif k == "Duplex":
            out["duplex"] = v.lower() if v.lower() in ("full", "half") else None
        elif k == "Link detected":
            out["link"] = v == "yes"
    return out


def parse_ethtool_eee(text: str) -> str:
    """`ethtool --show-eee IFACE` -> 'on' (active), 'enabled-inactive', 'off', 'unsupported' or 'unknown'."""
    for line in text.splitlines():
        k, _, v = line.strip().partition(":")
        if k.strip() == "EEE status":
            v = v.strip().lower()
            if v.startswith("not supported"):
                return "unsupported"
            if v.startswith("disabled"):
                return "off"
            if v.startswith("enabled"):
                return "on" if "inactive" not in v else "enabled-inactive"
    return "unknown"


def parse_win_eee(props: list[dict]) -> str:
    """Get-NetAdapterAdvancedProperty rows -> 'on' / 'off' / 'unsupported'. Intel calls it 'Energy Efficient
    Ethernet' (EEELinkAdvertisement); Realtek 'Energy-Efficient Ethernet' (*EEE). Matched on the keyword first."""
    for p in props:
        kw = (p.get("RegistryKeyword") or "").lower()
        name = (p.get("DisplayName") or "").lower().replace("-", " ")
        if kw in ("*eee", "eeelinkadvertisement", "eee") or name == "energy efficient ethernet":
            v = (p.get("DisplayValue") or "").strip().lower()
            if v in ("on", "enabled"):
                return "on"
            if v in ("off", "disabled"):
                return "off"
            return v or "unknown"
    return "unsupported"


def _ps(script: str, timeout: int = 60) -> str:
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                        "$ProgressPreference='SilentlyContinue'; " + script],
                       capture_output=True, text=True, timeout=timeout)
    return r.stdout


def _json(text: str):
    text = (text or "").strip()
    if not text:
        return []
    v = json.loads(text)
    return v if isinstance(v, list) else [v]


def iface_for(peer: str, run=subprocess.run) -> str | None:
    """The interface the system would send to PEER through (the route's own answer, not a guess)."""
    if sys.platform == "win32":
        rows = _json(_ps(f"Find-NetRoute -RemoteIPAddress '{peer}' -ErrorAction SilentlyContinue | "
                         "Select-Object -First 1 InterfaceAlias | ConvertTo-Json -Compress"))
        return rows[0].get("InterfaceAlias") if rows else None
    r = run(["ip", "-j", "route", "get", peer], capture_output=True, text=True)
    try:
        return (json.loads(r.stdout) or [{}])[0].get("dev")
    except (ValueError, IndexError):
        return None


def card(iface: str, run=subprocess.run) -> dict:
    """{iface, status, speed_mbps, duplex, eee, mtu, addresses:[{ip,prefix,origin}], dhcp} for one interface."""
    if sys.platform == "win32":
        q = iface.replace("'", "''")
        a = _json(_ps(f"Get-NetAdapter -Name '{q}' -ErrorAction SilentlyContinue | Select-Object Status,LinkSpeed,"
                      "FullDuplex,MtuSize,PhysicalMediaType | ConvertTo-Json -Compress"))
        if not a:
            return {"iface": iface, "status": "missing"}
        a = a[0]
        props = _json(_ps(f"Get-NetAdapterAdvancedProperty -Name '{q}' -ErrorAction SilentlyContinue | "
                          "Select-Object DisplayName,DisplayValue,RegistryKeyword | ConvertTo-Json -Compress"))
        addrs = _json(_ps(f"Get-NetIPAddress -InterfaceAlias '{q}' -AddressFamily IPv4 -ErrorAction SilentlyContinue"
                          " | Select-Object IPAddress,PrefixLength,PrefixOrigin | ConvertTo-Json -Compress"))
        ipif = _json(_ps(f"Get-NetIPInterface -InterfaceAlias '{q}' -AddressFamily IPv4 -ErrorAction "
                         "SilentlyContinue | Select-Object Dhcp | ConvertTo-Json -Compress"))
        gw = _json(_ps(f"Get-NetRoute -InterfaceAlias '{q}' -DestinationPrefix '0.0.0.0/0' -ErrorAction "
                       "SilentlyContinue | Select-Object NextHop | ConvertTo-Json -Compress"))
        dns = _json(_ps(f"(Get-DnsClientServerAddress -InterfaceAlias '{q}' -AddressFamily IPv4 -ErrorAction "
                        "SilentlyContinue).ServerAddresses | ConvertTo-Json -Compress"))
        origin = {1: "manual", 2: "wellknown", 3: "dhcp", 4: "routeradvertisement", 0: "other"}
        return {"iface": iface, "status": a.get("Status"), "speed_mbps": speed_mbps(a.get("LinkSpeed")),
                "duplex": "full" if a.get("FullDuplex") else "half", "eee": parse_win_eee(props),
                "mtu": a.get("MtuSize"), "wireless": "802.11" in str(a.get("PhysicalMediaType") or ""),
                "addresses": [{"ip": x["IPAddress"], "prefix": x["PrefixLength"],
                               "origin": origin.get(x.get("PrefixOrigin"), str(x.get("PrefixOrigin")))} for x in addrs],
                "dhcp": bool(ipif and ipif[0].get("Dhcp") == 1),
                "gateways": [g.get("NextHop") for g in gw], "dns": [d for d in dns if isinstance(d, str)]}
    base = Path("/sys/class/net") / iface
    if not base.is_dir():
        return {"iface": iface, "status": "missing"}

    def rd(name):
        try:
            return (base / name).read_text().strip()
        except OSError:
            return None
    out = {"iface": iface, "status": "Up" if rd("operstate") == "up" else (rd("operstate") or "unknown"),
           "speed_mbps": speed_mbps(rd("speed")), "duplex": rd("duplex"), "mtu": int(rd("mtu") or 0) or None,
           "eee": "unknown (ethtool not installed)", "wireless": (base / "wireless").is_dir(),
           "addresses": [], "gateways": [], "dns": []}
    if shutil.which("ethtool"):
        r = run(["ethtool", "--show-eee", iface], capture_output=True, text=True)
        out["eee"] = parse_ethtool_eee(r.stdout) if r.returncode == 0 else "unknown"
    r = run(["ip", "-j", "-4", "addr", "show", "dev", iface], capture_output=True, text=True)
    try:
        for a in (json.loads(r.stdout) or [{}])[0].get("addr_info", []):
            out["addresses"].append({"ip": a.get("local"), "prefix": a.get("prefixlen"),
                                     "origin": "dhcp" if a.get("dynamic") else "manual"})
    except (ValueError, IndexError):
        pass
    r = run(["ip", "-j", "-4", "route", "show", "default", "dev", iface], capture_output=True, text=True)
    try:
        out["gateways"] = [x.get("gateway") for x in json.loads(r.stdout or "[]")]
    except ValueError:
        pass
    return out


# ---- setup: the private address on the cable --------------------------------------------------------------------------
def parse_cidr(text: str) -> tuple[str, int]:
    iface = ipaddress.ip_interface(text)
    if iface.version != 4:
        raise ValueError("IPv4 only")
    if not iface.ip.is_private:
        raise ValueError(f"{iface.ip} is not a private address: the cable must not carry a routable one")
    return str(iface.ip), iface.network.prefixlen


def plan_windows_setup(c: dict, ip: str, prefix: int, undo: bool = False) -> list[str]:
    """PowerShell lines that bring the card to the wanted state. [] = already there (idempotent)."""
    q = "'" + c["iface"].replace("'", "''") + "'"
    have = {(a["ip"], a["prefix"]) for a in c.get("addresses", [])}
    if undo:
        if (ip, prefix) not in have:
            return []
        return [f"Remove-NetIPAddress -InterfaceAlias {q} -IPAddress '{ip}' -Confirm:$false",
                f"Set-NetIPInterface -InterfaceAlias {q} -AddressFamily IPv4 -Dhcp Enabled"]
    lines = []
    if c.get("dhcp"):
        lines.append(f"Set-NetIPInterface -InterfaceAlias {q} -AddressFamily IPv4 -Dhcp Disabled")
    if (ip, prefix) not in have:
        stale = [a["ip"] for a in c.get("addresses", []) if a["ip"] == ip]           # same address, wrong prefix
        lines += [f"Remove-NetIPAddress -InterfaceAlias {q} -IPAddress '{s}' -Confirm:$false" for s in stale]
        lines.append(f"New-NetIPAddress -InterfaceAlias {q} -IPAddress '{ip}' -PrefixLength {prefix} | Out-Null")
    for g in c.get("gateways") or []:                                               # never a gateway on the cable
        lines.append(f"Remove-NetRoute -InterfaceAlias {q} -DestinationPrefix '0.0.0.0/0' -NextHop '{g}' -Confirm:$false")
    if c.get("dns"):
        lines.append(f"Set-DnsClientServerAddress -InterfaceAlias {q} -ResetServerAddresses")
    if lines:
        lines.append(f"Set-DnsClient -InterfaceAlias {q} -RegisterThisConnectionsAddress $false")
    return lines


def setup_script(iface: str, lines: list[str]) -> str:
    """The elevated script: fail closed, one marker line per outcome."""
    body = "; ".join(lines)
    return ("$ErrorActionPreference = 'Stop'\n"
            f"try {{ {body}; Write-Host 'LINKCHECK: SET {iface}' }} catch {{ Write-Host \"LINKCHECK: FAILED {iface}: $_\" }}\n")


def setup_problems(c: dict, ip: str, prefix: int, undo: bool = False) -> list[str]:
    """Read back: what is still wrong with the card (empty = as wanted)."""
    have = {(a["ip"], a["prefix"]) for a in c.get("addresses", [])}
    if undo:
        return [f"{ip}/{prefix} is still on {c['iface']}"] if (ip, prefix) in have else []
    out = []
    if (ip, prefix) not in have:
        out.append(f"{ip}/{prefix} is not on {c['iface']} (has: {', '.join(f'{a[0]}/{a[1]}' for a in have) or 'none'})")
    if c.get("gateways"):
        out.append(f"a default gateway is on the cable: {', '.join(map(str, c['gateways']))}")
    if c.get("dns"):
        out.append(f"DNS servers are set on the cable: {', '.join(c['dns'])}")
    return out


def run_elevated(script_text: str, run=subprocess.run, timeout_min: float = 10) -> tuple[int, str]:
    """Write the script, start it through the elevate kit (one UAC prompt), wait for its transcript. -> (rc, log)."""
    elevate_py, results = _paths()
    results.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    sp, log = results / f"setup-{stamp}.ps1", results / f"setup-{stamp}.log"
    sp.write_text(script_text, encoding="utf-8-sig")
    if not elevate_py.is_file():
        return 2, f"no elevate kit at {elevate_py}: run {sp} in an administrator PowerShell, then setup again"
    r = run([sys.executable, str(elevate_py), "run", str(sp), "--log", str(log), "--close"], capture_output=True, text=True)
    if r.returncode != 0:
        return 2, (r.stdout + r.stderr).strip()
    w = run([sys.executable, str(elevate_py), "wait", str(log), "--marker", "ELEVATE: script finished",
             "--timeout", str(timeout_min)], capture_output=True, text=True)
    text = log.read_text(encoding="utf-8", errors="replace") if log.is_file() else (w.stdout or "")
    return (0 if w.returncode == 0 else 3), text


def linux_setup_cmds(iface: str, ip: str, prefix: int, undo: bool = False) -> list[list[str]]:
    name = f"linkcheck-{iface}"
    if undo:
        return [["nmcli", "connection", "delete", name]]
    return [["nmcli", "connection", "add", "type", "ethernet", "ifname", iface, "con-name", name,
             "ipv4.method", "manual", "ipv4.addresses", f"{ip}/{prefix}", "ipv4.never-default", "yes",
             "ipv4.ignore-auto-dns", "yes", "ipv4.dns", "", "ipv6.method", "link-local",
             "connection.autoconnect", "yes", "connection.autoconnect-priority", "50"],
            ["nmcli", "connection", "up", name]]


def setup(ip_cidr: str, iface: str, undo=False, read=card, run=subprocess.run, elevated=run_elevated,
          sleep=time.sleep) -> int:
    try:
        ip, prefix = parse_cidr(ip_cidr)
    except ValueError as e:
        print(f"VERDICT  REFUSED: {e}")
        return 2
    c = read(iface)
    if c.get("status") == "missing":
        print(f"VERDICT  NO SUCH CARD: {iface}\nNEXT     python linkcheck.py status   (lists the card the route uses)")
        return 2
    if sys.platform == "win32":
        lines = plan_windows_setup(c, ip, prefix, undo)
        if not lines:
            print(f"VERDICT  NOTHING TO DO: {iface} {'does not have' if undo else 'already has'} {ip}/{prefix}"
                  f"{'' if undo else ', no gateway, no DNS'}")
            print("NEXT     " + ("done" if undo else "python linkcheck.py check PEER   (with serve running on the peer)"))
            return 0
        print("admin step (one UAC prompt):\n  " + "\n  ".join(lines))
        rc, log = elevated(setup_script(iface, lines))
        if rc != 0 or "LINKCHECK: SET" not in log:
            print(f"VERDICT  FAILED: the admin step did not finish: {log.strip()[-400:]}")
            print("NEXT     read the log above; nothing else was changed")
            return 3
    else:
        prof = f"linkcheck-{iface}"
        have = run(["nmcli", "-t", "-f", "NAME", "connection", "show"], capture_output=True, text=True).stdout.split()
        wrong = setup_problems(c, ip, prefix, undo)
        if undo and prof not in have:
            if wrong:
                print(f"VERDICT  NOT MINE: {ip}/{prefix} on {iface} was not set by linkcheck (no profile {prof}); left alone")
                return 3
            print(f"VERDICT  NOTHING TO DO: {iface} does not have {ip}/{prefix}")
            return 0
        if not undo and not wrong:
            print(f"VERDICT  NOTHING TO DO: {iface} already has {ip}/{prefix}, no gateway, no DNS")
            print(f"NEXT     python3 linkcheck.py serve --bind {ip}")
            return 0
        cmds = linux_setup_cmds(iface, ip, prefix, undo)
        if not undo and prof in have:                    # our profile, but the card is not as wanted: rebuild it
            cmds = [["nmcli", "connection", "delete", prof]] + cmds
        for cmd in cmds:
            r = run(cmd, capture_output=True, text=True)
            if r.returncode != 0 and shutil.which("pkexec"):
                r = run(["pkexec", *cmd], capture_output=True, text=True)
            if r.returncode != 0:
                print(f"VERDICT  FAILED: {' '.join(cmd)}: {(r.stderr or r.stdout).strip()[:300]}")
                return 3
    bad = []
    for _ in range(15):                                  # Windows applies the address within a few seconds
        bad = setup_problems(read(iface), ip, prefix, undo)
        if not bad:
            break
        sleep(1)
    if bad:
        print("VERDICT  FAILED (read back): " + "; ".join(bad))
        return 3
    print(f"VERDICT  {'REMOVED' if undo else 'SET'}: {iface} {'no longer has' if undo else 'has'} {ip}/{prefix}"
          f"{'' if undo else ', no gateway, no DNS'} (read back)")
    print("NEXT     " + ("done" if undo else "prove the internet route did not move, then: python linkcheck.py check PEER"))
    return 0


# ---- serve: the other end ---------------------------------------------------------------------------------------------
def check_bind(bind: str) -> str | None:
    """None when BIND is a single concrete address; else why it is refused."""
    try:
        a = ipaddress.ip_address(bind)
    except ValueError:
        return f"--bind needs an address, not {bind!r}"
    if a.is_unspecified:
        return "--bind 0.0.0.0 listens on every network (Wi-Fi included): name the cable's own address"
    return None


class Server:
    """TCP sink/source + UDP echo on one address. Only `allow` is answered (None = anyone reaching that address)."""

    def __init__(self, bind: str, port: int = PORT, allow: str | None = None, idle: float = 600, once=False, say=print):
        self.bind, self.port, self.allow, self.idle, self.once, self.say = bind, port, allow, idle, once, say
        self.last = time.monotonic()
        self.stop = threading.Event()
        # port 0: UDP picks first - its ephemeral port honours UDP's excluded ranges, which a free TCP port did not
        # (WinError 10013 twenty times in a row on windows-latest, PR 297); TCP may still find it taken, so retry
        for attempt in range(20 if port == 0 else 1):
            self.tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                self.udp.bind((bind, port))
                self.port = self.udp.getsockname()[1]
                self.tcp.bind((bind, self.port))
                break
            except OSError:
                self.tcp.close()
                self.udp.close()
                if port != 0 or attempt == 19:
                    raise
        self.tcp.listen(4)
        self.tcp.settimeout(0.5)
        self.udp.settimeout(0.5)

    def allowed(self, addr) -> bool:
        return self.allow is None or addr[0] == self.allow

    def _echo(self):
        while not self.stop.is_set():
            try:
                data, addr = self.udp.recvfrom(2048)
            except (socket.timeout, OSError):
                continue
            if self.allowed(addr) and len(data) == struct.calcsize(ECHO_FMT):      # same size back: no amplification
                self.udp.sendto(data, addr)
                self.last = time.monotonic()

    def _session(self, conn):
        conn.settimeout(MAX_SECONDS + 10)
        f = conn.makefile("rb")
        req = json.loads(f.readline() or b"{}")
        secs = min(float(req.get("seconds", 5)), MAX_SECONDS)
        if req.get("op") == "up":                       # the client sends; we count
            n, t0 = 0, None
            while True:
                b = f.read1(CHUNK) if hasattr(f, "read1") else f.read(CHUNK)
                if not b:
                    break
                if t0 is None:
                    t0 = time.perf_counter()
                n += len(b)
            dt = time.perf_counter() - (t0 or time.perf_counter())
            conn.sendall((json.dumps({"bytes": n, "seconds": dt}) + "\n").encode())
        elif req.get("op") == "down":                   # we send; the client counts
            buf = memoryview(os.urandom(CHUNK))
            end = time.perf_counter() + secs
            while time.perf_counter() < end:
                conn.sendall(buf)
            conn.shutdown(socket.SHUT_WR)
            f.read()
        conn.close()

    def serve(self) -> int:
        threading.Thread(target=self._echo, daemon=True).start()
        self.say(f"SERVING  {self.bind}:{self.port} tcp+udp, answering {self.allow or 'anyone reaching this address'};"
                 f" stops after {self.idle:.0f} s idle" + (" or one client" if self.once else "") + " (Ctrl-C now)")
        served = 0
        try:
            while not self.stop.is_set():
                if time.monotonic() - self.last > self.idle:
                    self.say(f"STOPPED  idle {self.idle:.0f} s")
                    break
                try:
                    conn, addr = self.tcp.accept()
                except socket.timeout:
                    continue
                if not self.allowed(addr):
                    self.say(f"REFUSED  {addr[0]} (only {self.allow})")
                    conn.close()
                    continue
                self.last = time.monotonic()
                try:
                    self._session(conn)
                except (OSError, ValueError) as e:
                    self.say(f"session from {addr[0]} ended: {e}")
                self.last = time.monotonic()
                served += 1
                if self.once and served >= 2:           # one check = one up + one down session
                    self.say("STOPPED  one client served")
                    break
        except KeyboardInterrupt:
            self.say("STOPPED  Ctrl-C")
        finally:
            self.stop.set()
            self.tcp.close()
            self.udp.close()
        return 0


# ---- check: the measurements ------------------------------------------------------------------------------------------
def rtt_stats(rtts_ms: list[float], sent: int) -> dict:
    """p50, p99, jitter (mean change between consecutive round trips, as RFC 3550 smooths it), loss."""
    if not rtts_ms:
        return {"sent": sent, "received": 0, "loss_pct": 100.0, "p50_ms": None, "p99_ms": None, "jitter_ms": None}
    s = sorted(rtts_ms)
    p99 = s[min(len(s) - 1, int(round(0.99 * (len(s) - 1))))]
    jit = statistics.fmean(abs(b - a) for a, b in zip(rtts_ms, rtts_ms[1:])) if len(rtts_ms) > 1 else 0.0
    return {"sent": sent, "received": len(rtts_ms), "loss_pct": round(100.0 * (sent - len(rtts_ms)) / sent, 2),
            "p50_ms": round(statistics.median(s), 3), "p99_ms": round(p99, 3), "min_ms": round(s[0], 3),
            "max_ms": round(s[-1], 3), "jitter_ms": round(jit, 3)}


def rtt(peer: str, port: int = PORT, count: int = 1000, timeout: float = 0.5, gap: float = 0.001) -> dict:
    """COUNT UDP echoes, one outstanding at a time: the round trip a program sees (both stacks included)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    s.connect((peer, port))
    out = []
    for i in range(count):
        t0 = time.perf_counter_ns()
        try:
            s.send(struct.pack(ECHO_FMT, i, t0))
            while True:
                seq, ts = struct.unpack(ECHO_FMT, s.recv(64))
                if seq == i and ts == t0:
                    out.append((time.perf_counter_ns() - t0) / 1e6)
                    break
        except (socket.timeout, ConnectionError, OSError, struct.error):
            pass
        if gap:
            time.sleep(gap)
    s.close()
    return rtt_stats(out, count)


def throughput(peer: str, op: str, port: int = PORT, seconds: float = 5) -> dict:
    """One TCP leg. 'up' = this machine sends (the peer counts); 'down' = the peer sends (we count). Measured by the
    RECEIVER, from its first byte to the end of the stream."""
    seconds = min(seconds, MAX_SECONDS)
    c = socket.create_connection((peer, port), timeout=10)
    c.settimeout(MAX_SECONDS + 10)
    c.sendall((json.dumps({"op": op, "seconds": seconds}) + "\n").encode())
    if op == "up":
        buf = memoryview(os.urandom(CHUNK))
        end = time.perf_counter() + seconds
        while time.perf_counter() < end:
            c.sendall(buf)
        c.shutdown(socket.SHUT_WR)
        reply = json.loads(c.makefile("rb").readline() or b"{}")
        n, dt = reply.get("bytes", 0), reply.get("seconds", 0)
    else:
        n, t0 = 0, None
        while True:
            b = c.recv(CHUNK)
            if not b:
                break
            if t0 is None:
                t0 = time.perf_counter()
            n += len(b)
        dt = time.perf_counter() - (t0 or time.perf_counter())
    c.close()
    return {"op": op, "bytes": n, "seconds": round(dt, 3), "mbit_s": round(n * 8 / dt / 1e6, 1) if dt > 0 else None}


def ping_ok(peer: str, payload: int, run=subprocess.run) -> bool:
    """One ping of PAYLOAD bytes with don't-fragment set. True only on a real reply (a TTL in the answer)."""
    if sys.platform == "win32":
        cmd = ["ping", "-n", "1", "-w", "1000", "-f", "-l", str(payload), peer]
    else:
        cmd = ["ping", "-c", "1", "-W", "1", "-M", "do", "-s", str(payload), peer]
    r = run(cmd, capture_output=True, text=True)
    return r.returncode == 0 and "ttl=" in (r.stdout or "").lower()


def path_mtu(peer: str, iface_mtu: int | None, probe=ping_ok) -> dict:
    """Largest IPv4 packet that crosses unfragmented (payload + 28 bytes of IP and ICMP header)."""
    hi = (iface_mtu or 1500) - 28
    if not probe(peer, 64):
        return {"mtu": None, "why": "no ping reply at all (the peer's firewall may drop ICMP echo)"}
    if probe(peer, hi):
        return {"mtu": hi + 28, "why": "full card MTU passes"}
    lo = 64
    while lo < hi - 1:
        mid = (lo + hi) // 2
        if probe(peer, mid):
            lo = mid
        else:
            hi = mid
    return {"mtu": lo + 28, "why": f"the card says {iface_mtu}, the path passes only {lo + 28}"}


TCP_MAX = 0.941           # Ethernet at 1500 MTU carries at most 94.1 % of the line rate as TCP payload (941 of 1000)
SLOW_SHARE = 0.90         # a direction under 90 % of that maximum is SLOW
LOSS_FAIL = 1.0           # % of echoes lost


def decide(f: dict) -> tuple[int, str, str]:
    """facts -> (exit, LINK line, NEXT). Pure: the order of the checks IS the procedure."""
    c = f.get("card") or {}
    r = f.get("rtt") or {}
    up, down = (f.get("up") or {}).get("mbit_s"), (f.get("down") or {}).get("mbit_s")
    if f.get("error"):
        return 3, f"LINK: FAIL {f['error']}", "python linkcheck.py serve --bind PEER_ADDR on the peer, then check again"
    if c.get("status") not in (None, "Up"):
        return 3, f"LINK: FAIL {c.get('iface')} is {c.get('status')}", "check the cable is seated at both ends"
    if not r.get("received"):
        return 3, "LINK: FAIL no echo came back", "is `linkcheck serve` running on the peer, bound to its cable address?"
    if c.get("duplex") == "half" and not c.get("wireless"):          # Wi-Fi is half duplex by nature
        return 3, f"LINK: FAIL half duplex at {c.get('speed_mbps')} Mbit/s", "a bad cable or a forced speed setting: reseat, auto-negotiate"
    if r.get("loss_pct", 0) > LOSS_FAIL:
        return 3, f"LINK: FAIL {r['loss_pct']} % of echoes lost", "a damaged cable or connector: swap the cable and check again"
    line = (f"LINK: {up} Mbit/s up, {down} Mbit/s down, RTT p50 {r['p50_ms']} ms p99 {r['p99_ms']} ms jitter "
            f"{r['jitter_ms']} ms, loss {r['loss_pct']} %, MTU {(f.get('mtu') or {}).get('mtu')}, "
            f"{c.get('speed_mbps')} Mbit/s {c.get('duplex')} duplex, EEE {c.get('eee')}")
    line_rate = None if c.get("wireless") else c.get("speed_mbps")   # a radio's PHY rate is not a carrying capacity
    if line_rate and up is not None and down is not None and min(up, down) < SLOW_SHARE * TCP_MAX * line_rate:
        return 3, line.replace("LINK: ", "LINK: SLOW ", 1), ("one direction runs under 90 % of what the line carries: check the "
                                                            "slower machine's CPU during the run (Python sender), then the cable")
    if str(c.get("eee")).startswith("on"):
        return 0, line, "EEE is on: compare with EEE off before deciding (an admin change, the owner's call)"
    return 0, line, "record it; run once over Wi-Fi as well to compare"


def check(peer: str, port=PORT, count=1000, seconds=5.0, iface=None, as_json=False) -> int:
    f = {"peer": peer, "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "host": socket.gethostname(), "version": VERSION}
    iface = iface or iface_for(peer)
    f["card"] = card(iface) if iface else {"iface": None, "status": None}
    try:
        f["rtt"] = rtt(peer, port, count)
        if f["rtt"]["received"]:
            f["up"] = throughput(peer, "up", port, seconds)
            f["down"] = throughput(peer, "down", port, seconds)
            f["mtu"] = path_mtu(peer, f["card"].get("mtu"))
    except OSError as e:
        f["error"] = f"cannot reach {peer}:{port}: {e}"
    rc, line, nxt = decide(f)
    f["verdict"], f["exit"] = line, rc
    _, results = _paths()
    results.mkdir(parents=True, exist_ok=True)
    out = results / f"check-{peer.replace(':', '_')}-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps(f, indent=1), encoding="utf-8")
    if as_json:
        print(json.dumps(f, indent=1))
    print(line)
    print(f"VERDICT  {'OK' if rc == 0 else 'FAIL'} (kept: {out})")
    print(f"NEXT     {nxt}")
    return rc


def status(iface=None, peer=None, as_json=False) -> int:
    iface = iface or (iface_for(peer) if peer else None)
    if not iface:
        print("status needs --iface NAME or a peer address (the card its route uses)")
        return 2
    c = card(iface)
    addrs = ", ".join("%s/%s (%s)" % (a["ip"], a["prefix"], a["origin"]) for a in c.get("addresses", [])) or "none"
    print(json.dumps(c, indent=1) if as_json else
          f"{c.get('iface')}: {c.get('status')}, {c.get('speed_mbps')} Mbit/s {c.get('duplex')} duplex, EEE "
          f"{c.get('eee')}, MTU {c.get('mtu')}, addresses {addrs}, "
          f"gateway {', '.join(map(str, c.get('gateways') or [])) or 'none'}, DNS {', '.join(c.get('dns') or []) or 'none'}")
    return 0 if c.get("status") not in (None, "missing") else 2


def doctor() -> int:
    rows = [("python >= 3.8", sys.version_info >= (3, 8)), ("ping", bool(shutil.which("ping")))]
    if sys.platform == "win32":
        rows += [("powershell", bool(shutil.which("powershell"))),
                 ("elevate kit (setup only)", _paths()[0].is_file())]
    else:
        rows += [("ip (iproute2)", bool(shutil.which("ip"))), ("nmcli (setup only)", bool(shutil.which("nmcli"))),
                 ("ethtool (EEE state)", bool(shutil.which("ethtool"))), ("pkexec (setup only)", bool(shutil.which("pkexec")))]
    for name, ok in rows:
        print(f"  {'ok ' if ok else 'NO '} {name}")
    need = [n for n, ok in rows[:3] if not ok]
    print(f"VERDICT  {'READY' if not need else 'MISSING ' + ', '.join(need)}")
    return 0 if not need else 2


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["setup", "serve", "check", "status", "doctor"])
    ap.add_argument("peer", nargs="?", help="check: the other end's cable address")
    ap.add_argument("--ip", help="setup: this end's address with its prefix, e.g. 10.77.0.1/30")
    ap.add_argument("--iface", help="setup/status/check: the cable's network card (Windows: 'Ethernet'; Linux: enp0s25)")
    ap.add_argument("--undo", action="store_true", help="setup: take the address off again")
    ap.add_argument("--bind", help="serve: this end's cable address (required; never 0.0.0.0)")
    ap.add_argument("--allow", help="serve: the only peer address answered")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--idle", type=float, default=600, help="serve: stop after this many seconds with no client")
    ap.add_argument("--once", action="store_true", help="serve: stop after one check")
    ap.add_argument("--count", type=int, default=1000, help="check: round trips")
    ap.add_argument("--seconds", type=float, default=5.0, help="check: seconds per throughput leg (max 60)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--version", action="version", version=VERSION)
    a = ap.parse_args(argv)
    if sys.platform != "win32" and not sys.platform.startswith("linux"):
        print("linkcheck runs on Windows and Linux; not this platform")
        return 3
    if a.command == "doctor":
        return doctor()
    if a.command == "status":
        return status(a.iface, a.peer, a.json)
    if a.command == "setup":
        if not (a.ip and a.iface):
            print("setup needs --ip ADDR/PREFIX and --iface NAME, e.g. setup --ip 10.77.0.1/30 --iface Ethernet")
            return 2
        return setup(a.ip, a.iface, a.undo)
    if a.command == "serve":
        why = check_bind(a.bind) if a.bind else "serve needs --bind ADDR (this end's cable address)"
        if why:
            print(f"VERDICT  REFUSED: {why}")
            return 2
        return Server(a.bind, a.port, a.allow, a.idle, a.once).serve()
    if not a.peer:
        print("check needs the peer's address, e.g. check 10.77.0.2")
        return 2
    return check(a.peer, a.port, a.count, a.seconds, a.iface, a.json)


if __name__ == "__main__":
    sys.exit(main())
