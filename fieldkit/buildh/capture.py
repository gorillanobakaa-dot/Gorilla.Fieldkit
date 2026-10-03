"""Network forensics of a built Gorilla Firefox: what it sends, to whom, with what in it.

Three independent witnesses, because the browser's own log can only report what goes through the browser's own
logging (code can bypass necko, a component can open a raw socket, the OS can resolve on its behalf):

  mitm     every HTTP(S)/WebSocket request DECRYPTED by mitmproxy: host, path, headers, body sample. A throwaway copy
           of the build (from the hashed zip) gets distribution/policies.json pinning its proxy to mitmproxy and
           installing mitmproxy's CA; the owner's install is never touched. User level, no elevation.
  dns      every name the browser's resolver looks up (MOZ_LOG nsHostResolver), in a run WITHOUT the proxy (a proxy
           resolves on the browser's behalf, which would hide these).
  packets  (elevated only) pktmon captures every frame on the machine; the browser's own local ports (sampled from
           the socket table every second) select its traffic; DNS queries, TLS SNI, QUIC and STUN/UDP are read from
           the frames. This sees what bypasses the proxy and the browser's logging. Without elevation the row says
           so and prints the one command to run in an admin shell.

Scenarios, each a fresh throwaway profile: idle (about:blank, the browser on its own), newtab (top sites, weather,
stories triggers), addons (about:addons: discovery/recommendations), page (a real page), shutdown is included in
each (the log runs through taskkill /T of the process this run started; nothing else is touched).
Judgement: any host under mozilla/firefox domains or on the known-tracker list fails; the page's own hosts and the
bundled uBlock's list hosts pass by name; everything else is listed for the owner with what was sent.
"""
import ctypes
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

from .proof import VENDOR_HOST, LIST_HOSTS, AD_HOSTS

SCENARIOS = (("idle", "about:blank", 120), ("newtab", "about:newtab", 60), ("addons", "about:addons", 45),
             ("page", "https://www.anthropic.com/legal/archive/21d66aa9-68f6-4356-ba01-2825b0f81805", 60))

ADDON = r'''
import json, os, time
OUT = os.environ["GORILLA_MITM_OUT"]
def _w(rec):
    with open(OUT, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
def _sample(b):
    if not b:
        return ""
    try:
        return b[:400].decode("utf-8")
    except UnicodeDecodeError:
        return "<binary %d bytes>" % len(b)
def request(flow):
    r = flow.request
    _w({"t": time.time(), "kind": "http", "method": r.method, "host": r.pretty_host, "port": r.port, "path": r.path[:300],
        "ua": r.headers.get("user-agent", ""), "cookie": bool(r.headers.get("cookie")), "referer": r.headers.get("referer", ""),
        "req_bytes": len(r.raw_content or b""), "req_sample": _sample(r.raw_content)})
def websocket_message(flow):
    m = flow.websocket.messages[-1]
    _w({"t": time.time(), "kind": "ws", "host": flow.request.pretty_host, "from_client": m.from_client, "bytes": len(m.content), "sample": _sample(m.content)})
def tls_failed_client(data):
    _w({"t": time.time(), "kind": "tls-failed", "host": getattr(data.context.client, "sni", None) or "?", "error": str(getattr(data.conn, "error", ""))[:200]})
'''


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def test_copy(zip_path, dest, policies=None):
    """The build from its hashed zip, unpacked into `dest`, optionally with distribution/policies.json."""
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


def _profile():
    from . import throwaway
    return throwaway.profile("gcap_")


def _tree_pids(root_pid):
    ps = subprocess.run(["powershell", "-NoProfile", "-Command",
                         "Get-CimInstance Win32_Process | ForEach-Object { \"$($_.ProcessId) $($_.ParentProcessId)\" }"],
                        capture_output=True, text=True).stdout.split("\n")
    kids = {}
    for line in ps:
        parts = line.split()
        if len(parts) == 2:
            kids.setdefault(int(parts[1]), []).append(int(parts[0]))
    out, q = {root_pid}, [root_pid]
    while q:
        for k in kids.get(q.pop(), []):
            if k not in out:
                out.add(k)
                q.append(k)
    return out


def _sockets(pids):
    """-> set of (proto, local_port, remote) for the given processes."""
    ids = ",".join(str(p) for p in pids)
    ps = subprocess.run(["powershell", "-NoProfile", "-Command",
                         f"$ids=@({ids}); Get-NetTCPConnection -ErrorAction SilentlyContinue | Where-Object {{ $ids -contains $_.OwningProcess }} | "
                         "ForEach-Object { \"tcp $($_.LocalPort) $($_.RemoteAddress):$($_.RemotePort)\" }; "
                         "Get-NetUDPEndpoint -ErrorAction SilentlyContinue | Where-Object { $ids -contains $_.OwningProcess } | "
                         "ForEach-Object { \"udp $($_.LocalPort) $($_.LocalAddress)\" }"], capture_output=True, text=True).stdout
    return {tuple(l.split(" ", 2)) for l in ps.splitlines() if l.count(" ") >= 2}


def run_browser(exe, url, seconds, env_extra=None, sample_sockets=True, prof=None):
    """Start, sample the process tree's sockets every ~2 s, stop exactly what was started. -> (profile dir, sockets).
    A profile made here is deleted after the run; one passed in (`prof`) is the caller's to read and discard."""
    from . import throwaway
    own = prof is None
    if own:
        prof = _profile()
    env = dict(os.environ, **(env_extra or {}))
    proc = subprocess.Popen([str(exe), "-headless", "-no-remote", "-profile", str(prof), url], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    socks = set()
    t0 = time.time()
    while time.time() - t0 < seconds:
        if sample_sockets:
            try:
                socks |= _sockets(_tree_pids(proc.pid))
            except Exception:
                pass
        time.sleep(2)
    subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
    time.sleep(1)
    if own:
        throwaway.discard(prof)
    return prof, socks


def judge(hosts, page_host=None):
    """-> (vendor, trackers, unknown) host sets."""
    vendor, trackers, unknown = set(), set(), set()
    for h in hosts:
        h = h.lower().split(":")[0]
        if not h or h in ("127.0.0.1", "localhost"):
            continue
        if page_host and (h == page_host or h.endswith("." + page_host.split(".", 1)[-1])):
            continue
        if h in LIST_HOSTS:
            continue
        if VENDOR_HOST.search(h):
            vendor.add(h)
        elif any(h == a or h.endswith("." + a) for a in AD_HOSTS):
            trackers.add(h)
        else:
            unknown.add(h)
    return vendor, trackers, unknown


# ---------------------------------------------------------------------------------------------- mitm
def mitm_run(zip_path, workdir, scenarios=SCENARIOS, say=print):
    work = Path(workdir)
    work.mkdir(parents=True, exist_ok=True)
    confdir = work / "mitm-conf"
    confdir.mkdir(exist_ok=True)
    addon = work / "gorilla_mitm_addon.py"
    addon.write_text(ADDON, encoding="utf-8")
    mitmdump = shutil.which("mitmdump") or str(Path(sys.executable).parent / "Scripts" / "mitmdump.exe")
    port = _free_port()
    results = {}
    for name, url, secs in scenarios:
        out = work / f"mitm-{name}.jsonl"
        out.unlink(missing_ok=True)
        env = dict(os.environ, GORILLA_MITM_OUT=str(out))
        m = subprocess.Popen([mitmdump, "--listen-host", "127.0.0.1", "--listen-port", str(port), "--set", f"confdir={confdir}",
                              "-s", str(addon), "-q"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        ca = confdir / "mitmproxy-ca-cert.pem"
        t0 = time.time()
        while not ca.exists() and time.time() - t0 < 30:
            time.sleep(0.5)
        copy = test_copy(zip_path, work / "browser-mitm", {
            "Certificates": {"Install": [str(ca)]},
            "Proxy": {"Mode": "manual", "HTTPProxy": f"127.0.0.1:{port}", "UseHTTPProxyForAllProtocols": True, "Locked": True}})
        say(f"  mitm {name}: {url} for {secs} s")
        run_browser(copy / "firefox.exe", url, secs, sample_sockets=False)
        subprocess.run(["taskkill", "/PID", str(m.pid), "/T", "/F"], capture_output=True)
        flows = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines()] if out.is_file() else []
        results[name] = {"url": url, "flows": flows}
    return results


# ---------------------------------------------------------------------------------------------- dns
DNS_LINE = re.compile(r"(?:Resolving host|host \[|NameLookup|resolving) \[?([a-z0-9][a-z0-9.-]*\.[a-z]{2,})\]?", re.I)


def dns_run(exe, scenarios=SCENARIOS, say=print):
    results = {}
    for name, url, secs in scenarios:
        say(f"  dns {name}: {url} for {secs} s")
        prof = _profile()
        log = prof / "dns.log"
        env = {"MOZ_LOG": "nsHostResolver:5,timestamp", "MOZ_LOG_FILE": str(log)}
        prof, socks = run_browser(exe, url, secs, env_extra=env, prof=prof)
        text = "".join(f.read_text(encoding="utf-8", errors="replace") for f in sorted(Path(log.parent).glob("dns.log*")))
        from . import throwaway
        throwaway.discard(prof)
        names = sorted({m.group(1).lower() for m in DNS_LINE.finditer(text)})
        results[name] = {"url": url, "names": names, "sockets": sorted(socks)}
    return results


# ---------------------------------------------------------------------------------------------- packets
def _sni(payload):
    """Server name from a TLS ClientHello, or None."""
    try:
        if len(payload) < 43 or payload[0] != 0x16 or payload[5] != 0x01:
            return None
        i = 43
        i += 1 + payload[i]                                   # session id
        i += 2 + int.from_bytes(payload[i:i + 2], "big")      # cipher suites
        i += 1 + payload[i]                                   # compression
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


def parse_pcapng(path, local_ports):
    """-> {"sni": set, "dns": set, "udp": set of (ip, port), "tcp": set of (ip, port)} for the browser's local ports."""
    import dpkt
    out = {"sni": set(), "dns": set(), "udp": set(), "tcp": set()}
    with open(path, "rb") as f:
        for _ts, buf in dpkt.pcapng.Reader(f):
            try:
                ip = dpkt.ethernet.Ethernet(buf).data
            except Exception:
                continue
            if not isinstance(ip, (dpkt.ip.IP, dpkt.ip6.IP6)):
                continue
            l4 = ip.data
            dst = socket.inet_ntop(socket.AF_INET if isinstance(ip, dpkt.ip.IP) else socket.AF_INET6, ip.dst)
            if isinstance(l4, dpkt.tcp.TCP) and l4.sport in local_ports:
                out["tcp"].add((dst, l4.dport))
                s = _sni(bytes(l4.data))
                if s:
                    out["sni"].add(s)
            elif isinstance(l4, dpkt.udp.UDP) and l4.sport in local_ports:
                out["udp"].add((dst, l4.dport))
                if l4.dport == 53:
                    try:
                        out["dns"].update(q.name for q in dpkt.dns.DNS(l4.data).qd)
                    except Exception:
                        pass
    return out


def packet_run(exe, workdir, scenarios=SCENARIOS, say=print):
    if not is_admin():
        return None
    work = Path(workdir)
    results = {}
    for name, url, secs in scenarios:
        etl, pcap = work / f"pkt-{name}.etl", work / f"pkt-{name}.pcapng"
        for p in (etl, pcap):
            p.unlink(missing_ok=True)
        subprocess.run(["pktmon", "stop"], capture_output=True)
        subprocess.run(["pktmon", "start", "--capture", "--pkt-size", "0", "--file-name", str(etl)], capture_output=True)
        say(f"  packets {name}: {url} for {secs} s")
        _, socks = run_browser(exe, url, secs)
        subprocess.run(["pktmon", "stop"], capture_output=True)
        subprocess.run(["pktmon", "etl2pcap", str(etl), "--out", str(pcap)], capture_output=True)
        ports = {int(s[1]) for s in socks if s[1].isdigit()}
        results[name] = {"url": url, **{k: sorted(v) for k, v in parse_pcapng(pcap, ports).items()}, "ports": len(ports)}
    return results


# ---------------------------------------------------------------------------------------------- rows
def rows(task_id, zip_path, exe, workdir, say=print, packets=True):
    mitm = mitm_run(zip_path, Path(workdir) / "mitm", say=say)
    dns = dns_run(exe, say=say)
    pk = packet_run(exe, Path(workdir), say=say) if packets else None
    out = []
    for name, url, _ in SCENARIOS:
        page = url.split("/")[2] if url.startswith("http") else None
        flows = mitm[name]["flows"]
        hosts = {f["host"] for f in flows}
        v, tr, un = judge(hosts, page)
        took = (page in hosts) if page else True
        detail = [f"{f['method']} {f['host']}{f['path'][:80]} ({f['req_bytes']} B)" for f in flows if f.get("kind") == "http" and f["host"] in v | tr | un][:6]
        out.append({"check": f"capture/mitm {name}: no Mozilla or tracker host, everything decrypted ({len(flows)} requests)",
                    "ok": took and not v and not tr,
                    "evidence": ("" if took else "THE PROXY DID NOT TAKE (the page itself is missing) - ") + f"vendor {sorted(v)} trackers {sorted(tr)} other {sorted(un)[:8]}; {detail}",
                    "bad": sorted(v | tr), "flows": flows})
        dv, dtr, dun = judge(dns[name]["names"], page)
        out.append({"check": f"capture/dns {name}: the resolver looks up no Mozilla or tracker name ({len(dns[name]['names'])} names)",
                    "ok": not dv and not dtr, "evidence": f"vendor {sorted(dv)} trackers {sorted(dtr)} other {sorted(dun)[:10]}; udp sockets {[s for s in dns[name]['sockets'] if s[0] == 'udp'][:6]}",
                    "bad": sorted(dv | dtr)})
        if pk:
            names = set(pk[name]["sni"]) | set(pk[name]["dns"])
            pv, ptr, pun = judge(names, page)
            out.append({"check": f"capture/packets {name}: no Mozilla or tracker name in SNI or DNS on the wire",
                        "ok": not pv and not ptr, "evidence": f"SNI+DNS vendor {sorted(pv)} trackers {sorted(ptr)} other {sorted(pun)[:10]}; UDP peers {pk[name]['udp'][:8]}",
                        "bad": sorted(pv | ptr)})
    if pk is None and packets:
        out.append({"check": "capture/packets: frame-level capture (pktmon) of every scenario", "ok": False,
                    "evidence": "needs an elevated shell; run in an admin PowerShell: fieldkit build-harness capture " + task_id + " --packets-only",
                    "bad": ["not elevated"]})
    return out
