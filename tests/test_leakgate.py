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


def test_approval_refused_without_a_terminal(tmp_path):
    p = tmp_path / "allow.json"
    al.save(p, {"version": 1, "entries": [_entry()]})
    with pytest.raises(task.Refused, match="real terminal"):
        al.approve(p, {"e1"}, terminal=False)
    assert al.approve(p, {"*proposed*"}, say=lambda m: None, terminal=True) == ["e1"]
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
