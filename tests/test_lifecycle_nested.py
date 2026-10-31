"""No archive takes a live side session with it (owner, 2026-10-31: Zero 16's archive_session "self" also archived
Zero 17, the session it had started)."""
import json

from fieldkit.autopilot import toolrouter
from fieldkit.lifecycle import nested

CHILD = "local_d1b3d2a2-04c4-4f67-9927-8df8ce250275"
OTHER = "local_aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def transcript(tmp_path, *pairs):
    rows = []
    for k, (name, inp, out) in enumerate(pairs):
        rows.append({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": f"t{k}", "name": name, "input": inp}]}})
        rows.append({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": f"t{k}", "content": [{"type": "text", "text": out}]}]}})
    p = tmp_path / "s.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    return str(p)


START = ("mcp__ccd_session__start_session", {"title": "Zero"}, f"Started session {CHILD} (name zero-17)")
ARCH = {"tool_name": "mcp__ccd_session_mgmt__archive_session", "tool_input": {"session_id": "self"}}


def test_the_incident_archive_self_with_a_nested_child_is_refused(tmp_path):
    t = transcript(tmp_path, START)
    why = nested.pretool({**ARCH, "transcript_path": t}, ok=set(), recs={})
    assert why and CHILD in why and "detach_session" in why
    import fieldkit.lifecycle.nested as nm
    nm_load, nm.load_records = nm.load_records, lambda pattern=None: {}
    try:
        v = toolrouter.verdict({**ARCH, "transcript_path": t, "session_id": "x"})
    finally:
        nm.load_records = nm_load
    assert v and v["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_a_detached_child_or_one_recorded_ok_lets_the_archive_through(tmp_path):
    t = transcript(tmp_path, START, ("mcp__ccd_session_mgmt__detach_session", {"session_id": CHILD},
                                     "This session is now a top-level session."))
    assert nested.pretool({**ARCH, "transcript_path": t}, ok=set(), recs={}) is None
    t2 = transcript(tmp_path, START)
    assert nested.pretool({**ARCH, "transcript_path": t2}, ok={CHILD}, recs={}) is None


def test_a_failed_detach_and_archiving_another_session_are_judged_right(tmp_path):
    t = transcript(tmp_path, START, ("mcp__ccd_session_mgmt__detach_session", {"session_id": CHILD},
                                     "refused: a session the user placed"))
    assert nested.pretool({**ARCH, "transcript_path": t}, ok=set(), recs={})
    other = {"tool_name": "mcp__ccd_session_mgmt__archive_session", "tool_input": {"session_id": OTHER}}
    assert nested.pretool({**other, "transcript_path": t}, ok=set(), recs={}) is None
    assert nested.pretool({"tool_name": "Bash", "tool_input": {}, "transcript_path": t}, ok=set(), recs={}) is None


def test_the_cli_check_exits_3_while_a_child_is_nested(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(nested, "recorded_ok", lambda: set())
    monkeypatch.setattr(nested, "load_records", lambda pattern=None: {})
    assert nested.main(["check", "self", "--transcript", transcript(tmp_path, START)]) == 3
    assert CHILD in capsys.readouterr().out
    assert nested.main(["ok", "not-an-id"]) == 2


NOW = 1793474000.0
PARENT = "local_d7753e49-376d-41b8-8188-143b45ad1d8a"


def rec(sid, cli, parent=None, ago_min=60, detached=False, archived=False, title="t"):
    return {"sessionId": sid, "cliSessionId": cli, "title": title, "lastActivityAt": (NOW - ago_min * 60) * 1000,
            "spawnedFrom": {"sessionId": parent} if parent else None, "lineageDetached": detached,
            "isArchived": archived}


def world(**kw):
    r = {PARENT: rec(PARENT, "02951f75-0000", title="Zero 16"),
         CHILD: rec(CHILD, "a522dfa2-0000", parent=PARENT, ago_min=1, title="Zero 17", **kw)}
    return r


def test_the_real_incident_from_the_app_records_zero16_archiving_itself_is_refused():
    why = nested.verdict("self", "02951f75-0000", world(), zero_cli="a522dfa2", now=NOW)
    assert len(why) == 1 and why[0].startswith("NESTED") and CHILD in why[0]
    assert nested.verdict("self", "02951f75-0000", world(detached=True), "a522dfa2", NOW) == []


def test_never_archive_the_zero_seat_or_a_running_session_by_id():
    why = nested.verdict(CHILD, "02951f75-0000", world(detached=True), zero_cli="a522dfa2", now=NOW)
    assert [w.split(":")[0] for w in why] == ["ZERO", "RUNNING"]
    r = world(detached=True)
    r[CHILD]["lastActivityAt"] = (NOW - 3600) * 1000
    assert [w.split(":")[0] for w in nested.verdict(CHILD, "x", r, zero_cli="ffffffff", now=NOW)] == []


def test_zero_may_archive_finished_sessions_one_by_one():
    r = world(detached=True)
    assert nested.verdict(PARENT, "a522dfa2-0000", r, zero_cli="a522dfa2", now=NOW) == []
    assert nested.verdict("local_unknown", "a522dfa2-0000", r, "a522dfa2", NOW) == []


def test_the_hook_uses_the_records_and_says_why():
    call = {"tool_name": "mcp__ccd_session_mgmt__archive_session", "tool_input": {"session_id": "self"},
            "session_id": "02951f75-0000"}
    why = nested.pretool(call, recs=world(), zero_cli="a522dfa2", now=NOW)
    assert why.startswith("ARCHIVE REFUSED") and "NESTED" in why
