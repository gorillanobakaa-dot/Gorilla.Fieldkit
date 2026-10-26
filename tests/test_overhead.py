"""fieldkit overhead: every second of a job in one bucket, the bare build as the baseline, WARN over the budget."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from fieldkit import overhead

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "tests" / "data" / "overhead"
MIN = 60


@pytest.fixture(autouse=True)
def utc(monkeypatch):
    monkeypatch.setenv("FIELDKIT_OVERHEAD_UTC_OFFSET", "0")      # journals are local time; the fixtures are UTC


CFG = {"max_ratio": 1.5, "baselines": {}, "sessions": {}}


def _inbox(tmp_path):
    d = tmp_path / "inbox"
    d.mkdir()
    (d / "0001-approval.json").write_text(json.dumps(
        {"n": 1, "kind": "approval", "title": "synthetic-task: approve the decision record", "why": "", "command": "",
         "added": "2026-10-20 11:10:00", "done": "2026-10-20 11:15:00", "state": "done"}), encoding="utf-8")
    (d / "0002-approval.json").write_text(json.dumps(
        {"n": 2, "kind": "approval", "title": "another job entirely", "added": "2026-10-20 12:00:00",
         "state": "open"}), encoding="utf-8")
    return d


def _testcost(tmp_path):
    p = tmp_path / "runs.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in [
        {"t": "2026-10-20 12:55:00", "check": "full-suite", "status": "pass", "seconds": 300, "key": "K"},
        {"t": "2026-10-20 12:59:00", "check": "full-suite", "status": "pass", "seconds": 120, "key": "K"},
        {"t": "2026-10-20 12:59:30", "check": "full-suite", "status": "skipped", "seconds": 0, "key": "K"},
    ]) + "\n", encoding="utf-8")
    return p


def synthetic(tmp_path, sessions=("sess-abc",), **kw):
    jobs = overhead.build_jobs(DATA / "synthetic")
    return overhead.measure("synthetic-task", jobs["synthetic-task"], sessions=sessions, cfg=CFG,
                            state=DATA / "synthetic", projects=DATA / "projects", inbox_dir=_inbox(tmp_path),
                            testcost_log=_testcost(tmp_path), now=overhead.parse_t("2026-10-21 00:00:00"), **kw)


def test_synthetic_job_every_second_in_one_bucket(tmp_path):
    r = synthetic(tmp_path)
    b = r["buckets"]
    assert r["wall_s"] == 240 * MIN and sum(b.values()) == r["wall_s"]
    # bare = first migration 30 + the successful compile 60 + its package 5
    assert r["bare_parts"] == {"migrate": 30 * MIN, "compile": 60 * MIN, "package": 5 * MIN}
    assert r["bare_s"] == 95 * MIN and b["bare"] == 95 * MIN
    # rework: failed compile 30, failed package checks 20, second compile 5 + package 2, testcost re-run on tree K 2
    assert b["rework"] == (30 + 20 + 5 + 2 + 2) * MIN
    # tests: passing package checks 20, post-install 10, check-change 2, testcost 5, pytest call 2
    assert b["tests"] == (20 + 10 + 2 + 5 + 2) * MIN
    # owner: plan approve 5 min, inbox item 5 min, Edit prompt 120 s, denied Write 5 s, turn ended 300 s
    assert b["owner"] == 10 * MIN + 120 + 5 + 300
    assert b["llm"] == 30 + 30 + 30 + 30 + 25 + 29 + 50
    assert b["tools"] == 1
    assert r["counts"] == {"approval prompts": 1, "denied": 1, "owner messages": 2}
    assert r["ratio"] == round(240 / 95, 2) and r["level"] == "WARN"
    assert r["rework"]["compile"] == 35 * MIN and r["rework"]["package-checks"] == 20 * MIN


def test_line_names_ratio_and_where_the_time_went(tmp_path):
    line = overhead.report_line(synthetic(tmp_path))
    assert line.startswith("synthetic-task: bare 1 h 35 min (migrate 30 min, compile 1 h 00 min, package 5 min), "
                           "took 4 h 00 min = 2.5x WARN (max 1.5x); rework 59 min, tests+gates 39 min, idle 26 min")
    assert "waiting-for-owner 17 min" in line and "LLM 4 min" in line


def test_without_a_transcript_the_split_says_so_and_ratio_stands(tmp_path):
    r = synthetic(tmp_path, sessions=())
    assert r["ratio"] == round(240 / 95, 2) and r["buckets"]["llm"] == 0
    assert any("no session transcript" in n for n in r["notes"])


def test_a_silent_day_starts_a_follow_up_job():
    jobs = overhead.build_jobs(DATA / "synthetic")
    assert set(jobs) == {"synthetic-task", "synthetic-task #2"}
    r = overhead.measure("synthetic-task #2", jobs["synthetic-task #2"], cfg=CFG, transcripts=False, inbox_dir="",
                         testcost_log=DATA / "none.jsonl")
    assert r["level"] == "NOBASE" and r["bare_s"] is None


def test_budget_from_config_and_bare_override(tmp_path):
    r = synthetic(tmp_path, sessions=(), bare_min=200)
    assert r["bare_s"] == 200 * MIN and r["level"] == "OK"
    p = tmp_path / "local.json"
    p.write_text(json.dumps({"overhead": {"max_ratio": 3}}), encoding="utf-8")
    cfg = overhead.config(p)
    assert cfg["max_ratio"] == 3
    jobs = overhead.build_jobs(DATA / "synthetic")
    r = overhead.measure("synthetic-task", jobs["synthetic-task"], cfg=cfg, transcripts=False, inbox_dir="",
                         testcost_log=DATA / "none.jsonl")
    assert r["level"] == "OK"
    assert overhead.config(tmp_path / "missing.json")["max_ratio"] == 1.5
    p.write_text(json.dumps({"overhead": {"max_ratio": "x"}}), encoding="utf-8")
    assert overhead.config(p)["max_ratio"] == 1.5


def test_nobase_never_warns_and_config_baseline_counts(tmp_path):
    jobs = overhead.build_jobs(DATA / "synthetic")
    cfg = dict(CFG, baselines={"synthetic-task #2": 0.5})
    r = overhead.measure("synthetic-task #2", jobs["synthetic-task #2"], cfg=cfg, transcripts=False, inbox_dir="",
                         testcost_log=DATA / "none.jsonl")
    assert r["bare_s"] == 30 and r["level"] == "WARN"       # 1 min of wall for 30 s of bare


def test_recorded_firefox_158_journal():
    """The real 158 journal (timing fields only), measured 2026-10-26: 88-minute migration, 76-minute compile."""
    jobs = overhead.build_jobs(DATA)
    r = overhead.measure("firefox-158.0", jobs["firefox-158.0"], cfg=CFG, transcripts=False, inbox_dir="",
                         testcost_log=DATA / "none.jsonl")
    assert r["bare_parts"]["migrate"] == 87 * MIN + 53         # 16:12:37 -> 17:40:30
    assert r["bare_parts"]["compile"] == 76 * MIN + 4          # 23:37:12 -> 00:53:16
    assert r["runs"]["compile"] == 7 and r["runs"]["leak-gate"] == 5 and r["runs"]["post-install"] == 11
    assert r["start"] == "2026-10-16 16:11" and r["end"] == "2026-10-19 07:06"
    assert r["ratio"] > 20 and r["level"] == "WARN"
    assert r["rework"]["leak-gate"] > 8 * 60 * MIN             # four repeat gate runs
    assert "firefox-158.0 #2" in jobs                          # the 2026-10-23 follow-up is its own job


def test_sweep_priority_and_clipping():
    iv = [(0, 100, "idle", "x"), (10, 50, "llm", "m"), (20, 30, "bare", "c"), (40, 200, "owner", "o")]
    s = overhead.sweep(iv, 0, 100)
    assert s["bare"] == 10 and s["llm"] == 30 and s["owner"] == 50 and s["idle"] == 10
    assert sum(s.values()) == 100


def test_a_long_failed_compile_is_rework_not_bare():
    runs = [{"stage": "compile", "start": 0, "end": 5000, "ok": False},
            {"stage": "compile", "start": 6000, "end": 7000, "ok": True},
            {"stage": "migrate", "start": 10, "end": 20, "ok": True},
            {"stage": "migrate", "start": 8000, "end": 9000, "ok": True}]
    iv, kept = overhead.classify_runs(runs)
    assert kept["compile"]["end"] == 7000 and kept["migrate"]["start"] == 10
    assert sorted(b for a, e, b, s in iv) == ["bare", "bare", "rework", "rework"]


def test_leak_gate_run_never_outlasts_the_next(tmp_path):
    a, b = tmp_path / "20261020-100000", tmp_path / "20261020-120000"
    for d, s in ((a, "2026-10-20T10:00:00"), (b, "2026-10-20T12:00:00")):
        d.mkdir()
        (d / "gate-plan.json").write_text(json.dumps({"started": s}), encoding="utf-8")
    (a / "test-results.json").write_text("{}", encoding="utf-8")
    later = overhead.parse_t("2026-10-20 18:00:00")             # a re-judge touched it hours later
    os.utime(a / "test-results.json", (later, later))
    events = [{"event": "leakgate", "final": "PASS", "_t": overhead.parse_t("2026-10-20 14:00:00")}]
    runs = overhead.leakgate_runs(tmp_path, events)
    assert [(r["end"] - r["start"]) / MIN for r in runs] == [120, 120]
    assert [r["ok"] for r in runs] == [False, True]


def test_bash_kinds():
    assert overhead.bash_kind("python -m pytest -q") == "tests"
    assert overhead.bash_kind("git commit -m x") == "tests"
    assert overhead.bash_kind("fieldkit build-harness build-run firefox") == "compile"
    assert overhead.bash_kind("sleep 120; tail log") == "poll"
    assert overhead.bash_kind("ls; sleep 30") == "poll"
    assert overhead.bash_kind("git status") == "other"


def test_compile_call_is_bare_only_without_a_journal():
    assert overhead._tool_bucket("Bash", {"command": "go build ./..."}, 300, journal=False) == ("bare", "compile call")
    assert overhead._tool_bucket("Bash", {"command": "go build ./..."}, 300, journal=True)[0] == "tools"
    assert overhead._tool_bucket("Read", {}, 30, journal=True) == ("tools", "Read")
    assert overhead._tool_bucket("AskUserQuestion", {}, 1, journal=True) == ("owner", "prompt")


def test_goal_job(tmp_path):
    g = tmp_path / "goals" / "g1"
    (g / "jobs" / "j1").mkdir(parents=True)
    (g / "goal.json").write_text(json.dumps({"id": "g1", "bare_minutes": 60}), encoding="utf-8")
    (g / "state.json").write_text(json.dumps({"status": "done", "finished": "2026-10-20 14:00:00"}), encoding="utf-8")
    (g / "events.log").write_text("2026-10-20 10:00:00  goal created: x\n"
                                  "2026-10-20 10:30:00  pre-approvals approved by the owner\n", encoding="utf-8")
    (g / "jobs" / "j1" / "CHECKPOINT.json").write_text(json.dumps({"session_id": "sess-abc123", "attempts": [
        {"end": "failed: tests", "seconds": 1800, "at": "2026-10-20 11:30:00"},
        {"end": "done", "seconds": 600, "at": "2026-10-20 12:10:00"}]}), encoding="utf-8")
    jobs = overhead.goal_jobs(goals=tmp_path / "goals")
    r = overhead.measure("goal:g1", jobs["goal:g1"], cfg=CFG, projects=DATA / "projects", inbox_dir="",
                         testcost_log=DATA / "none.jsonl")
    assert r["wall_s"] == 240 * MIN and r["bare_s"] == 60 * MIN and r["level"] == "WARN"
    assert r["buckets"]["rework"] == 10 * MIN                  # the attempt after a failed one
    assert r["buckets"]["owner"] >= 30 * MIN                   # waiting for the pre-approvals
    assert r["sessions"] == ["sess-abc123"] and r["buckets"]["llm"] > 0
    line = overhead.goal_line("g1", goals=tmp_path / "goals", cfg=CFG)
    assert line.startswith("OVERHEAD goal:g1: bare 1 h 00 min") and "WARN" in line
    assert overhead.goal_line("nope", goals=tmp_path / "goals") == ""


def test_goal_report_carries_the_overhead_line(tmp_path, monkeypatch):
    from fieldkit.autopilot import goal
    g = tmp_path / "g2"
    g.mkdir()
    (g / "goal.json").write_text(json.dumps({"id": "g2", "bare_minutes": 10, "criteria": []}), encoding="utf-8")
    (g / "state.json").write_text(json.dumps({"status": "done", "finished": "2026-10-20 11:00:00", "spend": [],
                                              "rounds": 1, "criteria": {}, "jobs": {}, "answers": {},
                                              "actions": {}}), encoding="utf-8")
    (g / "events.log").write_text("2026-10-20 10:00:00  goal created: x\n", encoding="utf-8")
    monkeypatch.setattr(goal, "GOALS", tmp_path)
    goal.report("g2")
    text = (g / "REPORT.md").read_text(encoding="utf-8")
    assert "OVERHEAD goal:g2: bare 10 min" in text and "= 6.0x WARN" in text


def test_goal_line_never_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(overhead, "goal_jobs", lambda *a, **k: 1 / 0)
    assert overhead.goal_line("g1").startswith("OVERHEAD: not measured")


def test_board_line(tmp_path):
    b = overhead.board_line(state=DATA / "synthetic", cfg=CFG)
    assert b["level"] == "WARN" and b["line"].startswith("OVERHEAD WARN: 1 of 2 job(s) over 1.5x")
    b = overhead.board_line(state=DATA / "synthetic", cfg=dict(CFG, max_ratio=10))
    assert b["level"] == "OK"


def test_cli_exit_codes_and_json(tmp_path):
    env = dict(os.environ, FIELDKIT_OVERHEAD_UTC_OFFSET="0", FIELDKIT_AUTOPILOT_STATE=str(tmp_path))
    run = lambda *a: subprocess.run([sys.executable, "-m", "fieldkit", "overhead", *a], capture_output=True,
                                    text=True, cwd=ROOT, env=env)
    r = run("synthetic-task", "--state", str(DATA / "synthetic"), "--json")
    assert r.returncode == 1 and json.loads(r.stdout)["level"] == "WARN"
    r = run("synthetic-task", "--state", str(DATA / "synthetic"), "--bare", "500")
    assert r.returncode == 0 and "= 0.5x OK" in r.stdout
    assert run("no-such-job", "--state", str(DATA / "synthetic")).returncode == 2
    r = run("--state", str(DATA / "synthetic"))
    assert r.returncode == 1 and r.stdout.count("\n") == 2
    r = run("baselines", "--state", str(DATA / "synthetic"))
    assert r.returncode == 0 and "synthetic-task" in r.stdout and "kernel" in r.stdout


def test_no_network_and_no_model():
    src = (ROOT / "fieldkit" / "overhead.py").read_text(encoding="utf-8")
    for door in ("urllib", "requests", "socket", "http.client", "subprocess", "anthropic"):
        assert f"import {door}" not in src and f"from {door}" not in src
