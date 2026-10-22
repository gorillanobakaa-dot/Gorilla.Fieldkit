"""Long horizon chaos tests (DESIGN.md section 8): a fake `claude` (tests/fake_claude.py) that hits the turn cap,
the usage window, 429s, a crash mid-commit, a stuck process; planner and verifier that disagree; pre-approvals;
the supervisor lock. Real run.claude, real git, real hook; only the model is fake."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from fieldkit.autopilot import budget, checkpoint, goal, hardlines, inbox, job, run, runbook

FAKE = str(Path(__file__).with_name("fake_claude.py"))


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
    monkeypatch.setenv("FIELDKIT_CLAUDE_EXE", FAKE)
    for k, v in (("GIT_AUTHOR_NAME", "t"), ("GIT_AUTHOR_EMAIL", "t@t"), ("GIT_COMMITTER_NAME", "t"),
                 ("GIT_COMMITTER_EMAIL", "t@t")):
        monkeypatch.setenv(k, v)
    from fieldkit.core import eta
    monkeypatch.setattr(eta, "HISTORY", tmp_path / "history.jsonl")


def script(tmp_path, actions, monkeypatch):
    p = tmp_path / "fake-plan.json"
    p.write_text(json.dumps(actions), encoding="utf-8")
    monkeypatch.setenv("FAKE_CLAUDE_PLAN", str(p))
    return p


def calls(p):
    f = Path(str(p) + ".log.jsonl")
    return [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines()] if f.exists() else []


def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "work", str(r)], check=True)
    (r / "a.txt").write_text("base\n")
    subprocess.run(["git", "-C", str(r), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(r), "commit", "-qm", "base"], check=True)
    return r


class Clock:
    def __init__(self):
        self.t = time.time()
        self.slept = []

    def now(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += s


def git(r, *a):
    return subprocess.run(["git", "-C", str(r), *a], capture_output=True, text=True).stdout.strip()


# ---- step 1: budgets, checkpoint, resume ------------------------------------------------------------------------
def test_turn_cap_mid_job_resumes_from_the_checkpoint_never_from_scratch(tmp_path, monkeypatch):
    r = repo(tmp_path)
    monkeypatch.setattr(checkpoint, "FRESH_SECONDS", 0)        # the session is stale: a fresh one from HANDOFF.md
    p = script(tmp_path, [{"do": "max_turns", "write": {"path": str(r / "half.txt"), "text": "half"}},
                          {"do": "success"}], monkeypatch)
    jd = tmp_path / "job"
    head0 = git(r, "rev-parse", "HEAD")
    out = job.run_job(jd, "BRIEF: build the thing", r, say=lambda m: None)
    assert out["status"] == "done" and out["rc"] == 0
    c = calls(p)
    assert len(c) == 2
    assert "BRIEF: build the thing" in c[0]["prompt"] and "--resume" not in c[0]["args"]
    assert "Do NOT start over" in c[1]["prompt"] and str(jd / "HANDOFF.md") in c[1]["prompt"]
    assert "wip/" in c[1]["prompt"]                            # told where the snapshot is
    assert c[0]["job"] == str(jd)                              # the hook sees the job folder
    cp = checkpoint.load(jd)
    assert [a["end"] for a in cp["attempts"]] == ["turns", "success"] and cp["continuations"] == 1
    assert git(r, "show", f"{cp['wip']['ref'].split('refs/heads/')[1]}:half.txt") == "half"
    assert git(r, "rev-parse", "HEAD") == head0 and git(r, "branch", "--show-current") == "work"  # checkout untouched
    assert "turns" in (jd / "HANDOFF.md").read_text(encoding="utf-8")


def test_a_fresh_session_is_resumed_by_its_id(tmp_path, monkeypatch):
    r = repo(tmp_path)
    p = script(tmp_path, [{"do": "max_turns"}, {"do": "success"}], monkeypatch)
    job.run_job(tmp_path / "job", "BRIEF", r, say=lambda m: None)
    c = calls(p)
    assert c[1]["args"][c[1]["args"].index("--resume") + 1] == "sess-1"


def test_budget_exhausted_past_max_continuations_pauses_resumable_with_a_handoff(tmp_path, monkeypatch):
    r = repo(tmp_path)
    script(tmp_path, [{"do": "max_turns"}], monkeypatch)
    out = job.run_job(tmp_path / "job", "BRIEF", r, max_continuations=2, say=lambda m: None)
    assert out["status"] == "paused-resumable" and out["rc"] == 75
    assert checkpoint.load(tmp_path / "job")["continuations"] == 3
    items = inbox.items()
    assert items and items[0]["kind"] == "handoff" and "HANDOFF.md" in items[0]["files"][0]


def test_a_usage_limit_sleeps_until_the_reset_then_resumes(tmp_path, monkeypatch):
    r = repo(tmp_path)
    clk = Clock()
    reset = int(clk.t) + 7200
    p = script(tmp_path, [{"do": "usage_limit", "text": f"Claude AI usage limit reached|{reset}"}, {"do": "success"}],
               monkeypatch)
    out = job.run_job(tmp_path / "job", "BRIEF", r, sleep=clk.sleep, now=clk.now, say=lambda m: None)
    assert out["status"] == "done"
    assert clk.t >= reset + 60 and sum(clk.slept) >= 7200      # slept to the reset, in chunks
    assert max(clk.slept) <= job.SLEEP_CHUNK
    cp = checkpoint.load(tmp_path / "job")
    assert cp["continuations"] == 0                             # a usage window is not a budget end
    assert len(calls(p)) == 2


def test_a_usage_limit_beyond_max_sleep_returns_paused_with_the_time(tmp_path, monkeypatch):
    r = repo(tmp_path)
    clk = Clock()
    script(tmp_path, [{"do": "usage_limit", "text": f"Claude AI usage limit reached|{int(clk.t) + 3 * 86400}"}],
           monkeypatch)
    out = job.run_job(tmp_path / "job", "BRIEF", r, sleep=clk.sleep, now=clk.now, say=lambda m: None)
    assert out["rc"] == 75 and checkpoint.load(tmp_path / "job")["paused_until"] > clk.t + 2 * 86400


def test_429_backs_off_honoring_retry_after_and_is_not_a_failure(tmp_path, monkeypatch):
    r = repo(tmp_path)
    clk = Clock()
    p = script(tmp_path, [{"do": "429"}, {"do": "429"}, {"do": "success"}], monkeypatch)
    out = job.run_job(tmp_path / "job", "BRIEF", r, sleep=clk.sleep, now=clk.now, say=lambda m: None)
    assert out["status"] == "done" and len(calls(p)) == 3
    assert sum(clk.slept) == 10                                 # retry-after: 5, twice
    assert checkpoint.load(tmp_path / "job")["continuations"] == 0


def test_429_without_retry_after_backs_off_exponentially_then_pauses(tmp_path, monkeypatch):
    r = repo(tmp_path)
    clk = Clock()
    script(tmp_path, [{"do": "429", "text": "API Error: 529 Overloaded"}], monkeypatch)
    out = job.run_job(tmp_path / "job", "BRIEF", r, sleep=clk.sleep, now=clk.now, say=lambda m: None)
    assert out["rc"] == 75
    assert 30 * 0.5 <= sum(clk.slept) <= sum(min(900, 30 * 2 ** k) for k in range(job.BACKOFF_TRIES))
    assert sum(clk.slept) > 30 * 2 ** 3                        # it grew
    assert inbox.items()[0]["kind"] == "paused"


def test_a_crash_mid_commit_leaves_the_wip_safe_and_one_fix_run_carries_on(tmp_path, monkeypatch):
    r = repo(tmp_path)
    p = script(tmp_path, [{"do": "crash_mid_commit", "write": {"path": str(r / "new.txt"), "text": "precious"}},
                          {"do": "success"}], monkeypatch)
    out = job.run_job(tmp_path / "job", "BRIEF", r, say=lambda m: None)
    assert out["status"] == "done"
    cp = checkpoint.load(tmp_path / "job")
    assert cp["attempts"][0]["end"] == "crash" and cp["wip"]["ok"]
    assert git(r, "show", f"{cp['wip']['sha']}:new.txt") == "precious"
    assert (r / ".git" / "index.lock").exists()                 # the harness never deletes it behind git's back
    c = calls(p)
    assert "find the cause first" in c[1]["prompt"] and "--resume" not in c[1]["args"]
    assert "index.lock" in (tmp_path / "job" / "HANDOFF.md").read_text(encoding="utf-8")


def test_a_second_crash_is_a_real_failure_in_the_inbox(tmp_path, monkeypatch):
    r = repo(tmp_path)
    script(tmp_path, [{"do": "crash_mid_commit"}], monkeypatch)
    (r / ".git" / "index.lock").unlink(missing_ok=True)
    out = job.run_job(tmp_path / "job", "BRIEF", r, say=lambda m: None)
    assert out["status"] == "failed" and out["rc"] == 3 and inbox.items()[0]["kind"] == "failed"


def test_the_wall_budget_kills_a_stuck_run_and_the_job_resumes(tmp_path, monkeypatch):
    r = repo(tmp_path)
    p = script(tmp_path, [{"do": "sleep", "seconds": 60}, {"do": "success"}], monkeypatch)
    t0 = time.time()
    out = job.run_job(tmp_path / "job", "BRIEF", r, budget={"wall_minutes": 0.05}, say=lambda m: None)
    assert out["status"] == "done" and time.time() - t0 < 45
    assert checkpoint.load(tmp_path / "job")["attempts"][0]["end"] == "wall"
    assert len(calls(p)) == 2


def test_the_wall_budget_kills_the_whole_process_tree(tmp_path, monkeypatch):
    import psutil
    pidf = tmp_path / "child.pid"
    script(tmp_path, [{"do": "sleep", "seconds": 60, "child_pid_file": str(pidf)}], monkeypatch)
    t0 = time.time()
    r = run.claude("x", cwd=str(tmp_path), timeout=6)
    assert r["timed_out"] and r["rc"] == 124
    assert time.time() - t0 < 25                               # the grandchild did not hold the run hostage
    pid = int(pidf.read_text())
    try:
        assert not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
    finally:
        if psutil.pid_exists(pid):
            psutil.Process(pid).kill()                         # never leave a test's process behind


def test_the_defaults_are_10000_turns_and_no_hidden_timeout(tmp_path, monkeypatch):
    import inspect
    p = script(tmp_path, [{"do": "success"}], monkeypatch)
    run.claude("x", cwd=str(tmp_path))
    a = calls(p)[0]["args"]
    assert a[a.index("--max-turns") + 1] == "10000"
    assert inspect.signature(run.claude).parameters["timeout"].default is None
    assert budget.merge({})["turns"] == 10000 and budget.merge({})["wall_minutes"] == 720


def test_a_long_prompt_goes_on_stdin(tmp_path, monkeypatch):
    p = script(tmp_path, [{"do": "success"}], monkeypatch)
    big = "brief " * 8000                                       # 48k characters: over the Windows command line
    r = run.claude(big, cwd=str(tmp_path))
    assert r["ok"] and calls(p)[0]["prompt"] == big


def test_a_done_job_is_never_run_again(tmp_path, monkeypatch):
    r = repo(tmp_path)
    p = script(tmp_path, [{"do": "success"}], monkeypatch)
    job.run_job(tmp_path / "job", "BRIEF", r, say=lambda m: None)
    job.run_job(tmp_path / "job", "BRIEF", r, say=lambda m: None)
    assert len(calls(p)) == 1


def test_an_unknown_fallback_action_is_refused_before_the_run(tmp_path, monkeypatch):
    p = script(tmp_path, [{"do": "success"}], monkeypatch)
    out = job.run_job(tmp_path / "job", "B", tmp_path, fallback={"rate_limit": ["pray"]}, say=lambda m: None)
    assert out["rc"] == 2 and "pray" in out["why"] and not calls(p)
    assert job.check_fallback({"failed": [{"switch_model": None}]})


def test_the_owners_stop_file_pauses_a_job_before_it_starts(tmp_path, monkeypatch):
    p = script(tmp_path, [{"do": "success"}], monkeypatch)
    hardlines.STOP_FILE.write_text("stop")
    out = job.run_job(tmp_path / "job", "B", tmp_path, say=lambda m: None)
    assert out["rc"] == 75 and not calls(p)


# ---- the hook: 50/80/95 % and the forced checkpoint ------------------------------------------------------------
def test_the_hook_warns_at_50_80_95_and_forces_a_checkpoint_at_80(tmp_path, monkeypatch):
    jd = tmp_path / "job"
    budget.start(jd, budget.merge({"turns": 10}))
    msgs, denies = [], []
    for i in range(10):
        why = budget.hook_pre(jd, "Bash", {"command": "make"})
        denies.append(why)
        msgs.append(budget.hook_post(jd))
    said = [m for m in msgs if m]
    assert [m.split("%")[0] for m in said] == ["BUDGET 50 ", "BUDGET 80 ", "BUDGET 95 "] or len(said) == 3
    assert "50 %" in said[0] and "80 %" in said[1] and "HANDOFF.md" in said[1]
    assert denies[:7] == [None] * 7 and "CHECKPOINT REQUIRED" in denies[7]
    # writing HANDOFF.md is allowed and lifts the block
    assert budget.hook_pre(jd, "Write", {"file_path": str(jd / "HANDOFF.md"), "content": "x"}) is None
    (jd / "HANDOFF.md").write_text("done/next")
    assert budget.hook_pre(jd, "Bash", {"command": "make"}) is None


def test_the_hook_allows_only_a_checkpoint_once_the_wall_budget_is_spent(tmp_path):
    jd = tmp_path / "job"
    budget.start(jd, budget.merge({"wall_minutes": 1}), now=time.time() - 120)
    (jd / "HANDOFF.md").write_text("x")
    why = budget.hook_pre(jd, "Edit", {"file_path": "src.py"})
    assert "BUDGET EXHAUSTED (wall" in why
    assert budget.hook_pre(jd, "Bash", {"command": "git add -A && git commit -m wip"}) is None
    assert budget._read(jd / "meter.json")["exhausted"] == "wall"
    assert budget.classify({"rc": 0, "result": {"is_error": False, "subtype": "success"}},
                           budget._read(jd / "meter.json"))[0] == "budget"


def test_the_real_hook_inside_a_fake_claude_run_counts_and_denies(tmp_path, monkeypatch):
    r = repo(tmp_path)
    p = script(tmp_path, [{"do": "hook", "calls": 9}], monkeypatch)
    monkeypatch.setattr(job, "MAX_SLEEP", 0)
    job.run_job(tmp_path / "job", "B", r, budget={"turns": 10}, say=lambda m: None)
    outs = json.loads(Path(str(p) + ".hook0.json").read_text())
    assert any("additionalContext" in o["post"] and "BUDGET 50 %" in o["post"] for o in outs)
    assert "permissionDecision" in outs[8]["pre"] and "CHECKPOINT REQUIRED" in outs[8]["pre"]
    assert all(o["pre"] == "" for o in outs[:7])


def test_the_hard_lines_still_come_first_inside_a_job(tmp_path, monkeypatch):
    import io
    from fieldkit.autopilot import claude_hook
    jd = tmp_path / "job"
    budget.start(jd, budget.merge({}))
    monkeypatch.setenv("FIELDKIT_JOB_DIR", str(jd))
    out = io.StringIO()
    claude_hook.main(io.StringIO(json.dumps({"tool_name": "Bash", "tool_input": {"command": "git push origin x"}})), out)
    assert "HARD LINE 1" in out.getvalue()


# ---- what ended a run, and when the window reopens -------------------------------------------------------------
@pytest.mark.parametrize("r,kind", [
    ({"rc": 0, "result": {"is_error": False, "subtype": "success", "result": "ok"}}, "success"),
    ({"rc": 1, "result": {"is_error": True, "subtype": "error_max_turns"}}, "turns"),
    ({"rc": 124, "timed_out": True}, "wall"),
    ({"rc": 1, "result": {"is_error": True, "result": "Claude AI usage limit reached|1761300000"}}, "usage_limit"),
    ({"rc": 1, "result": {"is_error": True, "result": "You've hit your limit · resets 3pm (Europe/London)"}}, "usage_limit"),
    ({"rc": 1, "result": {"is_error": True, "result": "API Error: 429 rate_limit_error"}}, "rate_limit"),
    ({"rc": 1, "result": {"is_error": True, "result": "API Error: 529 Overloaded"}}, "rate_limit"),
    ({"rc": 1, "result": {"is_error": True, "result": "API Error: 400 invalid_request_error"}}, "api_error"),
    ({"rc": 139, "stdout": "", "stderr": "Segmentation fault"}, "crash"),
    ({"rc": 0, "result": {"is_error": False, "subtype": "success", "result": "I fixed the 429 handling"}}, "success"),
])
def test_classify(r, kind):
    assert budget.classify(r)[0] == kind


def test_reset_times():
    now = time.mktime((2026, 10, 22, 10, 0, 0, 0, 0, -1))
    assert budget.reset_at("usage limit reached|1761300000", now)[0] == 1761300000
    t, how = budget.reset_at("5-hour limit reached ∙ resets 3pm", now)
    assert time.localtime(t).tm_hour == 15 and t > now and "local" in how
    t, _ = budget.reset_at("resets 9am", now)                   # already past today: tomorrow
    assert t - now == 23 * 3600
    t, how = budget.reset_at("limit reached", now)
    assert t == now + 1800 and "probe" in how


# ---- runbook: paused is not failed; agent steps do not stop the run ---------------------------------------------
def test_a_paused_agent_step_does_not_stop_the_run_and_resumes_next_time(tmp_path, monkeypatch):
    p = tmp_path / "rb.md"
    p.write_text("```runbook\n" + json.dumps({"steps": [
        {"id": "a", "agent": "brief a", "cwd": str(tmp_path)},
        {"id": "b", "agent": "brief b", "cwd": str(tmp_path)},
        {"id": "c", "agent": "needs a", "cwd": str(tmp_path), "needs": ["a"]}]}) + "\n```\n", encoding="utf-8")
    seen = []

    def runner(jd, brief, cwd, **kw):
        seen.append(brief)
        if brief == "brief a" and seen.count("brief a") == 1:
            return {"status": "paused-resumable", "rc": 75, "why": "turns x4"}
        return {"status": "done", "rc": 0, "why": "ok"}
    r = runbook.run(p, say=lambda m: None, hold=False, job_runner=runner)
    assert r["paused"] == ["a"] and r["done"] == ["b"] and r["blocked"] == ["c"] and not r["failed"]
    assert runbook.state(p)["steps"]["a"]["status"] == "paused-resumable"
    r = runbook.run(p, say=lambda m: None, hold=False, job_runner=runner)
    assert r["done"] == ["a", "b", "c"] and seen.count("brief b") == 1


def test_an_agent_run_step_gets_its_job_folder_and_exit_75_is_paused(tmp_path, monkeypatch):
    marker = tmp_path / "env.txt"
    py = sys.executable.replace("\\", "/")
    code = (f"tag='autopilot claude'; import os,sys; open(r'{marker}','a').write(os.environ.get('FIELDKIT_JOB_DIR','')"
            " + chr(10)); sys.exit(75)")
    cmd = f'& "{py}" -c "{code}"' if sys.platform == "win32" else f'"{py}" -c "{code}"'
    p = tmp_path / "rb.md"
    p.write_text("```runbook\n" + json.dumps({"steps": [
        {"id": "x", "run": cmd, "retries": 2},                                  # paused: never retried as a failure
        {"id": "z", "run": cmd.replace("sys.exit(75)", "sys.exit(3)")},         # a failed agent step does not stop
        {"id": "y", "run": cmd.replace("sys.exit(75)", "sys.exit(0)")}]})
                 + "\n```\n", encoding="utf-8")
    r = runbook.run(p, say=lambda m: None, hold=False)
    assert r["paused"] == ["x"] and r["failed"] == ["z"] and r["done"] == ["y"]
    assert marker.read_text().split() == [str(runbook.JOBS / "rb" / s) for s in ("x", "z", "y")]


def test_a_failing_agent_step_defaults_to_stop_on_fail_false(tmp_path):
    p = tmp_path / "rb.md"
    p.write_text("```runbook\n" + json.dumps({"steps": [{"id": "a", "agent": "x", "cwd": str(tmp_path)},
                                                        {"id": "b", "agent": "y", "cwd": str(tmp_path)}]}) + "\n```\n")
    r = runbook.run(p, say=lambda m: None, hold=False,
                    job_runner=lambda jd, brief, cwd, **kw: {"status": "failed" if brief == "x" else "done",
                                                             "rc": 3 if brief == "x" else 0, "why": ""})
    assert r["failed"] == ["a"] and r["done"] == ["b"]


def test_runbook_check_refuses_a_bad_budget_and_a_bad_fallback(tmp_path):
    p = tmp_path / "rb.md"
    p.write_text("```runbook\n" + json.dumps({"steps": [{"id": "a", "agent": "x", "budget": {"turnz": 5}}]}) + "\n```\n")
    with pytest.raises(ValueError, match="turnz"):
        runbook.check(p)
    p.write_text("```runbook\n" + json.dumps({"steps": [{"id": "a", "agent": "x",
                                                         "fallback": {"failed": ["retry_forever"]}}]}) + "\n```\n")
    with pytest.raises(ValueError, match="retry_forever"):
        runbook.check(p)


# ---- step 0: the goal loop ---------------------------------------------------------------------------------------
def mkgoal(tmp_path, crit_file, **kw):
    py = sys.executable.replace("\\", "/")
    test_cmd = (f'& "{py}" -c "import os,sys; sys.exit(0 if os.path.exists(r\'{crit_file}\') else 1)"'
                if sys.platform == "win32" else
                f'"{py}" -c "import os,sys; sys.exit(0 if os.path.exists(r\'{crit_file}\') else 1)"')
    spec = {"id": "g1", "objective": "make the file", "cwd": str(tmp_path),
            "criteria": [{"id": "c1", "title": "the file exists", "run": test_cmd}]}
    spec.update(kw)
    goal.new(spec)
    return test_cmd


def plan_of(jobs=(), signal="execute", questions=()):
    return {"do": "plan", "plan": {"signal": signal, "why": "test", "jobs": list(jobs), "questions": list(questions)}}


def test_a_short_goal_runs_end_to_end_planner_worker_verifier(tmp_path, monkeypatch):
    r = repo(tmp_path)
    f = r / "made.txt"
    check = mkgoal(tmp_path, f)
    p = script(tmp_path, [
        plan_of([{"id": "j1", "brief": "make made.txt", "cwd": str(r), "verify": [{"run": check}]}]),
        {"do": "success", "write": {"path": str(f), "text": "made"}},
        plan_of(signal="done")], monkeypatch)
    rs = [goal.step("g1", say=lambda m: None) for _ in range(4)]
    assert [x["status"] for x in rs][:3] == ["active", "active", "done"] or rs[-1]["status"] == "done"
    g, st = goal.load("g1")
    assert st["status"] == "done" and st["criteria"]["c1"]["state"] == "completed"
    assert st["jobs"]["j1"]["status"] == "verified"
    v = [json.loads(l) for l in (goal.gdir("g1") / "verdicts.jsonl").read_text().splitlines()]
    assert {x["verdict"] for x in v} == {"PASS"} and "exit 0" in v[0]["why"]
    assert "every criterion PASSed" in (goal.gdir("g1") / "REPORT.md").read_text()
    assert "PLAN_FILE" in calls(p)[0]["prompt"] and "make made.txt" in calls(p)[1]["prompt"]


def test_planner_and_verifier_disagree_the_verifier_wins(tmp_path, monkeypatch):
    mkgoal(tmp_path, tmp_path / "never.txt")
    script(tmp_path, [plan_of(signal="done"), plan_of(signal="done"), plan_of(signal="done")], monkeypatch)
    out = goal.step("g1", say=lambda m: None)
    _, st = goal.load("g1")
    assert out["status"] == "active" and st["status"] == "active"
    assert st["criteria"]["c1"]["verdict"] == "FAIL" and st["needs_plan"]
    assert "verifier says FAIL" in st["verifier_note"]
    assert "verifier wins" in (goal.gdir("g1") / "events.log").read_text()


def test_a_worker_that_claims_done_without_the_evidence_is_not_verified(tmp_path, monkeypatch):
    r = repo(tmp_path)
    check = mkgoal(tmp_path, r / "made.txt")
    script(tmp_path, [plan_of([{"id": "j1", "brief": "b", "cwd": str(r), "verify": [{"run": check}]}]),
                      {"do": "success", "result": "All done, everything passes!"}], monkeypatch)
    goal.step("g1", say=lambda m: None)
    goal.step("g1", say=lambda m: None)
    _, st = goal.load("g1")
    assert st["jobs"]["j1"]["status"] == "queued" and "verifier says FAIL" in st["jobs"]["j1"]["why"]
    assert st["status"] == "active"


def test_an_agent_criterion_is_judged_by_a_separate_run_and_no_verdict_is_fail(tmp_path, monkeypatch):
    spec = {"id": "g2", "objective": "o", "cwd": str(tmp_path),
            "criteria": [{"id": "c1", "judge": "agent", "question": "is the doc good?"}]}
    goal.new(spec)
    g, st = goal.load("g2")
    p = script(tmp_path, [{"do": "verdict", "verdict": "PASS", "evidence": "read it"}], monkeypatch)
    res = goal.verify("g2", g, st, claude=run.claude)
    assert res[0]["verdict"] == "PASS" and "VERIFIER" in calls(p)[0]["prompt"]
    script(tmp_path, [{"do": "success", "result": "looks fine to me"}], monkeypatch)
    Path(str(tmp_path / "fake-plan.json") + ".n").unlink(missing_ok=True)
    assert goal.verify("g2", g, st, claude=run.claude)[0]["verdict"] == "FAIL"


def test_an_owner_question_goes_to_the_inbox_and_other_work_goes_on(tmp_path, monkeypatch):
    r = repo(tmp_path)
    mkgoal(tmp_path, r / "x.txt")
    script(tmp_path, [plan_of([{"id": "j1", "brief": "free work", "cwd": str(r)},
                               {"id": "j2", "brief": "after the answer", "cwd": str(r), "waits_on": ["q1"]}],
                              questions=[{"id": "q1", "title": "which license?", "category": "license",
                                          "default": "MIT"}]),
                      {"do": "success"}], monkeypatch)
    goal.step("g1", say=lambda m: None)
    _, st = goal.load("g1")
    assert st["jobs"]["j1"]["status"] == "verified" and st["jobs"]["j2"]["status"] == "queued"
    assert inbox.items()[0]["kind"] == "decision" and "MIT" in inbox.items()[0]["why"]
    assert goal.runnable(st) is None
    inbox.done(inbox.items()[0]["n"], "")                       # empty answer = the default
    assert goal.collect_answers("g1", st, inbox.items(None))
    assert st["answers"]["q1"]["answer"] == "MIT" and goal.runnable(st) == "j2"


def test_preapproved_answers_need_no_human_and_a_changed_register_is_void(tmp_path, monkeypatch):
    r = repo(tmp_path)
    mkgoal(tmp_path, r / "x.txt")
    reg = goal.gdir("g1") / "PREAPPROVALS.yaml"
    reg.write_text("answers:\n  - {category: license, take_default: true}\n", encoding="utf-8")
    assert goal.preapprovals("g1")[0] == {}                     # not approved yet: nothing applies
    assert goal.approve("g1", isatty=False, say=lambda m: None) == 3   # no terminal, no approval
    monkeypatch.setenv("FIELDKIT_AUTOPILOT", "1")
    assert goal.approve("g1", confirm=True, say=lambda m: None) == 3   # never from inside autopilot
    monkeypatch.delenv("FIELDKIT_AUTOPILOT")
    assert goal.approve("g1", confirm=True, say=lambda m: None) == 0
    assert goal.preapprovals("g1")[0]["answers"]
    script(tmp_path, [plan_of([{"id": "j2", "brief": "b", "cwd": str(r), "waits_on": ["q1"]}],
                              questions=[{"id": "q1", "title": "which license?", "category": "license",
                                          "default": "MIT"}]), {"do": "success"}], monkeypatch)
    goal.step("g1", say=lambda m: None)
    _, st = goal.load("g1")
    assert st["answers"]["q1"]["answer"] == "MIT" and "pre-approval" in st["answers"]["q1"]["by"]
    assert not inbox.items() and st["jobs"]["j2"]["status"] == "verified"
    reg.write_text(reg.read_text() + "  - {category: push, answer: yes}\n", encoding="utf-8")
    assert goal.preapprovals("g1") == ({}, "PREAPPROVALS.yaml changed after its approval: void until approved again")


def test_a_register_covering_main_is_refused_and_main_is_never_pushed(tmp_path):
    assert goal.check_register({"actions": [{"push_branch": {"repo": ".", "branches": "*"}}]})
    assert goal.check_register({"actions": [{"push_branch": {"repo": ".", "branches": "feat/*"}}]}) == []
    reg = {"actions": [{"push_branch": {"repo": str(tmp_path), "branches": "*"}}]}
    assert goal.covered_action(reg, "push_branch", tmp_path, "main") is None
    assert goal.do_action("g", "push_branch", tmp_path, "master", {}, run=lambda *a, **k: 1 / 0)[0] is False


def test_windows_port_is_protected_like_main(tmp_path):
    reg = {"actions": [{"push_branch": {"repo": str(tmp_path), "branches": "*"}}]}
    assert goal.covered_action(reg, "push_branch", tmp_path, "windows-port") is None
    assert goal.check_register({"actions": [{"push_branch": {"repo": ".", "branches": "windows-*"}}]})


def test_on_done_pushes_every_job_branch_a_glob_names_under_the_preapproval(tmp_path, monkeypatch):
    r = repo(tmp_path)
    for b in ("night/a", "night/b", "other"):
        subprocess.run(["git", "-C", str(r), "branch", b], check=True)
    mkgoal(tmp_path, tmp_path / "x", on_done=[{"kind": "push_branch", "repo": str(r), "branch": "night/*"}])
    g, st = goal.load("g1")
    reg = {"actions": [{"push_branch": {"repo": str(r), "branches": "night/*"}}]}
    done = []
    goal._actions("g1", g, st, reg, lambda gid, kind, repo, branch, spec, **k: (done.append(branch), (True, "ok"))[1],
                  print)
    assert sorted(done) == ["night/a", "night/b"]
    assert not inbox.items()


def test_a_preapproved_push_runs_the_privacy_check_first_and_never_force(tmp_path, monkeypatch):
    seen = []

    class R:
        def __init__(self, rc):
            self.returncode, self.stdout, self.stderr = rc, "", ""

    def fake(args, **kw):
        seen.append(args)
        return R(0)
    ok, _ = goal.do_action("g", "push_branch", tmp_path, "feat/x", {"base": "origin/main"}, run=fake)
    assert ok and "push-check" in seen[0] and seen[1][-1] == "refs/heads/feat/x:refs/heads/feat/x"
    assert not any("--force" in a or "-f" == a for a in seen[1])
    seen.clear()
    ok, why = goal.do_action("g", "push_branch", tmp_path, "feat/x", {},
                             run=lambda a, **k: (seen.append(a), R(3 if "push-check" in a else 0))[1])
    assert not ok and len(seen) == 1 and "privacy" in why      # refused before any push


def test_whole_goal_budgets_block_and_spread(tmp_path, monkeypatch):
    mkgoal(tmp_path, tmp_path / "x", budget={"cost_usd": 10, "days": 2, "window_usd": 3, "window_hours": 5})
    g, st = goal.load("g1")
    now = time.time()
    st["spend"] = [{"t": now - 60, "usd": 3.5, "who": "job"}]
    s, why, until = goal.budget_block(g, st, now)
    assert s == "pause" and until == pytest.approx(now - 60 + 5 * 3600) and "spreading" in why
    st["spend"] = [{"t": now - 6 * 3600, "usd": 10, "who": "job"}]
    assert goal.budget_block(g, st, now)[0] == "blocked"
    st["spend"], st["started"] = [], now - 3 * 86400
    assert goal.budget_block(g, st, now)[0] == "blocked"


def test_the_planner_hitting_the_usage_window_pauses_the_goal(tmp_path, monkeypatch):
    mkgoal(tmp_path, tmp_path / "x")
    reset = int(time.time()) + 4000
    script(tmp_path, [{"do": "usage_limit", "text": f"Claude AI usage limit reached|{reset}"}], monkeypatch)
    out = goal.step("g1", say=lambda m: None)
    assert out["status"] == "waiting" and out["wait_until"] == reset + 60


def test_a_goal_stop_file_stops_it(tmp_path, monkeypatch):
    mkgoal(tmp_path, tmp_path / "x")
    assert goal.stop("g1") == ["g1"]
    assert goal.step("g1", say=lambda m: None)["status"] == "stopped"


def test_goal_spec_validation():
    assert goal.validate({"id": "Bad Id", "objective": "", "criteria": []})
    assert goal.validate({"id": "ok", "objective": "o", "criteria": [{"id": "c", "run": "x"}],
                          "seed_jobs": [{"id": "j", "brief": "b", "cwd": "."}]}) == []


# ---- the supervisor ---------------------------------------------------------------------------------------------
def test_one_supervisor_at_a_time_and_a_dead_one_is_taken_over(tmp_path, monkeypatch):
    goal.GOALS.mkdir(parents=True)
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        goal._wj(goal.GOALS / ".supervisor.lock", {"pid": other.pid, "heartbeat": time.time()})
        ok, why = goal.lock()
        assert not ok and str(other.pid) in why
        goal._wj(goal.GOALS / ".supervisor.lock", {"pid": other.pid, "heartbeat": time.time() - 3600})
        assert goal.lock()[0]                                   # silent for an hour: taken over
    finally:
        other.kill()
    goal._wj(goal.GOALS / ".supervisor.lock", {"pid": 999999, "heartbeat": time.time()})
    assert goal.lock()[0]                                       # its process is gone


def test_the_supervisor_resumes_unfinished_goals_after_a_restart(tmp_path, monkeypatch):
    r = repo(tmp_path)
    f = r / "made.txt"
    check = mkgoal(tmp_path, f)
    script(tmp_path, [plan_of([{"id": "j1", "brief": "make it", "cwd": str(r), "verify": [{"run": check}]},
                               {"id": "j2", "brief": "then this", "cwd": str(r), "needs": ["j1"]}]),
                      {"do": "success", "write": {"path": str(f), "text": "x"}}, plan_of(), {"do": "success"}],
           monkeypatch)
    assert goal.supervise(once=True, say=lambda m: None, hold=False) == 0     # one round, then "the machine dies"
    assert goal.load("g1")[1]["status"] == "active"
    assert goal.supervise(say=lambda m: None, hold=False) == 0                # the next logon carries on
    assert goal.load("g1")[1]["status"] == "done"
    assert not (goal.GOALS / ".supervisor.lock").exists()
    assert "every goal is finished" in (goal.GOALS / "supervisor.log").read_text(encoding="utf-8")


def test_the_supervisor_registration_script(tmp_path):
    ps, xml = goal.supervisor_script(tmp_path / "reg.ps1", python=r"C:\Py\python.exe", fieldkit_root=r"C:\FK")
    text = ps.read_text(encoding="utf-8-sig")
    assert "schtasks /Create" in text and "REGISTER: OK" in text and "goal status" in text
    x = xml.read_text(encoding="utf-16")
    import xml.etree.ElementTree as ET
    root = ET.fromstring(x.split("?>", 1)[1])
    ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
    assert root.find(".//t:LogonTrigger", ns) is not None
    assert "Power-Troubleshooter" in root.find(".//t:EventTrigger/t:Subscription", ns).text
    assert root.find(".//t:LogonType", ns).text == "InteractiveToken"
    assert root.find(".//t:RunLevel", ns).text == "LeastPrivilege"
    assert root.find(".//t:Arguments", ns).text == "-m fieldkit goal run"
    if sys.platform == "win32":
        chk = subprocess.run(["powershell", "-NoProfile", "-Command",
                              f"$e=$null; [System.Management.Automation.Language.Parser]::ParseFile('{ps}',[ref]$null,[ref]$e)"
                              " | Out-Null; $e.Count"], capture_output=True, text=True)
        assert chk.stdout.strip() == "0"


# ---- hard lines for goals ---------------------------------------------------------------------------------------
@pytest.mark.parametrize("path", ["state/goals/g1/goal.json", "state/goals/g1/GOAL.md", "state/goals/g1/state.json",
                                  "state/goals/g1/verdicts.jsonl", "state/goals/g1/PREAPPROVALS.yaml",
                                  "state/goals/g1/APPROVAL.json", "state/goals/g1/jobs/j/meter.json",
                                  "state/jobs/rb/x/CHECKPOINT.json"])
def test_a_model_never_edits_what_judges_it(path):
    assert hardlines.decide("Write", {"file_path": "C:/fk/" + path, "content": "x"})[0] == "deny"
    assert hardlines.decide("Bash", {"command": f"echo PASS > C:/fk/{path}"})[0] == "deny"


@pytest.mark.parametrize("path", ["state/goals/g1/rounds/001/plan.json", "state/goals/g1/jobs/j/HANDOFF.md"])
def test_the_planner_and_workers_may_write_their_own_files(path):
    assert hardlines.decide("Write", {"file_path": "C:/fk/" + path, "content": "x"})[0] == "allow"


# ---- publish: a branch never pushed (2026-10-22: publish refused all three of the day's new branches) ------------
def test_publish_pushes_a_new_branch_with_upstream_and_checks_its_own_commits(tmp_path, monkeypatch):
    monkeypatch.delenv("FIELDKIT_AUTOPILOT", raising=False)
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    r = repo(tmp_path)
    subprocess.run(["git", "-C", str(r), "branch", "-m", "main"], check=True)
    subprocess.run(["git", "-C", str(r), "remote", "add", "origin", str(origin)], check=True)
    subprocess.run(["git", "-C", str(r), "push", "-q", "origin", "main"], check=True)
    subprocess.run(["git", "-C", str(r), "fetch", "-q", "origin"], check=True)
    subprocess.run(["git", "-C", str(r), "switch", "-q", "-c", "feat/x"], check=True)
    (r / "b.txt").write_text("new\n")
    subprocess.run(["git", "-C", str(r), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(r), "commit", "-qm", "the new work"], check=True)
    plan = inbox.publish_plan(r)
    assert plan["new"] and plan["range"].endswith("..HEAD") and plan["range"].startswith("origin/")
    assert len(plan["ahead"]) == 1 and "the new work" in plan["ahead"][0]
    seen = []

    class R:
        returncode, stdout, stderr = 0, "", ""

    def run(args, **kw):
        seen.append(args)
        return R()
    assert inbox.publish(r, confirm=True, run=run, say=lambda m: None) == 0
    assert seen[0][-1] == plan["range"]                                   # the privacy check sees the new commits
    assert seen[1][-4:] == ["push", "-u", "origin", "feat/x"]
    subprocess.run(["git", "-C", str(r), "switch", "-q", "main"], check=True)
    subprocess.run(["git", "-C", str(r), "branch", "--unset-upstream"], capture_output=True)
    assert "never a new main" in " ".join(inbox.publish_plan(r)["blockers"])
