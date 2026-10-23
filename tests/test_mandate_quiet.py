# Version: 1.0.0 · updated 26-10-23-19-43
"""While a mandate is in force no session asks the owner (owner, 2026-10-23: "i am still getting called like a monkey to
jump between 3 devices and 15 sessions ... 'approve this, i need you to have a look at this'")."""
import io
import json

import pytest

from fieldkit.mandate import quiet as Q


@pytest.mark.parametrize("text,asks", [
    ("Done: pushed 4777808.\n[NEEDS YOU] approve the push", True),
    ("All tests pass. Should I merge it into main now?", True),
    ("Gata. Vrei să urc și pe Windows?", True),
    ("Am nevoie de aprobarea ta pentru merge.", True),
    ("Pushed 4777808, the full suite passed in the hook. Next: the release cycle.", False),
    ("Is the store guarded? Yes: root-owned, a plain write is refused.", False),       # a question it answered itself
    ("Deci e gata. Mandatul e activ până pe 25.", False),
])
def test_what_counts_as_asking_the_owner(text, asks):
    assert Q.asks_owner(text) is asks


def _transcript(tmp_path, text):
    p = tmp_path / "t.jsonl"
    p.write_text(json.dumps({"type": "assistant", "message": {"role": "assistant",
                                                             "content": [{"type": "text", "text": text}]}}) + "\n")
    return str(p)


def test_the_stop_hook_sends_a_question_back_once_and_only_under_a_mandate(tmp_path):
    t = _transcript(tmp_path, "Built it. Should I push it to main now?")
    out = io.StringIO()
    Q.stop_hook(io.StringIO(json.dumps({"transcript_path": t})), out, force=True)
    assert json.loads(out.getvalue())["decision"] == "block" and "fieldkit mandate ask" in out.getvalue()
    for payload, force in (({"transcript_path": t, "stop_hook_active": True}, True),     # never twice
                           ({"transcript_path": t}, False)):                              # no mandate: nothing
        out = io.StringIO()
        Q.stop_hook(io.StringIO(json.dumps(payload)), out, force=force)
        assert out.getvalue() == ""


@pytest.mark.parametrize("tool,args,denied", [
    ("AskUserQuestion", {"questions": []}, True),
    ("mcp__ccd_turn__wrap_up", {"recap": "done", "ask": "Merge it?"}, True),
    ("mcp__ccd_turn__wrap_up", {"recap": "done"}, False),
    ("SendMessage", {"to": "x", "message": "[NEEDS YOU] look at this"}, True),
    ("SendMessage", {"to": "x", "message": "[DONE] pushed"}, False),
    ("Bash", {"command": "python -m fieldkit peers link send --needs-you \"approve\""}, True),
    ("Bash", {"command": "git status"}, False),
])
def test_the_pre_hook_refuses_the_ways_a_session_calls_the_owner(tool, args, denied):
    out = io.StringIO()
    Q.pre_hook(io.StringIO(json.dumps({"tool_name": tool, "tool_input": args})), out, force=True)
    assert ("deny" in out.getvalue()) is denied
    out = io.StringIO()
    Q.pre_hook(io.StringIO(json.dumps({"tool_name": tool, "tool_input": args})), out, force=False)
    assert out.getvalue() == ""                                              # without a mandate, nothing changes


def test_a_broken_payload_never_blocks():
    out = io.StringIO()
    Q.stop_hook(io.StringIO("not json"), out, force=True)
    Q.pre_hook(io.StringIO("not json"), out, force=True)
    assert out.getvalue() == ""


def test_asking_goes_to_the_ledger_and_the_inbox(tmp_path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_LINK_HOME", str(tmp_path / "link"))
    from fieldkit.mandate import jobs, project
    got = []
    Q.queue("merge PR 94 into main?", "it touches the hard lines", inbox_add=lambda t, w, kind: got.append((t, kind)))
    assert jobs.events()[-1]["what"] == "ask" and got == [("merge PR 94 into main?", "mandate")]
    assert "NEEDS YOU: merge PR 94 into main?" in "\n".join(project.report())


def test_install_adds_both_hooks_once_and_keeps_the_old_file(tmp_path):
    s = tmp_path / "settings.json"
    s.write_text(json.dumps({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "x laptop alerts hook"}]}]},
                             "permissions": {"defaultMode": "bypassPermissions"}}))
    code, lines = Q.install(s, python="/v/bin/python", keep_dir=tmp_path / "kept")
    back = json.loads(s.read_text())
    assert code == 0 and "laptop alerts hook" in json.dumps(back["hooks"]["Stop"])          # the owner's hook stays
    hooks = json.dumps(back)
    assert "--stop-hook" in hooks and "--pre-hook" in hooks and "fieldkit.mandate.quiet" in hooks
    assert back["permissions"] == {"defaultMode": "bypassPermissions"} and list((tmp_path / "kept").iterdir())
    Q.install(s, python="/v/bin/python", keep_dir=tmp_path / "kept")
    assert json.dumps(json.loads(s.read_text())).count("--stop-hook") == 1                  # once


def test_ask_and_quiet_through_the_command_line(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FIELDKIT_LINK_HOME", str(tmp_path / "link"))
    from fieldkit.mandate import mandate as M
    monkeypatch.setattr(Q, "queue", lambda q, why="": 7)
    assert M.main(["ask", "merge", "PR", "94?", "--why", "touches", "the", "hard", "lines"]) == 0
    assert "QUEUED" in capsys.readouterr().out
    assert M.main(["quiet"]) == 2


def test_the_installed_hook_runs_this_code_whatever_folder_a_session_is_in(tmp_path):
    import subprocess
    import sys
    s = tmp_path / "settings.json"
    Q.install(s, python=sys.executable, keep_dir=tmp_path / "kept")
    cmd = json.loads(s.read_text())["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    r = subprocess.run(cmd, shell=True, input=json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}}),
                       capture_output=True, text=True, cwd=str(tmp_path))
    assert r.returncode == 0 and r.stderr == "", r.stderr                            # runs, from a foreign folder
