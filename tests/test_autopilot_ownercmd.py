# Version: 1.0.0 · updated 26-10-18-19-10
"""Owner commands are checked before the owner sees them (owner, 2026-10-18: "you were always supposed to give me
the whole path ... a deterministic rule/check")."""
import io
import json

from fieldkit.autopilot import ownercmd


def block(cmd):
    return "Run this:\n\n```powershell\n" + cmd + "\n```\n"


def test_the_command_that_failed_the_owner_is_refused_and_its_fix_passes():
    bad = block(r'& "$env:APPDATA\Claude\claude-code\2.1.299\f0584be0fbaf\claude.exe"')
    good = block(r'cd $HOME; & "$env:APPDATA\Claude\claude-code\2.1.299\f0584be0fbaf\claude.exe"')
    assert any("cd" in p for _, p in ownercmd.check(bad))
    assert ownercmd.check(good) == []


def test_programs_need_full_paths_except_the_path_commands():
    assert ownercmd.check(block(r"cd $HOME\Documents\Gorilla.firefox; python harness\record_decisions.py --write")) == []
    assert ownercmd.check(block(r"cd C:\Tools; claude --version"))           # not on PATH: full path needed
    assert ownercmd.check(block(r"cd $HOME; Get-ChildItem $env:TEMP | Select-Object -First 3")) == []


def test_bash_syntax_is_refused():
    probs = [p for _, p in ownercmd.check(block("cd $HOME; export X=1 && git status"))]
    assert any("&&" in p for p in probs) and any("bash syntax" in p for p in probs)
    assert ownercmd.check(block("cd $HOME; git log $BRANCH"))                 # bash variable
    assert ownercmd.check(block("cd $HOME; $env:BRANCH = 'x'; git log $env:BRANCH")) == []


def test_non_powershell_blocks_are_not_judged():
    assert ownercmd.check("```bash\nls -la && make\n```") == []


def test_the_stop_hook_sends_a_bad_answer_back_once(tmp_path):
    t = tmp_path / "t.jsonl"
    t.write_text(json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "text", "text": block(r'& "$env:APPDATA\Claude\claude.exe"')}]}}) + "\n", encoding="utf-8")
    out = io.StringIO()
    ownercmd.stop_hook(io.StringIO(json.dumps({"transcript_path": str(t), "stop_hook_active": False})), out)
    assert json.loads(out.getvalue())["decision"] == "block"
    again = io.StringIO()
    ownercmd.stop_hook(io.StringIO(json.dumps({"transcript_path": str(t), "stop_hook_active": True})), again)
    assert again.getvalue() == ""                                            # never a loop
