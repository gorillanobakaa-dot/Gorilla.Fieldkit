"""leakgate fails closed: no entry -> unexpected, unapproved entry -> pending, a policy whose sensors did not collect
fails, a canary anywhere fails, approval is the owner's at a real terminal."""
import pytest

from fieldkit.buildh import task
from fieldkit.leakgate import allow as al, gate


def _entry(**kw):
    e = {f: "x" for f in al.FIELDS}
    e.update({"id": "e1", "kind": "dest", "values": ["ublockorigin.github.io"], "scenarios": "*", "approval": None})
    e.update(kw)
    return e


def test_unexpected_pending_allowed():
    data = {"entries": [_entry()]}
    assert al.verdict(data, "dest", "push.services.mozilla.com", "idle") == ("unexpected", None)
    assert al.verdict(data, "dest", "ublockorigin.github.io", "idle") == ("pending", "e1")
    data["entries"][0]["approval"] = {"by": "owner"}
    assert al.verdict(data, "dest", "ublockorigin.github.io", "idle") == ("allowed", "e1")
    assert al.problems({"entries": [{"id": "x", "kind": "dest"}]})


def test_approval_refused_without_a_terminal(tmp_path, monkeypatch):
    p = tmp_path / "allow.json"
    al.save(p, {"version": 1, "entries": [_entry()]})
    monkeypatch.setattr(task, "owner_terminal", lambda: False)
    with pytest.raises(task.Refused, match="real terminal"):
        al.approve(p, {"e1"})
    with pytest.raises(TypeError):
        al.approve(p, {"e1"}, terminal=True)                     # no argument stands in for the terminal any more
    assert al.load(p)["entries"][0]["approval"] is None
    monkeypatch.setattr(task, "owner_terminal", lambda: True)
    assert al.approve(p, {"*proposed*"}, say=lambda m: None) == ["e1"]
    assert al.load(p)["entries"][0]["approval"]["how"] == "terminal"


def _ev(**kw):
    e = {"scenario": "startup-idle", "mode": "direct", "sensor": "necko-http", "kind": "dest", "value": "x", "port": None}
    e.update(kw)
    return e


ALL_SENSORS = [_ev(sensor=s, kind="file", value="prefs.js") for s in ("necko-http", "necko-dns", "mitm", "sockets", "process-tree", "filesystem")]


def test_judge_fails_closed():
    allow = {"entries": [_entry(approval={"by": "owner"}), _entry(id="f", kind="file", values=["*"], approval={"by": "owner"}),
                         _entry(id="p", kind="process", values=["firefox.exe"], approval={"by": "owner"})]}
    events = ALL_SENSORS + [
        _ev(value="ublockorigin.github.io"),
        _ev(value="incoming.telemetry.mozilla.org"),
        _ev(kind="canary", value="form", sensor="mitm", detail="POST https://x.example/"),
        _ev(kind="process", value="pingsender.exe", sensor="process-tree", detail="C:/build/pingsender.exe"),
        _ev(kind="process", value="firefox.exe", sensor="process-tree", detail="C:/elsewhere/firefox.exe"),
        _ev(scenario="page", value="www.anthropic.com"),
        _ev(kind="file", value="datareporting/state.json", sensor="filesystem"),
    ]
    fail, lists = gate.judge(events, allow, {"page": ["www.anthropic.com"]}, ["C:/build"], [], 3, True, False)
    assert any("incoming.telemetry" in x for x in lists["UNEXPECTED_TELEMETRY"])
    assert any("incoming.telemetry" in x for x in lists["UNEXPECTED_DESTINATIONS"])
    assert not any("ublockorigin" in x or "anthropic" in x for x in lists["UNEXPECTED_DESTINATIONS"])
    assert lists["UNEXPECTED_CANARIES"] and fail["CANARY_POLICY"]
    assert any("pingsender" in x for x in lists["UNEXPECTED_PROCESSES"])
    assert any("elsewhere" in x for x in lists["UNEXPECTED_EXECUTABLES"])
    assert any("datareporting" in x for x in lists["UNEXPECTED_TELEMETRY"])
    assert any("pktmon" in x for x in fail["NETWORK_POLICY"])           # packets required but never collected


def test_missing_packets_and_repeats_fail():
    fail, _ = gate.judge(ALL_SENSORS, {"entries": [_entry(id="f", kind="file", values=["*"], approval={"by": "o"})]}, {}, ["C:/b"], [], 1, False, True)
    assert fail["REPRODUCIBILITY_POLICY"] and any("packet-level" in x for x in fail["NETWORK_POLICY"])
    assert not fail["FILESYSTEM_POLICY"] and not fail["CANARY_POLICY"]


def test_no_approval_path_exists_without_the_owner_terminal_check():
    """Every function in leakgate that writes an approval (or is named approve*) must call task.owner_terminal()
    itself, with no parameter able to stand in for it. The chat route approve_from_chat() is gone."""
    import ast
    from pathlib import Path
    import fieldkit.leakgate as lg
    offenders, approvers = [], []
    for f in sorted(Path(lg.__file__).parent.glob("*.py")):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for fn in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            src = ast.unparse(fn)
            writes = any(isinstance(n, ast.Subscript) and isinstance(n.slice, ast.Constant) and n.slice.value == "approval"
                         and isinstance(n.ctx, ast.Store) for n in ast.walk(fn))
            if fn.name.startswith("approve") or writes:
                approvers.append(f"{f.name}:{fn.name}")
                if "owner_terminal()" not in src or "terminal" in [a.arg for a in fn.args.args + fn.args.kwonlyargs]:
                    offenders.append(f"{f.name}:{fn.name}")
    assert approvers == ["allow.py:approve", "dispositions.py:approve_from"] and offenders == []
    from fieldkit.leakgate import dispositions
    assert not hasattr(dispositions, "approve_from_chat") and not hasattr(dispositions, "build")


def test_ensure_ca_stops_only_the_mitmdump_it_started_on_every_platform(tmp_path, monkeypatch):
    started, calls = [], []

    class P:
        pid = 777001

        def __init__(self, *a, **k):
            started.append(self)
            self.terminated = self.killed = False
            (tmp_path / "w" / "mitm-conf" / "mitmproxy-ca-cert.pem").write_text("CA", encoding="utf-8")

        def poll(self):
            return None

        def terminate(self):
            self.terminated = True

        def kill(self):
            self.killed = True

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(gate.subprocess, "Popen", P)
    monkeypatch.setattr(gate.subprocess, "run", lambda cmd, *a, **k: calls.append(cmd))
    monkeypatch.setattr(gate.se, "free_port", lambda: 1)
    monkeypatch.setattr(gate.sys, "platform", "linux")
    ca = gate.ensure_ca(tmp_path / "w")
    assert ca.read_text() == "CA" and started[0].terminated and calls == []          # no taskkill on Linux
    ca.unlink()
    monkeypatch.setattr(gate.sys, "platform", "win32")
    gate.ensure_ca(tmp_path / "w")
    assert calls == [["taskkill", "/PID", "777001", "/T", "/F"]] and not started[1].terminated
    # already exited: nothing is sent to anyone
    calls.clear()
    done = type("Done", (), {"poll": lambda self: 0, "pid": 5})()
    gate.stop_started(done)
    assert calls == []


def test_the_scenarios_own_host_holds_for_wire_dns_but_a_vendor_name_still_fails():
    allow = {"entries": [_entry(id="f", kind="file", values=["*"], approval={"by": "owner"})]}
    events = ALL_SENSORS + [
        _ev(scenario="page", sensor="pktmon", kind="dns-wire", value="www.anthropic.com"),
        _ev(scenario="startup-idle", sensor="pktmon", kind="dns-wire", value="incoming.telemetry.mozilla.org"),
    ]
    fail, lists = gate.judge(events, allow, {"page": ["www.anthropic.com"]}, ["C:/build"], [], 3, True, False)
    assert not any("anthropic" in x for x in lists["UNEXPECTED_DNS"])
    assert any("incoming.telemetry" in x for x in lists["UNEXPECTED_TELEMETRY"])      # still fails closed


def test_proposals_are_stable_patterns_not_random_names():
    assert al.generalise("file", "03c63d37.sqlite-wal") == "*.sqlite-wal"
    assert al.generalise("file", "prefs.js") == "prefs.js"
    assert al.generalise("udp", "0.0.0.0:55838") == "0.0.0.0:*"
    assert al.generalise("udp", "0.0.0.0:5353") == "0.0.0.0:5353"          # a fixed, low port stays exact


def test_benign_profile_folders_collapse_but_reporting_folders_stay_visible():
    assert al.generalise("file", "storage/default/moz-extension+++x/idb/1.sqlite") == "storage/*"
    assert al.generalise("file", "datareporting/state.json") == "datareporting/state.json"
    assert al.generalise("file", "saved-telemetry-pings/0a1b2c3d-1111-2222-3333-444455556666") == "saved-telemetry-pings/*"
    assert al.generalise("file", "{1a2b3c4d-1111-2222-3333-444455556666}.json") == "*.json"


def test_the_dns_witness_pseudo_scenario_knows_the_tests_own_hosts():
    allow = {"entries": [_entry(id="f", kind="file", values=["*"], approval={"by": "owner"})]}
    events = ALL_SENSORS + [_ev(scenario="(dns-controlled)", sensor="doh-server", kind="dns", value="www.anthropic.com"),
                            _ev(scenario="(dns-controlled)", sensor="doh-server", kind="dns", value="incoming.telemetry.mozilla.org")]
    fail, lists = gate.judge(events, allow, {"page": ["www.anthropic.com"]}, ["C:/build"], [], 3, True, False)
    assert not any("anthropic" in x for x in lists["UNEXPECTED_DNS"])
    assert any("incoming.telemetry" in x for x in lists["UNEXPECTED_TELEMETRY"])


def test_the_dns_witness_names_another_program_but_never_excuses_the_browser():
    allow = {"entries": [_entry(id="f", kind="file", values=["*"], approval={"by": "owner"})]}
    wire = _ev(sensor="pktmon", kind="dns-wire", value="incoming.telemetry.mozilla.org", rep=0)
    other = _ev(sensor="dns-client", kind="dns-attribution", value="incoming.telemetry.mozilla.org", rep=0,
                pid=42, in_build=False, detail="2026-10-04T04:23 pid 42 python.exe (C:/mozilla-build/python.exe)")
    fail, lists = gate.judge(ALL_SENSORS + [wire, other], allow, {}, ["C:/build"], [], 3, True, False)
    assert lists["OTHER_PROGRAMS"] and "python.exe" in lists["OTHER_PROGRAMS"][0]
    assert not lists["UNEXPECTED_TELEMETRY"]
    ours = dict(other, in_build=True, detail="2026-10-04T04:23 pid 7 firefox.exe (C:/build/firefox.exe)")
    fail, lists = gate.judge(ALL_SENSORS + [wire, ours], allow, {}, ["C:/build"], [], 3, True, False)
    assert lists["UNEXPECTED_TELEMETRY"] and any("BY THE BROWSER" in x for x in lists["UNEXPECTED_DNS"])
    fail, lists = gate.judge(ALL_SENSORS + [wire], allow, {}, ["C:/build"], [], 3, True, False)
    assert lists["UNEXPECTED_TELEMETRY"]                        # no witness: still fails closed
