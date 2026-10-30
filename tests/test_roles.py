"""HARNESS-PLAN I5 + I7: cheap workers by data, a reviewer with a small context, a verdict of three kinds, and a task
done only on a passing verdict. The model, the cable and the clock are fakes; the acceptance command is a real one."""
import concurrent.futures as cf
import json
import sys
from pathlib import Path

import pytest

from fieldkit.autopilot import goal, hardlines, inbox
from fieldkit.lifecycle import roles, tasks

NOW = 1_800_000_000.0
PY = sys.executable


@pytest.fixture(autouse=True)
def iso(tmp_path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_LIFECYCLE_DIR", str(tmp_path / "lc"))
    monkeypatch.setenv("FIELDKIT_ROOM_DIR", str(tmp_path / "room"))
    monkeypatch.setenv("FIELDKIT_COORDINATION_DIR", str(tmp_path / "coord"))
    (tmp_path / "room").mkdir()
    from fieldkit.autopilot import room
    monkeypatch.setattr(room, "ROOM_DIR", tmp_path / "room")
    st = tmp_path / "ap"
    st.mkdir()
    monkeypatch.setattr(hardlines, "STATE", st)
    monkeypatch.setattr(inbox, "INBOX", st / "inbox")
    monkeypatch.setattr(goal, "GOALS", tmp_path / "goals")


def brief(tmp_path, text="Fix the thing.\nmore"):
    p = tmp_path / "brief.md"
    p.write_text(text, encoding="utf-8")
    return p


def fake_claude(text, usd=0.5, seen=None):
    def claude(prompt, **kw):
        if seen is not None:
            seen.append((prompt, kw))
        return {"ok": True, "rc": 0, "result": {"result": text, "total_cost_usd": usd}}
    return claude


# ---- the worker model is data ------------------------------------------------------------------------------------

def test_worker_default_comes_from_the_data_and_override_wins(tmp_path):
    d = roles.load()
    assert roles.worker_model() == d["worker"]["default"] == "sonnet"
    assert roles.worker_model("haiku") == "haiku"
    custom = tmp_path / "r.yaml"
    custom.write_text(Path(roles.DATA).read_text(encoding="utf-8").replace("default: sonnet", "default: haiku"))
    assert roles.worker_model(data=roles.load(custom)) == "haiku"


@pytest.mark.parametrize("m", ["opus", "claude-opus-5-5", "fable", "claude-fable-5-1"])
def test_a_worker_on_an_expensive_model_is_refused(m):
    with pytest.raises(ValueError, match="reviewer model"):
        roles.worker_model(m)
    assert roles.worker_model(m, allow_expensive=True) == m


def test_missing_roles_data_is_an_error_not_defaults(tmp_path):
    with pytest.raises(ValueError, match="unreadable"):
        roles.load(tmp_path / "nope.yaml")
    bad = tmp_path / "bad.yaml"
    bad.write_text("worker: {default: x}\n")
    with pytest.raises(ValueError, match="lacks"):
        roles.load(bad)


def test_task_start_records_the_model_and_puts_it_in_the_runbook(tmp_path):
    r = tasks.start(brief(tmp_path), cwd=tmp_path, launch=lambda a: {"pid": 7, "log": "L"}, now=NOW)
    assert r["model"] == "sonnet"
    from fieldkit.autopilot import runbook
    assert runbook.load(r["runbook"])["steps"][0]["model"] == "sonnet"
    r2 = tasks.start(brief(tmp_path), cwd=tmp_path, launch=lambda a: {"pid": 8, "log": "L"}, now=NOW + 1,
                     model="haiku", again=True)
    assert r2["model"] == "haiku" and "model: haiku" in Path(r2["runbook"]).read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="reviewer model"):
        tasks.start(brief(tmp_path), cwd=tmp_path, launch=lambda a: {}, now=NOW + 2, model="opus", again=True)


def test_task_start_on_a_peer_sends_the_model_over_the_cable(tmp_path):
    sent = []
    tasks.start(brief(tmp_path), on="PEER", call=lambda req, timeout, device: sent.append(req) or
                {"ok": True, "started": 1}, now=NOW, model="haiku")
    assert sent[0]["model"] == "haiku"


def test_the_headless_run_passes_the_model_to_claude(tmp_path):
    from fieldkit.autopilot import job
    seen = []
    def fake(prompt, **kw):
        seen.append(kw.get("model"))
        return {"ok": True, "rc": 0, "result": {"result": "done", "session_id": "s"}}
    job.run_job(tmp_path / "j", "do it", str(tmp_path), model="haiku", claude=fake, inbox_add=lambda *a, **k: None)
    assert seen and seen[0] == "haiku"


def test_the_listener_gives_a_started_session_the_model(tmp_path, monkeypatch):
    from fieldkit.peers import link
    monkeypatch.setenv("FIELDKIT_LINK_HOME", str(tmp_path))
    (tmp_path / "link.yaml").write_text("allow_start: [claude]\ncwd: " + str(tmp_path).replace("\\", "/") + "\n")
    link.approve(confirm=True, say=lambda m: None)
    proc = type("P", (), {"pid": 7, "poll": lambda self: None})()
    for model, flags, want in (("haiku", {"--model", "--job", "--wall-minutes"}, True), ("haiku", set(), False),
                               ("x; rm -rf", {"--model"}, False)):
        seen = {}
        link.start_session("claude", "fieldkit-task T-9-z", "win", popen=lambda argv, **k: seen.update(argv=argv) or proc,
                           inbox_add=lambda *a, **k: None, flags_fn=lambda f=flags: f, sleep=lambda s: None,
                           check_fn=lambda a: (True, ""), follow=lambda *a: None, model=model,
                           worktree=lambda repo, name: (tmp_path, "w"))
        if model.startswith("x;"):                      # a malformed model is refused now (models.py), not dropped
            assert "argv" not in seen
            continue
        assert ("--model" in seen["argv"]) is want
        if want:
            assert seen["argv"][seen["argv"].index("--model") + 1] == "haiku"


# ---- the verdict: three kinds -------------------------------------------------------------------------------------

def test_parse_reads_the_last_verdict_line_and_nothing_else():
    assert roles.parse("blah\nVERDICT: PASS fine\n") == ("pass", "fine")
    assert roles.parse("VERDICT: PASS x\nVERDICT: SEND_BACK it lies") == ("send_back", "it lies")
    assert roles.parse("looks good to me") == (None, "")


def test_decide_is_pass_send_back_or_fix_itself_by_failures():
    assert roles.decide(True, "pass", "ok", 0)["kind"] == "pass"
    assert roles.decide(None, "pass", "ok", 0)["kind"] == "pass"
    assert roles.decide(True, "send_back", "wrong file", 0) == {"kind": "send_back", "why": "wrong file"}
    assert roles.decide(True, "send_back", "wrong file", 1)["kind"] == "fix_itself"      # the second failed round
    assert roles.decide(True, None, "", 0)["kind"] == "send_back"                         # no verdict line: closed
    assert roles.decide(True, None, "", 0)["why"] == "the reviewer gave no verdict line"


def test_a_failing_acceptance_command_is_never_overruled_by_the_reviewer():
    v = roles.decide(False, "pass", "looks right to me", 0)
    assert v["kind"] == "send_back" and "acceptance command failed" in v["why"]


def test_review_runs_acceptance_then_the_reviewer_on_a_small_context(tmp_path):
    seen = []
    task = "TASK-TEXT " + "t" * 9000
    result = "r" * 9000 + " RESULT-END"
    v = roles.review(task, result, f"{PY} -c \"print('ok')\"", 0, claude=fake_claude("VERDICT: PASS good", seen=seen),
                     cwd=str(tmp_path))
    assert v["kind"] == "pass" and v["accept_ok"] is True and v["reviewer"] == "opus"
    prompt, kw = seen[0]
    assert kw["model"] == "opus" and "TASK-TEXT" in prompt and "RESULT-END" in prompt
    limit = roles.load()["reviewer"]
    assert len(prompt) < limit["max_task_chars"] + limit["max_result_chars"] + 1500       # not the whole history
    assert kw["max_turns"] == limit["turns"]
    f = roles.review("t", "r", f"{PY} -c \"raise SystemExit(3)\"", 0, claude=fake_claude("VERDICT: PASS fine"),
                     cwd=str(tmp_path))
    assert f["kind"] == "send_back" and f["accept_ok"] is False


def test_fix_itself_passes_only_when_acceptance_passes_afterwards(tmp_path):
    flag = tmp_path / "fixed"
    acc = f"{PY} -c \"import sys,pathlib; sys.exit(0 if pathlib.Path(r'{flag}').exists() else 1)\""
    assert roles.fix_itself("t", "r", "why", acc, cwd=str(tmp_path), claude=fake_claude("I fixed it"))["kind"] == "fix_itself"
    def fixer(prompt, **kw):
        flag.write_text("x")
        return {"ok": True, "result": {"result": "done", "total_cost_usd": 1.0}}
    f = roles.fix_itself("t", "r", "why", acc, cwd=str(tmp_path), claude=fixer)
    assert f["kind"] == "pass" and f["usd"] == 1.0


# ---- C3b: a task is done on a passing verdict only ----------------------------------------------------------------

def start_gated(tmp_path, accept):
    return tasks.start(brief(tmp_path), cwd=tmp_path, launch=lambda a: {"pid": 7, "log": "L"}, now=NOW,
                       accept=accept, worktree=lambda repo, tid: (None, "none"))


def test_done_without_a_passing_verdict_is_not_done_and_counts_a_failure(tmp_path):
    t = start_gated(tmp_path, f"{PY} -c \"raise SystemExit(1)\"")
    rev = lambda task, result, accept, failures, cwd=None: roles.review(          # noqa: E731
        task, result, accept, failures, claude=fake_claude("VERDICT: PASS fine"), cwd=cwd)
    r = tasks.report(t["id"], "done", note="all green", reviewer=rev)
    assert r["status"] == "failed" and r["verdict"] == "send_back" and r["failures"] == 1
    assert "all green" in r["note"] and tasks.ledger()[t["id"]]["status"] == "failed"


def test_second_failure_is_fixed_by_the_reviewer_and_then_passes(tmp_path):
    flag = tmp_path / "ok"
    t = start_gated(tmp_path, f"{PY} -c \"import sys,pathlib; sys.exit(0 if pathlib.Path(r'{flag}').exists() else 1)\"")
    rev = lambda task, result, accept, failures, cwd=None: roles.review(          # noqa: E731
        task, result, accept, failures, claude=fake_claude("VERDICT: SEND_BACK no"), cwd=cwd)
    def fixer(task, result, why, accept, cwd=None):
        flag.write_text("x")
        return roles.fix_itself(task, result, why, accept, cwd=cwd, claude=fake_claude("fixed"))
    first = tasks.report(t["id"], "done", note="claim 1", reviewer=rev, fixer=fixer)
    assert first["status"] == "failed" and first["verdict"] == "send_back"
    second = tasks.report(t["id"], "done", note="claim 2", reviewer=rev, fixer=fixer)
    assert second["status"] == "done" and second["verdict"] == "pass" and tasks.ledger()[t["id"]]["status"] == "done"


def test_a_pass_verdict_is_done(tmp_path):
    t = start_gated(tmp_path, f"{PY} -c \"print(1)\"")
    rev = lambda task, result, accept, failures, cwd=None: roles.review(          # noqa: E731
        task, result, accept, failures, claude=fake_claude("VERDICT: PASS fine"), cwd=cwd)
    assert tasks.report(t["id"], "done", note="ok", reviewer=rev)["status"] == "done"


def test_a_task_without_an_acceptance_command_is_not_gated(tmp_path):
    t = tasks.start(brief(tmp_path), cwd=tmp_path, launch=lambda a: {"pid": 7, "log": "L"}, now=NOW,
                    worktree=lambda repo, tid: (None, "none"))
    boom = lambda *a, **k: pytest.fail("reviewer called")                          # noqa: E731
    assert tasks.report(t["id"], "done", reviewer=boom)["status"] == "done"


# ---- the goal path ------------------------------------------------------------------------------------------------

def end_job(tmp_path, attempts, check_ok, claude):
    gid = "g1"
    (goal.gdir(gid)).mkdir(parents=True)
    fut = cf.Future()
    fut.set_result({"status": "done", "why": "claims done"})
    goal._WORKERS[gid] = {"j1": {"future": fut, "spend": []}}
    run_cmd = f"{PY} -c \"raise SystemExit({0 if check_ok else 1})\""
    st = {"spend": [], "criteria": {}, "answers": {}, "needs_plan": False,
          "jobs": {"j1": {"status": "running", "attempts": attempts,
                          "spec": {"brief": "do j1", "cwd": str(tmp_path), "verify": [{"run": run_cmd}]}}}}
    g = {"id": gid, "criteria": [], "cwd": str(tmp_path), "objective": "o"}
    goal._end_job(gid, g, st, "j1", claude, __import__("subprocess").run)
    return st["jobs"]["j1"], st


def test_goal_job_failing_its_check_is_sent_back_with_the_mistake_in_the_brief(tmp_path):
    j, _ = end_job(tmp_path, 1, False, pytest.fail)
    assert j["verdict"] == "send_back" and j["status"] == "queued"
    assert "SENT BACK (round 1)" in j["spec"]["brief"]


def test_goal_job_failed_twice_is_fixed_by_the_reviewer_and_checked_again(tmp_path):
    seen = []
    j, st = end_job(tmp_path, 2, False, fake_claude("I tried", seen=seen))
    assert seen and seen[0][1]["model"] == "opus"                      # the reviewer model, not the worker's
    assert j["verdict"] == "fix_itself" and j["status"] == "queued" and not j.get("fixed_by")  # still failing: planner
    assert any(x["who"].startswith("reviewer fix") for x in st["spend"])


def test_goal_job_passing_its_check_is_verified_with_no_reviewer(tmp_path):
    j, _ = end_job(tmp_path, 1, True, pytest.fail)
    assert j["status"] == "verified" and j["verdict"] == "pass"
