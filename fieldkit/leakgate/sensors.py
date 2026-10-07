"""Windows sensors. Each scenario runs twice on a fresh profile:

  direct   the build as shipped: necko logs (nsHttp, nsHostResolver, nsSocketTransport), process tree, sockets and
           listeners sampled every 2 s, filesystem snapshot before/after (profile + Mozilla system dirs), packet
           capture (pktmon) from before start to after the post-kill watch when the shell is elevated.
  proxied  the same build with distribution/policies.json pinning mitmproxy as proxy and installing its CA: every
           HTTP(S)/WebSocket request decrypted in full; any socket to a non-loopback address is a proxy bypass.

Every observation becomes an event {sensor, scenario, mode, kind, value, port, detail}; nothing is judged here.
Only processes this run started are stopped (taskkill /T on our own PID).
"""
import ctypes
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path

NECKO = "nsHttp:3,nsHostResolver:5,nsSocketTransport:4,timestamp"
URL_RX = re.compile(r"(?:uri=|URI |spec=|BeginConnect )\[?(https?|wss?)://([^/\s\]]+)([^\s\]]*)")
RES_RX = re.compile(r"(?:Resolving host|host \[|NameLookup|resolving) \[?([a-z0-9][a-z0-9.-]*\.[a-z]{2,})\]?", re.I)
SOCK_RX = re.compile(r"nsSocketTransport::Init.*?host=([^:\s\]]+):(\d+)")
HOSTNAME = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,}$")

MITM_ADDON = r'''
import json, os, time
OUT, RAW = os.environ["LEAKGATE_MITM_OUT"], os.environ["LEAKGATE_MITM_RAW"]
def _w(rec):
    with open(OUT, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
def request(flow):
    r = flow.request
    with open(RAW, "ab") as f:
        f.write(("%s %s\n" % (r.method, r.pretty_url)).encode("utf-8", "replace"))
        f.write("\n".join("%s: %s" % kv for kv in r.headers.items()).encode("utf-8", "replace"))
        f.write(b"\n\n" + (r.raw_content or b"") + b"\n--LEAKGATE--\n")
    _w({"t": time.time(), "kind": "http", "method": r.method, "host": r.pretty_host, "port": r.port, "path": r.path[:500],
        "req_bytes": len(r.raw_content or b""), "http_version": r.http_version})
def response(flow):
    _w({"t": time.time(), "kind": "response", "host": flow.request.pretty_host, "status": flow.response.status_code,
        "resp_bytes": len(flow.response.raw_content or b"")})
def websocket_message(flow):
    m = flow.websocket.messages[-1]
    with open(RAW, "ab") as f:
        f.write(("WS %s\n" % flow.request.pretty_url).encode() + m.content + b"\n--LEAKGATE--\n")
    _w({"t": time.time(), "kind": "ws", "host": flow.request.pretty_host, "from_client": m.from_client, "bytes": len(m.content)})
def tls_failed_client(data):
    _w({"t": time.time(), "kind": "tls-failed", "host": getattr(data.context.client, "sni", None) or "?"})
_POISON = [(500, b"internal error"), (200, b""), (200, b"{not json"), (200, b'{"data": [{"id": 1, "unexpected": true}'), (403, b"forbidden"),
           (200, b"\x00" * 2_000_000)]
_n = [0]
def requestheaders(flow):
    if os.environ.get("LEAKGATE_POISON") and flow.request.pretty_host not in ("127.0.0.1", "localhost"):
        from mitmproxy import http
        code, body = _POISON[_n[0] % len(_POISON)]
        _n[0] += 1
        flow.response = http.Response.make(code, body, {"Content-Type": "application/json"})
'''


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def build_copy(zip_path, dest, policies=None):
    """The build from its hashed zip into `dest`; `policies` -> distribution/policies.json. Never the owner's install."""
    dest = Path(dest)
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    with zipfile.ZipFile(zip_path) as z:
        names = [n for n in z.namelist() if not n.endswith("/")]
        top = names[0].split("/")[0]
        for n in names:
            t = dest / n[len(top) + 1:]
            t.parent.mkdir(parents=True, exist_ok=True)
            with z.open(n) as a, open(t, "wb") as b:
                shutil.copyfileobj(a, b)
    if policies:
        (dest / "distribution").mkdir(exist_ok=True)
        (dest / "distribution" / "policies.json").write_text(json.dumps({"policies": policies}, indent=1), encoding="utf-8")
    return dest


def new_profile(root, name, prefs=None):
    """A fresh profile; `prefs` {name: value} are appended to user.js (per-scenario test prefs, scenarios.prefs_for)."""
    prof = Path(root) / f"profile-{name}"
    if prof.exists():
        shutil.rmtree(prof, ignore_errors=True)
    prof.mkdir(parents=True)
    dl = prof / "downloads"
    dl.mkdir()
    (prof / "user.js").write_text("\n".join([
        'user_pref("browser.shell.checkDefaultBrowser", false);',
        'user_pref("browser.aboutwelcome.enabled", false);',
        'user_pref("browser.download.folderList", 2);',
        'user_pref("browser.download.dir", "%s");' % str(dl).replace("\\", "\\\\"),
        'user_pref("browser.download.useDownloadDir", true);',
        'user_pref("browser.download.always_ask_before_handling_new_types", false);']
        + ['user_pref(%s, %s);' % (json.dumps(k), json.dumps(v)) for k, v in (prefs or {}).items()] + [""]), encoding="utf-8")
    return prof


# ------------------------------------------------------------------------------------------- process + sockets
_PS_SNAPSHOT = r"""
$all = Get-CimInstance Win32_Process | Select-Object ProcessId, ParentProcessId, Name, ExecutablePath, CommandLine, @{n='Created';e={ if ($_.CreationDate) { $_.CreationDate.ToFileTimeUtc() } else { 0 } }}
$all | ConvertTo-Json -Compress -Depth 2
"""


def snapshot_processes():
    out = subprocess.run(["powershell", "-NoProfile", "-Command", _PS_SNAPSHOT], capture_output=True, text=True, errors="replace").stdout
    try:
        rows = json.loads(out) if out.strip() else []
    except ValueError:
        return []
    return rows if isinstance(rows, list) else [rows]


def tree(rows, root_pid):
    kids = {}
    for r in rows:
        kids.setdefault(r.get("ParentProcessId"), []).append(r)
    out, q = [], [root_pid]
    seen = {root_pid}
    by_pid = {r["ProcessId"]: r for r in rows}
    if root_pid in by_pid:
        out.append(by_pid[root_pid])
    while q:
        parent = q.pop()
        born = (by_pid.get(parent) or {}).get("Created") or 0
        for k in kids.get(parent, []):
            # Windows reuses process ids: a process whose recorded parent id now belongs to OUR process, but which was
            # created BEFORE it, is not its child (2026-10-03: Edge's startup boost, started by Windows, was counted
            # as a child of the test browser because its dead parent's id had been given to firefox.exe).
            if born and (k.get("Created") or 0) and k["Created"] < born:
                continue
            if k["ProcessId"] not in seen:
                seen.add(k["ProcessId"])
                out.append(k)
                q.append(k["ProcessId"])
    return out


def snapshot_all_sockets():
    """-> every TCP connection and UDP endpoint on the machine with its owning process (one PowerShell call)."""
    ps = ("Get-NetTCPConnection -ErrorAction SilentlyContinue | ForEach-Object { \"tcp|$($_.State)|$($_.LocalAddress)|"
          "$($_.LocalPort)|$($_.RemoteAddress)|$($_.RemotePort)|$($_.OwningProcess)\" }; "
          "Get-NetUDPEndpoint -ErrorAction SilentlyContinue | ForEach-Object { \"udp|Bound|$($_.LocalAddress)|"
          "$($_.LocalPort)|||$($_.OwningProcess)\" }")
    out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, errors="replace").stdout
    return [tuple(l.strip().split("|")) for l in out.splitlines() if l.count("|") == 6]


class PortOwners:
    """Who held each local port, sample by sample (time, proto, port, pid). A packet capture names no process, and a
    port the browser held can be reused by another program seconds later (2026-10-06, build 27 gate: WhatsApp, which
    wakes on every incoming message, and the Claude desktop app failed NETWORK_POLICY through reused ports).
    owner(proto, port, ts) -> ("build" | "other" | None, pid): "other" only when every sample near the packet shows a
    process outside the build holding that port; no sample near it -> None, and the packet stays the browser's."""

    def __init__(self, slack=3.0):
        self.samples, self.slack, self.build = [], slack, set()

    def add(self, t, rows, build_pids):
        self.build |= set(build_pids)
        for r in rows:
            if r[3].isdigit() and r[6].isdigit():
                self.samples.append((t, r[0], int(r[3]), int(r[6])))

    def owner(self, proto, port, ts):
        near = [pid for t, pr, po, pid in self.samples if pr == proto and po == port and abs(t - ts) <= self.slack]
        if not near:
            return None, None
        if any(pid in self.build for pid in near):
            return "build", next(pid for pid in near if pid in self.build)
        return "other", near[0]


def snapshot_sockets(pids):
    if not pids:
        return []
    ids = ",".join(str(p) for p in pids)
    ps = (f"$ids=@({ids}); "
          "Get-NetTCPConnection -ErrorAction SilentlyContinue | Where-Object { $ids -contains $_.OwningProcess } | "
          "ForEach-Object { \"tcp|$($_.State)|$($_.LocalAddress)|$($_.LocalPort)|$($_.RemoteAddress)|$($_.RemotePort)|$($_.OwningProcess)\" }; "
          "Get-NetUDPEndpoint -ErrorAction SilentlyContinue | Where-Object { $ids -contains $_.OwningProcess } | "
          "ForEach-Object { \"udp|Bound|$($_.LocalAddress)|$($_.LocalPort)|||$($_.OwningProcess)\" }")
    out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, errors="replace").stdout
    return [tuple(l.strip().split("|")) for l in out.splitlines() if l.count("|") == 6]


class Sampler(threading.Thread):
    """Process tree + sockets every `interval` s until stopped; then the post-kill watch looks for any process whose
    executable lives in the build directory (a relaunch, an updater, a crash helper)."""

    def __init__(self, root_pid, interval=2.0, build_dir=None):
        super().__init__(daemon=True)
        self.root, self.interval, self.stop_flag = root_pid, interval, threading.Event()
        self.build_dir = str(build_dir).lower() if build_dir else None
        self.procs, self.socks, self.timeline = {}, set(), []
        self.owners = PortOwners()

    def run(self):
        t0 = time.time()
        while not self.stop_flag.is_set():
            snap = snapshot_processes()
            rows = tree(snap, self.root)
            # 2026-10-05 (build 27 gate): started from an ELEVATED shell, Firefox's launcher process starts the browser
            # de-elevated through the shell, so the real browser is not a child of the process we started; the
            # tree walk never saw it, WM_CLOSE never reached its window and it ran on. Every process running from
            # this scene's own build copy is the browser's.
            if self.build_dir:
                have = {r["ProcessId"] for r in rows}
                rows += [r for r in snap if r["ProcessId"] not in have
                         and (r.get("ExecutablePath") or "").lower().startswith(self.build_dir)]
            for r in rows:
                self.procs.setdefault(r["ProcessId"], r)
            pids = {r["ProcessId"] for r in rows}
            every = snapshot_all_sockets()
            self.owners.add(time.time(), every, pids)
            s = [x for x in every if x[6].isdigit() and int(x[6]) in pids]
            self.socks.update(s)
            self.timeline.append({"t": round(time.time() - t0, 1), "processes": len(rows), "sockets": len(s),
                                  "remote": sorted({f"{x[4]}:{x[5]}" for x in s if x[0] == "tcp" and x[4] not in ("", "0.0.0.0", "::", "127.0.0.1", "::1")})})
            self.stop_flag.wait(self.interval)


def build_pids(build_dir):
    """-> ids of the processes running from this build copy (each scene and mode has its own copy)."""
    bd = str(Path(build_dir)).lower()
    return [r["ProcessId"] for r in snapshot_processes() if (r.get("ExecutablePath") or "").lower().startswith(bd)]


def watch_after_kill(build_dir, seconds, killed_at=None):
    """-> processes from build_dir that outlive the browser: still running at the END of the `seconds` window, or
    started after the kill (a relaunch or a detached task). 2026-10-04 (build 26 gate): every process merely SEEN in the
    window counted, and taskkill returns before Windows has finished removing the processes it ends, so a process in
    the middle of dying read as "alive after shutdown" (8 times in one run). Each returned row says why it counts."""
    bd = str(Path(build_dir)).lower()
    killed_ft = int(((killed_at or time.time()) + 11644473600) * 10_000_000)      # Windows FILETIME of the kill
    seen, last = {}, {}
    t0 = time.time()
    while True:
        last = {}
        for r in snapshot_processes():
            if (r.get("ExecutablePath") or "").lower().startswith(bd):
                seen.setdefault(r["ProcessId"], r)
                last[r["ProcessId"]] = r
        if time.time() - t0 >= seconds:
            break
        time.sleep(2)
    out = []
    for pid, r in seen.items():
        if pid in last:
            out.append({**r, "why": f"still running {seconds} s after the browser was closed"})
        elif (r.get("Created") or 0) > killed_ft:
            out.append({**r, "why": "started after the browser was closed"})
    return out


# ------------------------------------------------------------------------------------------- DNS witness
# Windows' own DNS-client log: every query with the process that made it (event 3006, logged inside the caller).
# 2026-10-04: one lookup of incoming.telemetry.mozilla.org per 4-hour run, seen only on the wire, never by the
# browser's logs; nothing could say which program asked. With this witness the gate names it.
DNS_CLIENT_LOG = "Microsoft-Windows-DNS-Client/Operational"


def dnsclient_enable():
    """Switch the DNS-client log on. -> its previous state (True/False), or None when it could not be read."""
    r = subprocess.run(["wevtutil", "gl", DNS_CLIENT_LOG], capture_output=True, text=True, errors="replace")
    if r.returncode != 0:
        return None
    prev = "enabled: true" in r.stdout.lower()
    if not prev:
        subprocess.run(["wevtutil", "sl", DNS_CLIENT_LOG, "/e:true"], capture_output=True)
    return prev


def dnsclient_restore(prev):
    """Put the log back as it was (only switched off again when it was off before)."""
    if prev is False:
        subprocess.run(["wevtutil", "sl", DNS_CLIENT_LOG, "/e:false"], capture_output=True)


def dnsclient_queries(since_iso):
    """-> [(iso time, pid, query name)] logged since `since_iso`."""
    ps = ("$s=[datetime]::Parse('" + since_iso + "'); "
          "Get-WinEvent -FilterHashtable @{LogName='" + DNS_CLIENT_LOG + "'; Id=3006; StartTime=$s} -ErrorAction SilentlyContinue | "
          "ForEach-Object { \"$($_.TimeCreated.ToString('o'))|$($_.ProcessId)|$($_.Properties[0].Value)\" }")
    out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, errors="replace").stdout
    rows = []
    for line in out.splitlines():
        parts = line.strip().split("|", 2)
        if len(parts) == 3 and parts[1].isdigit():
            rows.append((parts[0], int(parts[1]), parts[2].strip().lower().rstrip(".")))
    return rows


def process_names(rows):
    """{pid: "name (path)"} from a process snapshot."""
    return {r["ProcessId"]: f"{r.get('Name') or '?'} ({r.get('ExecutablePath') or 'no path'})" for r in rows}


# ------------------------------------------------------------------------------------------- filesystem
def watch_dirs():
    env = os.environ
    return [Path(env.get("APPDATA", "")) / "Mozilla" / "Firefox" / "Crash Reports",
            Path(env.get("LOCALAPPDATA", "")) / "Mozilla" / "updates",
            Path(env.get("LOCALAPPDATA", "")) / "Mozilla" / "Firefox" / "Crash Reports",
            Path(env.get("PROGRAMDATA", "C:/ProgramData")) / "Mozilla",
            Path(env.get("PROGRAMDATA", "C:/ProgramData")) / "Mozilla-1de4eec8-1241-4177-a864-e594e8d1fb38"]


def fs_snapshot(paths):
    out = {}
    for base in paths:
        base = Path(base)
        if not base.exists():
            continue
        for p in base.rglob("*"):
            if p.is_file():
                try:
                    st = p.stat()
                    out[str(p)] = (st.st_size, st.st_mtime_ns)
                except OSError:
                    pass
    return out


def fs_diff(before, after):
    return sorted(k for k, v in after.items() if before.get(k) != v)


# ------------------------------------------------------------------------------------------- packets
_WATCHDOG = None


def pktmon_watchdog():
    """2026-10-02: a release run died mid-scenario and its capture kept recording every connection on the laptop for
    seven hours. A hidden watchdog, started once per gate process, runs `pktmon stop` the moment the gate process is
    gone, however it ended (crash, closed window, killed). It inherits the gate's elevation."""
    global _WATCHDOG
    if _WATCHDOG is not None and _WATCHDOG.poll() is None:
        return
    import os
    if os.name != "nt":
        return
    ps = f"Wait-Process -Id {os.getpid()} -ErrorAction SilentlyContinue; pktmon stop | Out-Null"
    _WATCHDOG = subprocess.Popen(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def pktmon_start(etl):
    pktmon_watchdog()
    subprocess.run(["pktmon", "stop"], capture_output=True)
    subprocess.run(["pktmon", "filter", "remove"], capture_output=True)
    subprocess.run(["pktmon", "start", "--capture", "--pkt-size", "0", "--file-name", str(etl)], capture_output=True)


def pktmon_stop(etl, pcap):
    subprocess.run(["pktmon", "stop"], capture_output=True)
    subprocess.run(["pktmon", "etl2pcap", str(etl), "--out", str(pcap)], capture_output=True)


def tls_sni(payload):
    try:
        if len(payload) < 43 or payload[0] != 0x16 or payload[5] != 0x01:
            return None
        i = 43
        i += 1 + payload[i]
        i += 2 + int.from_bytes(payload[i:i + 2], "big")
        i += 1 + payload[i]
        end = i + 2 + int.from_bytes(payload[i:i + 2], "big")
        i += 2
        while i + 4 <= end:
            et, el = int.from_bytes(payload[i:i + 2], "big"), int.from_bytes(payload[i + 2:i + 4], "big")
            if et == 0:
                return payload[i + 9:i + 9 + int.from_bytes(payload[i + 7:i + 9], "big")].decode("ascii", "replace")
            i += 4 + el
    except (IndexError, ValueError):
        return None
    return None


def parse_pcap(path, local_ports, canaries=(), owners=None, names=None):
    """-> events from frames: SNI/TCP/UDP of the browser's ports, every DNS query on the wire in the window,
    canaries in any non-loopback payload. With `owners` (PortOwners), a packet from a port that another program held
    at that moment is reported as kind "foreign-packet" (named, never hidden) instead of as the browser's."""
    import dpkt
    ev = []
    if not Path(path).is_file():
        return ev
    with open(path, "rb") as f:
        try:
            reader = dpkt.pcapng.Reader(f)
        except Exception:
            return ev
        for _ts, buf in reader:
            try:
                ip = dpkt.ethernet.Ethernet(buf).data
            except Exception:
                continue
            if not isinstance(ip, (dpkt.ip.IP, dpkt.ip6.IP6)):
                continue
            fam = socket.AF_INET if isinstance(ip, dpkt.ip.IP) else socket.AF_INET6
            src, dst = socket.inet_ntop(fam, ip.src), socket.inet_ntop(fam, ip.dst)
            if dst.startswith("127.") or dst == "::1":
                continue
            l4 = ip.data
            payload = bytes(getattr(l4, "data", b""))
            for name, c in canaries:
                if c.encode() in payload:
                    ev.append({"kind": "canary", "value": name, "detail": f"on the wire to {dst}"})
            if isinstance(l4, dpkt.udp.UDP) and l4.dport == 53:
                try:
                    for q in dpkt.dns.DNS(l4.data).qd:
                        ev.append({"kind": "dns-wire", "value": q.name.lower(), "detail": f"qtype {q.type} from {src}:{l4.sport}"})
                except Exception:
                    pass
            if isinstance(l4, dpkt.udp.UDP) and l4.sport == 53:
                # answers: the CNAME chain of a name (2026-10-04: Windows' DNS client chased cdn.jsdelivr.net to
                # cdn.jsdelivr.net.cdn.cloudflare.net for the browser; the gate must know that name is the approved
                # host's own alias, from the wire, not from a list)
                try:
                    for rr in dpkt.dns.DNS(l4.data).an:
                        if rr.type == dpkt.dns.DNS_CNAME:
                            ev.append({"kind": "dns-cname", "value": rr.name.lower(), "target": rr.cname.lower(),
                                       "detail": f"answer from {src}"})
                        elif rr.type in (dpkt.dns.DNS_A, dpkt.dns.DNS_AAAA):
                            # the addresses a name really had in THIS run (CDN addresses rotate: resolving again at
                            # judging time gave other addresses, and an approved host's packets read as unknown)
                            fam2 = socket.AF_INET if rr.type == dpkt.dns.DNS_A else socket.AF_INET6
                            ev.append({"kind": "dns-a", "value": rr.name.lower(), "ip": socket.inet_ntop(fam2, rr.rdata),
                                       "detail": f"answer from {src}"})
                except Exception:
                    pass
            if isinstance(l4, (dpkt.tcp.TCP, dpkt.udp.UDP)) and l4.sport in local_ports and owners is not None:
                proto = "tcp" if isinstance(l4, dpkt.tcp.TCP) else "udp"
                who, pid = owners.owner(proto, l4.sport, _ts)
                if who == "other" and not (proto == "udp" and l4.dport == 53):
                    ev.append({"kind": "foreign-packet", "value": dst, "port": l4.dport,
                               "detail": f"{proto} from local port {l4.sport}, held then by pid {pid} "
                                         f"({(names or {}).get(pid, 'exited before the snapshot')})"})
                    continue
            if isinstance(l4, dpkt.tcp.TCP) and l4.sport in local_ports:
                s = tls_sni(payload)
                ev.append({"kind": "dest-ip", "value": dst, "port": l4.dport, "detail": "tcp"})
                if s:
                    ev.append({"kind": "sni", "value": s.lower(), "port": l4.dport, "detail": dst})
            elif isinstance(l4, dpkt.udp.UDP) and l4.sport in local_ports and l4.dport != 53:
                ev.append({"kind": "dest-ip", "value": dst, "port": l4.dport, "detail": "udp" + (" quic" if l4.dport == 443 else "")})
    seen, out = set(), []
    for e in ev:
        k = (e["kind"], e["value"], e.get("port"))
        if k not in seen:
            seen.add(k)
            out.append(e)
    return out


# ------------------------------------------------------------------------------------------- one run
def lan_address(host):
    """True for a private, link-local or loopback IP literal (the Local Network Access address spaces)."""
    import ipaddress
    try:
        a = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    return (a.is_private or a.is_link_local or a.is_loopback) and not a.is_unspecified and not a.is_multicast


def coverage_events(base, sampled, necko_text, mitm_alive, pcap, mode, packets):
    """One `coverage` event per sensor that demonstrably ran in this scenario and mode, even when it saw nothing.
    The per-scenario fail-closed check (gate.action_checks) needs them: no coverage event = the sensor collected
    nothing = failure. The global sensor check in gate.judge ignores them."""
    out = []
    if sampled:
        out.append({**base, "sensor": "sockets", "kind": "coverage", "value": f"{sampled} samples"})
    if mode == "direct" and necko_text:
        out += [{**base, "sensor": s, "kind": "coverage", "value": f"necko log {len(necko_text)} chars"} for s in ("necko-http", "necko-dns")]
    if mode in ("proxied", "poisoned") and mitm_alive:
        out.append({**base, "sensor": "mitm", "kind": "coverage", "value": "mitmdump ran for the whole scenario"})
    if packets and pcap and Path(pcap).is_file() and Path(pcap).stat().st_size > 0:
        out.append({**base, "sensor": "pktmon", "kind": "coverage", "value": f"pcap {Path(pcap).stat().st_size} bytes"})
    return out


def run_one(build_dir, url, seconds, args, workdir, name, mode, canaries, proxy_port=None, watch=10, packets=False, say=print,
            graceful=False, poison=False, prefs=None):
    """Run one scenario in one mode. -> (events, artifacts dict)."""
    work = Path(workdir)
    prof = new_profile(work, f"{name}-{mode}", prefs)
    log = work / f"necko-{name}-{mode}.log"
    env = dict(os.environ)
    if mode == "direct":
        env.update(MOZ_LOG=NECKO, MOZ_LOG_FILE=str(log))
    mitm_out, mitm_raw, mitm = work / f"mitm-{name}.jsonl", work / f"mitm-{name}.raw", None
    if mode in ("proxied", "poisoned"):
        for p in (mitm_out, mitm_raw):
            p.unlink(missing_ok=True)
        addon = work / "leakgate_mitm_addon.py"
        addon.write_text(MITM_ADDON, encoding="utf-8")
        menv = dict(os.environ, LEAKGATE_MITM_OUT=str(mitm_out), LEAKGATE_MITM_RAW=str(mitm_raw), LEAKGATE_POISON="1" if poison else "")
        mitmdump = shutil.which("mitmdump") or str(Path(sys.executable).parent / "Scripts" / "mitmdump.exe")
        mitm = subprocess.Popen([mitmdump, "--listen-host", "127.0.0.1", "--listen-port", str(proxy_port), "--set",
                                 f"confdir={work / 'mitm-conf'}", "-s", str(addon), "-q"], env=menv,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(3)
    etl, pcap = work / f"pkt-{name}-{mode}.etl", work / f"pkt-{name}-{mode}.pcapng"
    if packets:
        pktmon_start(etl)
    import datetime as _dt
    dns_since = _dt.datetime.now().isoformat()
    names_before = process_names(snapshot_processes()) if packets else {}
    before = fs_snapshot(watch_dirs())
    from . import extras
    head = [] if graceful else ["-headless"]
    cmd = [str(Path(build_dir) / "firefox.exe")] + head + ["-no-remote", "-profile", str(prof)] + list(args) + [url]
    proc = subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            startupinfo=extras.startupinfo_minimized() if graceful else None)
    sampler = Sampler(proc.pid, build_dir=build_dir)
    sampler.start()
    time.sleep(seconds)
    closed_normally = None
    if graceful:
        pids = set(sampler.procs) | {proc.pid} | set(build_pids(build_dir))
        extras.graceful_close(pids)
        # the process we started is only the launcher (it exits at once): wait for the build's own processes
        deadline = time.time() + 45
        while build_pids(build_dir) and time.time() < deadline:
            time.sleep(1)
        closed_normally = not build_pids(build_dir)
    mitm_alive = mitm is not None and mitm.poll() is None
    sampler.stop_flag.set()
    sampler.join(timeout=30)
    killed_at = time.time()
    subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
    for pid in build_pids(build_dir):                 # a de-elevated browser is not in the launcher's tree
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
    after_kill = watch_after_kill(build_dir, watch, killed_at=killed_at)
    after = fs_snapshot(watch_dirs())
    if packets:
        pktmon_stop(etl, pcap)
    if mitm:
        subprocess.run(["taskkill", "/PID", str(mitm.pid), "/T", "/F"], capture_output=True)

    ev = []
    base = {"scenario": name, "mode": mode}
    if packets:                                   # the DNS witness: who asked for each name in this window
        names = {**names_before, **process_names(snapshot_processes())}
        ours = set(sampler.procs)
        for when, pid, q in dnsclient_queries(dns_since):
            ev.append({**base, "sensor": "dns-client", "kind": "dns-attribution", "value": q, "pid": pid,
                       "in_build": pid in ours, "detail": f"{when} pid {pid} {names.get(pid, 'exited before the snapshot')}"})
    if graceful and closed_normally is False:
        ev.append({**base, "sensor": "process-tree", "kind": "shutdown-failed", "value": "the browser did not exit within 45 s of WM_CLOSE"})
    for r in sampler.procs.values():
        ev.append({**base, "sensor": "process-tree", "kind": "process", "value": (r.get("Name") or "?").lower(),
                   "detail": r.get("ExecutablePath") or "", "cmd": (r.get("CommandLine") or "")[:300]})
    for r in after_kill:
        ev.append({**base, "sensor": "process-tree", "kind": "process-after-shutdown", "value": (r.get("Name") or "?").lower(),
                   "detail": f"{r.get('why', '')}: pid {r.get('ProcessId')} {(r.get('CommandLine') or r.get('ExecutablePath') or '')[:200]}"})
    local_ports = set()
    for s in sampler.socks:
        proto, state, laddr, lport, raddr, rport, pid = s
        local_ports.add(int(lport)) if lport.isdigit() else None
        if proto == "tcp" and state == "Listen":
            ev.append({**base, "sensor": "sockets", "kind": "listener", "value": f"{laddr}:{lport}", "detail": f"pid {pid}"})
        elif proto == "tcp" and raddr not in ("", "0.0.0.0", "::"):
            loop = raddr.startswith("127.") or raddr == "::1"
            if not loop:
                ev.append({**base, "sensor": "sockets", "kind": "dest-ip", "value": raddr, "port": int(rport), "detail": f"tcp {state} pid {pid}"})
                if mode in ("proxied", "poisoned", "dns-controlled"):
                    ev.append({**base, "sensor": "sockets", "kind": "proxy-bypass" if mode != "dns-controlled" else "dest-ip", "value": raddr, "port": int(rport), "detail": f"tcp {state}"})
        elif proto == "udp":
            ev.append({**base, "sensor": "sockets", "kind": "udp", "value": f"{laddr}:{lport}", "detail": f"pid {pid}"})
    for p in fs_diff(before, after):
        ev.append({**base, "sensor": "filesystem", "kind": "file-system", "value": p.replace("\\", "/")})
    for p in sorted(prof.rglob("*")):
        if p.is_file():
            ev.append({**base, "sensor": "filesystem", "kind": "file", "value": p.relative_to(prof).as_posix()})
    text = ""
    if mode == "direct":
        text = "".join(f.read_text(encoding="utf-8", errors="replace") for f in sorted(work.glob(f"necko-{name}-{mode}.log*")))
        for m in URL_RX.finditer(text):
            host = m.group(2).lower().split("@")[-1].split(":")[0]
            if HOSTNAME.match(host):
                ev.append({**base, "sensor": "necko-http", "kind": "dest", "value": host, "detail": f"{m.group(1)}://{host}{m.group(3)[:120]}"})
        for m in RES_RX.finditer(text):
            ev.append({**base, "sensor": "necko-dns", "kind": "dns", "value": m.group(1).lower()})
        for m in SOCK_RX.finditer(text):
            h = m.group(1).lower()
            if HOSTNAME.match(h):
                ev.append({**base, "sensor": "necko-socket", "kind": "dest", "value": h, "port": int(m.group(2))})
            elif lan_address(h):                  # judged only in lan-probe (LAN_POLICY); loopback includes the local server
                ev.append({**base, "sensor": "necko-socket", "kind": "dest-lan", "value": h.strip("[]"), "port": int(m.group(2))})
        for cname, c in canaries:
            for line in text.splitlines():
                if c in line and "127.0.0.1" not in line and "localhost" not in line and ("uri=" in line or "host" in line):
                    ev.append({**base, "sensor": "necko-http", "kind": "canary", "value": cname, "detail": line.strip()[:200]})
                    break
    if mode in ("proxied", "poisoned") and mitm_out.is_file():
        for l in mitm_out.read_text(encoding="utf-8").splitlines():
            r = json.loads(l)
            if r.get("kind") in ("http", "ws", "tls-failed"):
                ev.append({**base, "sensor": "mitm", "kind": "dest", "value": (r.get("host") or "?").lower(), "port": r.get("port"),
                           "detail": f"{r.get('method', r['kind'])} {r.get('path', '')[:150]} ({r.get('req_bytes', r.get('bytes', 0))} B)"})
        if mitm_raw.is_file():
            raw = mitm_raw.read_bytes()
            for block in raw.split(b"\n--LEAKGATE--\n"):
                first = block.split(b"\n", 1)[0].decode("utf-8", "replace")
                if "127.0.0.1" in first or "localhost" in first:
                    continue
                for cname, c in canaries:
                    if c.encode() in block:
                        ev.append({**base, "sensor": "mitm", "kind": "canary", "value": cname, "detail": first[:200]})
    if packets:
        owner_names = {**names_before, **process_names(snapshot_processes())} if packets else {}
        for e in parse_pcap(pcap, local_ports, canaries, owners=sampler.owners, names=owner_names):
            ev.append({**base, "sensor": "pktmon", **e})
    ev += coverage_events(base, len(sampler.timeline), text, mitm_alive, pcap, mode, packets)
    dedup, seen = [], set()
    for e in ev:
        k = (e["sensor"], e["kind"], e["value"], e.get("port"))
        if k not in seen:
            seen.add(k)
            dedup.append(e)
    return dedup, {"profile": str(prof), "necko": str(log), "mitm": str(mitm_out), "pcap": str(pcap) if packets else None,
                   "timeline": sampler.timeline, "local_ports": sorted(local_ports), "closed_normally": closed_normally}
