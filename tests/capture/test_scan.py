"""P26: `capture scan` journals what a transcript holds and no hook captured; once, in the hash chain."""
import json

import pytest

from fieldkit.capture import episodes as ep, scan, store as st, switch


def line(typ, content, sid="s1", ts="2026-11-01T10:00:00.000Z", cwd="/w"):
    return json.dumps({"type": typ, "sessionId": sid, "cwd": cwd, "timestamp": ts, "message": {"role": typ, "content": content}})


def transcript(home, sid, calls, name="proj"):
    d = home / ".claude" / "projects" / name
    d.mkdir(parents=True, exist_ok=True)
    rows = [line("user", "build the thing please", sid)]
    for i, (tool, inp, out, err) in enumerate(calls):
        rows.append(line("assistant", [{"type": "tool_use", "id": f"t{i}", "name": tool, "input": inp}], sid))
        rows.append(line("user", [{"type": "tool_result", "tool_use_id": f"t{i}", "content": out, "is_error": err}], sid))
    (d / f"{sid}.jsonl").write_text("\n".join(rows) + "\n", encoding="utf-8")


@pytest.fixture
def env(tmp_path):
    home = tmp_path / "home"
    transcript(home, "s1", [("Bash", {"command": "make all"}, "Exit code 2\nboom", True),
                            ("Bash", {"command": "make all -j4"}, "ok", False),
                            ("Read", {"file_path": "/x"}, "text", False),
                            ("Write", {"file_path": "/w/a.py", "content": "print(1)"}, "done", False)])
    return home, st.Store(tmp_path / "cap")


def test_scan_journals_work_calls_with_exit_code_time_and_request(env):
    home, s = env
    r = scan.scan(s, home)
    assert r["calls"] == 3 and r["new_items"] == 3          # the Read is not work
    items = list(s.state()["items"].values())
    cmds = {d["command"]: d for d in items if d["kind"] == "command"}
    assert cmds["make all"]["exit_code"] == 2 and cmds["make all -j4"]["exit_code"] == 0
    assert cmds["make all"]["timestamp"].startswith("2026-11-01T10:00") and cmds["make all"]["problem"] == "build the thing please"
    assert cmds["make all"]["env"] == {} and cmds["make all"]["origin"].startswith("scan:claude")


def test_second_scan_writes_nothing_and_the_chain_holds(env):
    home, s = env
    scan.scan(s, home)
    size = s.journal.stat().st_size
    r = scan.scan(s, home)
    assert r["calls"] == 0 and r["already"] == 3 and s.journal.stat().st_size == size
    assert s.verify()[0] == []


def test_the_same_call_in_two_sessions_is_one_item_and_a_counted_repeat(tmp_path):
    home, s = tmp_path / "h", st.Store(tmp_path / "cap")
    transcript(home, "a", [("Bash", {"command": "ls"}, "x", False)])
    transcript(home, "b", [("Bash", {"command": "ls"}, "x", False)], name="p2")
    r = scan.scan(s, home)
    assert r["new_items"] == 1 and r["repeats"] == 1       # one ls item and a repeat of it
    assert len(s.state()["items"]) == 3                    # plus one session marker each, so coverage sees both sessions
    assert s.verify()[0] == []


def test_a_session_the_hook_already_captured_is_left_to_it(env):
    home, s = env
    s.record({"kind": "command", "source": "x", "command": "x", "cwd": "/w", "exit_code": 0, "session": "s1",
              "agent": "claude", "origin": "hook:claude:PostToolUse", "path": None})
    r = scan.scan(s, home)
    assert r["calls"] == 0 and r["skipped_hooked"] == 1


def test_a_limited_scan_is_finished_by_the_next_one(env):
    home, s = env
    assert scan.scan(s, home, limit=1)["limited"]
    r = scan.scan(s, home)                                    # its own items do not make the session "done"
    assert r["calls"] == 2 and r["already"] == 1


def test_coverage_has_no_gap_after_the_scan_and_a_read_only_chat_is_no_gap(env):
    home, s = env
    transcript(home, "ro", [("Read", {"file_path": "/x"}, "t", False), ("Grep", {"pattern": "a"}, "t", False)], name="p3")
    gaps = lambda: [u for u in ep.coverage(s.state(), home)["agents"]["claude"]["uncaptured"] if u["reason"].startswith("GAP")]
    assert [g["session"] for g in gaps()] == ["s1"]
    scan.scan(s, home)
    assert gaps() == []


def test_the_peer_line_names_the_machine_tag_not_a_fixed_laptop():
    h = {"me": "vaio", "devices": [{"name": "vaio"}, {"name": "l15", "tag": "[L15-WIN]", "host": "l15"}]}
    down = ep.peer_note(h, connect=lambda n: (_ for _ in ()).throw(OSError("no route")))
    assert "[L15-WIN] not scanned" in down and "Linux laptop" not in down
    assert "[L15-WIN] is reachable" in ep.peer_note(h, connect=lambda n: None)


def test_schedule_on_plans_a_timer_for_the_scan_and_never_raises(tmp_path):
    got = []
    assert switch.schedule_on("linux", "/py", tmp_path, tmp_path, apply_fn=lambda steps: got.extend(steps) or 0) == "installed"
    text = " ".join(str(x) for s in got for x in s)
    assert "fieldkit-capture.timer" in text and "capture scan --scheduled" in text
    assert switch.schedule_on("linux", apply_fn=lambda steps: 1 / 0).startswith("ERROR")


def test_scheduled_scan_does_nothing_while_capture_is_off(tmp_path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_CAPTURE_DIR", str(tmp_path / "cap"))
    switch.turn_off(home=tmp_path, run=lambda a: (1, ""), system="linux")
    from fieldkit.capture import cli
    assert cli.main(["scan", "--scheduled"]) == 0
    assert not (tmp_path / "cap" / "journal.jsonl").exists()


def test_a_run_started_while_capture_is_on_carries_the_hook_itself(tmp_path, monkeypatch):
    from fieldkit.autopilot import run
    monkeypatch.setenv("FIELDKIT_CAPTURE_DIR", str(tmp_path / "cap"))
    switch.turn_on("lesson", 60, home=tmp_path)
    on = json.loads(run.settings(tmp_path / "s.json").read_text())
    assert "capture hook" in json.dumps(on["hooks"]["PostToolUse"]) and "capture hook" in json.dumps(on["hooks"]["Stop"])
    switch.turn_off(home=tmp_path, run=lambda a: (1, ""), system="linux")
    off = json.loads(run.settings(tmp_path / "s.json").read_text())
    assert "capture hook" not in json.dumps(off["hooks"])

