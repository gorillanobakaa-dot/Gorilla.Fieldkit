"""The Linux leak-gate readers (leakgate/linux_sensors.py) on recorded Linux output: each emits the same events the
Windows sensors do, so gate.judge() applies one set of policies (P9, 2026-11-12). Fixtures are real strace, ss,
dnsmasq, readelf and MOZ_LOG line shapes; nothing here needs root, a namespace or a browser."""
import json
import os
import socket
import stat
import struct
import sys

import pytest

from fieldkit.leakgate import allow as al, audit, gate, linux_sensors as ls, sensors as se

BASE = {"scenario": "page", "mode": "direct"}
PROF = "/work/runs/profile-page-direct"

STRACE = f'''4100 10:00:00.100000 execve("/work/build-direct/firefox", ["/work/build-direct/firefox", "-headless"], 0x7ffd /* 40 vars */) = 0
4100 10:00:00.200000 openat(AT_FDCWD, "/work/build-direct/libxul.so", O_RDONLY|O_CLOEXEC) = 5
4100 10:00:00.300000 openat(AT_FDCWD, "{PROF}/prefs.js", O_WRONLY|O_CREAT|O_TRUNC, 0600) = 7
4100 10:00:00.310000 openat(AT_FDCWD, "/tmp/lgprofile/.cache/mozilla/firefox/crash/report.dmp", O_WRONLY|O_CREAT|O_EXCL|O_CLOEXEC, 0600) = 8
4100 10:00:00.320000 openat(AT_FDCWD, "/etc/hosts", O_RDONLY|O_CLOEXEC) = 9
4100 10:00:00.330000 openat(AT_FDCWD, "/tmp/denied.txt", O_WRONLY|O_CREAT, 0600) = -1 EACCES (Permission denied)
4100 10:00:00.340000 openat(AT_FDCWD, "/dev/shm/ipc-1", O_RDWR|O_CREAT|O_NOFOLLOW, 0600) = 10
4100 10:00:00.350000 openat(AT_FDCWD, "/proc/self/maps", O_RDONLY) = 11
4101 10:00:00.400000 execve("/proc/self/exe", ["/proc/self/exe", "-contentproc"], 0x7ffd /* 40 vars */) = 0
4101 10:00:00.410000 execve("/usr/bin/xdg-open", ["xdg-open", "x"], 0x7ffd) = 0
4101 10:00:00.420000 execve("/usr/local/bin/helper", ["helper"], 0x7ffd) = -1 ENOENT (No such file or directory)
4102 10:00:01.000000 unlink("/tmp/lgprofile/.cache/mozilla/firefox/old.tmp") = 0
4102 10:00:01.100000 rename("/tmp/lgprofile/.mozilla/a.tmp", "/tmp/lgprofile/.mozilla/a.json") = 0
4102 10:00:01.200000 mkdir("/tmp/lgprofile/.cache/mozilla/updates", 0700) = 0
4102 10:00:01.250000 mkdirat(AT_FDCWD, "/tmp/lgprofile/.cache/mozilla/x", 0755) = -1 EEXIST (File exists)
4103 10:00:01.300000 openat(AT_FDCWD, "/tmp/lgprofile/.config/mozilla/updates.xml", O_WRONLY|O_CREAT|O_TRUNC <unfinished ...>
4103 10:00:01.310000 <... openat resumed>, 0644) = 12
4100 10:00:02.000000 connect(30, {{sa_family=AF_INET, sin_port=htons(443), sin_addr=inet_addr("93.184.216.34")}}, 16) = -1 EINPROGRESS (Operation now in progress)
4100 10:00:02.100000 connect(31, {{sa_family=AF_INET, sin_port=htons(8080), sin_addr=inet_addr("10.77.0.1")}}, 16) = 0
4100 10:00:02.200000 connect(32, {{sa_family=AF_INET, sin_port=htons(9), sin_addr=inet_addr("127.0.0.1")}}, 16) = -1 ECONNREFUSED (Connection refused)
4100 10:00:02.300000 connect(33, {{sa_family=AF_INET6, sin6_port=htons(443), inet_pton(AF_INET6, "2606:4700::1111", &sin6_addr), sin6_flowinfo=htonl(0), sin6_scope_id=0}}, 28) = -1 ENETUNREACH (Network is unreachable)
'''

SS = '''tcp   LISTEN 0      128        127.0.0.1:9222       0.0.0.0:*     users:(("firefox",pid=4100,fd=60))
tcp   ESTAB  0      0         10.77.0.2:41234    10.77.0.1:8080  users:(("firefox",pid=4100,fd=40))
tcp   ESTAB  0      0         10.77.0.2:50000   93.184.216.34:443 users:(("firefox",pid=4102,fd=41))
tcp   TIME-WAIT 0   0         10.77.0.2:50001   93.184.216.34:443
udp   UNCONN 0      0          0.0.0.0:5353       0.0.0.0:*     users:(("firefox",pid=4100,fd=45),("x",pid=1,fd=2))
udp   UNCONN 0      0          [::]:5353          [::]:*        users:(("firefox",pid=4100,fd=46))
tcp   LISTEN 0      5         [::1]:4444          [::]:*     users:(("firefox",pid=4100,fd=47))
'''

DNSMASQ = '''Nov 12 10:00:00 dnsmasq[1]: 1 10.77.0.2/53211 query[A] www.anthropic.com from 10.77.0.2
Nov 12 10:00:00 dnsmasq[1]: 1 10.77.0.2/53211 forwarded www.anthropic.com to 1.1.1.1
Nov 12 10:00:00 dnsmasq[1]: 1 10.77.0.2/53211 reply www.anthropic.com is 160.79.104.10
Nov 12 10:00:00 dnsmasq[1]: 1 10.77.0.2/53211 reply www.anthropic.com is <CNAME>
Nov 12 10:00:01 dnsmasq[1]: 2 10.77.0.2/53212 query[AAAA] incoming.telemetry.mozilla.org from 10.77.0.2
Nov 12 10:00:01 dnsmasq[1]: 2 10.77.0.2/53212 config incoming.telemetry.mozilla.org is NXDOMAIN
Nov 12 10:00:02 dnsmasq[1]: 3 10.77.0.2/53213 cached www.anthropic.com is 160.79.104.10
'''  # telemetry-ok: test fixture of a refused lookup

NECKO = '''2026-11-12 10:00:00.1 UTC - [Parent: nsHttp]: nsHttpChannel::BeginConnect [this=0x1] uri=https://www.anthropic.com/x?y=1
2026-11-12 10:00:00.2 UTC - [Parent: nsHostResolver]: Resolving host [www.anthropic.com]
2026-11-12 10:00:00.3 UTC - [Parent: nsHostResolver]: CompleteLookup: www.anthropic.com has 160.79.104.10
2026-11-12 10:00:00.4 UTC - [Parent: nsSocketTransport]: nsSocketTransport::Init [this=0x2 host=www.anthropic.com:443]
2026-11-12 10:00:00.5 UTC - [Parent: nsHttp]: nsHttpChannel::BeginConnect uri=https://tracker.example.net/?c=CANARY-1234
'''


def _write_proc(root, pid, ppid, comm, exe, cmd, start, state="S"):
    d = root / str(pid)
    d.mkdir()
    # a real /proc/PID/stat line: the name sits in parentheses and may hold spaces and a closing parenthesis
    fields = [state, str(ppid)] + ["0"] * 17 + [str(start)] + ["0"] * 5
    (d / "stat").write_text(f"{pid} ({comm}) " + " ".join(fields) + "\n")
    (d / "cmdline").write_bytes(cmd.replace(" ", "\0").encode() + b"\0")
    if exe:
        os.symlink(exe, d / "exe")


# ------------------------------------------------------------------------------------------------- strace
def test_strace_file_changes_become_filesystem_events():
    ev = ls.strace_fs_events(STRACE, BASE, skip=(PROF + "/",))
    paths = {e["value"] for e in ev}
    assert paths == {"/tmp/lgprofile/.cache/mozilla/firefox/crash/report.dmp", "/tmp/lgprofile/.cache/mozilla/firefox/old.tmp",
                     "/tmp/lgprofile/.mozilla/a.tmp", "/tmp/lgprofile/.mozilla/a.json", "/tmp/lgprofile/.cache/mozilla/updates",
                     "/tmp/lgprofile/.config/mozilla/updates.xml"}
    assert {(e["sensor"], e["kind"]) for e in ev} == {("filesystem", "file-system")}      # the Windows sensor and kind


def test_strace_fs_skips_reads_failures_profile_and_pseudo_filesystems():
    paths = {e["value"] for e in ls.strace_fs_events(STRACE, BASE, skip=(PROF + "/",))}
    assert "/etc/hosts" not in paths and "/work/build-direct/libxul.so" not in paths        # read-only opens
    assert "/tmp/denied.txt" not in paths and "/tmp/lgprofile/.cache/mozilla/x" not in paths      # -1 results
    assert not any(p.startswith(("/dev/", "/proc/", PROF)) for p in paths)


def test_an_unfinished_call_is_kept_not_dropped():
    assert "/tmp/lgprofile/.config/mozilla/updates.xml" in {e["value"] for e in ls.strace_fs_events(STRACE, BASE)}


def test_strace_exec_resolves_proc_self_exe_and_ignores_failed_execs():
    ev = ls.strace_exec_events(STRACE, BASE, "/work/build-direct/firefox")
    got = {(e["value"], e["detail"]) for e in ev}
    assert ("firefox", "/work/build-direct/firefox") in got
    assert ("xdg-open", "/usr/bin/xdg-open") in got                                      # an outside program is visible
    assert not any("helper" in e["detail"] or "/proc/" in e["detail"] for e in ev)
    assert all(e["kind"] == "process" for e in ev)


def test_strace_connect_keeps_destinations_and_loopback_apart_from_the_gates_own_servers():
    ev = ls.strace_connect_events(STRACE, BASE, {"10.77.0.1", "10.77.0.2"})
    assert {(e["kind"], e["value"], e["port"]) for e in ev} == {("dest-ip", "93.184.216.34", 443), ("dest-lan", "127.0.0.1", 9),
                                                               ("dest-ip", "2606:4700::1111", 443)}


# ------------------------------------------------------------------------------------------------------ /proc
@pytest.mark.skipif(sys.platform == "win32", reason="Linux-only code: tested on the ubuntu CI job")
def test_proc_snapshot_has_windows_shaped_rows_and_the_tree_follows_the_build(tmp_path):
    root = tmp_path / "proc"
    root.mkdir()
    _write_proc(root, 4000, 1, "strace", "/usr/bin/strace", "strace -f firefox", 100)
    _write_proc(root, 4100, 4000, "firefox", "/work/build-direct/firefox", "firefox -headless", 110)
    _write_proc(root, 4101, 4100, "Isolated Web Co) x", "/work/build-direct/firefox", "firefox -contentproc", 120)
    _write_proc(root, 4102, 4101, "helper", "/usr/bin/helper", "helper", 130)                # a descendant from outside
    _write_proc(root, 4200, 1, "bash", "/usr/bin/bash", "bash", 90)
    _write_proc(root, 4300, 4100, "firefox", "", "", 140, state="Z")
    rows = ls.snapshot_processes(root)
    by = {r["ProcessId"]: r for r in rows}
    assert by[4101]["Name"] == "firefox" and by[4101]["ParentProcessId"] == 4100 and by[4101]["Created"] == 120
    assert by[4300]["State"] == "Z" and by[4300]["ExecutablePath"] == ""
    tree = {r["ProcessId"] for r in se.browser_tree(rows, 0, "/work/build-direct")}
    assert tree == {4100, 4101, 4102, 4300}                  # strace and bash are not the browser's; every descendant is
    assert {r["ProcessId"] for r in ls.build_rows(rows, "/work/build-direct")} == {4100, 4101}      # zombie not alive


@pytest.mark.skipif(sys.platform == "win32", reason="Linux-only code: tested on the ubuntu CI job")
def test_after_shutdown_reports_survivors_and_relaunches_but_not_the_dying():
    def row(pid):
        return {"ProcessId": pid, "ParentProcessId": 1, "Name": "firefox", "ExecutablePath": "/b/firefox", "CommandLine": "firefox", "Created": pid}
    snaps = iter([[row(1), row(2)], [row(1), row(3)], [row(1), row(3)]])        # 2 died; 3 appeared after the kill; 1 stays
    t = [0.0]
    out = ls.watch_after_kill("/b", 4, pre_pids={1, 2}, snapshot=lambda: next(snaps), interval=2, clock=lambda: t[0],
                              sleep=lambda s: t.__setitem__(0, t[0] + s))
    why = {r["ProcessId"]: r["why"] for r in out}
    assert set(why) == {1, 3} and "still running" in why[1] and "started after" in why[3]


# ---------------------------------------------------------------------------------------------------- ss, dns
def test_ss_keeps_established_connections_with_their_pid():
    rows = ls.parse_ss(SS)
    assert ("tcp", "Established", "10.77.0.2", "50000", "93.184.216.34", "443", "4102") in rows
    assert ("tcp", "Listen", "127.0.0.1", "9222", "0.0.0.0", "", "4100") in rows
    assert ("udp", "Bound", "::", "5353", "", "", "4100") in rows
    assert ("tcp", "Listen", "::1", "4444", "::", "", "4100") in rows
    assert ("tcp", "TimeWait", "10.77.0.2", "50001", "93.184.216.34", "443", "") in rows


@pytest.mark.skipif(sys.platform == "win32", reason="Linux-only code: tested on the ubuntu CI job")
def test_ss_sockets_flow_through_the_shared_socket_reader(tmp_path):
    rows = ls.parse_ss(SS)
    s = se.Sampler(0, build_dir="/work/build-direct", snap_procs=lambda: [
        {"ProcessId": 4100, "ParentProcessId": 1, "Name": "firefox", "ExecutablePath": "/work/build-direct/firefox", "Created": 1},
        {"ProcessId": 4102, "ParentProcessId": 4100, "Name": "firefox", "ExecutablePath": "/work/build-direct/firefox", "Created": 2}],
        snap_all_socks=lambda: rows, interval=0.01)
    s.start()
    import time
    time.sleep(0.1)
    s.stop_flag.set()
    s.join(5)
    ev, ports = se.socket_events(s, BASE, "proxied", "/work/build-direct", local_ips={"10.77.0.1"})
    by = {(e["kind"], e["value"]) for e in ev}
    assert ("dest-ip", "93.184.216.34") in by and ("proxy-bypass", "93.184.216.34") in by     # the leak, with the pid
    assert not any(e["value"] == "10.77.0.1" for e in ev)                                     # the gate's own proxy is not one
    assert ("listener", "127.0.0.1:9222") in by and ("udp", "0.0.0.0:5353") in by
    assert next(e for e in ev if e["kind"] == "dest-ip")["pid"] == 4102
    assert 50000 in ports


def test_dnsmasq_names_and_answers():
    ev = ls.dnsmasq_events(DNSMASQ, BASE)
    assert {e["value"] for e in ev if e["kind"] == "dns"} == {"www.anthropic.com", "incoming.telemetry.mozilla.org"}  # telemetry-ok: fixture
    assert [(e["value"], e["ip"]) for e in ev if e["kind"] == "dns-a"] == [("www.anthropic.com", "160.79.104.10")]


# ------------------------------------------------------------------- shared parsers fed Linux logs
def test_the_windows_necko_parser_reads_a_linux_moz_log():
    ev = se.necko_events(NECKO, BASE, [("canary", "CANARY-1234")])
    assert ("necko-http", "dest", "www.anthropic.com") in {(e["sensor"], e["kind"], e["value"]) for e in ev}
    assert ("necko-dns", "dns", "www.anthropic.com") in {(e["sensor"], e["kind"], e["value"]) for e in ev}
    assert any(e["kind"] == "dns-a" and e["ip"] == "160.79.104.10" for e in ev)
    assert any(e["kind"] == "canary" for e in ev)


def test_the_windows_mitm_parser_reads_the_addon_files_the_linux_run_writes(tmp_path):
    out, raw = tmp_path / "m.jsonl", tmp_path / "m.raw"
    out.write_text(json.dumps({"kind": "http", "method": "POST", "host": "Tracker.Example.Net", "port": 443, "path": "/p", "req_bytes": 9}) + "\n"
                   + json.dumps({"kind": "response", "host": "x", "status": 200}) + "\n")
    raw.write_bytes(b"POST https://tracker.example.net/p\nHost: x\n\nCANARY-1234\n--LEAKGATE--\n")
    ev = se.mitm_events(out, raw, BASE, [("canary", "CANARY-1234")])
    assert [(e["sensor"], e["kind"], e["value"]) for e in ev] == [("mitm", "dest", "tracker.example.net"), ("mitm", "canary", "canary")]
    assert se.mitm_events(tmp_path / "none.jsonl", tmp_path / "none.raw", BASE, []) == []


# ------------------------------------------------------------------------------------------------- the wire
def _ip_udp_dns(src, dst, name):
    import dpkt
    q = dpkt.dns.DNS(qd=[dpkt.dns.DNS.Q(name=name, type=dpkt.dns.DNS_A)])
    u = dpkt.udp.UDP(sport=53000, dport=53, data=bytes(q))
    u.ulen = len(bytes(u))
    return dpkt.ip.IP(src=socket.inet_aton(src), dst=socket.inet_aton(dst), p=17, data=u)


def _ip_tcp(src, dst, dport, payload):
    import dpkt
    t = dpkt.tcp.TCP(sport=50000, dport=dport, flags=dpkt.tcp.TH_PUSH | dpkt.tcp.TH_ACK, data=payload)
    return dpkt.ip.IP(src=socket.inet_aton(src), dst=socket.inet_aton(dst), p=6, data=t)


def _client_hello(sni):
    name = sni.encode()
    ext = struct.pack(">HHHBH", 0, len(name) + 5, len(name) + 3, 0, len(name)) + name
    body = b"\x03\x03" + b"\0" * 32 + b"\x00" + b"\x00\x02\x13\x01" + b"\x01\x00" + struct.pack(">H", len(ext)) + ext
    hs = b"\x01" + len(body).to_bytes(3, "big") + body
    return b"\x16\x03\x01" + struct.pack(">H", len(hs)) + hs


def test_wire_frames_give_dns_sni_and_canaries_and_ignore_the_gates_own_host_side():
    ns, host = "10.77.0.2", "10.77.0.1"
    frames = [(1.0, _ip_udp_dns(ns, host, "www.anthropic.com")),
              (2.0, _ip_tcp(ns, "93.184.216.34", 443, _client_hello("tracker.example.net"))),
              (3.0, _ip_tcp(ns, "93.184.216.34", 80, b"GET /?c=CANARY-1234 HTTP/1.1\r\n")),
              (4.0, _ip_tcp(ns, host, 8080, b"GET /?c=CANARY-1234 HTTP/1.1\r\n")),      # the local server: never judged
              (5.0, "not an ip packet")]
    ev = se.frames_events(frames, set(), [("canary", "CANARY-1234")], all_local=True, local_ips={host})
    got = {(e["kind"], e["value"]) for e in ev}
    assert ("dns-wire", "www.anthropic.com") in got                                     # DNS to the gate's resolver is read
    assert ("sni", "tracker.example.net") in got and ("dest-ip", "93.184.216.34") in got
    assert ("canary", "canary") in got
    assert not any(e["value"] == host for e in ev)
    assert sum(1 for e in ev if e["kind"] == "canary") == 1                               # only the packet to the Internet


def test_a_pcap_file_is_read_whatever_its_link_type(tmp_path):
    import dpkt
    for dl, wrap in ((1, lambda ip: bytes(dpkt.ethernet.Ethernet(type=0x0800, data=ip))), (101, bytes)):
        p = tmp_path / f"c{dl}.pcap"
        with open(p, "wb") as f:
            w = dpkt.pcap.Writer(f, linktype=dl)
            w.writepkt(wrap(_ip_udp_dns("10.77.0.2", "10.77.0.1", "www.anthropic.com")), ts=1.0)
        ev = se.frames_events(ls.pcap_frames(p), set(), all_local=True, local_ips={"10.77.0.1"})
        assert [(e["kind"], e["value"]) for e in ev] == [("dns-wire", "www.anthropic.com")]


# ---------------------------------------------------------------------------- the judge, one vocabulary
def _ev(**kw):
    e = {"scenario": "startup-idle", "mode": "direct", "sensor": "necko-http", "kind": "dest", "value": "x", "port": None}
    e.update(kw)
    return e


LINUX_SENSORS = [_ev(sensor=s, kind="file", value="prefs.js") for s in ("necko-http", "necko-dns", "mitm", "sockets", "process-tree",
                                                                          "filesystem", "tcpdump")]


def test_tcpdump_satisfies_the_packet_sensor_the_judge_requires():
    fail, _ = gate.judge(LINUX_SENSORS, {"entries": []}, {}, ["/b"], [], 3, True, False)
    assert not any("required sensor" in x for p in fail.values() for x in p)
    without = [e for e in LINUX_SENSORS if e["sensor"] != "tcpdump"]
    fail, _ = gate.judge(without, {"entries": []}, {}, ["/b"], [], 3, True, False)
    assert any("pktmon" in x for x in fail["NETWORK_POLICY"]) and any("pktmon" in x for x in fail["DNS_POLICY"])


@pytest.mark.skipif(sys.platform == "win32", reason="Linux-only code: tested on the ubuntu CI job")
def test_each_linux_reader_is_what_its_policy_waits_for():
    """The policies the first Linux runner failed whatever the browser did: PROCESS, SHUTDOWN, FILESYSTEM, TELEMETRY need
    process-tree / filesystem / necko; with the Linux readers' events they see them, and still fail on a real finding."""
    proc = _ev(sensor="process-tree", kind="process", value="firefox", detail="/b/firefox")
    out = _ev(sensor="strace", kind="process", value="xdg-open", detail="/usr/bin/xdg-open")
    tele = _ev(sensor="filesystem", kind="file-system", value="/tmp/lgprofile/.cache/mozilla/firefox/Crash Reports/x.dmp")
    allow = {"entries": [{**{f: "x" for f in al.FIELDS}, "id": "p", "kind": "process", "values": ["firefox.exe"], "scenarios": "*", "approval": {"by": "owner"}},
                         {**{f: "x" for f in al.FIELDS}, "id": "f", "kind": "file-system", "values": ["*"], "scenarios": "*", "approval": {"by": "owner"}}]}
    fail, lists = gate.judge(LINUX_SENSORS + [proc, out, tele], allow, {}, ["/b"], [], 3, True, False)
    assert not any("required sensor" in x for p in fail.values() for x in p)
    assert not [x for x in fail["PROCESS_POLICY"] if "firefox" in x and "xdg" not in x]       # firefox == firefox.exe
    assert any("xdg-open" in x for x in lists["UNEXPECTED_EXECUTABLES"])                      # outside the build: fails
    assert any("Crash Reports" in x for x in lists["UNEXPECTED_TELEMETRY"])                    # telemetry file still caught


def test_the_per_scenario_coverage_rule_is_the_same_on_both_systems():
    assert gate.required_coverage("direct", True) == {"process-tree", "filesystem", "sockets", "necko-http", "necko-dns", "pktmon"}
    assert gate.required_coverage("proxied", False) == {"process-tree", "filesystem", "sockets", "mitm"}
    assert "linux" not in gate.required_coverage.__code__.co_varnames[:gate.required_coverage.__code__.co_argcount]
    assert gate.canon("tcpdump") == "pktmon" and gate.canon("sockets") == "sockets"


def test_a_scenario_whose_linux_sensors_collected_nothing_still_fails_closed():
    ran = {"lan-probe"}
    from fieldkit.leakgate import scenarios as sc
    scen = next(iter(sc.ACTION_SCENARIOS))
    events = [_ev(scenario=scen, mode="direct", sensor="tcpdump", kind="coverage", value="pcap 10 bytes")]
    fail, _ = gate.action_checks({scen}, events, [], [], True)
    assert any("sensor(s) collected nothing" in x for x in fail["NETWORK_POLICY"])           # strace+tcpdump alone are not enough
    assert ran


def test_process_names_match_with_or_without_exe():
    data = {"entries": [{**{f: "x" for f in al.FIELDS}, "id": "p", "kind": "process", "values": ["plugin-container.exe"], "scenarios": "*",
                         "approval": {"by": "owner"}}]}
    assert al.verdict(data, "process", "plugin-container", "page") == ("allowed", "p")
    assert al.verdict(data, "process", "plugin-container.exe", "page") == ("allowed", "p")
    assert al.verdict(data, "process", "other", "page") == ("unexpected", None)
    assert al.verdict(data, "dest", "plugin-container", "page") == ("unexpected", None)         # only programs are equated


# ------------------------------------------------------------------------------------------------ ELF audit
READELF = '''
Dynamic section at offset 0x1d1d0 contains 28 entries:
  Tag        Type                         Name/Value
 0x0000000000000001 (NEEDED)             Shared library: [libcurl.so.4]
 0x0000000000000001 (NEEDED)             Shared library: [libc.so.6]
 0x0000000000000001 (NEEDED)             Shared library: [libgio-2.0.so.0]
 0x000000000000000e (SONAME)             Library soname: [libxul.so]
'''


def _fake_readelf(tmp_path):
    p = tmp_path / "readelf"
    p.write_text("#!/bin/sh\ncat <<'EOF'\n" + READELF + "EOF\n")
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return str(p)


@pytest.mark.skipif(sys.platform == "win32", reason="Linux-only code: tested on the ubuntu CI job")
def test_elf_imports_find_network_libraries_and_say_so_when_readelf_is_missing(tmp_path, monkeypatch):
    inst = tmp_path / "build"
    inst.mkdir()
    (inst / "libxul.so").write_bytes(b"\x7fELF" + b"\0" * 60)
    (inst / "omni.ja").write_bytes(b"PK")
    (inst / "firefox").symlink_to("libxul.so")                                              # a symlink is not read twice
    r = _fake_readelf(tmp_path)
    exe, unscanned = audit.elf_executables(inst, readelf=r)
    assert exe == {"libxul.so": {"imports": ["libcurl", "libgio-2.0"]}} and unscanned == []
    monkeypatch.setattr(audit.shutil, "which", lambda n: None)
    exe, unscanned = audit.elf_executables(inst)
    assert exe == {} and unscanned == ["libxul.so"]                                            # not-this-platform, never "clean"
    got = audit.executable_audit(inst, None, {})
    assert "not-this-platform" in got["unexpected_network_imports"]["libxul.so"][0]


def test_elf_network_imports_need_an_owner_disposition(tmp_path, monkeypatch):
    inst = tmp_path / "build"
    inst.mkdir()
    (inst / "libxul.so").write_bytes(b"\x7fELF" + b"\0" * 60)
    monkeypatch.setattr(audit, "elf_needed", lambda p, readelf=None: ["libcurl.so.4", "libc.so.6"])
    assert audit.executable_audit(inst, None, {})["unexpected_network_imports"] == {"libxul.so": ["libcurl"]}
    approved = {"elf:libxul.so": {"approval": {"by": "owner"}}}
    assert audit.executable_audit(inst, None, approved)["unexpected_network_imports"] == {}


@pytest.mark.skipif(not hasattr(os, "geteuid"), reason="Linux only")
def test_selftest_reports_missing_pieces_never_assumes():
    from fieldkit.leakgate import linux as lx
    r = lx.selftest()
    assert set(r) == {"ok", "root", "tools", "missing_required"} and r["ok"] == (r["root"] and not r["missing_required"])


@pytest.mark.parametrize("missing,policy", [("process-tree", "PROCESS_POLICY"), ("process-tree", "SHUTDOWN_POLICY"),
                                            ("necko-dns", "DNS_POLICY"), ("filesystem", "FILESYSTEM_POLICY"),
                                            ("filesystem", "TELEMETRY_POLICY")])
def test_the_coverage_rule_fails_a_policy_whose_sensor_never_reported(missing, policy):
    """One rule for both systems: a policy nobody measured fails closed (mutate: removing a row survived before)."""
    fail, _ = gate.judge([s for s in LINUX_SENSORS if gate.canon(s["sensor"]) != missing], {"entries": []}, {}, ["/b"], [], 3, True, False)
    assert any("required sensor" in x and missing in x for x in fail[policy])
