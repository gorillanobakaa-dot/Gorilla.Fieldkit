"""A person's edit that no patch asked for is recorded as done hand-port steps, one per hunk, with the diff as the
hunk, so every later judge treats it like any other hand port (live run 16: the MOZ_WEBSPEECH gate across eight
files, upstream code reaching into a component the owner switched off)."""
import subprocess

import pytest

from fieldkit.buildh import handedit, task


def _repo(tmp_path):
    w = tmp_path / "w"
    (w / "dom").mkdir(parents=True)
    (w / "dom/P.ipdl").write_text("include protocol PA;\ninclude protocol PSpeech;\nasync protocol P {};\n", encoding="utf-8")
    (w / "dom/other.cpp").write_text("int x;\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(w)], check=True)
    subprocess.run(["git", "-C", str(w), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(w), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "pristine"], check=True)
    return w


def test_an_edit_becomes_done_hand_steps_and_a_checkpoint(tmp_path, monkeypatch):
    w = _repo(tmp_path)
    t = {"id": "he", "workflow": "firefox-upgrade", "workdir": str(w), "approved": True, "checkpoints": [], "meta": {},
         "steps": [{"id": "final-checks", "kind": "script", "status": "pending"}]}
    monkeypatch.setattr(task, "load", lambda tid: t)
    monkeypatch.setattr(task, "save", lambda t_: None)
    monkeypatch.setattr(task, "journal", lambda t_, ev, **kw: t_.setdefault("_j", []).append((ev, kw)))
    (w / "dom/P.ipdl").write_text("include protocol PA;\n#ifdef MOZ_WEBSPEECH\ninclude protocol PSpeech;\n#endif\nasync protocol P {};\n", encoding="utf-8")
    ids = handedit.record("he", ["dom/P.ipdl"], "gate on MOZ_WEBSPEECH")
    assert ids == [s["id"] for s in t["steps"] if s["id"].startswith("hand-")] and len(ids) == 1
    s = t["steps"][0]
    assert s["status"] == "done" and s["done_by"] == "hand" and s["hand_port"] and s["args"]["file"] == "dom/P.ipdl"
    assert any(l.startswith("+#ifdef MOZ_WEBSPEECH") for l in s["args"]["hunk"]["lines"])
    assert t["steps"][-1]["id"] == "final-checks"                       # recorded before the final checks
    assert t["_j"][0][0] == "hand-edit" and t["_j"][0][1]["files"] == ["dom/P.ipdl"]
    assert not subprocess.run(["git", "-C", str(w), "status", "--porcelain"], capture_output=True, text=True).stdout.strip()
    # the recorded hunk is judged like a hand port
    from fieldkit.buildh import firefox
    now = (w / "dom/P.ipdl").read_text(encoding="utf-8").splitlines()
    assert firefox.hand_port_holds(now, s["args"]["hunk"]) == []


def test_record_refuses_blind_and_partial_records(tmp_path, monkeypatch):
    w = _repo(tmp_path)
    t = {"id": "he", "workflow": "firefox-upgrade", "workdir": str(w), "approved": True, "checkpoints": [], "meta": {}, "steps": []}
    monkeypatch.setattr(task, "load", lambda tid: t)
    with pytest.raises(task.Refused, match="no diff"):
        handedit.record("he", ["dom/P.ipdl"], "nothing changed")
    (w / "dom/P.ipdl").write_text("changed\n", encoding="utf-8")
    (w / "dom/other.cpp").write_text("int y;\n", encoding="utf-8")
    with pytest.raises(task.Refused, match="other files changed too"):
        handedit.record("he", ["dom/P.ipdl"], "only one named")
