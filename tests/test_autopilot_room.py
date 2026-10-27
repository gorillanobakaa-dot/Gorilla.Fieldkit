"""The shared room (2026-10-19, owner: "is there any way for you guys to sit in the same room and see what each other
is doing?"): shared-ground actions are written by a hook, each session sees the others' new lines, secrets never land
in the log, and the hook never fails a tool call."""
import io
import json

import pytest

from fieldkit.autopilot import room


@pytest.fixture(autouse=True)
def _room_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(room, "ROOM_DIR", tmp_path)
    return tmp_path


def call(session, tool, cwd="C:/x/Fieldkit", **ti):
    return {"session_id": session, "cwd": cwd, "tool_name": tool, "tool_input": ti, "hook_event_name": "PostToolUse"}


@pytest.mark.parametrize("tool,ti,verb", [
    ("Bash", {"command": "git commit -qm 'x'"}, "commit"),
    ("Bash", {"command": "git rebase -q 6bb6bc7"}, "rebase"),
    ("Bash", {"command": "git merge --ff-only wave0/hygiene"}, "ff"),
    ("Bash", {"command": "git push origin main"}, "net"),
    ("PowerShell", {"command": "Stop-Process -Id 8276 -Force"}, "stop"),
    ("PowerShell", {"command": "Start-Process powershell -Verb RunAs"}, "start"),
    ("Bash", {"command": "python -m fieldkit build-harness post-install firefox-158.0"}, "heavy"),
    ("Edit", {"file_path": "C:/Users/you/Documents/Gorilla.firefox/gorilla-patchset/decisions/THEME.yaml"}, "edit"),
    ("SendMessage", {"to": "peer", "summary": "HEAD x", "message": "HEAD is x"}, "ask"),
    ("SendMessage", {"to": "peer", "summary": "Owner approval", "message": 'OWNER APPROVAL verbatim: "yes to all"'}, "approve"),
])
def test_shared_ground_actions_are_written_with_their_verb(tool, ti, verb):
    line = room.write(call("s1", tool, **ti))
    assert line and line.split("\t")[3] == verb and line.split("\t")[3] in room.VERBS
    assert " " not in line.split("\t")[4]                          # the object never holds a space
    assert line[:4].isdigit() and line[10] == " "                   # a full date: sessions cross midnight


@pytest.mark.parametrize("tool,ti", [
    ("Bash", {"command": "git status --short"}), ("Bash", {"command": "ls -la"}), ("Read", {"file_path": "x"}),
    ("Edit", {"file_path": "C:/Users/you/AppData/Local/Temp/claude/scratchpad/a.py"}),
    ("Bash", {"command": "cat > notes.md <<'EOF'\ngit push origin main\nEOF"}),   # a heredoc body is data
])
def test_reads_and_private_work_are_not_written(tool, ti):
    assert room.write(call("s1", tool, **ti)) is None


def test_a_stop_line_carries_the_pid():
    assert room.write(call("s1", "PowerShell", command="Stop-Process -Id 8276 -Force")).split("\t")[4] == "pid=8276"


def test_secrets_home_paths_and_addresses_never_reach_the_log(_room_dir):
    tok = "ghp_" + "A" * 36                                          # privacy-scan: allow
    room.write(call("s1", "Bash", command=f"git push https://x:{tok}@github.com/o/r C:\\Users\\someone\\a"))  # privacy-scan: allow
    text = (_room_dir / "ROOM.log").read_text(encoding="utf-8")
    assert tok not in text and "[redacted]" in text and "someone" not in text and "$HOME" in text
    room.write(call("s1", "SendMessage", to="p", summary="mail", message="write to a.person@gmail.com"))  # privacy-scan: allow
    assert "a.person@gmail.com" not in (_room_dir / "ROOM.log").read_text(encoding="utf-8")  # privacy-scan: allow


def test_an_unknown_verb_is_refused():
    with pytest.raises(ValueError):
        room.line_for("s1", "", "deploy", "x")


def test_each_session_sees_only_the_others_new_lines():
    assert room.unseen("s2") == []                                  # a new reader starts from now
    room.write(call("s1", "Bash", command="git commit -m a"))
    room.write(call("s2", "Bash", command="git commit -m b"))
    got = room.unseen("s2")
    assert len(got) == 1 and got[0].split("\t")[2] == "s1"
    assert room.unseen("s2") == []                                   # the cursor moved


def test_the_reader_shows_at_most_max_and_says_how_many_more():
    room.unseen("s2")
    for i in range(room.MAX_SHOW + 5):
        room.write(call("s1", "Bash", command=f"git commit -m {i}"))
    ctx = room.context(room.unseen("s2"))
    assert "+5 more:" in ctx and len(ctx) < 600


def test_an_injection_is_capped_and_repeats_are_one_row():
    """#108 fix 6: 3141 injections of up to 20 rows were re-read on every later call; now about 400 chars of rows."""
    room.unseen("s2")
    for i in range(6):
        room.write(call("s1", "Bash", command=f"taskkill /PID {1000 + i} /F"))
    for i in range(8):
        room.write(call("s1", "Bash", command=f"git -C C:/r{i} commit -m n{i}"))
    ctx = room.context(room.unseen("s2"))
    rows = ctx.split("\n")[1:]
    assert ctx.startswith("ROOM: what the other session")             # token-audit keys on this prefix
    assert sum(len(r) for r in rows if not r.startswith("  +")) <= room.MAX_CHARS + room.ROW_CHARS
    assert "r7" in ctx and "(x8)" in ctx and "(x6)" in ctx and "more:" not in ctx   # the newest of each kind
    many = [f"2026-10-27 13:{i:02d}:00\tw@x\ts1\tedit\tC:/f/{chr(97 + i)}.py\t{'y' * 100}" for i in range(15)]
    capped = room.context(many)
    assert len(capped) < room.MAX_CHARS + 2 * room.ROW_CHARS and "f/o.py" in capped and "more:" in capped
    assert room.context([]) == ""


def test_the_hook_writes_shows_and_never_fails(capsys):
    room.unseen("s2")
    room.hook(io.StringIO(json.dumps(call("s1", "Bash", command="git commit -m a"))))
    assert capsys.readouterr().out == ""                            # s1 sees nothing of its own
    room.hook(io.StringIO(json.dumps({"session_id": "s2", "hook_event_name": "UserPromptSubmit", "prompt": "go"})))
    out = json.loads(capsys.readouterr().out)["hookSpecificOutput"]
    assert out["hookEventName"] == "UserPromptSubmit" and "commit" in out["additionalContext"]
    assert room.hook(io.StringIO("not json")) == 0 and capsys.readouterr().out == ""


def test_doctor_reports_the_missing_hooks_and_prints_the_change(tmp_path):
    s = tmp_path / "settings.json"
    s.write_text(json.dumps({"hooks": {}}), encoding="utf-8")
    r = room.doctor(s, python="py")
    assert not r["ok"] and set(r["missing"]) == {"PostToolUse", "UserPromptSubmit"}
    s.write_text(json.dumps({"hooks": r["snippet"]}), encoding="utf-8")
    assert room.doctor(s, python="py")["ok"]


def test_a_note_that_mentions_approvals_is_an_ask_and_a_marked_one_is_an_approve():
    ask = room.write(call("s1", "SendMessage", to="p", summary="room live",
                          message="keep asking first and send owner approvals verbatim"))
    assert ask.split("\t")[3] == "ask"
    yes = room.write(call("s1", "SendMessage", to="p", summary="x", message='OWNER DECISION, verbatim: "keep it"'))
    assert yes.split("\t")[3] == "approve"


def test_the_object_is_where_the_command_acts():
    line = room.write(call("s1", "PowerShell", cwd="C:/elsewhere", command="cd C:/repo/Gorilla.Size; git commit -m x"))
    assert line.split("\t")[4] == "C:/repo/Gorilla.Size"


def test_repeated_edits_of_one_file_are_one_line(_room_dir):
    path = "C:/x/Documents/Gorilla.firefox/a.css"
    assert room.write(call("s1", "Edit", file_path=path), now=1000)
    assert room.write(call("s1", "Edit", file_path=path), now=1010) is None
    assert room.write(call("s2", "Edit", file_path=path), now=1011)          # another session: its own line


def test_a_powershell_here_string_body_is_data():
    cmd = "$code=@'\nimport re, sys\nfrom pip import x\ngh = 1\n'@; python -c $code"
    assert room.write(call("s1", "PowerShell", command=cmd)) is None
