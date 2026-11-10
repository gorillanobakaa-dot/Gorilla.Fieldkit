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
