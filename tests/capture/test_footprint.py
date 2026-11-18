import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from fieldkit.capture import cleanslate, cli, footprint, lesson, retention, session_history, switch


SID = "01234567-89ab-cdef-0123-456789abcdef"


def rollout(old, recent=None, *, closed=True):
    def row(kind, when=old, **payload):
        return {"timestamp": when.isoformat(), "type": kind, "payload": payload}

    rows = [row("session_meta", id=SID, timestamp=old.isoformat()),
            row("event_msg", type="task_started", turn_id="turn"),
            row("response_item", type="function_call", call_id="work"),
            row("response_item", recent or old, type="function_call_output", call_id="work")]
    if closed:
        rows.append(row("event_msg", recent or old, type="task_complete", turn_id="turn"))
    return b"".join(json.dumps(row).encode() + b"\r\n" for row in rows)


@pytest.fixture
def native(tmp_path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_CAPTURE_DIR", str(tmp_path / "capture"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setattr(cleanslate, "scratch_roots", lambda: [])
    monkeypatch.setattr(switch, "is_on", lambda *a: False)
    monkeypatch.setattr(footprint, "_temp_roots", lambda *a: [])
    monkeypatch.setattr(footprint, "_machine_temp", lambda *a: [])
    codex = tmp_path / "codex" / "archived_sessions"
    codex.mkdir(parents=True)
    return codex / f"rollout-2026-01-01T00-00-00-{SID}.jsonl"


def test_all_archives_native_bytes_and_deterministic_disposition_before_recycling(native, tmp_path, monkeypatch):
    raw = rollout(datetime.now(timezone.utc) - timedelta(minutes=10))
    native.write_bytes(raw)
    assert not cleanslate.clean(86400, inventory=True)["footprint"]["session_candidates"]
    assert len(cleanslate.clean(inventory=True)["footprint"]["session_candidates"]) == 1
    monkeypatch.setattr(session_history, "quiescent", lambda _: False)
    active = cleanslate.clean(apply=True, inventory=True, retire_sessions=True,
                              recycle_fn=lambda _: pytest.fail("recycled a session with Codex running"))
    assert "still running" in active["sessions_retained"][0]["reason"]
    assert not session_history.archive_dir(tmp_path / "capture").exists()
    monkeypatch.setattr(session_history, "quiescent", lambda _: True)
    receipts = []

    def trash(path):
        archive = session_history.archive_dir(tmp_path / "capture")
        assert list(archive.glob("*.jsonl"))[0].read_bytes() == raw
        eid = lesson.identity("codex", {"session_id": SID, "tool_use_id": "work"})
        assert session_history.events_for(tmp_path / "capture", "codex", SID) == {eid}
        assert path == native
        path.rename(tmp_path / "fixture-trash")
        receipts.append(path)
        return True

    monkeypatch.setattr(cleanslate, "recycle", trash)
    assert cli.main(["clean", "--all", "--apply", "--retire-sessions", "--json"]) == 0
    assert receipts == [native]
    assert (tmp_path / "fixture-trash").read_bytes() == raw


@pytest.mark.parametrize("closed,recent,reason", [(False, False, "unfinished"), (True, True, "cutoff")])
def test_refuses_open_and_mixed_age_native_sessions(native, monkeypatch, closed, recent, reason):
    now = datetime.now(timezone.utc)
    old = now - timedelta(days=5)
    raw = rollout(old, now if recent else None, closed=closed)
    native.write_bytes(raw)
    os.utime(native, (old.timestamp(), old.timestamp()))
    monkeypatch.setattr(session_history, "quiescent", lambda _: True)
    result = cleanslate.clean(3 * 86400, apply=True, inventory=True, retire_sessions=True,
                             recycle_fn=lambda _: pytest.fail("recycled a mixed or unfinished transcript"))
    assert reason in result["sessions_retained"][0]["reason"]
    assert native.read_bytes() == raw


def test_archive_corruption_blocks_recycle(native, tmp_path, monkeypatch):
    native.write_bytes(rollout(datetime.now(timezone.utc) - timedelta(minutes=10)))
    monkeypatch.setattr(session_history, "quiescent", lambda _: True)
    publish = session_history._persist

    def corrupt(path, raw, limit):
        if path.suffix == ".jsonl":
            path.write_bytes(b"changed")
        publish(path, raw, limit)

    monkeypatch.setattr(session_history, "_persist", corrupt)
    result = cleanslate.clean(apply=True, inventory=True, retire_sessions=True,
                             recycle_fn=lambda _: pytest.fail("recycled without a verified archive"))
    assert "read-back" in result["failure"]["error"]
    assert native.exists()
    assert not list((tmp_path / "capture-session-archive").glob("*.manifest.json"))


def test_legacy_timer_never_reads_history(tmp_path, monkeypatch):
    from fieldkit.capture import scan

    monkeypatch.setenv("FIELDKIT_CAPTURE_DIR", str(tmp_path / "capture"))
    monkeypatch.setattr(switch, "read", lambda: {"on": True})
    monkeypatch.setattr(switch, "is_on", lambda *a: True)
    monkeypatch.setattr(scan, "scan", lambda *a, **kw: pytest.fail("unscoped timer tried legacy scan"))
    assert cli.main(["scan", "--scheduled"]) == 0


@pytest.mark.parametrize("agent", ["claude", "codex", "gemini", "kiro-cli"])
def test_age_cleanup_retires_only_closed_old_transcripts_before_switch(agent, tmp_path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_CAPTURE_DIR", str(tmp_path / "capture"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("GEMINI_CLI_HOME", str(tmp_path / "gemini"))
    monkeypatch.setenv("KIRO_HOME", str(tmp_path / "kiro"))
    monkeypatch.setattr(cleanslate, "scratch_roots", lambda: [])
    monkeypatch.setattr(session_history, "quiescent", lambda name: name == agent)
    now = datetime.now(timezone.utc)
    old = now - timedelta(days=9)
    fresh = now - timedelta(hours=1)

    def fixture(index, *, complete, final):
        sid = str(index) + SID[1:]
        first, last = old.isoformat(), final.isoformat()
        if agent == "claude":
            file = tmp_path / "claude" / "projects" / "project" / f"{sid}.jsonl"
            rows = [{"type": "user", "sessionId": sid, "timestamp": first, "message": {"content": "fixture"}},
                    {"type": "assistant", "sessionId": sid, "timestamp": last,
                     "message": {"stop_reason": "end_turn" if complete else None, "content": "done"}}]
        elif agent == "codex":
            file = tmp_path / "codex" / "sessions/2026/01/01" / f"rollout-2026-01-01T00-00-00-{sid}.jsonl"
            rows = [json.loads(row.replace(SID.encode(), sid.encode())) for row in rollout(old, final).splitlines()]
            if complete:
                rows.append({"timestamp": last, "type": "event_msg", "payload": {"type": "shutdown_complete"}})
        elif agent == "gemini":
            file = tmp_path / "gemini/.gemini/tmp/project/chats" / f"session-2026-01-01T00-00-{sid[:8]}.json"
            rows = [{"sessionId": sid, "startTime": first, "lastUpdated": last,
                     "messages": [{"type": "user", "timestamp": first, "content": "fixture"},
                                  {"type": "gemini" if complete else "user", "timestamp": last, "content": "done"}]}]
        else:
            file = tmp_path / "kiro/sessions/project" / f"sess_{sid}" / "messages.jsonl"
            rows = [{"timestamp": int(when.timestamp() * 1000), "id": str(n),
                     "payload": {"type": kind, "executionId": "example"}}
                    for n, (when, kind) in enumerate(((old, "turn_start"), (final, "turn_end" if complete else "assistant")))]
        file.parent.mkdir(parents=True, exist_ok=True)
        raw = b"".join(json.dumps(row).encode() + b"\n" for row in rows)
        file.write_bytes(raw)
        os.utime(file, (old.timestamp(), old.timestamp()))
        return file, raw

    eligible, original = fixture(1, complete=True, final=old)
    unfinished, _ = fixture(2, complete=False, final=old)
    mixed, _ = fixture(3, complete=True, final=fresh)
    if agent == "kiro-cli":
        locked, _ = fixture(4, complete=True, final=old)
        locked.with_suffix(".lock").touch()
    if agent == "gemini":
        revised, _ = fixture(4, complete=True, final=old)
        revision = revised.with_suffix(".jsonl")
        revision.write_bytes(b"retained revision fixture")
    moved = []

    def recycle(path):
        assert path == eligible
        archive = session_history.archive_dir(tmp_path / "capture")
        assert any(file.read_bytes() == original for file in archive.glob("*" + eligible.suffix)
                   if not file.name.endswith(".manifest.json"))
        assert list(archive.glob("*.manifest.json"))
        path.rename(tmp_path / "fixture-trash")
        moved.append(path)
        return True

    def turn_on(*args, **kwargs):
        assert moved == [eligible]
        assert unfinished.exists() and mixed.exists()
        return {"on": True}

    result = cleanslate.start("fixture", "new lesson", seconds=3 * 86400, retire_sessions=True,
                              recycle_fn=recycle, turn_on=turn_on, disp={})
    assert result["started"] and result["sessions_recycled"][0]["path"] == str(eligible)
    retained = {Path(row["path"]): row["reason"] for row in result["sessions_retained"]}
    assert unfinished in retained and "cutoff" in retained[mixed]
    if agent == "kiro-cli":
        assert "lock" in retained[locked] and locked.exists()
    if agent == "gemini":
        assert "companion JSONL" in retained[revised] and revised.exists() and revision.exists()
