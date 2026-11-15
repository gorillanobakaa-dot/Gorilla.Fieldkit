"""The authoritative runner: Debian, root, a network namespace per run (spec: TEST ENVIRONMENT, NETWORK NAMESPACE
TEST, PACKET CAPTURE, DNS LEAK TEST, NETWORK ENFORCEMENT, SYSCALL TEST).

2026-11-12 (P9): the readers (linux_sensors.py) now emit the same sensor vocabulary as the Windows ones (process-tree,
filesystem, sockets, necko-http/necko-dns from MOZ_LOG, mitm from the same addon, tcpdump as the packet sensor), so
gate.judge() applies the same policies on both systems. Parsers are tested on recorded Linux fixtures; a full gate run
on Linux has NOT yet been seen to pass (needs root, nft, dnsmasq, mitmdump). `fieldkit build-harness leakgate-selftest`
reports, before any run, which tools and privileges are missing; nothing is assumed.

Layout per run (namespace `lg`, veth pair lg0 <-> lg1, 10.77.0.1 host side, 10.77.0.2 inside):
  nftables inside the namespace: output policy DROP; accept only loopback, 10.77.0.1:53 (dnsmasq), 10.77.0.1:<proxy>
           (mitmproxy) and the local test server; every other packet is logged with prefix "LEAKGATE-DROP " and dropped.
  dnsmasq on 10.77.0.1: log-queries to a file; address=/#/ (NXDOMAIN for everything) with server=/<allowed>/<upstream>
           only for allowlisted names and the scenario's own hosts.
  tcpdump -i lg1 (inside) and netsniff-ng (if present) independently, from before Firefox starts to the end of the
           post-shutdown watch; tshark/zeek/suricata decode the pcap when installed.
  strace -f (linux_sensors.TRACE) on Firefox; ss -tunap and the /proc process tree sampled every 2 s; mitmdump on the
           host side (10.77.0.1) in proxied and poisoned runs, MOZ_LOG necko log in direct runs.
Events use the same schema as sensors.py, so gate.judge() applies the same fail-closed policies.
"""
import json
import os
import re
import shutil
import signal
import subprocess
import time
from pathlib import Path

from . import linux_sensors as ls
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
    netns_down()                                           # a namespace left by a crashed run would make `ip netns add` fail
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


def allow_tcp_ports(ports):
    """Let the namespace reach more of the gate's own servers on the host side (certificate pages, the controlled
    DNS-over-HTTPS resolver) - created after the namespace, so their ports are not known when it comes up. Inserted
    before the log-and-drop rule; nothing but those ports on 10.77.0.1 opens."""
    ports = sorted({int(p) for p in ports})
    if ports:
        sh("ip", "netns", "exec", NS, "nft", "insert", "rule", "inet", "leakgate", "out", "ip", "daddr", HOST_IP, "tcp", "dport",
           "{ " + ", ".join(map(str, ports)) + " }", "accept")


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


def mitm_start(work, name, poison, confdir, port, mitm_out, mitm_raw):
    """The intercepting proxy on the host side of the veth, with the same addon as the Windows run
    (sensors.MITM_ADDON), so sensors.mitm_events reads its records. -> the process, or None when mitmdump is missing."""
    exe = shutil.which("mitmdump")
    if not exe:
        return None
    for p in (mitm_out, mitm_raw):
        Path(p).unlink(missing_ok=True)
    addon = Path(work) / "leakgate_mitm_addon.py"
    addon.write_text(se.MITM_ADDON, encoding="utf-8")
    env = dict(os.environ, LEAKGATE_MITM_OUT=str(mitm_out), LEAKGATE_MITM_RAW=str(mitm_raw), LEAKGATE_POISON="1" if poison else "")
    p = subprocess.Popen([exe, "--listen-host", HOST_IP, "--listen-port", str(port), "--set", f"confdir={confdir}", "-s", str(addon), "-q"],
                         env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(3)
    return p


def missing_libs(build_dir, runner=subprocess.run):
    """-> the shared libraries `ldd` cannot find for the build's libxul.so (the usual reason a browser dies at once)."""
    so = Path(build_dir) / "libxul.so"
    if not so.is_file() or not shutil.which("ldd"):
        return []
    out = runner(["ldd", str(so)], capture_output=True, text=True).stdout
    return sorted({l.split("=>")[0].strip() for l in out.splitlines() if "not found" in l})


def smoke(build_dir, server, work, seconds=60, say=print, in_ns=True):
    """Before the first scenario: start the browser exactly as the scenarios do (headless, inside the namespace), load the
    gate's local /smoke page and require its report. A browser that cannot show a page stops the run here, with its
    stderr, exit status and missing libraries, instead of 55 minutes of scenarios that measure nothing (P27, 2026-11-13).
    -> {"ok": True, "seconds": n}; raises RuntimeError with the evidence otherwise."""
    work = Path(work).resolve()
    work.mkdir(parents=True, exist_ok=True)
    build_dir = Path(build_dir).resolve()
    prof = se.new_profile(work, "smoke")
    home = work / "home-smoke"
    home.mkdir(exist_ok=True)
    env = dict(os.environ, HOME=str(home), XDG_CACHE_HOME=str(home / ".cache"), XDG_CONFIG_HOME=str(home / ".config"),
               XDG_DATA_HOME=str(home / ".local/share"), MOZ_HEADLESS="1")
    err = work / "smoke-stderr.txt"
    cmd = [str(build_dir / "firefox"), "-headless", "-no-remote", "-profile", str(prof), server.url("/smoke")]
    if in_ns:
        cmd = ["ip", "netns", "exec", NS] + cmd
    before = len(server.results)
    t0 = time.time()
    with open(err, "wb") as ef:
        proc = subprocess.Popen(cmd, env=env, stdout=ef, stderr=subprocess.STDOUT)
        while time.time() - t0 < seconds and proc.poll() is None:
            if any(isinstance(r, dict) and r.get("scenario") == "smoke" for r in server.results[before:]):
                break
            time.sleep(0.5)
        got = any(isinstance(r, dict) and r.get("scenario") == "smoke" for r in server.results[before:])
        code = proc.poll()
        ls.kill_build(build_dir)                           # by PID: only processes running from THIS build copy
        try:
            proc.kill()
        except OSError:
            pass
        proc.wait(timeout=30)
    del server.results[before:]
    del server.requests[:]
    if got:
        say(f"  browser smoke: the page reported after {time.time() - t0:.1f} s")
        return {"ok": True, "seconds": round(time.time() - t0, 1)}
    tail = err.read_text(errors="replace")[-1500:] if err.is_file() else ""
    why = f"exited with status {code}" + (f" (signal {signal.Signals(-code).name})" if isinstance(code, int) and code < 0 else "") \
        if code is not None else f"still running after {seconds} s but never reported"
    libs = missing_libs(build_dir)
    flags = [f for f in ("avx", "avx2", "sse4_2", "bmi1", "bmi2", "fma", "f16c") if f in ci_flags()]
    raise RuntimeError(f"leakgate (linux): the browser cannot show the local test page: {why}. missing libraries: {libs or 'none found'}. "
                       f"cpu flags: {flags}. stderr tail: {tail.strip() or '(empty)'}")


def ci_flags():
    from .ci import cpu_flags
    return set(cpu_flags())


def run_one(build_dir, url, seconds, args, workdir, name, mode, canaries, proxy_port, watch, allowed_names, say=print, prefs=None,
            graceful=False, poison=False, confdir=None):
    """Linux twin of sensors.run_one, inside the namespace. -> (events, artifacts). Same event vocabulary."""
    work = Path(workdir).resolve()
    build_dir = Path(build_dir).resolve()
    work.mkdir(parents=True, exist_ok=True)
    prof = se.new_profile(work, f"{name}-{mode}", prefs)
    pcap, dropped_mark = work / f"tcpdump-{name}-{mode}.pcap", time.time()
    log = work / f"necko-{name}-{mode}.log"
    mitm_out, mitm_raw = work / f"mitm-{name}.jsonl", work / f"mitm-{name}.raw"
    base = {"scenario": name, "mode": mode}
    dns = dnsmasq_start(work, allowed_names)
    mitm = mitm_start(work, name, poison, confdir or work / "mitm-conf", proxy_port, mitm_out, mitm_raw) if mode in ("proxied", "poisoned") else None
    tcpd = subprocess.Popen(["ip", "netns", "exec", NS, "tcpdump", "-i", "lg1", "-s", "0", "-U", "-w", str(pcap)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    nsg = None
    if shutil.which("netsniff-ng"):
        nsg = subprocess.Popen(["ip", "netns", "exec", NS, "netsniff-ng", "--in", "lg1", "--out", str(work / f"netsniff-{name}-{mode}.pcap"), "--silent"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(2)
    home = work / f"home-{name}-{mode}"                    # the real home is never written to or read from
    home.mkdir(exist_ok=True)
    env = dict(os.environ, HOME=str(home), XDG_CACHE_HOME=str(home / ".cache"), XDG_CONFIG_HOME=str(home / ".config"),
               XDG_DATA_HOME=str(home / ".local/share"), MOZ_HEADLESS="1")
    if mode == "direct":
        env.update(MOZ_LOG=se.NECKO, MOZ_LOG_FILE=str(log))
    strace_out = work / f"strace-{name}-{mode}.log"
    firefox = Path(build_dir) / "firefox"
    cmd = ["ip", "netns", "exec", NS, "strace", "-f", "-tt", "-e", "trace=" + ls.TRACE, "-o", str(strace_out),
           str(firefox), "-headless", "-no-remote", "-profile", str(prof)] + list(args) + [url]
    proc = subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # root pid 0: strace is the launcher and must not count as the browser; the build's own programs and all their
    # descendants are the tree (sensors.browser_tree with a build_dir)
    sampler = se.Sampler(0, build_dir=build_dir, snap_procs=ls.snapshot_processes, snap_all_socks=lambda: ls.snapshot_all_sockets(NS))
    sampler.start()
    time.sleep(seconds)
    closed_normally = None
    if graceful:                                           # SIGTERM is Firefox's own quit request (the WM_CLOSE twin)
        roots = [r for r in ls.build_rows(ls.snapshot_processes(), build_dir)]
        kids = {r["ProcessId"] for r in roots}
        for r in roots:
            if r["ParentProcessId"] not in kids:
                try:
                    os.kill(r["ProcessId"], signal.SIGTERM)
                except OSError:
                    pass
        deadline = time.time() + 45
        while ls.build_rows(ls.snapshot_processes(), build_dir) and time.time() < deadline:
            time.sleep(1)
        closed_normally = not ls.build_rows(ls.snapshot_processes(), build_dir)
    mitm_alive = mitm is not None and mitm.poll() is None
    sampler.stop_flag.set()
    sampler.join(timeout=30)
    pre_pids = set(sampler.procs)
    ls.kill_build(build_dir)                               # by PID: every process running from THIS build copy
    try:
        proc.kill()
    except OSError:
        pass
    time.sleep(1)
    after_kill = ls.watch_after_kill(build_dir, watch, pre_pids)
    for p in (tcpd, nsg, dns, mitm):
        if p:
            p.terminate()
    time.sleep(1)

    ev = []
    if graceful and closed_normally is False:
        ev.append({**base, "sensor": "process-tree", "kind": "shutdown-failed", "value": "the browser did not exit within 45 s of SIGTERM"})
    for r in sampler.procs.values():
        ev.append({**base, "sensor": "process-tree", "kind": "process", "value": (r.get("Name") or "?").lower(),
                   "pid": r.get("ProcessId"), "parent_pid": r.get("ParentProcessId"),
                   "detail": r.get("ExecutablePath") or "", "cmd": (r.get("CommandLine") or "")[:300]})
    for r in after_kill:
        ev.append({**base, "sensor": "process-tree", "kind": "process-after-shutdown", "value": (r.get("Name") or "?").lower(),
                   "detail": f"{r['why']}: pid {r.get('ProcessId')} {(r.get('CommandLine') or r.get('ExecutablePath') or '')[:200]}"})
    text = strace_out.read_text(errors="replace") if strace_out.is_file() else ""
    ev += ls.strace_exec_events(text, base, firefox)
    ev += ls.strace_connect_events(text, base, {HOST_IP, NS_IP})
    ev += ls.strace_fs_events(text, base, skip=(str(prof) + "/",))
    sock_ev, local_ports = se.socket_events(sampler, base, mode, build_dir, local_ips={HOST_IP, NS_IP})
    ev += sock_ev
    for p in sorted(prof.rglob("*")):
        if p.is_file():
            ev.append({**base, "sensor": "filesystem", "kind": "file", "value": p.relative_to(prof).as_posix()})
    necko = ""
    if mode == "direct":
        necko = "".join(f.read_text(encoding="utf-8", errors="replace") for f in sorted(work.glob(f"necko-{name}-{mode}.log*")))
        ev += se.necko_events(necko, base, canaries)
    ev += se.machine_addresses(base)
    ev.append({**base, "sensor": "machine", "kind": "machine-ip", "value": NS_IP})
    if mode in ("proxied", "poisoned"):
        ev += se.mitm_events(mitm_out, mitm_raw, base, canaries)
    dlog = work / "dns.log"
    if dlog.is_file():
        ev += ls.dnsmasq_events(dlog.read_text(errors="replace"), base)
    for l in sh("journalctl", "-k", "--since", f"@{int(dropped_mark)}", "--no-pager", check=False).stdout.splitlines():
        if "LEAKGATE-DROP" in l:
            dst = re.search(r"DST=(\S+)", l)
            dpt = re.search(r"DPT=(\d+)", l)
            ev.append({**base, "sensor": "nftables", "kind": "dest-ip", "value": dst.group(1) if dst else "?",
                       "port": int(dpt.group(1)) if dpt else None, "detail": "BLOCKED by the DROP policy (attempted egress)"})
    pcap_ok = pcap.is_file() and pcap.stat().st_size > 0
    if pcap_ok:
        try:
            ev += [{**base, "sensor": "tcpdump", **e} for e in
                   se.frames_events(ls.pcap_frames(pcap), set(), canaries, all_local=True, local_ips={HOST_IP})]
        except Exception as ex:                            # an unreadable capture is no coverage: the run fails closed
            say(f"    tcpdump capture unreadable: {ex}")
            pcap_ok = False
    # per-scenario fail-closed coverage (gate.action_checks): the sensor ran even if it saw nothing
    ev += se.coverage_events(base, len(sampler.timeline), necko, mitm_alive, None, mode, False)
    if text:
        ev.append({**base, "sensor": "strace", "kind": "coverage", "value": f"strace log {len(text)} chars"})
    if pcap_ok:
        ev.append({**base, "sensor": "tcpdump", "kind": "coverage", "value": f"pcap {pcap.stat().st_size} bytes"})
    if shutil.which("zeek"):
        subprocess.run(["zeek", "-r", str(pcap), f"Log::default_logdir={work / ('zeek-' + name + '-' + mode)}"], capture_output=True)
    if shutil.which("suricata"):
        subprocess.run(["suricata", "-r", str(pcap), "-l", str(work / f"suricata-{name}-{mode}")], capture_output=True)
    dedup, seen = [], set()
    for e in ev:
        k = (e["sensor"], e["kind"], e["value"], e.get("port"))
        if k not in seen:
            seen.add(k)
            dedup.append(e)
    return dedup, {"profile": str(prof), "necko": str(log), "mitm": str(mitm_out), "pcap": str(pcap), "strace": str(strace_out),
                   "dns": str(dlog), "timeline": sampler.timeline, "local_ports": sorted(local_ports), "closed_normally": closed_normally}
