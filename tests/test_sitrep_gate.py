"""No archive without a checked sitrep shown in chat (owner, 2026-11-25)."""
import json
from datetime import datetime, timezone

import pytest

from fieldkit import cli, sitrep
from fieldkit.autopilot import toolrouter
from fieldkit.lifecycle import permanence
from fieldkit.lifecycle import sitrepgate as gate

T0 = 1_800_000_000.0
SID = "aaaaaaaa-1111"
OTHER = "bbbbbbbb-2222"
REPORT = sitrep.example()
HEAD = gate.headline(REPORT)


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, "_dir", lambda: tmp_path / "sitrep")


def entry(kind, content, at):
    iso = datetime.fromtimestamp(at, timezone.utc).isoformat()
    return {"type": kind, "timestamp": iso, "message": {"content": content}}


def say(text, at):
    return entry("assistant", [{"type": "text", "text": text}], at)


def tool(name, at, **ti):
    return entry("assistant", [{"type": "tool_use", "name": name, "input": ti}], at)


def transcript(tmp_path, *rows, sid=SID):
    f = tmp_path / (sid + ".jsonl")
    f.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return str(f)


def call(tp, target="self"):
    return {"tool_name": "mcp__ccd_session_mgmt__archive_session", "session_id": SID,
            "tool_input": {"session_id": target}, "transcript_path": tp}


def checked(at, ok=True, sid=SID):
    gate.record(sid, REPORT, ok, now=at)


def test_checked_and_shown_report_passes(tmp_path):
    assert HEAD
    checked(T0 + 2)
    tp = transcript(tmp_path, entry("user", "go", T0), tool("Edit", T0 + 1, file_path="/r/a.py"),
                    say("**" + HEAD + "**", T0 + 3))
    assert gate.archive_refusal(call(tp)) is None
    assert gate.archive_refusal(dict(call(tp), tool_name="Bash")) is None


def test_a_last_message_that_is_itself_a_passing_report_counts(tmp_path):
    assert sitrep.check(REPORT)["ok"]
    tp = transcript(tmp_path, entry("user", "go", T0), say(REPORT, T0 + 3))
    assert gate.archive_refusal(call(tp)) is None


def test_missing_report_is_refused(tmp_path):
    tp = transcript(tmp_path, entry("user", "go", T0), say("done", T0 + 3))
    why = gate.archive_refusal(call(tp))
    assert why.startswith("ARCHIVE REFUSED (SITREP)") and "no checked sitrep" in why
    assert "sitrep template --lang ro" in why and "sitrep check" in why


def test_stale_report_is_refused(tmp_path):
    checked(T0 + 2)
    changed = transcript(tmp_path, entry("user", "go", T0), tool("Bash", T0 + 5, command="git commit -m x"),
                         say(HEAD, T0 + 6))
    assert "code changed" in gate.archive_refusal(call(changed))
    later = transcript(tmp_path, entry("user", "go", T0), entry("user", "more", T0 + 5), say(HEAD, T0 + 6))
    assert "earlier turn" in gate.archive_refusal(call(later))
    book = transcript(tmp_path, entry("user", "go", T0),
                      tool("Edit", T0 + 5, file_path="/r/state/coordination/PLAN.md"),
                      entry("user", [{"type": "tool_result"}], T0 + 6), say(HEAD, T0 + 7))
    assert gate.archive_refusal(call(book)) is None


def test_failing_sitrep_is_refused(tmp_path):
    checked(T0 + 1)
    checked(T0 + 2, ok=False)
    tp = transcript(tmp_path, entry("user", "go", T0), say(HEAD, T0 + 3))
    assert "FAILED" in gate.archive_refusal(call(tp))


def test_report_not_shown_is_refused(tmp_path):
    checked(T0 + 2)
    tp = transcript(tmp_path, entry("user", "go", T0), say("archiving now", T0 + 3))
    assert "not shown" in gate.archive_refusal(call(tp))


def test_archiving_another_session_needs_that_sessions_report(tmp_path, monkeypatch):
    mine = transcript(tmp_path, entry("user", "go", T0), say(REPORT, T0 + 1))
    theirs = transcript(tmp_path, entry("user", "go", T0), say(HEAD, T0 + 3), sid=OTHER)
    monkeypatch.setattr(permanence, "transcript_for", lambda sid: theirs if sid == OTHER else None)
    recs = {"local_x": {"cliSessionId": OTHER}}
    why = gate.archive_refusal(call(mine, "local_x"), records=recs)
    assert why and "no checked sitrep" in why
    checked(T0 + 2, sid=OTHER)
    assert gate.archive_refusal(call(mine, "local_x"), records=recs) is None
    assert "cannot be found" in gate.archive_refusal(call(mine, "local_nope"), records={})


def test_unreadable_evidence_refuses_and_the_router_uses_the_gate(tmp_path):
    assert "could not read" in gate.archive_refusal(call(str(tmp_path / "gone.jsonl")))
    tp = transcript(tmp_path, entry("user", "go", T0), say("done", T0 + 3))
    assert "SITREP" in toolrouter._sitrep(call(tp))


def test_sitrep_check_records_for_the_gate(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SID)
    f = tmp_path / "SITREP.md"
    f.write_text(REPORT, encoding="utf-8")
    assert cli.main(["sitrep", "check", str(f)]) == 0
    assert "recorded for the archive gate" in capsys.readouterr().out
    tp = transcript(tmp_path, entry("user", "go", T0), say(HEAD, T0 + 3))
    assert gate.archive_refusal(call(tp)) is None
