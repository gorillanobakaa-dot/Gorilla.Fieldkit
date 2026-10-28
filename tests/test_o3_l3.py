"""HARNESS-PLAN O3 (a brief names its plan item and stays short; task start refuses one that does not) and L3 (the
goal loop: commands judge first, a model only after they pass; a goal that stalls stops; verdict counts in the report)."""
import json
import subprocess
from types import SimpleNamespace

import pytest

from fieldkit.autopilot import goal, hardlines, inbox
from fieldkit.lifecycle import brieflint, tasks

PLAN = "# plan\n\n| id | what |\n|---|---|\n| O3 | fewer sessions |\n| L3 | the goal loop |\n"


# ---- O3 ----------------------------------------------------------------------------------------------------------
@pytest.fixture
def root(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "HARNESS-PLAN.md").write_text(PLAN, encoding="utf-8")
    return tmp_path


def test_a_brief_without_a_plan_id_is_refused_and_a_known_row_passes(root):
    assert brieflint.lint("Fix the thing.\nmore.", root)[0][0] == "refuse"
    assert brieflint.lint("Plan item O3: fix it.", root) == []
    assert brieflint.lint("Plan item Z9: fix it.", root)[0][0] == "refuse"      # not a row of the plan file
    assert brieflint.lint("Fix it.", root, plan="L3") == []
    assert brieflint.lint("Fix it.", root, plan="Z9")[0][0] == "refuse"


def test_a_long_brief_is_a_warning_not_a_refusal(root):
    found = brieflint.lint("O3 " + "word " * 2000, root)
    assert [sev for sev, _ in found] == ["warn"] and "tokens" in found[0][1]


def test_task_start_refuses_a_brief_with_no_plan_id_and_starts_one_with_it(root, tmp_path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_LIFECYCLE_DIR", str(tmp_path / "lc"))
    (tmp_path / "lc").mkdir()
    b = tmp_path / "brief.md"
    b.write_text("Do the job.\nMore.", encoding="utf-8")
    launched = []
    with pytest.raises(tasks.BriefRefused, match="plan id"):
        tasks.start(b, cwd=root, launch=launched.append, worktree=lambda r, t: (None, "test"))
    assert launched == []
    rec = tasks.start(b, cwd=root, launch=lambda a: {"pid": 1, "log": "L"}, worktree=lambda r, t: (None, "test"),
                      plan="O3")
    assert rec["status"] == "started"
    assert "PLAN ITEM: O3" in (tmp_path / "lc" / "tasks" / rec["id"] / f"{rec['id']}.md").read_text(encoding="utf-8")


# ---- L3 ----------------------------------------------------------------------------------------------------------
@pytest.fixture
def goals(tmp_path, monkeypatch):
    st = tmp_path / "ap"
    st.mkdir()
    monkeypatch.setattr(hardlines, "STATE", st)
    monkeypatch.setattr(hardlines, "STOP_FILE", st / "STOP")
    monkeypatch.setattr(inbox, "INBOX", st / "inbox")
    monkeypatch.setattr(goal, "GOALS", tmp_path / "goals")
    return tmp_path


def mk(criteria, **kw):
    spec = {"id": "g1", "objective": "o", "cwd": ".", "criteria": criteria}
    spec.update(kw)
    goal.new(spec)
    return goal.load("g1")


def fake_run(codes):
    """-> a stand-in for subprocess.run: the exit code is looked up by the command text."""
    def run(shell, **kw):
        cmd = shell[-1]
        return SimpleNamespace(stdout="", stderr="", returncode=codes[cmd])
    return run


class Judge:
    def __init__(self):
        self.n = 0

    def __call__(self, prompt, **kw):
        self.n += 1
        return {"rc": 0, "result": {"result": "VERDICT: PASS read it", "total_cost_usd": 0.5}}


def test_a_model_judges_only_when_every_command_passed(goals):
    g, st = mk([{"id": "cmd", "run": "cmd-a"}, {"id": "read", "judge": "agent", "question": "q?"}])
    j = Judge()
    r = goal.verify("g1", g, st, claude=j, run=fake_run({"cmd-a": 1}))
    assert j.n == 0                                                   # the command failed: no model call
    assert {x["id"]: x["verdict"] for x in r} == {"cmd": "FAIL", "read": "FAIL"}
    assert "not judged by a model" in r[1]["why"]
    r = goal.verify("g1", g, st, claude=j, run=fake_run({"cmd-a": 0}))
    assert j.n == 1 and {x["id"]: x["verdict"] for x in r} == {"cmd": "PASS", "read": "PASS"}


def test_an_agent_criterion_with_a_run_precheck_calls_the_model_only_after_the_precheck_passes(goals):
    g, st = mk([{"id": "read", "judge": "agent", "question": "q?", "run": "pre"}])
    j = Judge()
    assert goal.verify("g1", g, st, claude=j, run=fake_run({"pre": 2}))[0]["verdict"] == "FAIL" and j.n == 0
    assert goal.verify("g1", g, st, claude=j, run=fake_run({"pre": 0}))[0]["verdict"] == "PASS" and j.n == 1
    assert goal.lint(g) == []
    assert "no run precheck" in goal.lint({"criteria": [{"id": "x", "judge": "agent", "question": "q"}]})[0]


def test_a_goal_that_stalls_for_three_rounds_stops_and_reports(goals):
    g, st = mk([{"id": "c1", "run": "x"}, {"id": "c2", "run": "y"}])
    st["criteria"]["c1"]["state"] = "completed"
    st["needs_plan"] = True
    assert goal.stall_check(g, st) == ""                              # round 0: the baseline
    for rounds in (1, 2):                                             # rounds pass, c1 is still the only one met
        st["rounds"] = rounds
        assert goal.stall_check(g, st) == ""
    st["rounds"] = 3
    why = goal.stall_check(g, st)
    assert why.startswith("stalled: 3 planner rounds") and "1 of 2" in why
    goal.save_state("g1", st)
    called = []
    r = goal.step("g1", claude=lambda *a, **k: called.append(1), say=lambda m: None)
    assert r["status"] == "blocked" and "stalled" in r["why"] and called == []   # no fourth planner call
    assert "stalled" in (goal.gdir("g1") / "REPORT.md").read_text(encoding="utf-8")


def test_a_new_criterion_met_resets_the_stall(goals):
    g, st = mk([{"id": "c1", "run": "x"}, {"id": "c2", "run": "y"}], stall_rounds=2)
    st["rounds"] = 1
    goal.stall_check(g, st)
    st["rounds"] = 2
    assert goal.stall_check(g, st) == ""
    st["criteria"]["c1"]["state"] = "completed"
    st["rounds"] = 3
    assert goal.stall_check(g, st) == "" and st["stall"] == 0


def test_the_report_counts_verdicts_per_goal(goals):
    g, st = mk([{"id": "c1", "run": "a"}])
    goal.verify("g1", g, st, run=fake_run({"a": 1}))
    goal.verify("g1", g, st, run=fake_run({"a": 0}))
    assert goal.verdict_counts("g1") == {"PASS": 1, "FAIL": 1, "TIMEOUT": 0, "model_calls": 0}
    goal.report("g1")
    assert "Verdicts: 2 (1 PASS, 1 FAIL, 0 TIMEOUT); model-judged: 0" in (goal.gdir("g1") / "REPORT.md").read_text(
        encoding="utf-8")
    assert any("Verdicts: 2" in l for l in goal.status_lines("g1"))
