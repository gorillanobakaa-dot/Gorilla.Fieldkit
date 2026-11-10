"""fieldkit capture on|off|state: capture only while a lesson is taught (owner, 2026-11-08)."""
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest

from fieldkit.capture import switch

OTHER = {"type": "command", "command": "python -m fieldkit.tokens.rotate --stop-hook", "timeout": 30}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_CAPTURE_DIR", str(tmp_path / "store"))
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".gemini").mkdir()
    (home / ".claude" / "settings.json").write_text(json.dumps({"hooks": {"Stop": [{"hooks": [OTHER]}]}, "x": 1}))
    (home / ".gemini" / "settings.json").write_text("{}")
    return home


def _settings(home, agent):
    return json.loads((home / f".{agent}" / "settings.json").read_text())


def _no_schedule(argv):
    return 1, "not found"


def test_on_adds_hooks_once_and_keeps_others(env):
    d = switch.turn_on("teach the release recipe", 30, home=env, py="py")
    assert d["hooks_added"] == {"claude": 2, "gemini": 2}
    assert switch.turn_on("again", 30, home=env, py="py")["hooks_added"] == {"claude": 0, "gemini": 0}
    s = _settings(env, "claude")
    assert s["x"] == 1 and OTHER in s["hooks"]["Stop"][0]["hooks"]
    assert s["hooks"]["PostToolUse"][0]["matcher"] == switch.CLAUDE_TOOLS
    assert switch.count_hooks(s) == 2 and switch.count_hooks(_settings(env, "gemini")) == 2
    assert switch.is_on()


def test_off_removes_only_ours(env):
    switch.turn_on("lesson", 30, home=env, py="py")
    d = switch.turn_off(home=env, run=_no_schedule, system="win32")
    assert d["hooks_removed"] == {"claude": 2, "gemini": 2} and d["last_lesson"] == "lesson"
    assert d["schedule"] == {"task": "absent", "logon": "absent"}
    s = _settings(env, "claude")
    assert switch.count_hooks(s) == 0 and s["hooks"]["Stop"] == [{"hooks": [OTHER]}]
    assert not switch.is_on()


def test_expiry_and_fail_closed(env):
    t = datetime(2026, 11, 8, 12, tzinfo=timezone.utc)
    switch.turn_on("lesson", 60, home=env, py="py", now=t)
    assert switch.is_on(now=t + timedelta(minutes=59))
    assert not switch.is_on(now=t + timedelta(minutes=61))
    assert switch.state(home=env, run=_no_schedule, system="win32", now=t + timedelta(hours=2))["capture"] == "expired"
    switch.switch_path().write_text("not json")
    assert not switch.is_on()


def test_on_refuses_without_lesson_or_with_bad_time(env):
    with pytest.raises(ValueError):
        switch.turn_on("  ", 30, home=env)
    with pytest.raises(ValueError):
        switch.turn_on("x", switch.MAX_MINUTES + 1, home=env)


def test_no_switch_file_is_legacy_on(env):
    assert switch.read().get("legacy") and switch.is_on()


def test_schedule_off_windows_disables_what_exists():
    calls = []

    def run(argv):
        calls.append(argv[:2])
        return 0, "ok"
    out = switch.schedule_off(run, "win32")
    assert out == {"task": "disabled", "logon": "removed"}
    assert ["schtasks", "/change"] in calls and ["reg", "delete"] in calls


def _hook(store, payload):
    e = dict(os.environ, FIELDKIT_CAPTURE_DIR=str(store))
    r = subprocess.run([sys.executable, "-m", "fieldkit", "capture", "hook", "--agent", "claude"],
                       input=json.dumps(payload), capture_output=True, text=True, env=e, timeout=60)
    assert r.returncode == 0
    j = store / "journal.jsonl"
    return j.read_text(encoding="utf-8").count("\n") if j.is_file() else 0


def test_hook_records_nothing_while_off(tmp_path):
    store = tmp_path / "s"
    store.mkdir()
    (store / "switch.json").write_text(json.dumps({"on": False}))
    ev = {"hook_event_name": "PostToolUse", "tool_name": "Bash", "session_id": "s1",
          "tool_input": {"command": "echo hi"}, "tool_response": {"stdout": "hi"}}
    assert _hook(store, ev) == 0
    until = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    (store / "switch.json").write_text(json.dumps({"on": True, "lesson": "t", "until": until}))
    assert _hook(store, ev) > 0


def test_a_manual_lesson_may_run_one_week_and_not_one_minute_more(env):
    # owner 2026-11-10: a Firefox migration took 5-6 days; one week, an extension only when the owner grants it
    assert switch.MAX_MINUTES == 7 * 24 * 60
    switch.turn_on("migration", 7 * 24 * 60, home=env)
    with pytest.raises(ValueError):
        switch.turn_on("migration", 7 * 24 * 60 + 1, home=env)


# ---- P26: an extension is asked of the owner, never taken or let lapse silently ------------------------------------
T0 = datetime(2026, 11, 10, 12, tzinfo=timezone.utc)


def test_extension_asked_once_under_12h_with_the_job_named(env):
    switch.turn_on("kernel 7.1 build", 7 * 24 * 60, home=env, py="py", now=T0, owner="kernel:job-1")
    asked = []
    add = lambda *a, **k: asked.append((a, k))
    assert switch.ask_extension(T0 + timedelta(days=3), add) == "" and not asked
    line = switch.ask_extension(T0 + timedelta(days=7) - timedelta(hours=11), add)
    assert "kernel:job-1" in line and "fieldkit capture on --for" in line and "yourself" in line
    assert len(asked) == 1 and asked[0][1]["kind"] == "hardline" and "kernel:job-1" in asked[0][0][0]
    switch.ask_extension(T0 + timedelta(days=7) - timedelta(hours=5), add)
    assert len(asked) == 1                                       # once per end time, the line stays


def test_no_extension_when_off_or_expired(env):
    asked = []
    switch.turn_on("l", 60, home=env, py="py", now=T0)
    assert switch.ask_extension(T0 + timedelta(hours=2), lambda *a, **k: asked.append(a)) == "" and not asked
    switch.turn_off(home=env, run=_no_schedule, system="win32")
    assert switch.ask_extension(T0, lambda *a, **k: asked.append(a)) == "" and not asked


def test_extend_keeps_lesson_and_owner_and_asks_again_later(env):
    switch.turn_on("l", 60, home=env, py="py", now=T0, owner="o")
    switch.ask_extension(T0 + timedelta(minutes=30), lambda *a, **k: None)
    d = switch.extend(600, now=T0 + timedelta(minutes=40), home=env, py="py")
    assert d["lesson"] == "l" and d["owner"] == "o" and "asked_until" not in d
    assert switch.read()["until"].startswith("2026-11-10T22:40")
    with pytest.raises(ValueError):
        switch.extend(switch.MAX_MINUTES + 1, now=T0 + timedelta(minutes=40), home=env)
    switch.turn_off(home=env, run=_no_schedule, system="win32")
    with pytest.raises(ValueError):
        switch.extend(60, home=env)


def test_cli_on_without_lesson_extends(env, monkeypatch):
    monkeypatch.setenv("HOME", str(env))
    switch.turn_on("keep me", 30, home=env, py="py")
    r = subprocess.run([sys.executable, "-m", "fieldkit", "capture", "on", "--for", "600", "--json"],
                       capture_output=True, text=True, env={**os.environ, "HOME": str(env)})
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["lesson"] == "keep me"
    assert (datetime.fromisoformat(switch.read()["until"]) - datetime.now(timezone.utc)) > timedelta(hours=9)


def test_plan_hook_line_carries_the_capture_line(env, monkeypatch, tmp_path):
    from fieldkit import plan
    switch.turn_on("l", 60, home=env, py="py", owner="job-x")
    monkeypatch.setattr("fieldkit.autopilot.inbox.add", lambda *a, **k: {})
    (tmp_path / "st").mkdir()
    monkeypatch.setattr(plan, "state_file", lambda root=None: tmp_path / "st" / "plan.json")
    (tmp_path / "st" / "plan.json").write_text('{"line": "PLAN: x"}')
    out = plan.line(live=False)
    assert out.startswith("PLAN: x") and "job-x" in out
    switch.turn_off(home=env, run=_no_schedule, system="win32")
    assert plan.line(live=False) == "PLAN: x"
