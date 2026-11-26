"""Exercise the installed CLI against an isolated Antigravity desktop transcript."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

with tempfile.TemporaryDirectory() as home:
    uid = "11111111-1111-4111-8111-111111111111"
    root = Path(home, ".gemini", "antigravity")
    store = root / "conversations"
    store.mkdir(parents=True)
    (store / (uid + ".db")).touch()
    logs = root / "brain" / uid / ".system_generated" / "logs"
    logs.mkdir(parents=True)
    (logs / "transcript.jsonl").write_text(json.dumps({
        "type": "USER_INPUT", "content": "<USER_REQUEST>Review example-project</USER_REQUEST>"
    }) + "\n")
    env = dict(os.environ, HOME=home, USERPROFILE=home)
    cmd = [sys.executable, "-m", "fieldkit", "handoff"]
    rows = json.loads(subprocess.check_output(cmd + ["--list", "--project", "example-project", "--json"], env=env))
    assert rows["sessions"][0]["session"] == uid
    assert rows["sessions"][0]["last_activity"]
    result = json.loads(subprocess.check_output(cmd + [
        uid, "--provider", "codex", "--model", "gpt-6.2-astra", "--thinking", "medium", "--dry-run", "--json"
    ], env=env))
    assert result["status"] == "PREVIEW" and result["privacy_scan"] == "clean"
    assert "Review example-project" in Path(result["card"]).read_text(encoding="utf-8")
