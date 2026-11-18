"""Exercise the real clean preview and fresh lesson CLI in a disposable agent home."""

import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path


def run(root):
    home, workspace, bin_dir = (root / name for name in ("home", "workspace", "bin"))
    local = home / "AppData" / "Local"
    user_temp = local / "Temp"
    alternate = root / "temp"
    system_temp = root / "system-temp"
    claude = root / "claude"
    kiro = root / "kiro"
    xdg = root / "xdg"
    codex = root / "codex"
    gemini = root / "gemini"
    age = time.time() - 9 * 86400
    sources = {
        "claude": claude / "projects/unrelated/old.jsonl",
        "codex": codex / "sessions/2026/01/01/rollout-unrelated.jsonl",
        "gemini": gemini / ".gemini/tmp/project/chats/session-old.json",
        "kiro-cli": kiro / "sessions/cli/legacy.jsonl",
        "kiro-crew": kiro / "crew/sessions/member.jsonl",
        "agy": home / ".gemini/antigravity-cli/brain/session/.system_generated/logs/transcript.jsonl",
        "antigravity": home / ".gemini/antigravity/conversations/session.db",
        "gorilla-opencode": xdg / "gorilla-opencode/gorilla-opencode.db",
        "opencode": xdg / "opencode/opencode.db",
    }
    for file in (*sources.values(), user_temp / "unrelated.bin", alternate / "unrelated.bin",
                 system_temp / "unrelated.bin"):
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(b"old fixture contents must stay unopened")
        os.utime(file, (age, age))
    (alternate / "recent.bin").touch()
    (claude / "settings.json").write_text("{}", encoding="utf-8")
    workspace.mkdir()
    bin_dir.mkdir()
    stub = bin_dir / ("claude.cmd" if os.name == "nt" else "claude")
    stub.write_text("@exit /b 123\n" if os.name == "nt" else "#!/bin/sh\nexit 123\n", encoding="utf-8")
    stub.chmod(0o700)
    (root / "sitecustomize.py").write_text("""
import os
import sys

def norm(path):
    return os.path.normcase(os.path.abspath(os.fsdecode(path)))

blocked = {norm(path) for path in os.environ["FIXTURE_BLOCKED"].split(os.pathsep)}
projects = norm(os.environ["FIXTURE_CLAUDE_PROJECTS"])

def audit(event, args):
    if event == "open" and isinstance(args[0], (str, bytes)) and norm(args[0]) in blocked:
        raise AssertionError("opened unrelated or protected session contents")
    if event == "os.scandir" and os.environ.get("FIXTURE_FRESH") and isinstance(args[0], (str, bytes)):
        path = norm(args[0])
        if path == projects or path.startswith(projects + os.sep):
            raise AssertionError("fresh lesson crawled Claude history")
    if event == "subprocess.Popen":
        if os.path.basename(os.fsdecode(args[0])).lower() not in {"git", "git.exe"}:
            raise AssertionError("capture launched an external program")
    if event in {"socket.connect", "socket.getaddrinfo"}:
        raise AssertionError("capture tried a network call")

sys.addaudithook(audit)
""", encoding="utf-8")
    env = {**os.environ, "HOME": str(home), "USERPROFILE": str(home), "LOCALAPPDATA": str(local),
           "APPDATA": str(home / "AppData/Roaming"), "TEMP": str(alternate), "TMP": str(alternate),
           "TMPDIR": str(alternate), "SystemTemp": str(system_temp), "XDG_RUNTIME_DIR": str(system_temp),
           "CLAUDE_CONFIG_DIR": str(claude), "CODEX_HOME": str(codex), "KIRO_HOME": str(kiro),
           "GEMINI_CLI_HOME": str(gemini), "XDG_DATA_HOME": str(xdg),
           "FIELDKIT_CAPTURE_DIR": str(root / "capture"), "FIELDKIT_CAPTURE_NO_SCHEDULE": "1",
           "FIXTURE_CLAUDE_PROJECTS": str(claude / "projects"),
           "FIXTURE_BLOCKED": os.pathsep.join(map(str, sources.values())),
           "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"], "PYTHONPATH": str(root)}
    env.pop("FIELDKIT_OPENCODE_DATA_DIR", None)
    env.pop("OPENCODE_DB", None)

    def capture(*args, payload=None):
        result = subprocess.run([sys.executable, "-m", "fieldkit", "capture", *args], env=env,
                                input=json.dumps(payload) if payload is not None else None,
                                capture_output=True, text=True, encoding="utf-8", timeout=90)
        assert result.returncode == 0 and "AssertionError" not in result.stderr, (args, result.stdout, result.stderr)
        return json.loads(result.stdout) if result.stdout else None

    preview = capture("clean", "--keep-last", "3d", "--json")
    assert preview["recycled"] == [] and not (root / "capture").exists()
    temp_rows = {Path(row["root"]): row for row in preview["footprint"]["temp"]}
    assert temp_rows[alternate]["older"] == 1 and temp_rows[alternate]["recent"] == 1
    assert temp_rows[system_temp]["older"] == 1
    assert any("system temp" in row["sources"] for row in temp_rows.values())
    if os.name == "nt":
        assert temp_rows[user_temp]["older"] == 1
    found = {row["agent"] for row in preview["footprint"]["sessions"] if row["older"] and row["status"] == "found"}
    assert found == set(sources), (found, preview["footprint"]["sessions"])
    assert next(row for row in preview["footprint"]["sessions"] if row["agent"] == "gorilla-opencode")["retained"] == 1
    env["FIXTURE_FRESH"] = "1"
    started = capture("start", "--project", "fixture", "--lesson", "fresh hook lesson", "--workspace", str(workspace),
                      "--agents", "claude", "--json")
    assert started["started"]
    capture("hook", "--agent", "claude", payload={
        "session_id": "fresh", "cwd": str(workspace), "hook_event_name": "PostToolUse",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "tool_name": "Bash", "tool_input": {"command": "fixture-fresh"}, "tool_use_id": "use_fresh"})
    capture("scan", "--scheduled")
    assert capture("scan", "--json")["files"] == 0
    assert capture("find", "fixture-fresh", "--json")[0]["command"] == "fixture-fresh"
    closed = capture("off", "--json")
    assert not closed["on"] and closed["catchup"]["files"] == 0
    assert all(file.exists() for file in sources.values())
    print("Capture clean preview found configured temp and nine agent stores; fresh lesson made no transcript crawl or model call")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as directory:
        run(Path(directory).resolve())
