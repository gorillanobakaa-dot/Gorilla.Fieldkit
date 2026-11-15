"""The shared room (2026-10-19, owner: "is there any way for you guys to sit in the same room and see what each other
is doing?"): shared-ground actions are written by a hook, each session sees the others' new lines, secrets never land
in the log, and the hook never fails a tool call."""
import io
import json

import pytest

from fieldkit.autopilot import room

REAL_EAR = room._ear


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
    assert room.pretool(call("s1", "Bash", command="git commit -m a")) == ""   # s1 sees nothing of its own
    assert "commit" in room.tail()[-1]
    room.hook(io.StringIO(json.dumps({"session_id": "s2", "hook_event_name": "UserPromptSubmit", "prompt": "go"})))
    out = json.loads(capsys.readouterr().out)["hookSpecificOutput"]
    assert out["hookEventName"] == "UserPromptSubmit" and "commit" in out["additionalContext"]
    assert room.hook(io.StringIO("not json")) == 0 and capsys.readouterr().out == ""


def test_doctor_reports_the_missing_hooks_and_prints_the_change(tmp_path):
    s = tmp_path / "settings.json"
    s.write_text(json.dumps({"hooks": {}}), encoding="utf-8")
    r = room.doctor(s, python="py")
    assert not r["ok"] and set(r["missing"]) == {"PreToolUse", "UserPromptSubmit", "Stop"} and r["stale"] == []
    s.write_text(json.dumps({"hooks": r["snippet"]}), encoding="utf-8")
    assert room.doctor(s, python="py")["ok"]


def test_doctor_apply_makes_one_process_per_tool_call_and_keeps_every_other_hook(tmp_path):
    """C6: the pre-C6 wiring (router on Bash..Write, room on PostToolUse) is two Python starts per tool call."""
    s = tmp_path / "settings.json"
    other = {"type": "command", "command": "py -m fieldkit.autopilot.claude_hook --post"}
    old = {"hooks": {
        "PreToolUse": [{"matcher": "Bash|Edit|MultiEdit|NotebookEdit|PowerShell|Write",
                        "hooks": [{"type": "command", "command": "py -m fieldkit.autopilot.toolrouter --hook"}]}],
        "PostToolUse": [{"matcher": "Bash|PowerShell|Edit|Write|MultiEdit|NotebookEdit|SendMessage",
                         "hooks": [{"type": "command", "command": "py -m fieldkit.autopilot.room --hook"}, other]}],
        "UserPromptSubmit": [{"matcher": "*", "hooks": [{"type": "command",
                                                          "command": "py -m fieldkit.autopilot.room --hook"}]}],
        "Stop": [{"hooks": [{"type": "command", "command": "say done"}]}]}, "model": "x"}
    s.write_text(json.dumps(old), encoding="utf-8")
    r = room.doctor(s, python="py")
    assert not r["ok"] and r["missing"] == ["PreToolUse", "Stop"] and r["stale"] == ["PostToolUse"]
    code, lines = room.apply(s, python="py", keep_dir=tmp_path / "keep")
    assert code == 0 and "checked: ROOM HOOKS OK" in lines
    new = json.loads(s.read_text(encoding="utf-8"))
    assert new["hooks"]["PreToolUse"][0]["matcher"] == room.TOOL_MATCH
    assert new["hooks"]["PostToolUse"] == [{"matcher": old["hooks"]["PostToolUse"][0]["matcher"], "hooks": [other]}]
    assert new["hooks"]["Stop"][0] == old["hooks"]["Stop"][0] and new["model"] == "x"
    assert "fieldkit.tokens.rotate --stop-hook" in new["hooks"]["Stop"][1]["hooks"][0]["command"]
    assert json.loads(next((tmp_path / "keep").iterdir()).read_text(encoding="utf-8")) == old   # copied aside
    assert room.apply(s, python="py", keep_dir=tmp_path / "keep")[0] == 0                 # nothing left to change
    assert len(list((tmp_path / "keep").iterdir())) == 1


def test_a_post_tool_call_writes_nothing_the_pre_tool_hook_does_it_once(capsys):
    """C6: a stale PostToolUse entry must not write a second room line for the same call."""
    room.hook(io.StringIO(json.dumps(call("s1", "Bash", command="git commit -m a"))))
    assert capsys.readouterr().out == "" and room.tail() == []
    room.pretool(dict(call("s1", "Bash", command="git commit -m a"), hook_event_name="PreToolUse"))
    assert len(room.tail()) == 1


def test_a_detaching_call_has_its_printed_pid_registered_at_the_next_hook(_room_dir, monkeypatch):
    """C6: the hog registry used the PostToolUse output to catch a Start-Process'd PID; now the next hook reads the
    last tool result from the transcript's tail, and only after a call that can detach."""
    from fieldkit.hog import registry
    seen = []
    monkeypatch.setattr(registry, "observe", lambda c, classes: seen.append(c.get("tool_response")))
    tp = _room_dir / "t.jsonl"
    tp.write_text("\n".join(json.dumps(x) for x in (
        {"type": "user", "message": {"content": [{"type": "tool_result", "content": "old pid=1111"}]}},
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "PowerShell"}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "content": "Id : 4242"}]}})) + "\n",
        encoding="utf-8")
    pre = {"session_id": "s1", "transcript_path": str(tp), "hook_event_name": "PreToolUse", "tool_name": "PowerShell"}
    room.pretool(dict(pre, tool_input={"command": "Get-Date"}))
    assert seen == [""]                                             # no detaching call before: no transcript read
    room.pretool(dict(pre, tool_input={"command": "Start-Process python -PassThru"}))
    assert seen == ["", ""]
    room.pretool(dict(pre, tool_input={"command": "Get-Date"}))
    assert "4242" in seen[-1] and "1111" not in seen[-1]            # only the last tool result
    room.pretool(dict(pre, tool_input={"command": "Get-Date"}))
    assert seen[-1] == ""                                           # once


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


def test_a_rider_line_is_shown_once_and_again_only_when_it_changes(monkeypatch, capsys):
    """HARNESS-PLAN O2 (2026-10-28): the Zero session got the same 8 MISSING stations on every message."""
    said = {"line": "NET (you are Zero): a MISSING; b MISSING - fieldkit zero rollcall"}
    monkeypatch.setattr(room, "_net", lambda call, event, ctx: (ctx + "\n" if ctx else "") + said["line"])
    monkeypatch.setattr(room, "_hog", lambda call, event, ctx: ctx + "\nhog: RAM 7.1/8.0 GB (89%) now")
    monkeypatch.setattr(room, "_rotate", lambda call, event, ctx: ctx)
    prompt = json.dumps({"session_id": "z", "hook_event_name": "UserPromptSubmit", "prompt": "go"})

    def injected():
        room.hook(io.StringIO(prompt))
        out = capsys.readouterr().out
        return json.loads(out)["hookSpecificOutput"]["additionalContext"] if out else ""

    first = injected()
    assert "a MISSING; b MISSING" in first and "hog: RAM" in first
    assert injected() == ""                                         # nothing changed: nothing injected
    monkeypatch.setattr(room, "_hog", lambda call, event, ctx: ctx + "\nhog: RAM 7.3/8.0 GB (91%) now")
    assert injected() == ""                                         # only its numbers moved: the same line
    said["line"] = "NET (you are Zero): a MISSING; b MISSING; c SILENT - fieldkit zero rollcall"
    assert injected() == said["line"]                               # the list changed: shown, once
    assert injected() == ""
    other = json.dumps({"session_id": "y", "hook_event_name": "UserPromptSubmit", "prompt": "go"})
    room.hook(io.StringIO(other))
    assert "c SILENT" in capsys.readouterr().out                    # another session has its own record


def test_room_rows_still_reach_a_session_that_has_seen_the_riders(monkeypatch, capsys):
    monkeypatch.setattr(room, "_net", lambda call, event, ctx: (ctx + "\n" if ctx else "") + "NET: same")
    monkeypatch.setattr(room, "_hog", lambda call, event, ctx: ctx)
    monkeypatch.setattr(room, "_rotate", lambda call, event, ctx: ctx)
    prompt = json.dumps({"session_id": "s2", "hook_event_name": "UserPromptSubmit", "prompt": "go"})
    room.hook(io.StringIO(prompt))
    capsys.readouterr()
    room.pretool(call("s1", "Bash", command="git commit -m a"))
    room.hook(io.StringIO(prompt))
    ctx = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    assert "commit" in ctx and "NET: same" not in ctx


def test_a_reader_with_nothing_new_writes_nothing(_room_dir):
    room.append(room.line_for("s1", "", "commit", "x"))
    room.unseen("s2")
    room.unseen("s2")
    cur = _room_dir / "room-cursors" / "s2.json"
    t = cur.stat().st_mtime_ns
    cur.touch()
    import os
    os.utime(cur, ns=(t - 10**9, t - 10**9))
    room.unseen("s2")
    assert cur.stat().st_mtime_ns == t - 10**9


def test_a_session_whose_ear_is_not_running_is_told_the_line_every_time(monkeypatch, capsys, tmp_path):
    """I-27 (owner, 2026-11-10: "that is not real-time communication"): the one relay died with its session and 97
    notes sat unheard. Every session is told to start its own ear, again after it ended (not deduped), never on the
    call that starts it, never headless, never without a household."""
    from fieldkit.peers import link, link_relay
    monkeypatch.setattr(room, "_ear", REAL_EAR)                      # conftest turns it off for every other test
    for f in ("_net", "_hog", "_rotate", "_plan", "_workflows", "_refusals"):
        monkeypatch.setattr(room, f, lambda call, event, ctx: ctx)
    monkeypatch.delenv("FIELDKIT_AUTOPILOT", raising=False)
    monkeypatch.setenv("FIELDKIT_LINK_HOME", str(tmp_path / "link"))
    monkeypatch.setattr(link, "household", lambda: {"me": "vaio"})
    alive = {"pid": None}
    monkeypatch.setattr(link_relay.local_peers, "pid_alive", lambda pid: pid == alive["pid"])
    monkeypatch.setattr(link_relay, "ear_state", lambda st, alive_=None: {"state": "running" if alive["pid"] else
                                                                          "stopped"})
    monkeypatch.setattr(link_relay, "ear_line", lambda st: f"fk peers link relay listen --as {st} --since 7")

    def said(event="UserPromptSubmit", sid="r1", cmd=None):
        call = {"session_id": sid, "hook_event_name": event, "prompt": "go"}
        if cmd:
            call["tool_input"] = {"command": cmd}
        room.hook(io.StringIO(json.dumps(call)))
        out = capsys.readouterr().out
        return json.loads(out)["hookSpecificOutput"]["additionalContext"] if out else ""
    first = said()
    assert first.startswith("EAR NOT RUNNING") and "relay listen --as claude:r1@" in first and "--since 7" in first
    second = said()
    assert second.startswith("EAR NOT RUNNING")                      # every prompt while it is down: still told ...
    assert second != first and len(second) < len(first) // 3          # ... P37: in full once, then the short reminder
    assert said(cmd="fk peers link relay listen --as x") == ""        # the call that starts it
    alive["pid"] = 4242
    assert said(sid="r3") == ""                                      # its ear runs: nothing to say
    alive["pid"] = None
    monkeypatch.setenv("FIELDKIT_AUTOPILOT", "1")
    assert said(sid="r4") == ""                                      # a headless run ends too soon to listen
    monkeypatch.delenv("FIELDKIT_AUTOPILOT")
    monkeypatch.setattr(link, "household", lambda: {})
    assert said(sid="r5") == ""                                      # no cable on this computer


def test_hook_riders_are_capped_and_the_injected_bytes_recorded(monkeypatch, capsys, tmp_path):
    """P37: what a hook injects stays in the conversation for ever, so the non-exempt riders are cut to the event's cap
    (rules/song.yaml), NET lines are never cut, and every injection's bytes are recorded for `fieldkit rules hooks`."""
    from fieldkit import rules_song
    log = tmp_path / "hook-bytes.jsonl"
    monkeypatch.setattr(rules_song, "hook_log", lambda: log)
    big = "\n".join(f"hog: note {chr(97 + i)} " + "x" * 90 for i in range(26))   # distinct once digits are normalized
    monkeypatch.setattr(room, "_hog", lambda call, event, ctx: big)
    monkeypatch.setattr(room, "_net", lambda call, event, ctx: ctx + "\nNET: a message that must arrive")
    call = {"session_id": "cap1", "hook_event_name": "UserPromptSubmit", "prompt": "go"}
    room.hook(io.StringIO(json.dumps(call)))
    ctx = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    assert "NET: a message that must arrive" in ctx and "B of hook notes cut" in ctx and len(ctx) < 1200
    rec = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()]
    assert rec[-1]["event"] == "UserPromptSubmit" and rec[-1]["bytes"] == len(ctx.encode("utf-8"))
