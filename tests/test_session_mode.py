"""Session modes (owner O4, 2026-11-22): the five modes, enforced at the tool layer before the model's first turn."""
import json

import pytest

from fieldkit.autopilot import toolrouter as tr
from fieldkit.lifecycle import sessionmode as sm


@pytest.fixture(autouse=True)
def _dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_MODE_DIR", str(tmp_path / "mode"))
    monkeypatch.delenv("FIELDKIT_AUDIT_ONLY", raising=False)
    monkeypatch.delenv("FIELDKIT_AUTOPILOT", raising=False)


def _agent(sid="s1", **ti):
    return {"session_id": sid, "tool_name": "Agent", "tool_input": dict({"prompt": "x"}, **ti)}


def _denied(out):
    return bool(out) and out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_economy_refuses_agent():
    out = tr.verdict(_agent())                       # a fresh session: no mode file, Economy
    assert _denied(out) and "SESSION MODE" in out["hookSpecificOutput"]["permissionDecisionReason"]
    assert _denied(tr.verdict(dict(_agent(), tool_name="Task")))


def test_a_fresh_session_gets_economy_and_a_bad_file_too(tmp_path):
    assert sm.current("new")["mode"] == "1" and sm.current("new")["cap"] == 0
    (tmp_path / "mode").mkdir(parents=True, exist_ok=True)
    (tmp_path / "mode" / "bad.json").write_text("{not json", encoding="utf-8")
    assert sm.current("bad")["cap"] == 0


@pytest.mark.parametrize("code,cap", [("1", 0), ("2", 6), ("3", 1000), ("4a", 0), ("4b", 6), ("4c", 1000),
                                      ("5a", 0), ("5b", 6), ("5c", 1000), ("4", 0), ("5", 0)])
def test_each_mode_writes_its_caps(code, cap):
    rec = sm.write("s", code)
    assert rec["cap"] == cap and sm.current("s")["cap"] == cap
    assert rec["per_node"] == (code.startswith("5"))


def test_an_unknown_mode_is_refused():
    with pytest.raises(ValueError):
        sm.record("9")


def test_time_sensitive_allows_six_and_refuses_the_seventh():
    sm.write("s1", "2")
    for _ in range(6):
        assert tr.verdict(_agent()) is None
    assert _denied(tr.verdict(_agent()))


def test_fast_allows_many():
    sm.write("s1", "3")
    for _ in range(20):
        assert tr.verdict(_agent()) is None


def test_explore_needs_haiku():
    sm.write("s1", "3")
    out = tr.verdict(_agent(subagent_type="Explore", model="sonnet"))
    assert _denied(out) and "pfind" in out["hookSpecificOutput"]["permissionDecisionReason"]
    assert tr.verdict(_agent(subagent_type="Explore", model="haiku")) is None


def test_grep_is_refused_and_names_pfind():
    out = tr.verdict({"session_id": "s1", "tool_name": "Grep", "tool_input": {"pattern": "x"}})
    assert _denied(out) and "pfind" in out["hookSpecificOutput"]["permissionDecisionReason"]


def test_the_question_is_asked_once_per_session():
    from fieldkit.autopilot import room
    call = {"session_id": "q1"}
    first = room._mode(call, "UserPromptSubmit", "")
    assert sm.QUESTION in first and "Economy" in first
    assert room._mode(call, "UserPromptSubmit", "") == ""
    assert sm.QUESTION in room._mode({"session_id": "q2"}, "SessionStart", "")


def test_the_agent_tool_is_in_the_room_match():
    from fieldkit.autopilot import room
    assert "Agent" in room.TOOL_MATCH and "Task" in room.TOOL_MATCH


def test_inherit_carries_the_mode_to_another_session():
    rec = sm.write("a", "4b")
    sm.inherit(rec, "b")
    assert sm.current("b")["mode"] == "4b" and sm.current("b")["cap"] == 6
    assert sm.inherit({"mode": "zz"}, "c") is None and not sm.asked("c")


def test_route_choose_writes_the_session_mode(capsys):
    from fieldkit.lifecycle import routing
    assert routing.main(["choose", "--session-mode", "2", "--session", "rr"]) == 0
    assert sm.current("rr")["cap"] == 6
    assert routing.main(["choose", "--session-mode", "9", "--session", "rr"]) == 2
    json.dumps(sm.current("rr"))


class _V:
    def check(self, req):
        return None


def test_a_peer_task_start_without_a_mode_is_refused(tmp_path, monkeypatch):
    from fieldkit.peers import link
    started = []
    monkeypatch.setattr(link, "start_session", lambda *a, **k: started.append(a) or (True, "ok"))
    monkeypatch.setattr(link, "store", lambda kind, req: {"n": 1})
    r = link.handle({"verb": "ask", "agent": "claude", "brief": "do it", "from_host": "L15"}, _V(), via="cable")
    assert not r["ok"] and "session mode" in r["error"] and not started
    r = link.handle({"verb": "ask", "agent": "claude", "brief": "do it", "from_host": "L15",
                     "session_mode": {"mode": "zz"}}, _V(), via="cable")
    assert not r["ok"] and not started


def test_a_peer_task_start_with_a_mode_carries_it_into_the_brief(monkeypatch):
    from fieldkit.peers import link
    started = []
    monkeypatch.setattr(link, "start_session", lambda *a, **k: started.append(a) or (True, "ok"))
    monkeypatch.setattr(link, "store", lambda kind, req: {"n": 1})
    rec = sm.record("5b")
    r = link.handle({"verb": "ask", "agent": "claude", "brief": "do it", "from_host": "L15", "session_mode": rec},
                    _V(), via="cable")
    assert r["ok"] and started and "SESSION MODE 5b" in started[0][1] and "per node" in started[0][1]
