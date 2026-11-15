"""e2e: the real `python -m fieldkit pool plan` entry point on this OS (plan P37): a session list in, ordered app-tool calls out."""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

LIST = [
    {"sessionId": "local_00000002-0000-0000-0000-000000000000", "title": "[CI-OS] worker", "isArchived": False,
     "isRunning": True, "lastActivityAt": "2026-11-15T17:00:00.000Z"},
    {"sessionId": "local_00000003-0000-0000-0000-000000000000", "title": "[CI-OS] finished", "isArchived": False,
     "isRunning": False, "lastActivityAt": "2026-11-15T10:00:00.000Z", "remoteControlActive": True},
]
with tempfile.TemporaryDirectory() as d:
    f = Path(d) / "list.json"
    f.write_text(json.dumps(LIST), encoding="utf-8")
    r = subprocess.run([sys.executable, "-m", "fieldkit", "pool", "plan", "--sessions", str(f), "--reserve", "2",
                        "--event-turn", "--no-permanence-lookup", "--clean", LIST[1]["sessionId"], "--json"],
                       capture_output=True, text=True, timeout=120)
print(r.stdout, r.stderr)
assert r.returncode == 0, r.returncode
p = json.loads(r.stdout)
acts = {d["id"]: d["action"] for d in p["decisions"]}
assert acts[LIST[0]["sessionId"]] == "KEEP" and acts[LIST[1]["sessionId"]] == "RECYCLE", acts
tools = [s["tool"].split("__")[-1] for s in p["steps"]]
assert tools == ["SendMessage", "set_remote_control", "clear_session", "set_session_title", "WAIT"], tools
print("E2E OK: pool plan on", sys.platform)
