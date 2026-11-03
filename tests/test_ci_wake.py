"""The open-PR guard and the ci-wait wake (fieldkit/method/ci_wake.py, rotate.endcheck, rotate.hook_line).

A PR is OPEN unless gh says MERGED or CLOSED. A ci-wait counts only while its marker's process is alive.
"""
import json

import pytest

from fieldkit.method import ci_wake
from fieldkit.tokens import rotate

PR = "https://github.com/o/r/pull/7"


@pytest.fixture(autouse=True)
def iso(tmp_path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_CIWAIT_DIR", str(tmp_path / "ciw"))
    monkeypatch.setenv("FIELDKIT_ROTATE_DIR", str(tmp_path / "rot"))
    monkeypatch.delenv("FIELDKIT_JOB_DIR", raising=False)
    monkeypatch.delenv("FIELDKIT_ROTATE", raising=False)


def test_an_open_pr_without_a_wait_fails_the_check():
    fails = ci_wake.check([PR], lambda u: "OPEN", alive=lambda p: False)
    assert len(fails) == 1 and "fieldkit ship ci-wait 7" in fails[0]


def test_a_running_wait_for_that_pr_passes():
    ci_wake.begin(7, "sess-a", pid=4242)
    assert ci_wake.check([PR], lambda u: "OPEN", alive=lambda p: p == 4242) == []


def test_a_wait_whose_process_is_gone_fails():
    ci_wake.begin(7, "sess-a", pid=4242)
    assert ci_wake.check([PR], lambda u: "OPEN", alive=lambda p: False)


def test_merged_and_closed_prs_pass_and_unknown_fails_closed():
    assert ci_wake.check([PR], lambda u: "MERGED", alive=lambda p: False) == []
    assert ci_wake.check([PR], lambda u: "CLOSED", alive=lambda p: False) == []
    assert ci_wake.check([PR], lambda u: None, alive=lambda p: False)


def test_the_finish_removes_the_marker_and_wakes_only_the_session_that_started_it():
    ci_wake.begin(7, "sess-a", pid=4242)
    line = ci_wake.end(7, "sess-a", {"state": "green", "why": "", "next": "merge it"})
    assert "CI #7 finished: GREEN" in line and "NEXT: merge it" in line
    assert ci_wake.running(7, alive=lambda p: True) is False
    assert ci_wake.take("sess-b") == []
    assert ci_wake.take("sess-a") == [line]
    assert ci_wake.take("sess-a") == []                                  # read once


def test_the_wake_reaches_the_session_at_its_next_hook(tmp_path):
    t = tmp_path / "sess-hook.jsonl"
    t.write_text("", encoding="utf-8")
    line = ci_wake.end(7, "sess-hook", {"state": "red", "why": "tests failed"})
    out = rotate.hook_line({"transcript_path": str(t), "hook_event_name": "PostToolUse"}, "PostToolUse")
    assert out == line


def test_endcheck_fails_on_an_open_pr_the_session_mentioned(tmp_path):
    t = tmp_path / "projects" / "p" / "sess-end.jsonl"
    t.parent.mkdir(parents=True)
    rows = [{"type": "user", "timestamp": "2026-11-03T10:00:00Z", "cwd": "C:/work",
             "message": {"content": f"Opened {PR}"}},
            {"type": "assistant", "timestamp": "2026-11-03T10:01:00Z", "cwd": "C:/work",
             "message": {"id": "m1", "model": "claude-opus-5-5", "stop_reason": "end_turn", "content": [],
                         "usage": {"input_tokens": 1, "output_tokens": 1}}}]
    t.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    ok, fails = rotate.endcheck(str(t), state_fn=lambda u: "OPEN", alive=lambda p: False)
    assert ok is False and fails
    ci_wake.begin(7, "sess-end", pid=99)
    assert rotate.endcheck(str(t), state_fn=lambda u: "OPEN", alive=lambda p: True) == (True, [])
