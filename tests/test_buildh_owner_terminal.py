"""Owner-only steps refuse an AI agent's shell (2026-10-19: Claude Code's Bash tool has a terminal on stdin, and the
orchestrator ran an owner-only allowlist approval that the stdin check let through; restored from git at once)."""
import pytest

from fieldkit.buildh import task as t

PERSON = [("powershell.exe", ""), ("WindowsTerminal.exe", ""), ("explorer.exe", "")]


def test_a_person_terminal_from_the_desktop_passes():
    assert t.agent_shell(env={}, ancestors=[("powershell.exe", ""), ("explorer.exe", "")]) == ""
    assert t.agent_shell(env={}, ancestors=PERSON) == ""
    assert t.agent_shell(env={}, ancestors=[("python.exe", ""), ("cmd.exe", ""), ("explorer.exe", "")]) == ""


@pytest.mark.parametrize("var", ["CLAUDECODE", "AI_AGENT", "CLAUDE_CODE_ENTRYPOINT"])
def test_an_agent_environment_marker_refuses(var):
    assert "agent environment" in t.agent_shell(env={var: "1"}, ancestors=PERSON)


@pytest.mark.parametrize("chain", [
    [("bash.exe", ""), ("claude.exe", ""), ("explorer.exe", "")],                       # Claude Code
    [("node.exe", "node C:/x/@anthropic-ai/claude-code/cli.js"), ("explorer.exe", "")],  # by its command line
    [("powershell.exe", ""), ("Antigravity.exe", "")],
    [("cmd.exe", ""), ("codex.exe", "")],
])
def test_an_agent_ancestor_refuses_even_with_a_clean_environment(chain):
    assert "agent ancestor" in t.agent_shell(env={}, ancestors=chain)


@pytest.mark.parametrize("chain", [
    [("env.exe", "")],                                   # Git Bash's env.exe cut the chain before claude.exe
    [],                                                  # detached
    [("WmiPrvSE.exe", ""), ("svchost.exe", "")],         # started through WMI
    [("?", ""), ("explorer.exe", "")],                   # an unreadable ancestor is not a terminal
])
def test_no_positive_path_to_the_desktop_refuses(chain):
    assert t.agent_shell(env={}, ancestors=chain) != ""


def test_owner_terminal_is_false_whenever_agent_shell_names_a_reason(monkeypatch):
    monkeypatch.setattr(t, "agent_shell", lambda: "agent environment (CLAUDECODE)")
    monkeypatch.setattr(t.sys.stdin, "isatty", lambda: True, raising=False)
    assert t.owner_terminal() is False
