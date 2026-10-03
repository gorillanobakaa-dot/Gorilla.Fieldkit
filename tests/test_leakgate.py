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
    assert approvers == ["allow.py:approve"] and offenders == []
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
