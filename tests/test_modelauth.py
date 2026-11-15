"""P9 f: a lost claude CLI login makes model jobs wait and resume, never die (fieldkit/lifecycle/modelauth.py)."""
import json
import subprocess

import pytest

from fieldkit.lifecycle import modelauth, tasks

pytestmark = pytest.mark.real_modelauth


class P:
    def __init__(self, out, rc=0):
        self.stdout, self.returncode = out, rc


def runner(logged=True, exp="2026-12-10T20:52:40Z", calls=None):
    def run(cmd, **kw):
        if calls is not None:
            calls.append(cmd)
        assert cmd[-3:] == ["auth", "status", "--json"]          # never a model call
        return P(json.dumps({"loggedIn": logged, "loginExpiresAt": exp, "email": "x@y.z"}))
    return run


@pytest.fixture(autouse=True)
def iso(tmp_path, monkeypatch):
    monkeypatch.setattr(modelauth, "state_file", lambda: tmp_path / "modelauth.json")
    monkeypatch.setattr(tasks, "state_dir", lambda: tmp_path)
    monkeypatch.setattr("fieldkit.autopilot.run.claude_cmd", lambda: ["claude"])
    monkeypatch.setattr("fieldkit.autopilot.run.claude_exe", lambda: "/usr/bin/claude")
    return tmp_path


def test_auth_lines_are_recognised():
    assert modelauth.is_auth_failure("API Error: OAuth session expired and could not be refreshed")
    assert modelauth.is_auth_failure("Invalid API key · Please run /login")
    assert not modelauth.is_auth_failure("rate limited, retry after 30s")


def test_logged_out_is_a_wait_with_the_fix(iso):
    r = modelauth.check(now=1000, run=runner(logged=False))
    assert not r["ok"] and "not logged in" in r["why"]
    assert r["fix"] == "/usr/bin/claude auth login"
    assert "email" not in (iso / "modelauth.json").read_text()


def test_ok_is_cached_and_free(iso):
    calls = []
    assert modelauth.check(now=1000, run=runner(calls=calls))["ok"]
    assert modelauth.check(now=1100, run=runner(calls=calls))["cached"]
    assert len(calls) == 1
    modelauth.check(now=1000 + modelauth.OK_TTL + 1, run=runner(calls=calls))
    assert len(calls) == 2


def test_failed_job_waits_until_login_changes_or_retry_window(iso):
    seen = []
    modelauth.mark_failed("OAuth session expired and could not be refreshed", now=1000, run=runner(),
                          inbox_add=lambda *a, **k: seen.append((a, k)))
    assert seen and seen[0][1]["kind"] == "hardline" and "auth login" in seen[0][1]["command"]
    # status still says loggedIn (that is what happened on 2026-11-11), the login unchanged: still waiting
    assert not modelauth.check(now=1200, run=runner())["ok"]
    # the owner logged in again: the expiry moved, the job may go
    assert modelauth.check(now=1300, run=runner(exp="2027-01-01T00:00:00Z"))["ok"]


def test_retry_window_lets_one_try_through(iso):
    modelauth.mark_failed("Not logged in", now=1000, run=runner(), inbox_add=lambda *a, **k: None)
    assert modelauth.check(now=1000 + modelauth.RETRY_AFTER + 1, run=runner())["ok"]


def test_status_unknown_does_not_block(iso, monkeypatch):
    def boom(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 30)
    assert modelauth.check(now=1000, run=boom)["ok"]


def test_worker_dead_of_auth_is_waiting_not_died(iso, monkeypatch):
    log = iso / "w.log"
    log.write_text("...\nAPI Error: OAuth session expired and could not be refreshed\n")
    monkeypatch.setattr(modelauth, "mark_failed", lambda *a, **k: None)
    led = {"T1": {"id": "T1", "status": "started", "on": "here", "pid": 4242, "log": str(log)},
           "T2": {"id": "T2", "status": "started", "on": "here", "pid": 4243, "log": str(iso / "none.log")}}
    tasks.settle(led, alive=lambda pid: False)
    assert led["T1"]["status"] == modelauth.WAITING
    assert led["T2"]["status"] == "died"


def test_waiting_task_resumes_once_auth_works(iso, monkeypatch):
    rb = iso / "T1.md"
    rb.write_text("runbook")
    led = {"T1": {"id": "T1", "status": modelauth.WAITING, "on": "here", "runbook": str(rb)},
           "T2": {"id": "T2", "status": modelauth.WAITING, "on": "here", "runbook": str(rb)}}
    launched = []
    out = tasks.resume_waiting(led, check=lambda: {"ok": False, "why": "x", "fix": "f"},
                               launch=lambda argv: launched.append(argv) or {"pid": 1, "log": "l"})
    assert out == [] and not launched
    out = tasks.resume_waiting(led, check=lambda: {"ok": True},
                               launch=lambda argv: launched.append(argv) or {"pid": 77, "log": "l"})
    assert out == ["T1"] and launched == [["runbook", "run", str(rb)]]     # one per pass: no refresh burst
    assert led["T1"]["status"] == "started" and led["T1"]["pid"] == 77
    assert tasks.ledger()["T1"]["status"] == "started"


def test_start_with_lost_login_records_waiting_and_does_not_launch(iso, monkeypatch):
    brief = iso / "b.md"
    brief.write_text("# fix a thing\nPLAN ITEM: P9\n")
    monkeypatch.setattr(modelauth, "check", lambda: {"ok": False, "why": "the claude CLI is not logged in",
                                                     "fix": "claude auth login"})
    monkeypatch.setattr(modelauth, "file_inbox", lambda *a, **k: None)
    monkeypatch.setattr("fieldkit.lifecycle.brieflint.lint", lambda *a, **k: [])
    monkeypatch.setattr("fieldkit.lifecycle.routing.admit", lambda *a, **k: (True, ""))
    monkeypatch.setattr("fieldkit.lifecycle.routing.route", lambda *a, **k: {"action": "run", "model": None,
                                                                            "why": ""})
    monkeypatch.setattr("fieldkit.lifecycle.routing.log", lambda *a, **k: None)
    launched = []
    rec = tasks.start(brief, cwd=str(iso), worktree=lambda repo, tid: (None, "test"),
                      launch=lambda argv: launched.append(argv) or {"pid": 1})
    assert rec["status"] == modelauth.WAITING and not launched
    assert "MODEL AUTH: WAITING" in rec["answer"]
