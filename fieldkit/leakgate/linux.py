"""The authoritative runner: Debian, root, a network namespace per run (spec: TEST ENVIRONMENT, NETWORK NAMESPACE
TEST, PACKET CAPTURE, DNS LEAK TEST, NETWORK ENFORCEMENT, SYSCALL TEST).

UNTESTED ON LINUX AS OF 2026-10-02: written on the Windows laptop. `fieldkit build-harness leakgate-selftest` reports, before
any run, which tools and privileges are missing; nothing is assumed.

Layout per run (namespace `lg`, veth pair lg0 <-> lg1, 10.77.0.1 host side, 10.77.0.2 inside):
  nftables inside the namespace: output policy DROP; accept only loopback, 10.77.0.1:53 (dnsmasq), 10.77.0.1:<proxy>
           (mitmproxy) and the local test server; every other packet is logged with prefix "LEAKGATE-DROP " and dropped.
  dnsmasq on 10.77.0.1: log-queries to a file; address=/#/ (NXDOMAIN for everything) with server=/<allowed>/<upstream>
           only for allowlisted names and the scenario's own hosts.
  tcpdump -i lg1 (inside) and netsniff-ng (if present) independently, from before Firefox starts to the end of the
           post-shutdown watch; tshark/zeek/suricata decode the pcap when installed.
  strace -f -e trace=network,process,file -o on Firefox; ss -tunap and /proc process tree sampled every 2 s.
Events use the same schema as sensors.py, so gate.judge() applies the same fail-closed policies.
"""
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from . import sensors as se

TOOLS = ("ip", "nft", "dnsmasq", "tcpdump", "strace", "ss", "mitmdump", "netsniff-ng", "tshark", "zeek", "suricata", "conntrack")
REQUIRED = ("ip", "nft", "dnsmasq", "tcpdump", "strace", "ss", "mitmdump")
NS, HOST_IP, NS_IP = "lg", "10.77.0.1", "10.77.0.2"


def sh(*args, check=True, **kw):
    return subprocess.run(list(args), capture_output=True, text=True, check=check, **kw)


def selftest():
    """-> {"ok", "root", "tools": {name: path|None}, "missing_required": [...]}."""
    tools = {t: shutil.which(t) for t in TOOLS}
    missing = [t for t in REQUIRED if not tools[t]]
    root = hasattr(os, "geteuid") and os.geteuid() == 0
    return {"ok": root and not missing, "root": root, "tools": tools, "missing_required": missing}


def netns_up(proxy_port, server_port):
    sh("ip", "netns", "add", NS)
    sh("ip", "link", "add", "lg0", "type", "veth", "peer", "name", "lg1")
    sh("ip", "link", "set", "lg1", "netns", NS)
    sh("ip", "addr", "add", f"{HOST_IP}/30", "dev", "lg0")
    sh("ip", "link", "set", "lg0", "up")
    sh("ip", "netns", "exec", NS, "ip", "addr", "add", f"{NS_IP}/30", "dev", "lg1")
    sh("ip", "netns", "exec", NS, "ip", "link", "set", "lg1", "up")
    sh("ip", "netns", "exec", NS, "ip", "link", "set", "lo", "up")
    sh("ip", "netns", "exec", NS, "ip", "route", "add", "default", "via", HOST_IP)
    rules = f"""
table inet leakgate {{
  chain out {{
    type filter hook output priority 0; policy drop;
    oifname "lo" accept
    ip daddr {HOST_IP} udp dport 53 accept
    ip daddr {HOST_IP} tcp dport 53 accept
    ip daddr {HOST_IP} tcp dport {{ {proxy_port}, {server_port} }} accept
    ct state established,related accept
    log prefix "LEAKGATE-DROP " flags all
    drop
  }}
}}"""
    p = subprocess.run(["ip", "netns", "exec", NS, "nft", "-f", "-"], input=rules, capture_output=True, text=True)
    if p.returncode:
        raise RuntimeError("nft: " + p.stderr)
    resolv = Path(f"/etc/netns/{NS}")
    resolv.mkdir(parents=True, exist_ok=True)
    (resolv / "resolv.conf").write_text(f"nameserver {HOST_IP}\n")


def netns_down():
    sh("ip", "netns", "del", NS, check=False)
    sh("ip", "link", "del", "lg0", check=False)


def snapshot_namespace(work):
    """Spec: record interfaces, addresses, routes, ARP/neighbours, rules, DNS and firewall before the run."""
    out = {}
    for name, cmd in (("interfaces", ["ip", "-j", "addr"]), ("routes", ["ip", "-j", "route"]), ("neighbours", ["ip", "-j", "neigh"]),
                      ("rules", ["ip", "-j", "rule"]), ("firewall", ["nft", "list", "ruleset"])):
        out[name] = sh("ip", "netns", "exec", NS, *cmd, check=False).stdout
    out["resolv.conf"] = Path(f"/etc/netns/{NS}/resolv.conf").read_text()
    (Path(work) / "namespace.json").write_text(json.dumps(out, indent=1))
    return out


def dnsmasq_start(work, allowed_names, upstream="1.1.1.1"):
    conf = Path(work) / "dnsmasq.conf"
    lines = ["no-resolv", "no-hosts", f"listen-address={HOST_IP}", "bind-interfaces", "log-queries=extra",
             f"log-facility={Path(work) / 'dns.log'}", "address=/#/"]
    lines += [f"server=/{n.lstrip('*.')}/{upstream}" for n in sorted(set(allowed_names))]
    conf.write_text("\n".join(lines) + "\n")
    return subprocess.Popen(["dnsmasq", "-k", "-C", str(conf)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


DNSMASQ_Q = re.compile(r"query\[(\w+)\] (\S+) from (\S+)")
STRACE_CONNECT = re.compile(r"connect\(\d+, \{sa_family=AF_INET6?, sin6?_port=htons\((\d+)\), (?:sin_addr=inet_addr\(\"([^\"]+)\"\)|inet_pton\(AF_INET6, \"([^\"]+)\")")
STRACE_EXEC = re.compile(r'execve\("([^"]+)"')


def run_one(build_dir, url, seconds, args, workdir, name, mode, canaries, proxy_port, watch, allowed_names, say=print, prefs=None):
    """Linux twin of sensors.run_one, inside the namespace. -> (events, artifacts)."""
    work = Path(workdir)
    work.mkdir(parents=True, exist_ok=True)
    prof = se.new_profile(work, f"{name}-{mode}", prefs)
    pcap, dropped_mark = work / f"tcpdump-{name}-{mode}.pcap", time.time()
    dns = dnsmasq_start(work, allowed_names)
    tcpd = subprocess.Popen(["ip", "netns", "exec", NS, "tcpdump", "-i", "any", "-s", "0", "-w", str(pcap)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    nsg = None
    if shutil.which("netsniff-ng"):
        nsg = subprocess.Popen(["ip", "netns", "exec", NS, "netsniff-ng", "--in", "any", "--out", str(work / f"netsniff-{name}-{mode}.pcap"), "--silent"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(2)
    env = dict(os.environ, MOZ_LOG=se.NECKO, MOZ_LOG_FILE=str(work / f"necko-{name}-{mode}.log")) if mode == "direct" else dict(os.environ)
    strace_out = work / f"strace-{name}-{mode}.log"
    cmd = ["ip", "netns", "exec", NS, "strace", "-f", "-tt", "-e", "trace=network,process,openat,unlink,rename,mkdir",
           "-o", str(strace_out), str(Path(build_dir) / "firefox"), "-headless", "-no-remote", "-profile", str(prof)] + list(args) + [url]
    proc = subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    socks, t0 = set(), time.time()
    while time.time() - t0 < seconds:
        for l in sh("ip", "netns", "exec", NS, "ss", "-tunapH", check=False).stdout.splitlines():
            if "firefox" in l or "Socket" in l:
                socks.add(l.strip())
        time.sleep(2)
    sh("pkill", "-KILL", "-P", str(proc.pid), check=False)
    proc.kill()
    time.sleep(watch)
    leftover = [l for l in sh("ps", "-eo", "pid,ppid,comm,args", check=False).stdout.splitlines() if str(build_dir) in l]
    for p in (tcpd, nsg, dns):
        if p:
            p.terminate()
    time.sleep(1)

    base = {"scenario": name, "mode": mode}
    ev = []
    text = strace_out.read_text(errors="replace") if strace_out.is_file() else ""
    for m in STRACE_CONNECT.finditer(text):
        ip = m.group(2) or m.group(3)
        if ip and not ip.startswith(("127.", "::1", HOST_IP)):
            ev.append({**base, "sensor": "strace", "kind": "dest-ip", "value": ip, "port": int(m.group(1)), "detail": "connect()"})
        elif ip and ip.startswith(("127.", "::1")):          # judged only in lan-probe (the closed loopback port)
            ev.append({**base, "sensor": "strace", "kind": "dest-lan", "value": ip, "port": int(m.group(1)), "detail": "connect()"})
    for m in STRACE_EXEC.finditer(text):
        ev.append({**base, "sensor": "strace", "kind": "process", "value": Path(m.group(1)).name, "detail": m.group(1)})
    for l in leftover:
        ev.append({**base, "sensor": "ps", "kind": "process-after-shutdown", "value": l.split()[2], "detail": l})
    for l in socks:
        if "LISTEN" in l or "UNCONN" in l:
            parts = l.split()
            ev.append({**base, "sensor": "sockets", "kind": "listener" if "LISTEN" in l else "udp", "value": parts[4] if len(parts) > 4 else l})
    dlog = work / "dns.log"
    if dlog.is_file():
        for m in DNSMASQ_Q.finditer(dlog.read_text(errors="replace")):
            ev.append({**base, "sensor": "dnsmasq", "kind": "dns", "value": m.group(2).lower(), "detail": f"type {m.group(1)}"})
    for l in sh("journalctl", "-k", "--since", f"@{int(dropped_mark)}", "--no-pager", check=False).stdout.splitlines():
        if "LEAKGATE-DROP" in l:
            dst = re.search(r"DST=(\S+)", l)
            dpt = re.search(r"DPT=(\d+)", l)
            ev.append({**base, "sensor": "nftables", "kind": "dest-ip", "value": dst.group(1) if dst else "?",
                       "port": int(dpt.group(1)) if dpt else None, "detail": "BLOCKED by the DROP policy (attempted egress)"})
    ev += [{**base, "sensor": "tcpdump", **e} for e in parse_pcap_classic(pcap, canaries)]
    # per-scenario fail-closed coverage (gate.action_checks): the sensor ran even if it saw nothing
    if text:
        ev.append({**base, "sensor": "strace", "kind": "coverage", "value": f"strace log {len(text)} chars"})
    if pcap.is_file() and pcap.stat().st_size > 0:
        ev.append({**base, "sensor": "tcpdump", "kind": "coverage", "value": f"pcap {pcap.stat().st_size} bytes"})
    if shutil.which("zeek"):
        subprocess.run(["zeek", "-r", str(pcap), f"Log::default_logdir={work / ('zeek-' + name + '-' + mode)}"], capture_output=True)
    if shutil.which("suricata"):
        subprocess.run(["suricata", "-r", str(pcap), "-l", str(work / f"suricata-{name}-{mode}")], capture_output=True)
    return ev, {"profile": str(prof), "pcap": str(pcap), "strace": str(strace_out), "dns": str(dlog)}


def parse_pcap_classic(path, canaries):
    """tcpdump -w writes classic pcap; same extraction as sensors.parse_pcap (all frames leave the namespace)."""
    import dpkt
    import socket as so
    out = []
    if not Path(path).is_file():
        return out
    with open(path, "rb") as f:
        for _ts, buf in dpkt.pcap.Reader(f):
            try:
                pkt = dpkt.sll.SLL(buf)
                ip = pkt.data
            except Exception:
                continue
            if not isinstance(ip, (dpkt.ip.IP, dpkt.ip6.IP6)):
                continue
            fam = so.AF_INET if isinstance(ip, dpkt.ip.IP) else so.AF_INET6
            dst = so.inet_ntop(fam, ip.dst)
            if dst.startswith("127.") or dst == "::1":
                continue
            l4 = ip.data
            payload = bytes(getattr(l4, "data", b""))
            for n, c in canaries:
                if c.encode() in payload:
                    out.append({"kind": "canary", "value": n, "detail": f"on the wire to {dst}"})
            if isinstance(l4, dpkt.tcp.TCP):
                s = se.tls_sni(payload)
                if s:
                    out.append({"kind": "sni", "value": s.lower(), "port": l4.dport, "detail": dst})
    return out
