import json
from datetime import datetime, timedelta, timezone

import pytest

from fieldkit.capture import agy, cleanslate, hook, lesson, scan, store, switch


SESSION = "01234567-89ab-cdef-0123-456789abcdef"
OTHER = "01234567-89ab-cdef-0123-456789abcdee"


def test_selected_codex_rollout_pairs_only_admitted_calls(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    sessions = tmp_path / ".codex" / "sessions" / "2026" / "11" / "17"
    sessions.mkdir(parents=True)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / ".codex"))
    now = datetime.now(timezone.utc)
    clock = lambda minutes: (now - timedelta(minutes=minutes)).isoformat()
    scope = {"lesson_id": "fixture", "agents": ["codex"], "workspace": str(workspace),
             "since": clock(0), "retrospective_since": clock(10), "until": clock(-10),
             "sessions": {"codex": SESSION}}

    def row(time, kind, **payload):
        return {"timestamp": clock(time), "type": kind, "payload": payload}

    def shell(time, call_id, cwd, output):
        return [row(time, "response_item", type="function_call", name="exec_command", call_id=call_id,
                    arguments=json.dumps({"cmd": "fixture command", "workdir": str(cwd)})),
                row(time, "event_msg", type="exec_command_end", call_id=call_id, command="mirror"),
                row(time, "response_item", type="function_call_output", call_id=call_id, output=output)]

    target = sessions / f"rollout-2026-11-17T00-00-00-{SESSION}.jsonl"
    rows = [row(60, "session_meta", id=SESSION, cwd=str(workspace)),
            *shell(11, "stale", workspace, "old"),
            *shell(10, "historical", workspace, "Process exited with code 1\nfixture failed"),
            *shell(9, "foreign", tmp_path / "other", "foreign"),
            row(9, "response_item", type="function_call", name="exec_command", call_id="late",
                arguments=json.dumps({"cmd": "late result", "workdir": str(workspace)})),
            row(-11, "response_item", type="function_call_output", call_id="late", output="late result"),
            row(8, "response_item", type="reasoning", summary="private reasoning"),
            row(8, "response_item", type="custom_tool_call", name="apply_patch", call_id="mixed",
                input="*** Begin Patch\n*** Update File: fix.py\n*** Move to: ../outside.py\n*** End Patch"),
            row(7, "response_item", type="custom_tool_call_output", call_id="mixed", output="mixed result"),
            row(7, "response_item", type="custom_tool_call", name="apply_patch", call_id="patch",
                input="*** Begin Patch\n*** Add File: fix.py\n+print(1)\n*** End Patch"),
            row(6, "response_item", type="custom_tool_call_output", call_id="patch", output="applied")]
    target.write_text("\n".join(json.dumps(item) for item in rows), encoding="utf-8")
    (sessions / f"rollout-2026-11-17T00-00-00-{OTHER}.jsonl").write_text(
        json.dumps(row(60, "session_meta", id=OTHER, cwd=str(workspace))), encoding="utf-8")
    assert scan.verify_sessions(scope) == {}
    s = store.Store(tmp_path / "capture")
    result = scan.scan(store=s, scope=scope)
    assert result["files"] == 1 and result["calls"] == 2
    records = list(s.state()["items"].values())
    assert {(item["kind"], item["exit_code"]) for item in records} == {("command", 1), ("edit", 0)}
    assert scan.scan(store=s, scope=scope)["already"] == 2
    archive = tmp_path / "capture-archive"
    archive.mkdir()
    (archive / "capture-fixture.manifest.json").write_text(
        json.dumps({"history": {"events": list(s.state()["events"])}}), encoding="utf-8")
    assert scan.scan(store=store.Store(tmp_path / "next-capture"), scope=scope)["already"] == 2
    assert "private reasoning" not in s.journal.read_text(encoding="utf-8")


def test_claude_lookback_requires_a_matching_completed_pair(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    transcripts = tmp_path / ".claude" / "projects" / "workspace"
    transcripts.mkdir(parents=True)
    now = datetime.now(timezone.utc)
    scope = {"lesson_id": "fixture", "agents": ["claude"], "workspace": str(workspace),
             "since": (now - timedelta(minutes=2)).isoformat(), "retrospective_since": (now - timedelta(minutes=10)).isoformat(),
             "until": (now - timedelta(minutes=1)).isoformat(), "sessions": {"claude": SESSION}}

    def row(minutes, sid, **block):
        return {"timestamp": (now - timedelta(minutes=minutes)).isoformat(), "sessionId": sid,
                "cwd": str(workspace), "message": {"content": [block]}}

    def pair(call, end, sid=SESSION):
        return [
            row(5, SESSION, type="tool_use", name="Bash", id=call, input={"command": f"fixture {call}"}),
            row(end, sid, type="tool_result", tool_use_id=call, content="done"),
        ]

    rows = pair("accepted", 4) + pair("foreign", 4, OTHER) + pair("reversed", 6) + pair("late", 0)
    (transcripts / f"{SESSION}.jsonl").write_text("\n".join(map(json.dumps, rows)), encoding="utf-8")
    (transcripts / f"{OTHER}.jsonl").write_text("\n".join(map(json.dumps, pair("other-file", 3))), encoding="utf-8")
    s = store.Store(tmp_path / "capture")
    result = scan.scan(s, home=tmp_path, scope=scope)
    assert (result["files"], result["calls"], result["out_of_scope"]) == (1, 1, 3)
    assert [item["command"] for item in s.state()["items"].values()] == ["fixture accepted"]
    duplicate = transcripts.parent / "duplicate" / f"{SESSION}.jsonl"
    duplicate.parent.mkdir()
    duplicate.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="ambiguous"):
        scan.scan(s, home=tmp_path, scope=scope)


def test_lookback_missing_session_stops_before_scratch(tmp_path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_CAPTURE_DIR", str(tmp_path / "capture"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / ".codex"))
    with pytest.raises(ValueError, match="explicit"):
        cleanslate.start("kernel", "build", workspace=tmp_path, agents=["codex"], lookback=600)
    result = cleanslate.start("kernel", "build", workspace=tmp_path, agents=["codex"], lookback=600,
                              sessions=[f"codex={SESSION}"], recycle_fn=lambda _: pytest.fail("scratch was moved"))
    assert not result["started"]
    assert "rollout is missing" in result["agents"]["codex"]


def test_agy_cli_hook_validates_provenance_workspace_and_session(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".gemini").mkdir(parents=True)
    monkeypatch.setattr(agy.Path, "home", lambda: home)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(switch.shutil, "which", lambda _: "installed")
    assert switch.check_routes(["agy"], home) == {}
    assert switch.prepare_routes(["agy"], home) == {}
    assert agy.load(home)[agy.NAME] == agy.spec(switch.hook_command("agy"))
    now = datetime.now(timezone.utc)
    scope = {"lesson_id": "fixture", "agents": ["agy"], "workspace": str(workspace),
             "since": (now - timedelta(seconds=1)).isoformat(), "until": (now + timedelta(minutes=1)).isoformat(),
             "sessions": {"agy": SESSION}}
    data = {"toolCall": {"name": "run_command", "args": {"CommandLine": "fixture command", "Cwd": str(workspace)}},
            "stepIdx": 12, "conversationId": SESSION, "workspacePaths": [str(workspace)],
            "transcriptPath": str(home / ".gemini" / "antigravity-cli" / "transcript.jsonl"),
            "error": "fixture failed"}
    s = store.Store(tmp_path / "capture")
    assert hook.handle("agy", json.dumps(data), s, scope, now)
    assert not hook.handle("agy", json.dumps(data), s, scope, now)
    assert not hook.handle("agy", json.dumps({**data, "conversationId": OTHER}), s, scope, now)
    app_trace = str(home / ".gemini" / "antigravity" / "transcript.jsonl")
    assert not hook.handle("agy", json.dumps({**data, "transcriptPath": app_trace}), s, scope, now)
    assert switch.remove_routes(["agy"], home) == {"agy": 1}
    assert agy.NAME not in agy.load(home)


def test_agy_does_not_replace_an_unrelated_hook_or_leave_its_temporary_file(tmp_path, monkeypatch):
    path = agy.config_path(tmp_path)
    path.parent.mkdir(parents=True)
    other = {agy.NAME: {"PostToolUse": None}}
    path.write_text(json.dumps(other), encoding="utf-8")
    with pytest.raises(ValueError, match="unrelated"):
        agy.edit(tmp_path, switch.hook_command("agy"))
    assert agy.load(tmp_path) == other

    path.write_text("{}", encoding="utf-8")

    def deny_replace(*_):
        raise PermissionError("fixture failure")

    monkeypatch.setattr(agy.Path, "replace", deny_replace)
    with pytest.raises(PermissionError, match="fixture failure"):
        agy.edit(tmp_path, switch.hook_command("agy"))
    assert agy.load(tmp_path) == {}
    assert list(path.parent.iterdir()) == [path]
