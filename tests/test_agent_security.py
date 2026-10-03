"""The agent door's security holes (review of 2026-10-02): each refusal must leave every file untouched."""
import json
import shutil
import sys
import time

import pytest

from fieldkit import agent, mcp
from fieldkit.desk import cards as cardmod

PY = sys.executable
WRITER = [PY, "-c", "import sys, pathlib; pathlib.Path(sys.argv[1]).write_text('changed')"]


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "RUNS", tmp_path / "runs")
    monkeypatch.setattr(agent, "BACKUPS", tmp_path / "backups")
    monkeypatch.setattr(cardmod, "test_results", lambda: {})


def _card(**kw):
    c = {"id": "t", "title": "Write a file", "path": None, "entry": [PY, "-c", "print('hi')"],
         "inputs": [], "effects": [], "safety": "read-only", "modes": {}, "tests": ["x"], "platforms": [],
         "draft": False, "reviewed": True, "portable": True, "probe": "safe", "scope": []}
    c.update(kw)
    return c


def _file_card(**kw):
    kw.setdefault("entry", WRITER)
    return _card(safety="reversible", scope=["file"],
                 inputs=[{"name": "file", "flag": None, "type": "str", "required": True}], **kw)


def _snapshot(root):
    return {p.relative_to(root).as_posix(): (p.read_bytes() if p.is_file() else None)
            for p in sorted(root.rglob("*")) if "runs" not in p.parts and "backups" not in p.parts}


def _tamper(run_id, fn):
    p = agent.RUNS / f"{run_id}.json"
    rec = json.loads(p.read_text(encoding="utf-8"))
    fn(rec)
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        json.dump(rec, f)


def _write(p, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(text.encode("utf-8"))


# -- 1. run ids cannot escape the journal ------------------------------------------------------
@pytest.mark.parametrize("bad", ["../decoy", "..\\decoy", "../../x", "20261002-142530-ABCDEF",
                                 "20261002-142530-a1b2c3/../../decoy", "", None, 5])
def test_undo_refuses_a_run_id_not_in_the_generated_format(tmp_path, bad):
    victim = tmp_path / "victim.txt"
    _write(victim, "keep me")
    _write(tmp_path / "evil" / "0" / "victim.txt", "EVIL")
    decoy = {"run_id": "../decoy", "tool": "t", "scope": [str(tmp_path)], "inputs": {},
             "backup": [{"path": str(victim), "kind": "file", "copy": str(tmp_path / "evil" / "0" / "victim.txt"),
                         "existed": True}]}
    _write(tmp_path / "decoy.json", json.dumps(decoy))
    before = _snapshot(tmp_path)
    with pytest.raises(agent.Refused, match="not a run id") as e:
        agent.undo(bad, cards=[_file_card()])
    assert "NEXT:" in str(e.value) and _snapshot(tmp_path) == before


def test_run_ids_from_run_are_accepted(tmp_path):
    target = tmp_path / "a.txt"
    _write(target, "original")
    rec = agent.run("t", {"file": str(target)}, cards=[_file_card()])
    assert agent.RUN_ID.fullmatch(rec["run_id"])
    agent.undo(rec["run_id"], cards=[_file_card()])
    assert target.read_text() == "original"


# -- 2. restore only from this run's backups, only into its recorded scope -----------------------
def test_run_records_the_resolved_scope(tmp_path):
    target = tmp_path / "sub" / ".." / "a.txt"
    _write(tmp_path / "a.txt", "original")
    rec = agent.run("t", {"file": str(target)}, cards=[_file_card()])
    assert rec["scope"] == [str((tmp_path / "a.txt").resolve())]


def test_undo_refuses_a_backup_copy_outside_the_runs_own_backup_folder(tmp_path):
    target = tmp_path / "a.txt"
    _write(target, "original")
    rec = agent.run("t", {"file": str(target)}, cards=[_file_card()])
    other = agent.run("t", {"file": str(target)}, cards=[_file_card()])     # a second run's backup folder
    evil = tmp_path / "evil.txt"
    _write(evil, "EVIL")
    for copy in (str(evil), other["backup"][0]["copy"], str(agent.BACKUPS / rec["run_id"] / ".." / "x")):
        _tamper(rec["run_id"], lambda r: r["backup"][0].update(copy=copy))
        before = _snapshot(tmp_path)
        with pytest.raises(agent.Refused, match="outside this run's backup folder") as e:
            agent.undo(rec["run_id"], cards=[_file_card()])
        assert "NEXT:" in str(e.value) and _snapshot(tmp_path) == before


def test_undo_refuses_a_path_outside_the_recorded_scope_and_changes_nothing(tmp_path):
    target = tmp_path / "a.txt"
    victim = tmp_path / "victim.txt"
    _write(target, "original")
    _write(victim, "keep me")
    rec = agent.run("t", {"file": str(target)}, cards=[_file_card()])
    good = rec["backup"][0]
    bad = dict(good, path=str(victim))                                    # one valid entry, one hostile one
    deleter = {"path": str(victim), "kind": "absent", "copy": None, "existed": False}
    for extra in (bad, deleter):
        _tamper(rec["run_id"], lambda r: r.__setitem__("backup", [good, extra]))
        before = _snapshot(tmp_path)
        with pytest.raises(agent.Refused, match="outside the run's scope") as e:
            agent.undo(rec["run_id"], cards=[_file_card()])
        assert "NEXT:" in str(e.value) and _snapshot(tmp_path) == before
        assert target.read_text() == "changed" and victim.read_text() == "keep me"


def test_undo_refuses_a_journal_without_a_scope_record(tmp_path):
    target = tmp_path / "a.txt"
    _write(target, "original")
    rec = agent.run("t", {"file": str(target)}, cards=[_file_card()])
    _tamper(rec["run_id"], lambda r: r.pop("scope"))
    with pytest.raises(agent.Refused, match="no usable scope record") as e:
        agent.undo(rec["run_id"], cards=[_file_card()])
    assert "NEXT:" in str(e.value) and target.read_text() == "changed"


def test_undo_refuses_a_journal_naming_another_run(tmp_path):
    target = tmp_path / "a.txt"
    _write(target, "original")
    rec = agent.run("t", {"file": str(target)}, cards=[_file_card()])
    _tamper(rec["run_id"], lambda r: r.update(run_id="20200101-000000-abcdef"))
    with pytest.raises(agent.Refused, match="different run") as e:
        agent.undo(rec["run_id"], cards=[_file_card()])
    assert "NEXT:" in str(e.value) and target.read_text() == "changed"


# -- 3. folders in scope are backed up; anything that cannot be backed up refuses the apply ------------
FOLDER_BREAKER = [PY, "-c", "import sys, pathlib; d = pathlib.Path(sys.argv[1]); (d / 'a.txt').write_text('BROKEN'); "
                            "(d / 'new.txt').write_text('new'); (d / 'sub' / 'b.txt').unlink(); sys.exit(1)"]


def _folder_card(entry):
    return _card(safety="reversible", scope=["dir"], entry=entry,
                 inputs=[{"name": "dir", "flag": None, "type": "str", "required": True}])


def test_folder_in_scope_is_backed_up_and_restored_after_a_failed_run(tmp_path):
    d = tmp_path / "work"
    _write(d / "a.txt", "one")
    _write(d / "sub" / "b.txt", "two")
    before = _snapshot(d)
    rec = agent.run("t", {"dir": str(d)}, cards=[_folder_card(FOLDER_BREAKER)])
    assert rec["status"] == "FAILED - files restored from backup"
    assert _snapshot(d) == before


def test_folder_run_is_undone_and_its_changes_are_listed(tmp_path):
    d = tmp_path / "work"
    _write(d / "a.txt", "one")
    _write(d / "sub" / "b.txt", "two")
    before = _snapshot(d)
    c = _folder_card(FOLDER_BREAKER[:-1] + [FOLDER_BREAKER[-1].replace("; sys.exit(1)", "")])
    rec = agent.run("t", {"dir": str(d)}, cards=[c])
    assert rec["status"] == "DONE (not verified)" and rec["changed"] == [str(d.resolve())]
    assert any(l.startswith("  changed:") for l in rec["answer"])
    assert (d / "new.txt").exists()
    agent.undo(rec["run_id"], cards=[c])
    assert _snapshot(d) == before


def test_scope_entry_that_cannot_be_backed_up_refuses_the_apply(tmp_path, monkeypatch):
    marker = tmp_path / "ran.txt"
    target = tmp_path / "a.txt"
    _write(target, "original")
    c = _file_card(entry=[PY, "-c", f"import pathlib; pathlib.Path(r'{marker}').write_text('ran')", "x"])

    def broken_copy(*a, **k):
        raise PermissionError("locked")
    monkeypatch.setattr(agent.shutil, "copy2", broken_copy)
    with pytest.raises(agent.Refused, match="could not back up") as e:
        agent.run("t", {"file": str(target)}, cards=[c])
    assert "NEXT:" in str(e.value) and not marker.exists() and target.read_text() == "original"
    assert not agent.BACKUPS.exists() or not any(agent.BACKUPS.iterdir())   # no half backup left


def test_folder_holding_fieldkits_own_backups_is_refused(tmp_path):
    marker = tmp_path / "ran.txt"
    c = _folder_card([PY, "-c", f"import pathlib; pathlib.Path(r'{marker}').write_text('ran')", "x"])
    with pytest.raises(agent.Refused, match="holds fieldkit's own backups") as e:
        agent.run("t", {"dir": str(tmp_path)}, cards=[c])
    assert "NEXT:" in str(e.value) and not marker.exists()


# -- 4. inputs starting with "-" -------------------------------------------------------------------
def test_inputs_starting_with_a_dash_are_refused_unless_the_card_allows_them(tmp_path):
    marker = tmp_path / "ran.txt"
    entry = [PY, "-c", f"import pathlib; pathlib.Path(r'{marker}').write_text('ran')"]
    specs = [{"name": "src", "flag": None, "type": "str"}, {"name": "tag", "flag": "--tag", "type": "list"},
             {"name": "n", "flag": None, "type": "int"}]
    c = _card(entry=entry, inputs=specs)
    for bad in ({"src": "-rf"}, {"src": "--output=x"}, {"tag": ["ok", "--evil"]}, {"n": -3}):
        with pytest.raises(agent.Refused, match="starts with '-'") as e:
            agent.run("t", bad, cards=[c])
        assert "NEXT:" in str(e.value) and not marker.exists()
    allowed = _card(entry=entry, inputs=[dict(specs[0], allow_dash=True)])
    assert agent.run("t", {"src": "-rf"}, cards=[allowed])["ok"] and marker.exists()
    chosen = _card(entry=entry, inputs=[dict(specs[0], choices=["-v", "-q"])])
    assert agent.check_inputs(chosen, {"src": "-v"}) == {"src": "-v"}
    assert agent.check_inputs(c, {"src": "a-b", "n": 3}) == {"src": "a-b", "n": 3}


# -- 5. verify and undo commands have a timeout -----------------------------------------------------
SLEEP = [PY, "-c", "import time; time.sleep(60)"]


def test_verify_command_times_out(tmp_path):
    target = tmp_path / "a.txt"
    _write(target, "original")
    c = _file_card(modes={"verify": [{"command": SLEEP}]})
    t0 = time.monotonic()
    rec = agent.run("t", {"file": str(target)}, cards=[c], timeout=3)
    assert time.monotonic() - t0 < 30
    assert rec["verified"] is False and target.read_text() == "original"


def test_undo_command_times_out(tmp_path):
    c = _card(safety="reversible", modes={"undo": SLEEP})
    rec = agent.run("t", {}, cards=[c])
    t0 = time.monotonic()
    out = agent.undo(rec["run_id"], cards=[c], timeout=3)
    assert time.monotonic() - t0 < 30
    assert out["ok"] is False and out["answer"][-1].startswith("NEXT:")


# -- 6. MCP isError follows a failed verify ---------------------------------------------------------
def test_mcp_marks_a_read_only_tool_with_failed_verify_as_an_error(monkeypatch):
    failing = _card(modes={"verify": [{"output_contains": "never-printed"}]})
    monkeypatch.setattr(agent, "_cards", lambda: [failing])
    text, err = mcp.call_tool("run", {"tool": "t"})
    assert err is True and "FAIL" in text and text.splitlines()[-1].startswith("NEXT:")
    passing = _card(modes={"verify": [{"output_contains": "hi"}]})
    monkeypatch.setattr(agent, "_cards", lambda: [passing])
    text, err = mcp.call_tool("run", {"tool": "t"})
    assert err is False


# -- 7. undo does not overwrite edits made after the run --------------------------------------------
def test_undo_refuses_to_overwrite_later_edits_unless_the_owner_approves(tmp_path, monkeypatch):
    target = tmp_path / "a.txt"
    _write(target, "original")
    c = _file_card()
    rec = agent.run("t", {"file": str(target)}, cards=[c])
    assert target.read_text() == "changed"
    _write(target, "the owner's later edit")
    with pytest.raises(agent.Refused, match="changed after run") as e:
        agent.undo(rec["run_id"], cards=[c])
    assert "--approve" in str(e.value) and "NEXT:" in str(e.value)
    assert target.read_text() == "the owner's later edit"
    monkeypatch.setattr(agent, "_cards", lambda: [c])                   # the MCP door cannot force it
    text, err = mcp.call_tool("undo", {"run_id": rec["run_id"], "approve": True})
    assert text.startswith("REFUSED:") and target.read_text() == "the owner's later edit"
    agent.undo(rec["run_id"], cards=[c], approve=True)
    assert target.read_text() == "original"


def test_undo_refuses_when_a_file_deleted_by_the_run_was_recreated(tmp_path):
    target = tmp_path / "a.txt"
    _write(target, "original")
    c = _file_card(entry=[PY, "-c", "import sys, pathlib; pathlib.Path(sys.argv[1]).unlink()"])
    rec = agent.run("t", {"file": str(target)}, cards=[c])
    assert not target.exists()
    _write(target, "new work")
    with pytest.raises(agent.Refused, match="changed after run"):
        agent.undo(rec["run_id"], cards=[c])
    assert target.read_text() == "new work"


def test_mcp_offers_no_approval_anywhere():
    for t in mcp.TOOLS:
        assert "approve" not in json.dumps(t["inputSchema"])


# -- 8. tidy -----------------------------------------------------------------------------------------
def test_call_tool_has_its_docstring_and_the_journal_lists_changes(tmp_path):
    assert mcp.call_tool.__doc__.startswith("-> (text, is_error)")
    target = tmp_path / "a.txt"
    _write(target, "changed")                                            # the run writes the same bytes
    rec = agent.run("t", {"file": str(target)}, cards=[_file_card()])
    saved = json.loads((agent.RUNS / f"{rec['run_id']}.json").read_text(encoding="utf-8"))
    assert saved["changed"] == [] and saved["backup"][0]["before"] == saved["backup"][0]["after"]
    assert "  changed: none of the files in scope" in rec["answer"]
