"""The tool router (2026-10-18): hand-rolled scans are refused with the harness tool's command; ordinary commands
and the tools themselves pass; an override must say why."""
import io
import json

import pytest

from fieldkit.autopilot import toolrouter as tr


@pytest.mark.parametrize("cmd,route", [
    ('Get-ChildItem -Path "C:\\Program Files" -Recurse -Filter vctip.exe', "recursive-file-search"),
    ("gci C:\\ -r -fi *.log", "recursive-file-search"),
    ("dir C:\\Users /s /b vctip.exe", "recursive-file-search"),
    ("find / -name vctip.exe 2>/dev/null", "recursive-file-search"),
    ("where /r C:\\ vctip.exe", "recursive-file-search"),
    ("du -sh ~/Documents/*", "folder-size"),
    ("robocopy C:\\a C:\\b /L /E /BYTES", "folder-size"),
    ("(Get-ChildItem x -File | Measure-Object -Property Length -Sum).Sum", "folder-size"),
    ("findstr /s /i telemetry *.py", "text-search-in-tree"),
])
def test_hand_rolled_scans_are_routed_to_the_tool(cmd, route):
    r = tr.route(cmd)
    assert r and r["route"] == route and "fieldkit" in r["example"]


@pytest.mark.parametrize("cmd", [
    "git status --short",
    "ls -la /c/Users",
    "Get-ChildItem C:\\gfobj\\dist\\*.zip",
    "python -m fieldkit size find --name vctip.exe --type f C:/",
    "find . -maxdepth 1",
    "Get-ChildItem -Recurse x  # toolrouter-ok: a 3-file fixture folder in a test",
])
def test_ordinary_commands_and_the_tools_pass(cmd):
    assert tr.route(cmd) is None


def test_a_heredoc_body_is_data_but_the_command_around_it_is_judged():
    doc = "cat >> GEMINI.md <<'EOF'\nNever run Get-ChildItem -Recurse or du -sh by hand.\nEOF\necho done"
    assert tr.route(doc) is None
    assert tr.route(doc + "\nGet-ChildItem C:\\ -Recurse -Filter x") is not None


def test_python_with_backslashes_in_a_heredoc_goes_to_the_write_tool():
    bad = "python - <<'EOF'\nimport re\np = re.compile(r'\\s+')\nEOF"
    assert tr.route(bad)["route"] == "code-with-backslashes-in-a-heredoc"
    assert tr.route("python - <<'EOF'\nprint(1 + 1)\nEOF") is None          # no backslash: nothing to mangle
    assert tr.route("cat >> notes.md <<'EOF'\npath C:\\x\\y\nEOF") is None   # text, not code


def test_the_claude_hook_denies_with_the_tool_command(capsys):
    call = {"tool_name": "PowerShell", "tool_input": {"command": "Get-ChildItem C:\\ -Recurse -Filter a.exe"}}
    tr.hook(io.StringIO(json.dumps(call)))
    out = json.loads(capsys.readouterr().out)["hookSpecificOutput"]
    assert out["permissionDecision"] == "deny" and "size find" in out["permissionDecisionReason"]
    tr.hook(io.StringIO(json.dumps({"tool_name": "Read", "tool_input": {"file_path": "x"}})))
    assert capsys.readouterr().out == ""
