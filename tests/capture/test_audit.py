import json
from types import SimpleNamespace

import pytest

from fieldkit import mcp
from fieldkit.capture import audit
from fieldkit.peers import link, link_probe


def test_local_audit_reports_missing_and_disposition_without_touching_fixture(tmp_path, monkeypatch):
    root = tmp_path / "scratch"
    sample = root / "job with spaces"
    sample.mkdir(parents=True)
    file = sample / "a.py"
    file.write_bytes(b"abc")
    before = file.stat()
    missing = tmp_path / "missing"
    monkeypatch.setattr(audit.cleanslate, "scratch_roots", lambda **kw: [root, missing])
    monkeypatch.setattr(audit.cleanslate, "_protected", lambda: [])
    monkeypatch.setattr(audit.cleanslate, "dispositions", lambda: {})
    monkeypatch.setattr(audit.retention, "active_paths", lambda _: [])
    monkeypatch.setattr(audit.size, "locate", lambda: tmp_path / "gorilla-size")
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        assert argv[-1] == str(sample)
        assert "shell" not in kwargs
        data = {"roots": [{"path": str(sample), "totals": {"bytes": 3, "files": 1, "unreadable": 0}}]}
        return SimpleNamespace(returncode=0, stdout=json.dumps(data))

    monkeypatch.setattr(audit.subprocess, "run", run)
    result = audit.local()
    rows = {row["path"]: row for row in result["roots"]}
    assert rows[str(missing)]["exists"] is False
    assert rows[str(root)]["cleanup"] == "needs-disposition"
    assert (rows[str(root)]["files"], rows[str(root)]["bytes"]) == (1, 3)
    assert result["size_calls"] == len(calls) == 1
    assert file.read_bytes() == b"abc"
    assert file.stat().st_mtime_ns == before.st_mtime_ns
    assert list(sample.iterdir()) == [file]


@pytest.mark.parametrize(("choice", "expected"), [
    ("cable", ["cable"]), ("ssh", ["ssh"]), ("both", ["cable", "ssh"]),
])
@pytest.mark.parametrize("reachable", [True, False])
def test_only_chosen_routes_and_one_inventory(monkeypatch, choice, expected, reachable):
    monkeypatch.setattr(link, "household", lambda: {
        "me": "here", "devices": [{"name": "peer", "address": ["192.0.2.1"]}]})
    attempts = []
    report = {"schema": 1, "host": "remote", "roots": []}

    def cable(*_):
        attempts.append("cable")
        return {"ok": True, "data": report} if reachable else {"error": "cable unavailable"}

    def ssh(_, previous):
        attempts.append("ssh")
        if not reachable:
            return {"error": "ssh unavailable"}
        return {"host": previous, "already_audited": True} if previous else report

    monkeypatch.setattr(link, "cable_probe", cable)
    monkeypatch.setattr(audit, "_ssh_audit", ssh)
    result = audit.remote("peer", choice)
    assert attempts == expected
    assert result["hosts"] == ([report] if reachable else [])
    assert sum("error" in route for route in result["routes"].values()) == (0 if reachable else len(expected))


def test_fixed_probe_and_audit_only_model_surface(monkeypatch):
    report = {"roots": [], "errors": []}
    monkeypatch.setattr(audit, "local", lambda: report)
    monkeypatch.setenv("FIELDKIT_AUDIT_ONLY", "1")
    assert [t["name"] for t in mcp.offered()] == ["clean_slate_audit"]
    assert mcp.call_tool("run", {})[1] is True
    assert mcp.call_tool("clean_slate_audit", {}) == (json.dumps(report), False)
    assert link_probe.run("clean-slate-audit")["data"] == report
    assert not link_probe.run("clean-slate-audit", "arbitrary path")["ok"]
