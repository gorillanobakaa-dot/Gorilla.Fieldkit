"""Leak gate 158.0 build 2 (run 20261018-223018), owner-approved fixes A, B and D (2026-10-19).

A: an address the build's own resolver logged for an ALLOWED name is that name's (uBlock's list hosts over QUIC
   failed NETWORK, IPV6 and WEBRTC with no DNS answer on the wire); an address only an unallowed name resolves to
   still fails.
B: this machine's own addresses include its interface addresses and any locally bound socket's address (the WebRTC
   socket on a rotated temporary IPv6 address failed SOCKET).
D: a re-judge can compare the crates with a named earlier build's Cargo.lock.
"""
import json
import subprocess

import pytest

from fieldkit.leakgate import allow as al, gate, sensors as se

V6_LIST = "2606:4700::6811:cf05"
V6_ELSE = "2001:db8::66"
V6_TEMP = "2a00:23ee:2878:9d67:3ce4:83ec:6203:f65b"


def _entry(**kw):
    e = {f: "x" for f in al.FIELDS}
    e.update({"id": "e1", "kind": "dns", "values": ["cdn.jsdelivr.net"], "scenarios": "*", "approval": {"by": "owner"}})
    e.update(kw)
    return e


def _ev(**kw):
    e = {"scenario": "webrtc", "mode": "direct", "sensor": "necko-http", "kind": "dest", "value": "x", "port": None, "rep": 0}
    e.update(kw)
    return e


ALL_SENSORS = [_ev(sensor=s, kind="file", value="prefs.js") for s in
               ("necko-http", "necko-dns", "mitm", "sockets", "process-tree", "filesystem")]
ALLOW = {"entries": [_entry(), _entry(id="d", kind="dest", values=["cdn.jsdelivr.net"]),
                     _entry(id="f", kind="file", values=["*"]),
                     _entry(id="u", kind="udp", values=["<this-machine>:*"])]}
LOG = (f"2026-10-18 22:44:38.132000 UTC - [Parent 8512: DNS Resolver #1]: D/nsHostResolver CompleteLookup: "
       f"cdn.jsdelivr.net has {V6_LIST}\n")


def _judge(events):
    fail, lists = gate.judge(ALL_SENSORS + events, ALLOW, {}, ["C:/build"], [], 1, True, False)
    return fail


def test_necko_lookups_reads_the_build_resolver():
    ev = se.necko_lookups(LOG + LOG, {"scenario": "webrtc", "mode": "direct", "rep": 0})
    assert ev == [{"scenario": "webrtc", "mode": "direct", "rep": 0, "sensor": "necko-dns", "kind": "dns-a",
                   "value": "cdn.jsdelivr.net", "ip": V6_LIST}]


def test_a_quic_packet_to_an_allowed_names_logged_address_passes():
    pkt = _ev(sensor="pktmon", kind="dest-ip", value=V6_LIST, port=443, detail="udp quic")
    before = _judge([_ev(sensor="necko-dns", kind="dns", value="cdn.jsdelivr.net"), pkt])
    assert before["NETWORK_POLICY"] and before["WEBRTC_POLICY"] and before["IPV6_POLICY"]      # the 158 failure
    lookup = se.necko_lookups(LOG, {"scenario": "webrtc", "mode": "direct", "rep": 0})
    after = _judge([_ev(sensor="necko-dns", kind="dns", value="cdn.jsdelivr.net"), *lookup, pkt])
    assert not after["NETWORK_POLICY"] and not after["WEBRTC_POLICY"] and not after["IPV6_POLICY"]


def test_an_address_only_an_unallowed_name_resolves_to_still_fails():
    log = f"D/nsHostResolver CompleteLookup: tracker.example has {V6_ELSE}\n"
    lookup = se.necko_lookups(log, {"scenario": "webrtc", "mode": "direct", "rep": 0})
    pkt = _ev(sensor="pktmon", kind="dest-ip", value=V6_ELSE, port=443, detail="udp quic")
    fail = _judge([*lookup, pkt])
    assert fail["NETWORK_POLICY"] and fail["WEBRTC_POLICY"]


def test_a_socket_on_a_rotated_temporary_ipv6_address_is_this_machine():
    sock = _ev(sensor="sockets", kind="udp", value=f"{V6_TEMP}:60068", detail="pid 1 firefox.exe")
    assert not _judge([sock])["SOCKET_POLICY"]                       # its own bound address proves it is local
    allow_literal = {"entries": [e for e in ALLOW["entries"] if e["id"] != "u"]}
    fail, _ = gate.judge(ALL_SENSORS + [sock], allow_literal, {}, ["C:/build"], [], 1, True, False)
    assert fail["SOCKET_POLICY"]                                     # without the <this-machine> entry: still judged


def test_machine_addresses_lists_interfaces_without_loopback():
    ev = se.machine_addresses({"scenario": "s", "mode": "direct", "rep": 0})
    assert ev and all(e["kind"] == "machine-ip" for e in ev)
    assert not {e["value"] for e in ev} & {"127.0.0.1", "::1"}


def test_recorded_lookups_rereads_a_runs_necko_logs(tmp_path):
    (tmp_path / "runs").mkdir()
    (tmp_path / "runs" / "necko-h264-call-r2-direct.log.moz_log").write_text(LOG, encoding="utf-8")
    got = gate.recorded_lookups(tmp_path, [])
    assert got and got[0]["scenario"] == "h264-call" and got[0]["rep"] == 2 and got[0]["ip"] == V6_LIST
    assert gate.recorded_lookups(tmp_path, got) == []               # a run that already has them is left alone


def test_rejudge_compares_crates_with_a_named_build(tmp_path, monkeypatch):
    tree = tmp_path / "tree"
    tree.mkdir()
    git = lambda *a: subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-C", str(tree), *a],
                                    check=True, capture_output=True)
    (tree / "Cargo.lock").write_text('[[package]]\nname = "a"\nversion = "1.0.0"\n', encoding="utf-8")
    git("init", "-q")
    git("add", "-A")
    git("commit", "-qm", "build 1")
    run = tmp_path / "run"
    run.mkdir()
    (run / "events.jsonl").write_text("\n".join(json.dumps(e) for e in ALL_SENSORS), encoding="utf-8")
    (run / "test-results.json").write_text(json.dumps({"DEPENDENCY_POLICY": "FAIL", "FINAL_RESULT": "FAIL",
                                                       "WHY": {"DEPENDENCY_POLICY": ["no previous Cargo.lock to compare with"]}}),
                                           encoding="utf-8")
    owner = tmp_path / "owner"
    (owner / "leakgate").mkdir(parents=True)
    al.save(owner / "leakgate" / "allow.json", ALLOW)
    new, changes = gate.rejudge(run, owner, why="test", dependency_baseline=(tree, "HEAD"))
    assert new["DEPENDENCY_POLICY"] == "PASS" and changes["DEPENDENCY_POLICY"]["before"] == "FAIL"
    assert (run / "dependencies.vs-HEAD.json").is_file()


def test_the_judge_makes_no_live_lookup(monkeypatch):
    import socket
    asked = []
    monkeypatch.setattr(socket, "getaddrinfo", lambda h, *a, **k: asked.append(h) or [])
    _judge([_ev(kind="dest", value="incoming.telemetry.mozilla.org")])  # telemetry-ok: test fixture
    assert asked == []


def test_confirm_exit_tells_dying_from_surviving():
    rows = [{"ProcessId": 1, "why": "still running 10 s after the browser was closed"},
            {"ProcessId": 2, "why": "still running 10 s after the browser was closed"},
            {"ProcessId": 3, "why": "still running 10 s after the browser was closed"},
            {"ProcessId": 4, "why": "started after the browser was closed"}]
    alive = {2: [False, True], 3: [False, False]}                      # per pid: exited by the 1st wait? the 2nd?
    waiter = lambda pid, t: True if pid == 1 else alive[pid].pop(0)
    got = {r["ProcessId"]: r.get("outcome") for r in se.confirm_exit(rows, kill=lambda pid: 0, waiter=waiter)}
    assert got == {1: "exited-late", 2: "killed-again", 3: "survived", 4: None}


def _run(tmp_path, name, plan, build, events):
    d = tmp_path / name
    d.mkdir()
    (d / "gate-plan.json").write_text(json.dumps(plan), encoding="utf-8")
    (d / "test-results.json").write_text(json.dumps({"BUILD": build, "FINAL_RESULT": "FAIL", "SHUTDOWN_POLICY": "FAIL",
                                                     "WHY": {}}), encoding="utf-8")
    (d / "events.jsonl").write_text("\n".join(json.dumps(e) for e in ALL_SENSORS + events), encoding="utf-8")
    return d


def test_a_rerun_replaces_only_its_own_scenarios(tmp_path):
    late = _ev(scenario="newtab", sensor="process-tree", kind="process-after-shutdown", value="firefox.exe")
    other = _ev(scenario="crash", sensor="process-tree", kind="process-after-shutdown", value="firefox.exe")
    full = _run(tmp_path, "full", {"release": True, "only": None}, "20261018195930", [late, other])
    fail, lists = gate.judge(ALL_SENSORS + [late, other], ALLOW, {}, ["C:/build"], [], 1, True, False)
    re_ok = _run(tmp_path, "re", {"release": True, "only": ["newtab"]}, "20261018195930", [])
    merged, _l, note = gate._fold_rerun(full, re_ok, fail, lists, ALLOW, {})
    assert [x for x in merged["SHUTDOWN_POLICY"] if "[newtab/" in x] == []          # newtab: the re-run's clean result
    assert [x for x in merged["SHUTDOWN_POLICY"] if "[crash/" in x]                  # crash: the full run's, unchanged
    assert "re-run re" in note


def test_a_rerun_of_another_build_or_not_release_or_unscoped_is_refused(tmp_path):
    full = _run(tmp_path, "full", {"release": True, "only": None}, "B2", [])
    for name, plan, build in (("b1", {"release": True, "only": ["newtab"]}, "B1"),
                              ("dev", {"release": False, "only": ["newtab"]}, "B2"),
                              ("all", {"release": True, "only": None}, "B2")):
        with pytest.raises(ValueError):
            gate._fold_rerun(full, _run(tmp_path, name, plan, build, []), {p: [] for p in gate.POLICIES}, {}, ALLOW, {})
