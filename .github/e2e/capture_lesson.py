"""Run the real capture CLI with synthetic transcripts, hook input and a disposable home."""

import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

CODEX = "01234567-89ab-cdef-0123-456789abcdef"
CLAUDE = "01234567-89ab-cdef-0123-456789abcdee"


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def write_rows(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def transcripts(home, codex_home, workspace):
    timestamp = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()

    def codex(kind, **payload):
        return {"type": kind, "timestamp": timestamp, "payload": payload}

    write_rows(codex_home / "sessions" / "2026" / "11" / "17" / f"rollout-2026-11-17T12-00-00-{CODEX}.jsonl", [
        codex("session_meta", id=CODEX, cwd=str(workspace)),
        codex("response_item", type="function_call", name="exec_command", call_id="call_fixture",
              arguments=json.dumps({"cmd": "fixture-codex"})),
        codex("response_item", type="function_call_output", call_id="call_fixture",
              output="Process exited with code 7\nOutput:\nfixture output"),
    ])
    claude = {"cwd": str(workspace), "timestamp": timestamp, "sessionId": CLAUDE}
    write_rows(home / ".claude" / "projects" / "fixture" / f"{CLAUDE}.jsonl", [
        {**claude, "type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "use_fixture", "name": "Bash", "input": {"command": "fixture-claude"}}]}},
        {**claude, "type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "use_fixture", "content": "fixture output"}]}},
    ])


def run_lesson(root):
    home, workspace, bin_dir, temp = (root / name for name in ("home", "workspace", "bin", "temp"))
    for directory in (home, workspace, bin_dir, temp):
        directory.mkdir()
    codex_home = home / "custom-codex"
    env = {**os.environ, "HOME": str(home), "USERPROFILE": str(home), "CODEX_HOME": str(codex_home),
           "LOCALAPPDATA": str(home / "AppData" / "Local"), "APPDATA": str(home / "AppData" / "Roaming"),
           "TEMP": str(temp), "TMP": str(temp), "TMPDIR": str(temp), "FIELDKIT_CAPTURE_DIR": str(root / "capture"),
           "FIELDKIT_CAPTURE_NO_SCHEDULE": "1", "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"]}
    for name in ("claude", "agy"):
        windows = os.name == "nt"
        stub = bin_dir / (name + ".cmd" if windows else name)
        stub.write_text("@exit /b 0\n" if windows else "#!/bin/sh\nexit 0\n", encoding="utf-8")
        stub.chmod(0o700)
    write_json(home / ".claude" / "settings.json", {})
    agy_cfg = home / ".gemini" / "config" / "hooks.json"
    other_hook = {"other-tool": {"enabled": False}}
    write_json(agy_cfg, other_hook)
    transcripts(home, codex_home, workspace)

    def capture(*args, payload=None):
        result = subprocess.run([sys.executable, "-m", "fieldkit", "capture", *args], env=env,
                                input=None if payload is None else json.dumps(payload), capture_output=True,
                                text=True, encoding="utf-8", timeout=90)
        assert result.returncode == 0, (args, result.stdout, result.stderr)
        return json.loads(result.stdout)

    started = capture("start", "--project", "fixture", "--lesson", "capture fixture", "--workspace", str(workspace),
                      "--agents", "claude,codex,agy", "--session", f"codex={CODEX}", "--session", f"claude={CLAUDE}",
                      "--lookback", "30m", "--keep-last", "1d", "--json")
    assert started["started"] and started["recycled"] == []
    hooks = json.loads(agy_cfg.read_text(encoding="utf-8"))
    command = hooks["fieldkit-capture-agy"]["PostToolUse"][0]["hooks"][0]["command"]
    assert command == f'"{sys.executable}" -m fieldkit capture hook --agent agy'
    assert capture("hook", "--agent", "agy", payload={
        "conversationId": "01234567-89ab-cdef-0123-456789abcdec", "workspacePaths": [str(workspace)], "stepIdx": 0,
        "transcriptPath": str(home / ".gemini" / "antigravity-cli" / "transcript.jsonl"),
        "toolCall": {"name": "run_command", "args": {"CommandLine": "fixture-agy", "Cwd": str(workspace)}},
    }) == {}
    assert capture("scan", "--json")["calls"] == 2
    assert capture("scan", "--json")["already"] == 2
    closed = capture("off", "--json")
    assert not closed["on"] and closed["catchup"]["already"] == 2
    assert closed["hooks_removed"]["agy"] == 1
    assert json.loads(agy_cfg.read_text(encoding="utf-8")) == other_hook
    records = capture("find", "fixture", "--json")
    assert {(r["agent"], r["command"], r["exit_code"]) for r in records} == {
        ("claude", "fixture-claude", 0), ("codex", "fixture-codex", 7), ("agy", "fixture-agy", 0)}
    print("Capture lesson CLI retained three scoped fixture calls and removed its Antigravity hook")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as area:
        run_lesson(Path(area).resolve())
