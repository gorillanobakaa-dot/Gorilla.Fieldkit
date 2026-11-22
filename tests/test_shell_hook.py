"""F0.4: every command a Qwen run used in finding 3.1 is refused by the shell hook of OpenCode and Kiro."""
import io
import json

import pytest

from fieldkit.autopilot import shell_hook

QWEN = [
    "gh release create v0.10.9 --notes x",
    "git commit --no-verify -m x",
    "git tag -f v0.10.9",
    "git push --force origin main",
    "gh pr merge 12 --admin",
    "gh run rerun 123",
    "gh run cancel 123",
]


@pytest.mark.parametrize("cmd", QWEN)
def test_each_qwen_command_is_refused(cmd, monkeypatch, tmp_path):
    from fieldkit.autopilot import hardlines
    monkeypatch.setattr(hardlines, "STOP_FILE", tmp_path / "STOP")
    err = io.StringIO()
    stdin = io.StringIO(json.dumps({"tool_name": "execute_bash", "tool_input": {"command": cmd}}))
    assert shell_hook.main([], stdin=stdin, stderr=err) == 2
    assert err.getvalue().strip()
    assert shell_hook.main(["--command", cmd], stderr=io.StringIO()) == 2


def test_a_plain_command_and_a_non_shell_tool_pass(monkeypatch, tmp_path):
    from fieldkit.autopilot import hardlines
    monkeypatch.setattr(hardlines, "STOP_FILE", tmp_path / "STOP")
    assert shell_hook.main(["--command", "git status"], stderr=io.StringIO()) == 0
    assert shell_hook.main([], stdin=io.StringIO(json.dumps({"tool_name": "fs_write", "tool_input": {}}))) == 0


def test_a_broken_payload_denies():
    assert shell_hook.main([], stdin=io.StringIO("not json"), stderr=io.StringIO()) == 2


def test_the_config_names_the_hook():
    assert "shell_hook" in shell_hook.config("kiro") and "tool.execute.before" in shell_hook.config("opencode")
    with pytest.raises(ValueError):
        shell_hook.config("nope")
