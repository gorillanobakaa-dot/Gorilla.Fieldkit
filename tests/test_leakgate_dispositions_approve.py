"""`leakgate-dispositions`: a model's proposals become approved dispositions only at the owner's real terminal."""
import json

import pytest

from fieldkit.buildh import task
from fieldkit.leakgate import dispositions as ld


def _files(tmp_path):
    prop = tmp_path / "proposed-dispositions-build27.json"
    prop.write_text(json.dumps({
        "host:a.example": {"disposition": "dead-caller-cut", "evidence": "caller cut", "approval": None},
        "host:b.example": {"disposition": "OPEN", "evidence": "nothing stops it", "approval": None},
        "host:c.example": {"disposition": "cut-in-source", "evidence": "x", "approval": None, "proposal": {"needs_fix": True}},
        "exe:d.dll": {"disposition": "OWNER-DECISION", "evidence": "ship or drop", "approval": None},
    }), encoding="utf-8")
    disp = tmp_path / "dispositions.json"
    disp.write_text(json.dumps({"old/file.cpp": {"disposition": "not-built", "approval": {"by": "owner"}}}), encoding="utf-8")
    return disp, prop


def test_an_agent_shell_is_refused_and_nothing_is_written(tmp_path, monkeypatch):
    disp, prop = _files(tmp_path)
    before = disp.read_bytes()
    monkeypatch.setattr(task, "owner_terminal", lambda: False)
    with pytest.raises(task.Refused):
        ld.approve_from(disp, prop, say=lambda m: None)
    assert disp.read_bytes() == before


def test_the_blanket_never_takes_open_needs_fix_or_owner_decisions(tmp_path, monkeypatch):
    disp, prop = _files(tmp_path)
    monkeypatch.setattr(task, "owner_terminal", lambda: True)
    said = []
    r = ld.approve_from(disp, prop, say=said.append)
    assert r["approved"] == ["host:a.example"]
    assert {k for k, _ in r["left"]} == {"host:b.example", "host:c.example", "exe:d.dll"}
    data = json.loads(disp.read_text(encoding="utf-8"))
    assert data["host:a.example"]["approval"]["how"] == "terminal"
    assert data["old/file.cpp"]["approval"] == {"by": "owner"}          # earlier approvals stay as they are
    assert "host:b.example" not in data and "exe:d.dll" not in data
    assert any("host:a.example" in m and "caller cut" in m for m in said)   # shown in full before it is written


def test_an_owner_decision_is_approved_only_when_named(tmp_path, monkeypatch):
    disp, prop = _files(tmp_path)
    monkeypatch.setattr(task, "owner_terminal", lambda: True)
    r = ld.approve_from(disp, prop, named={"exe:d.dll", "host:b.example"}, say=lambda m: None)
    assert "exe:d.dll" in r["approved"]
    assert "host:b.example" not in r["approved"]                        # OPEN stays out even when named
