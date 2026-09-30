"""The agent interface: refuses what it should, runs what it may, and undoes a failed change by itself."""
import sys

import pytest

from fieldkit import agent
from fieldkit.desk import cards as cardmod
from fieldkit.office import create, scrub

PY = sys.executable


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "RUNS", tmp_path / "runs")
    monkeypatch.setattr(agent, "BACKUPS", tmp_path / "backups")
    monkeypatch.setattr(cardmod, "test_results", lambda: {})


def _card(**kw):
    c = {"id": "t", "title": "Write a greeting file", "path": None, "entry": [PY, "-c", "print('hi')"],
         "inputs": [], "effects": [], "safety": "read-only", "modes": {}, "tests": ["x"], "platforms": [],
         "draft": False, "reviewed": True, "portable": True, "probe": "safe", "scope": []}
    c.update(kw)
    return c


def test_draft_cards_are_refused():
    c = _card(draft=True, reviewed=False)
    with pytest.raises(agent.Refused, match="not ready for an agent"):
        agent.run("t", {}, cards=[c])


def test_inputs_are_checked_before_anything_runs():
    c = _card(inputs=[{"name": "size", "flag": "--size", "type": "int", "required": True},
                      {"name": "mode", "flag": "--mode", "type": "str", "choices": ["fit", "fill"]}])
    with pytest.raises(agent.Refused, match="unknown input"):
        agent.run("t", {"size": 1, "colour": "red"}, cards=[c])
    with pytest.raises(agent.Refused, match="missing required"):
        agent.run("t", {}, cards=[c])
    with pytest.raises(agent.Refused, match="must be int"):
        agent.run("t", {"size": "big"}, cards=[c])
    with pytest.raises(agent.Refused, match="one of"):
        agent.run("t", {"size": 3, "mode": "stretch"}, cards=[c])


def test_argv_order_positionals_then_flags():
    c = _card(entry=["tool"], inputs=[{"name": "src", "flag": None, "type": "str"},
                                      {"name": "v", "flag": "-v", "type": "flag"},
                                      {"name": "tag", "flag": "--tag", "type": "list"}])
    assert agent.build_argv(c, {"src": "a.png", "v": True, "tag": ["x", "y"]}) == ["tool", "a.png", "-v", "--tag", "x", "y"]


def test_read_only_runs_directly():
    rec = agent.run("t", {}, cards=[_card()])
    assert rec["status"] == "DONE" and "hi" in rec["output"] and rec["answer"][-1].startswith("NEXT:")


def test_irreversible_needs_approval(tmp_path):
    c = _card(safety="irreversible")
    with pytest.raises(agent.Refused, match="approve=true"):
        agent.run("t", {}, cards=[c])


def test_change_without_preview_says_so():
    c = _card(safety="reversible")
    with pytest.raises(agent.Refused, match="no preview"):
        agent.run("t", {}, mode="preview", cards=[c])


def test_failed_verify_restores_the_file(tmp_path):
    target = tmp_path / "data.txt"
    target.write_text("original")
    breaker = [PY, "-c", "import sys, pathlib; pathlib.Path(sys.argv[1]).write_text('BROKEN')"]
    c = _card(safety="reversible", entry=breaker, scope=["file"],
              inputs=[{"name": "file", "flag": None, "type": "str", "required": True}],
              modes={"verify": [{"command": [PY, "-c", "import sys, pathlib; "
                                              "sys.exit(pathlib.Path(sys.argv[1]).read_text() != 'fixed')", "{file}"]}]})
    rec = agent.run("t", {"file": str(target)}, cards=[c])
    assert rec["status"].startswith("FAILED - files restored") and target.read_text() == "original"


def test_file_created_by_a_failed_run_is_removed(tmp_path):
    target = tmp_path / "new.txt"
    maker = [PY, "-c", "import sys, pathlib; pathlib.Path(sys.argv[1]).write_text('x'); sys.exit(1)"]
    c = _card(safety="reversible", entry=maker, scope=["file"],
              inputs=[{"name": "file", "flag": None, "type": "str", "required": True}])
    agent.run("t", {"file": str(target)}, cards=[c])
    assert not target.exists()


def test_real_card_office_scrub_preview_apply_verify_undo(tmp_path):
    doc = tmp_path / "essay.docx"
    create.create({"type": "docx", "author": "Jane Public", "blocks": [{"paragraph": "Body text."}]}, doc)
    c = [x for x in cardmod.all_cards() if x["id"] == "office-scrub"][0]
    prev = agent.run("office-scrub", {"file": str(doc)}, mode="preview", cards=[c])
    assert prev["status"].startswith("PREVIEW") and "Jane Public" in prev["output"]
    assert scrub.inspect(doc, [])                                      # nothing changed by the preview
    done = agent.run("office-scrub", {"file": str(doc)}, cards=[c])
    assert done["status"] == "DONE and verified", done["answer"]
    assert scrub.inspect(doc, []) == []
    agent.undo(done["run_id"])
    assert ("properties", "creator", "Jane Public") in scrub.inspect(doc, [])


def test_discover_prefers_trusted_and_skips_drafts():
    cs = [_card(id="scrub-a", title="remove names from office files"),
          _card(id="scrub-b", title="remove names from office files", draft=True, reviewed=False)]
    hits = agent.discover("remove names from a word file", cards=cs)
    assert [h["tool"] for h in hits] == ["scrub-a"]
    assert len(agent.discover("remove names", cards=cs, include_drafts=True)) == 2


def test_system_changes_need_the_owner_even_when_reversible(tmp_path):
    c = _card(safety="reversible", effects=["registry"], modes={"preview": ["+", "--dry"]},
              entry=[PY, "-c", "import sys; print('would set', sys.argv[1:])"])
    prev = agent.run("t", {}, mode="preview", cards=[c])
    assert prev["answer"][-1].startswith("NEXT: show this preview to the owner")
    with pytest.raises(agent.Refused, match=r"changes the system \(registry\)"):
        agent.run("t", {}, cards=[c])
    assert agent.run("t", {}, approve=True, cards=[c])["ok"]


def test_undo_runs_the_cards_own_undo_command(tmp_path):
    flag = tmp_path / "state.txt"
    c = _card(safety="reversible", effects=["registry"],
              entry=[PY, "-c", f"import pathlib; pathlib.Path(r'{flag}').write_text('applied')"],
              modes={"undo": [PY, "-c", f"import pathlib; pathlib.Path(r'{flag}').write_text('original')"]})
    rec = agent.run("t", {}, approve=True, cards=[c])
    assert flag.read_text() == "applied"
    out = agent.undo(rec["run_id"], cards=[c])
    assert flag.read_text() == "original" and out["answer"][0].startswith("UNDO COMMAND ran")
    with pytest.raises(agent.Refused, match="already undone"):
        agent.undo(rec["run_id"], cards=[c])

