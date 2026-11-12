"""P35: the work kind of a brief picks the cheapest capable model (models.yaml work_kinds)."""
import subprocess
from types import SimpleNamespace

import pytest

from fieldkit.lifecycle import counsel, models

D = models.load()
ALL = lambda r: {"ok": True}                                     # noqa: E731
GATHER = ["Gather the status of every task and write the sitrep",
          "Collect the open inbox items and summarize them",
          "Search the plan for rows that name P9 and list them",
          "Classify the parked ideas by topic",
          "Write up the report for the owner"]
CODE = ["Fix the defect in fieldkit/lifecycle/tasks.py and add a test",
        "Implement the hook that counts reads",
        "Change rules/CORE.md and commit on a branch"]


@pytest.fixture(autouse=True)
def _state(tmp_path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_LIFECYCLE_DIR", str(tmp_path / "lc"))


@pytest.mark.parametrize("brief", GATHER)
def test_a_gather_brief_never_routes_above_haiku(brief):
    assert counsel.work_kind(brief, D)["kind"] in counsel.CHEAP_KINDS
    assert counsel.apply_kind(brief, "sonnet", D, ALL)["model"] == "haiku"
    assert counsel.apply_kind(brief, "opus", D, ALL)["model"] == "haiku"        # an expensive ceiling is lowered too


@pytest.mark.parametrize("brief", CODE)
def test_a_code_brief_never_routes_below_sonnet(brief):
    assert counsel.work_kind(brief, D)["kind"] == "code"
    assert counsel.apply_kind(brief, "haiku", D, ALL)["model"] == "sonnet"      # raised
    assert counsel.apply_kind(brief, "sonnet", D, ALL)["model"] == "sonnet"
    assert counsel.model_for_kind("code", D, ALL)["advisor"] is True


def test_a_floor_word_makes_a_gather_brief_code():
    assert counsel.work_kind("Gather the status and run the tests", D)["kind"] == "code"


def test_the_protocol_tail_does_not_turn_a_gather_brief_into_code():
    tail = "\n---\nSIDE JOBS: x\n\nDONE PROTOCOL: 1. the tests covering your changes pass; commit on your own branch"
    assert counsel.work_kind("Gather the status of every task" + tail, D)["kind"] == "gather"


def test_every_cheap_kind_is_haiku_in_the_data_and_code_is_sonnet():
    kinds = D["work_kinds"]["kinds"]
    assert {k: kinds[k]["rung"] for k in counsel.CHEAP_KINDS} == {k: "haiku" for k in counsel.CHEAP_KINDS}
    assert kinds["code"]["rung"] == "sonnet" and kinds["design"]["rung"] == "sonnet"


def test_haiku_unavailable_falls_to_the_fallback_never_a_refusal():
    no_haiku = lambda r: {"ok": r != "haiku"}                    # noqa: E731
    r = counsel.apply_kind(GATHER[0], "sonnet", D, no_haiku)
    assert r["model"] == "sonnet" and not r["changed"]


def test_no_kind_leaves_the_router_alone():
    r = counsel.apply_kind("Make the thing nicer", "sonnet", D, ALL)
    assert r["model"] == "sonnet" and r["kind"] == "" and not r["changed"]


def test_a_pin_on_an_expensive_model_for_a_gather_brief_is_said_never_refused():
    note = counsel.waste_note(GATHER[0], "opus", D)
    assert note.startswith("WASTEFUL") and "haiku" in note
    assert counsel.waste_note(GATHER[0], "haiku", D) == ""
    assert counsel.waste_note(CODE[0], "opus", D) == ""


def test_prose_never_goes_above_haiku_and_a_reply_without_a_verdict_is_an_answer(monkeypatch):
    monkeypatch.setenv("FIELDKIT_REVIEWER_CMD", '["fake", "{model}"]')
    seen = {}

    def run(cmd, **kw):
        seen["cmd"], seen["env"] = cmd, kw["env"]
        return SimpleNamespace(returncode=0, stdout="two tasks run, none blocked", stderr="")
    r = counsel.prose_ask("facts", D, run=run)
    assert r["ok"] and r["model"] == "haiku" and seen["cmd"] == ["fake", "haiku"]
    assert models.is_available("haiku")                          # no VERDICT line is not a reason to bench Haiku
    assert seen["env"]["CLAUDE_CODE_DISABLE_ADVISOR_TOOL"] == "1"


def test_prose_refuses_when_only_a_dearer_model_is_left(monkeypatch):
    monkeypatch.setenv("FIELDKIT_REVIEWER_CMD", '["fake", "{model}"]')
    d = dict(D, availability=dict(D["availability"], unavailable={"haiku": "always"}))
    r = counsel.prose_ask("facts", d, run=lambda *a, **k: pytest.fail("must not run"))
    assert not r["ok"] and r["model"] == ""


def test_task_start_uses_the_kind_and_warns_on_a_wasteful_pin(tmp_path, monkeypatch):
    from fieldkit.lifecycle import tasks, routing
    monkeypatch.setattr(routing, "admit", lambda *a, **k: (True, ""))
    monkeypatch.setattr(routing, "log", lambda r: None)
    monkeypatch.setattr(tasks, "_append", lambda r: None)
    monkeypatch.setattr(tasks, "_room", lambda *a: None)
    monkeypatch.setattr(tasks, "ledger", lambda: {})
    b = tmp_path / "brief.md"
    b.write_text("Gather the status of every task.\nPLAN ITEM: P34\n", encoding="utf-8")
    kw = dict(cwd=str(tmp_path), worktree=lambda r, t: (None, "t"), launch=lambda a: {"pid": 1, "log": "l"},
              plan="P34", allow_expensive=True, again=True)
    rec = tasks.start(str(b), **kw)
    assert rec["model"] == "haiku"
    rec = tasks.start(str(b), model="opus", pin=True, **kw)
    assert rec["model"] == "opus" and any(w.startswith("WASTEFUL") for w in rec["warnings"])
