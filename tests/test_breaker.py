"""Circuit breakers (fieldkit/autopilot/breaker.py, L2 in docs/HARNESS-PLAN.md): the repeated-call breaker."""
import io
import json

import pytest

from fieldkit.autopilot import breaker, toolrouter


@pytest.fixture(autouse=True)
def iso(tmp_path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_ROTATE_DIR", str(tmp_path / "rot"))
    monkeypatch.delenv("FIELDKIT_BREAKER", raising=False)


def call(cmd, tp="C:/t/s1.jsonl", tool="Bash"):
    return {"transcript_path": tp, "tool_name": tool, "tool_input": {"command": cmd}, "hook_event_name": "PreToolUse"}


def test_trips_on_the_fifth_identical_call_in_a_row():
    for _ in range(breaker.LIMIT - 1):
        assert breaker.repeat_refusal(call("gh pr checks 1")) is None
    assert breaker.repeat_refusal(call("gh pr checks 1")).startswith("CIRCUIT BREAKER")
    assert breaker.repeat_refusal(call("gh pr checks 1"))                        # stays open while it repeats


def test_a_different_call_resets_the_count():
    for _ in range(breaker.LIMIT - 1):
        breaker.repeat_refusal(call("a"))
    assert breaker.repeat_refusal(call("b")) is None
    for _ in range(breaker.LIMIT - 2):
        assert breaker.repeat_refusal(call("a")) is None


def test_sessions_are_counted_apart_and_tools_differ():
    for _ in range(breaker.LIMIT - 1):
        breaker.repeat_refusal(call("a", tp="C:/t/s1.jsonl"))
    assert breaker.repeat_refusal(call("a", tp="C:/t/s2.jsonl")) is None
    assert breaker.repeat_refusal(call("a", tool="PowerShell")) is None


def test_off_switch_and_no_transcript(monkeypatch):
    assert breaker.repeat_refusal({"tool_name": "Bash", "tool_input": {"command": "x"}}) is None
    monkeypatch.setenv("FIELDKIT_BREAKER", "off")
    for _ in range(breaker.LIMIT + 1):
        assert breaker.repeat_refusal(call("x")) is None


def test_toolrouter_hook_denies_the_loop(capsys):
    for _ in range(breaker.LIMIT):
        toolrouter.hook(io.StringIO(json.dumps(call("echo again"))))
    outs = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()]
    assert len(outs) == 1
    d = json.loads(outs[0])["hookSpecificOutput"]
    assert d["permissionDecision"] == "deny" and "CIRCUIT BREAKER" in d["permissionDecisionReason"]
