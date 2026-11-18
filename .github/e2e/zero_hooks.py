"""Install the native hooks into a disposable home and exchange real Zero messages."""

import json
import os
import socket
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory


def run(args, env, payload=None, shell=False):
    result = subprocess.run(args, env=env, shell=shell, capture_output=True, text=True,
                            encoding="utf-8", input=json.dumps(payload) if payload is not None else None,
                            timeout=45)
    if result.returncode:
        raise RuntimeError(result.stderr or result.stdout or f"exit {result.returncode}")
    return json.loads(result.stdout) if result.stdout.strip() else {}


def main():
    with TemporaryDirectory(prefix="fieldkit-zero-hooks-") as folder:
        root = Path(folder).resolve()
        env = {**os.environ, "HOME": str(root), "USERPROFILE": str(root), "XDG_DATA_HOME": str(root / "data"),
               "CODEX_HOME": str(root / ".codex"), "GEMINI_CLI_HOME": str(root),
               "FIELDKIT_COORDINATION_DIR": str(root / "coordination"), "FIELDKIT_LINK_HOME": str(root / "link"),
               "FIELDKIT_ROOM_DIR": str(root / "room"), "FIELDKIT_JOB_DIR": str(root / "job")}

        def zero(*args):
            return run([sys.executable, "-m", "fieldkit", "zero", *args, "--json"], env)

        zero("open", "--as", "shell:ci")
        agents = ("codex", "gemini", "antigravity")
        ids = {agent: f"{i:08d}-test-session" for i, agent in enumerate(agents, 1)}
        commands = {}
        for agent in agents:
            installed = zero("hooks", agent, "--apply")
            assert installed["configured"] and installed["changed"]
            assert not zero("hooks", agent, "--apply")["changed"]
            data = json.loads(Path(installed["path"]).read_text(encoding="utf-8"))
            groups = data["fieldkit-zero"] if agent == "antigravity" else data["hooks"]
            for event, entries in groups.items():
                commands[agent, event] = (entries[0] if agent == "antigravity" else entries[0]["hooks"][0])["command"]

        def hook(agent, event):
            payload = ({"conversationId": ids[agent], "workspacePaths": [folder]} if agent == "antigravity" else
                       {"session_id": ids[agent], "cwd": folder, "hook_event_name": event})
            return run(commands[agent, event], env, payload, shell=True)

        for agent in agents:
            assert hook(agent, "SessionStart")
        host = socket.gethostname()
        station = {agent: f"{agent}:{ids[agent][:8]}@{host}" for agent in agents}
        for source, target, event in (("gemini", "codex", "PreToolUse"), ("codex", "gemini", "AfterTool"),
                                      ("codex", "antigravity", "PreInvocation"), ("antigravity", "codex", "UserPromptSubmit")):
            message = zero("send", f"test from {source}", "--as", station[source],
                           "--to", station[target], "--flash")["entry"]["id"]
            out = hook(target, event)
            assert message in json.dumps(out) and f"test from {source}" in json.dumps(out)
            assert not hook(target, event)
        print("Zero native hooks delivered and acknowledged Codex, Gemini CLI, and Antigravity messages")


if __name__ == "__main__":
    main()
