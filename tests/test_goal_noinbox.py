"""F2.4: a goal run with no pre-approval files puts 0 items in the owner's inbox: decisions take the recorded default,
an uncovered action is recorded "not done", the finish report is REPORT.md and a Board line."""
import pytest

from fieldkit.autopilot import goal, hardlines, inbox, runbook
from fieldkit.lifecycle import board


@pytest.fixture(autouse=True)
def iso(tmp_path, monkeypatch):
    st = tmp_path / "ap"
    st.mkdir()
    monkeypatch.setattr(hardlines, "STATE", st)
    monkeypatch.setattr(hardlines, "STOP_FILE", st / "STOP")
    monkeypatch.setattr(inbox, "INBOX", st / "inbox")
    monkeypatch.setattr(goal, "GOALS", tmp_path / "goals")
    monkeypatch.setattr(runbook, "JOBS", tmp_path / "jobs")
    monkeypatch.setenv("FIELDKIT_AUTOPILOT_STATE", str(st))
    monkeypatch.setenv("FIELDKIT_COORDINATION_DIR", str(tmp_path / "coord"))


def mk(tmp_path, **extra):
    goal.new(dict({"id": "g1", "objective": "o", "cwd": str(tmp_path), "criteria": [{"id": "c1", "run": "exit 1"}]},
                  **extra))
    return goal.load("g1")


def boom(*a, **kw):
    raise AssertionError(f"the supervisor filed an inbox item: {a}")


def test_a_question_with_no_pre_approval_takes_the_default_and_files_nothing(tmp_path):
    g, st = mk(tmp_path)
    plan = {"jobs": [], "questions": [{"id": "q1", "title": "which db", "why": "w", "category": "design",
                                       "default": "sqlite"}]}
    goal.apply_plan("g1", g, st, plan, {}, boom, say=lambda m: None)
    assert st["answers"]["q1"]["answer"] == "sqlite" and "default" in st["answers"]["q1"]["by"]
    assert inbox.items(None) == []


def test_an_action_no_pre_approval_covers_is_recorded_not_done_and_files_nothing(tmp_path):
    g, st = mk(tmp_path, on_done=[{"kind": "push_branch", "repo": str(tmp_path), "branch": "feat/x"}])
    goal._actions("g1", g, st, {}, lambda *a, **k: (True, "ran"), lambda m: None)
    (rec,) = st["actions"].values()
    assert rec["ok"] is False and "not done" in rec["why"]
    assert inbox.items(None) == []


def test_the_finish_report_goes_to_report_md_and_the_board_not_the_inbox(tmp_path):
    g, st = mk(tmp_path)
    plan = board.plan_file()
    plan.parent.mkdir(parents=True)
    plan.write_text("| who | what | where | x | y | z |\n|---|---|---|---|---|---|\n", encoding="utf-8")
    goal._finish("g1", st, "done", "every criterion PASSed", boom)
    assert (goal.gdir("g1") / "REPORT.md").is_file()
    assert "goal g1: done" in plan.read_text(encoding="utf-8")
    assert inbox.items(None) == []


def test_no_board_means_no_note_and_no_crash(tmp_path):
    assert board.note("x", "y") is False                         # PLAN.md is never made
    assert not (tmp_path / "coord" / "PLAN.md").exists()
