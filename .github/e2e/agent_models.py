"""e2e: fieldkit agent-models through its real entry point, in a throwaway Claude home.

Sabotage cases: file deleted, model switched to opus, a second Explore file. Each is found by `check` (exit 1),
repaired by `enforce` (exit 0), and `check` passes afterwards. The old file must survive in the trash folder."""
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def run(env, *args):
    p = subprocess.run([sys.executable, "-m", "fieldkit", "agent-models", *args], env=env,
                       capture_output=True, text=True, timeout=120)
    print(f"$ fieldkit agent-models {' '.join(args)} -> {p.returncode}\n{p.stdout}{p.stderr}")
    return p.returncode


def main():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        home = td / "claude"
        env = dict(os.environ, FIELDKIT_CLAUDE_HOME=str(home), FIELDKIT_AGENT_MODELS_DIR=str(td / "state"),
                   FIELDKIT_AGENT_MODELS_TRASH=str(td / "trash"))
        env.pop("CLAUDE_CODE_SUBAGENT_MODEL", None)
        f = home / "agents" / "explore.md"
        assert run(env, "check") == 1, "missing file not found"
        assert run(env, "enforce") == 0 and f.is_file()
        assert run(env, "check") == 0
        f.write_text(f.read_text(encoding="utf-8").replace("model: haiku", "model: opus"), encoding="utf-8")
        assert run(env, "check") == 1, "opus switch not found"
        assert run(env, "enforce") == 0 and "model: haiku" in f.read_text(encoding="utf-8")
        assert any("model: opus" in p.read_text(encoding="utf-8") for p in (td / "trash").rglob("explore.md"))
        (home / "agents" / "explore2.md").write_text("---\nname: Explore\nmodel: opus\n---\n", encoding="utf-8")
        assert run(env, "check") == 1, "second Explore file not found"
        assert run(env, "enforce") == 0 and not (home / "agents" / "explore2.md").exists()
        assert run(env, "check") == 0
        assert run(env, "audit", "--days", "1") == 0
    print("E2E agent-models PASS")


if __name__ == "__main__":
    main()
