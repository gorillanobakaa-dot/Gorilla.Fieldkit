"""drive: one job at a time, stop when the machine is broken, never start on a failed preflight."""
import subprocess
import threading
import time
from types import SimpleNamespace

import pytest

from fieldkit.buildh import cli, preflight, task, worker
from tests.test_buildh_task import job  # noqa: F401  (fixture: a working copy with a two-step model workflow)


def args(**kw):
    return SimpleNamespace(agent="fake-agent", job_timeout="1m", max_jobs=6, tools=True, line_ops=False, **kw)


@pytest.fixture
def quiet(monkeypatch, tmp_path):
    monkeypatch.setenv("FIELDKIT_ALLOW_MODEL_TOOLS", "1")
    monkeypatch.setattr(worker, "write_profile", lambda **k: tmp_path / "profile")
    monkeypatch.setattr(worker, "environment", lambda root: {})
    monkeypatch.setattr(preflight, "run", lambda *a, **k: [{"check": "x", "ok": True, "evidence": ""}])


def test_jobs_never_overlap(job, quiet, monkeypatch):
    task.approve("t1", "owner")
    live, peak, calls = [0], [0], [0]
    real = subprocess.run

    def fake(cmd, *a, **k):
        if cmd and cmd[0] == "fake-agent":
            live[0] += 1
            peak[0] = max(peak[0], live[0])
            calls[0] += 1
            time.sleep(0.15)
            live[0] -= 1
            return subprocess.CompletedProcess(cmd, 0, "I did it", "")
        return real(cmd, *a, **k)
    monkeypatch.setattr(subprocess, "run", fake)
    cli.drive("t1", args())
    assert calls[0] >= 2 and peak[0] == 1, (calls[0], peak[0])


def test_a_failed_preflight_starts_nothing(job, monkeypatch, tmp_path):
    monkeypatch.setenv("FIELDKIT_ALLOW_MODEL_TOOLS", "1")
    task.approve("t1", "owner")
    monkeypatch.setattr(worker, "write_profile", lambda **k: tmp_path / "profile")
    monkeypatch.setattr(worker, "environment", lambda root: {})
    monkeypatch.setattr(preflight, "run", lambda *a, **k: [{"check": "git locks", "ok": False, "evidence": "stale"}])
    called = []
    real = subprocess.run
    monkeypatch.setattr(subprocess, "run", lambda cmd, *a, **k: called.append(cmd) if cmd[0] == "fake-agent" else real(cmd, *a, **k))
    assert cli.drive("t1", args()) == 5 and not called


def test_two_crashes_in_a_row_stop_the_run(job, quiet, monkeypatch):
    task.approve("t1", "owner")
    real = subprocess.run
    started = []

    def fake(cmd, *a, **k):
        if cmd and cmd[0] == "fake-agent":
            started.append(1)
            raise OSError("the machine is on fire")
        return real(cmd, *a, **k)
    monkeypatch.setattr(subprocess, "run", fake)
    assert cli.drive("t1", args()) == 4
    assert len(started) == 2


def test_tool_mode_is_refused_unless_the_owner_opts_in(job, monkeypatch):
    monkeypatch.delenv("FIELDKIT_ALLOW_MODEL_TOOLS", raising=False)
    task.approve("t1", "owner")
    with pytest.raises(task.Refused, match="anywhere on this computer"):
        cli.drive("t1", args())


def test_an_oversized_job_text_is_parked_once_without_burning_attempts(job, quiet, monkeypatch):
    task.approve("t1", "owner")
    monkeypatch.setattr(cli, "PACKET_LIMIT", 50)
    called = []
    real = subprocess.run
    monkeypatch.setattr(subprocess, "run", lambda cmd, *a, **k: called.append(cmd) if cmd[0] == "fake-agent" else real(cmd, *a, **k))
    assert cli.drive("t1", args()) == 3 and not called
    s = next(x for x in task.load("t1")["steps"] if x["id"] == "fix-alpha")
    assert s["status"] == "blocked" and s["attempts"] == 0 and "over the 50 limit" in s["last_why"][0]


def test_a_hunk_with_no_question_form_is_parked_for_a_person_not_sent_as_line_operations(job, quiet, monkeypatch):
    task.approve("t1", "owner")
    task.packet("t1")                                   # runs the prepare step, which creates fix-alpha
    monkeypatch.setenv("FIELDKIT_ALLOW_MODEL_TOOLS", "1")
    monkeypatch.setattr(task, "packet", lambda tid, **k: {"state": "MODEL STEP", "step": "fix-alpha", "chars": 100,
                                                            "packet": "DO: ...\nAnswer with line operations. ..."})
    called = []
    real = subprocess.run
    monkeypatch.setattr(subprocess, "run", lambda cmd, *a, **k: called.append(cmd) if cmd[0] == "fake-agent" else real(cmd, *a, **k))
    a = args()
    a.tools = False
    assert cli.drive("t1", a) == 3 and not called
    s = next(x for x in task.load("t1")["steps"] if x["id"] == "fix-alpha")
    assert s["status"] == "blocked" and "a person ports it" in s["last_why"][0]


def test_a_parked_job_does_not_end_the_run(job, quiet, monkeypatch):
    task.approve("t1", "owner")
    monkeypatch.setattr(cli, "PACKET_LIMIT", 50)         # every job text is 'too large' -> parked, one after another
    called = []
    real = subprocess.run
    monkeypatch.setattr(subprocess, "run", lambda cmd, *a, **k: called.append(cmd) if cmd[0] == "fake-agent" else real(cmd, *a, **k))
    assert cli.drive("t1", args()) == 3 and not called
    t = task.load("t1")
    assert [s["status"] for s in t["steps"] if s["kind"] == "model"] == ["blocked", "blocked"]     # both, not just the first
