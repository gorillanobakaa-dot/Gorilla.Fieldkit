"""fieldkit refusals (plan R14): a refusal repeated twice opens exactly one defect row, never an inbox item."""
import json

from fieldkit import refusals


def _problems(tmp_path):
    p = tmp_path / "state" / "coordination" / "ZERO-OPEN-PROBLEMS.md"
    return p.read_text(encoding="utf-8") if p.is_file() else ""


def test_key_strips_heads_ids_and_numbers():
    a = refusals.key_of("train", "head moved after the declaration (fd12f33ab -> b0677a4cd)")
    b = refusals.key_of("train", "head moved after the declaration (1c84dd6ef -> 8e5fafb6d)")
    assert a == b
    assert refusals.key_of("guard", "x") != refusals.key_of("train", "x")


def test_one_refusal_opens_no_row(tmp_path):
    assert refusals.record("train", "head moved (abcdef1 -> 1234567)", root=tmp_path) == 0
    assert "repeated refusal" not in _problems(tmp_path)


def test_planted_refusal_twice_opens_exactly_one_row(tmp_path):
    (tmp_path / "state" / "coordination").mkdir(parents=True)
    (tmp_path / "state" / "coordination" / "ZERO-OPEN-PROBLEMS.md").write_text(
        "| # | Problem | Status | Proof |\n|---|---|---|---|\n| 40 | old | OPEN | x |\n\nafter\n", encoding="utf-8")
    assert refusals.record("train", "head moved (abcdef1 -> 1234567)", root=tmp_path) == 0
    assert refusals.record("train", "head moved (9999999 -> 8888888)", root=tmp_path) == 41
    assert refusals.record("train", "head moved (1111111 -> 2222222)", root=tmp_path) == 0      # a third: no second row
    body = _problems(tmp_path)
    assert body.count("repeated refusal:") == 1
    assert "| 41 |" in body and "| OPEN |" in body
    assert body.index("| 41 |") < body.index("after")                  # inside the table
    assert not (tmp_path / "state" / "inbox").exists()
    rows = [json.loads(x) for x in (tmp_path / "state" / "refusals.jsonl").read_text().splitlines()]
    assert len(rows) == 3 and {"tool", "key", "text", "t", "session"} <= set(rows[0])


def test_list_and_hook_line(tmp_path):
    assert refusals.hook_text(tmp_path) == ""
    refusals.record("guard", "no", root=tmp_path)
    refusals.record("guard", "no", root=tmp_path)
    s = refusals.summary(tmp_path)
    assert s[0]["count"] == 2 and s[0]["row_open"]
    assert refusals.hook_text(tmp_path) == "1 repeated refusals open - fieldkit refusals list"
    p = tmp_path / "state" / "coordination" / "ZERO-OPEN-PROBLEMS.md"
    p.write_text(p.read_text(encoding="utf-8").replace("| OPEN |", "| DONE |"), encoding="utf-8")
    assert refusals.hook_text(tmp_path) == ""


def test_record_never_raises(tmp_path):
    blocker = tmp_path / "state"
    blocker.write_text("a file where the folder should be", encoding="utf-8")
    assert refusals.record("train", "x", root=tmp_path) == 0


def test_train_log_records_refusals(tmp_path, monkeypatch):
    from fieldkit.method import train
    seen = []
    monkeypatch.setattr(refusals, "record", lambda tool, text, **k: seen.append((tool, text)))
    monkeypatch.setattr(train, "save_state", lambda *a, **k: None)
    train._log(tmp_path, {}, 5, "not-ready", why="head moved")
    train._log(tmp_path, {}, 5, "conflict", paths=[])
    assert seen == [("train", "not-ready: head moved")]


def test_ship_emit_records_refused_state(monkeypatch, capsys):
    from fieldkit import method
    seen = []
    monkeypatch.setattr(refusals, "record", lambda tool, text, **k: seen.append((tool, text)))
    method.emit({"ok": False, "state": "push-refused", "error": "hook said no"}, True, lambda d: [])
    method.emit({"ok": True, "state": "done"}, True, lambda d: [])
    assert seen == [("ship", "push-refused: hook said no")]


def test_a_guard_refusal_reaches_the_ledger(monkeypatch):
    # 2026-11-09: the PreToolUse guard denied `fieldkit ship release-ready` twice and the ledger never heard of it
    import io
    import json
    from fieldkit.autopilot import claude_hook
    seen = []
    monkeypatch.setattr(refusals, "record", lambda tool, text, session=None, root=None, **kw: seen.append((tool, text, kw)) or 0)
    out = io.StringIO()
    claude_hook.main(io.StringIO(json.dumps({"tool_name": "Bash", "tool_input": {"command": "git push origin main"},
                                             "session_id": "s1"})), out)
    assert "deny" in out.getvalue()
    assert seen and seen[0][0] == "guard" and "HARD LINE 1" in seen[0][1]
    seen.clear()
    claude_hook.main(io.StringIO(json.dumps({"tool_name": "Bash", "tool_input": {"command": "git status"}})), io.StringIO())
    assert seen == []


# ---- P23: one learning loop for every mistake, both computers ----------------------------------------------------------
import pytest  # noqa: E402


@pytest.fixture(autouse=False)
def me(monkeypatch):
    monkeypatch.setattr(refusals, "_machine_cache", ["[VAIO-LNX]"])
    monkeypatch.delenv("FIELDKIT_JOB_DIR", raising=False)


def test_rows_carry_machine_and_id(tmp_path, me):
    refusals.record("guard", "no", root=tmp_path)
    r = refusals._rows(tmp_path)[0]
    assert r["machine"] == "[VAIO-LNX]" and len(r["id"]) == 12


def test_windows_rows_reach_the_linux_ledger_deduplicated(tmp_path, me):
    win, lin = tmp_path / "win", tmp_path / "lin"
    refusals._machine_cache[0] = "[L15-WIN]"
    refusals.record("ci", "CI red: x (feat/a)", root=win)
    sent = []
    refusals.push(win, call=lambda req, device=None, timeout=None: sent.append(req) or {"ok": True}, devices=["vaio"])
    assert len(sent) == 1 and sent[0]["text"].startswith(refusals.LEDGER1)
    again = refusals.push(win, call=lambda *a, **k: sent.append(1) or {"ok": True}, devices=["vaio"])
    assert again == {"vaio": 0} and len(sent) == 1                       # nothing new: nothing sent
    refusals._machine_cache[0] = "[VAIO-LNX]"
    items = [{"n": 1, "text": "[FROM WINDOWS #1] " + sent[0]["text"]}, {"n": 2, "text": sent[0]["text"]}]
    assert refusals.ingest(lin, items) == 1                                # the duplicate note adds nothing
    row = refusals._rows(lin)[0]
    assert row["machine"] == "[L15-WIN]" and row["via"] == "cable"
    assert refusals.ingest(lin, items) == 0
    assert refusals._load(lin, refusals.SYNC, {})["since"] == 2


def test_own_rows_echoed_back_are_not_added(tmp_path, me):
    refusals.record("guard", "no", root=tmp_path)
    note = refusals.LEDGER1 + __import__("json").dumps(refusals.unsent(tmp_path, "x"))
    assert refusals.ingest(tmp_path, [{"n": 1, "text": note}]) == 0


def test_same_reason_on_two_computers_opens_one_defect_not_an_inbox_item(tmp_path, me):
    refusals._machine_cache[0] = "[L15-WIN]"
    refusals.record("guard", "blocked: release", root=tmp_path / "w")
    note = refusals.LEDGER1 + __import__("json").dumps(refusals.unsent(tmp_path / "w", "x"))
    refusals._machine_cache[0] = "[VAIO-LNX]"
    refusals.record("guard", "blocked: release", root=tmp_path)
    assert refusals.defects(tmp_path) == {}
    refusals.ingest(tmp_path, [{"n": 1, "text": note}])
    d = refusals.defects(tmp_path)
    (dfx,) = d.values()
    assert dfx["status"] == "open" and dfx["machines"] == ["[L15-WIN]", "[VAIO-LNX]"] and dfx["count"] == 2
    assert "repeated refusal" in _problems(tmp_path) and not (tmp_path / "state" / "inbox").exists()


def test_readonly_refusal_in_a_job_opens_a_defect_at_once(tmp_path, me, monkeypatch):
    monkeypatch.setenv("FIELDKIT_JOB_DIR", "/s/jobs/T-20261110-002216-855a2c/work")
    refusals.record("guard", "release blocked", command="fieldkit ship release-ready v0.9.0", root=tmp_path)
    (dfx,) = refusals.defects(tmp_path).values()
    assert dfx["readonly"] and dfx["blocks"] and dfx["jobs"] == ["T-20261110-002216-855a2c"]
    refusals.record("guard", "other", command="git push origin main", root=tmp_path)    # a write: no defect at once
    assert len(refusals.defects(tmp_path)) == 1


def test_blocking_defect_stops_the_same_brief_only(tmp_path, me, monkeypatch):
    monkeypatch.setenv("FIELDKIT_JOB_DIR", "/s/jobs/T-20261110-002216-855a2c/work")
    refusals.record("guard", "release blocked", command="ls", root=tmp_path)
    tasks = {"T-20261110-002216-855a2c": {"hash": "abc"}}
    assert refusals.blocking_defect("abc", tasks, tmp_path)
    assert refusals.blocking_defect("other", tasks, tmp_path) is None
    key = next(iter(refusals.defects(tmp_path)))
    d = refusals.defects(tmp_path)
    d[key]["status"] = "closed"
    refusals._save(tmp_path, refusals.DEFECTS, d)
    assert refusals.blocking_defect("abc", tasks, tmp_path) is None


def test_task_start_refuses_a_brief_whose_run_hit_a_readonly_defect(tmp_path, monkeypatch):
    from fieldkit.lifecycle import tasks
    brief = tmp_path / "b.md"
    brief.write_text("PLAN ITEM: P23\ndo the thing", encoding="utf-8")
    monkeypatch.setattr(tasks, "ledger", lambda: {"T-1": {"hash": tasks.brief_hash("PLAN ITEM: P23\ndo the thing")}})
    want = tasks.brief_hash("PLAN ITEM: P23\ndo the thing\nPLAN ITEM: P23")
    monkeypatch.setattr(refusals, "blocking_defect", lambda h, t, root=None: {"key": "guard: x"} if h == want else None)
    with pytest.raises(tasks.DefectOpen):
        tasks.start(brief, plan="P23", call=lambda *a, **k: {}, launch=lambda *a: {}, worktree=lambda *a: (None, ""))


def test_false_positive_closes_only_with_a_passing_test(tmp_path, me, monkeypatch):
    monkeypatch.setenv("FIELDKIT_JOB_DIR", "/s/jobs/T-20261110-002216-855a2c/work")
    refusals.record("guard", "release blocked", command="fieldkit ship release-ready v1", root=tmp_path)
    key = next(iter(refusals.defects(tmp_path)))
    ok, why = refusals.close(key, "", root=tmp_path)
    assert not ok
    ok, why = refusals.close(key, "tests/test_refusals.py::test_no_such", root=tmp_path)
    assert not ok and "not found" in why
    t = "tests/test_refusals.py::test_key_strips_heads_ids_and_numbers"
    ok, why = refusals.close(key, t, root=tmp_path, run=lambda x: (False, "red"))
    assert not ok and refusals.defects(tmp_path)[key]["status"] == "open"
    ok, why = refusals.close(key, t, root=tmp_path, run=lambda x: (True, ""))
    assert ok
    d = refusals.defects(tmp_path)[key]
    assert d["status"] == "closed" and d["test"] == t and not d["blocks"]
    assert "| DONE |" in _problems(tmp_path)
    refusals.record("guard", "release blocked", command="ls", root=tmp_path)        # it came back: reopened
    assert refusals.defects(tmp_path)[key]["status"] == "open"


def test_list_shows_defect_state_and_machines(tmp_path, me, capsys, monkeypatch):
    refusals.record("guard", "no", root=tmp_path)
    refusals.record("guard", "no", root=tmp_path)
    s = refusals.summary(tmp_path)
    assert s[0]["defect"] == "open" and s[0]["machines"] == ["[VAIO-LNX]"]


def test_readonly_policy():
    assert refusals.is_readonly("fieldkit ship release-ready v0.9.0")
    assert refusals.is_readonly("git status")
    assert not refusals.is_readonly("fieldkit ship push --tag v1")
    assert not refusals.is_readonly("git push origin main")


def test_hours_lost_per_cause(tmp_path, me, capsys):
    refusals.record("ci", "CI red: a (b)", root=tmp_path)
    refusals.record("guard", "no", root=tmp_path)
    refusals.record("guard", "no", root=tmp_path)
    rows = refusals.lost(tmp_path)
    assert rows[0]["key"].startswith("ci:") and rows[0]["minutes"] == 25 and rows[0]["hours"] == 0.42
    assert rows[1]["minutes"] == 6 and rows[1]["defect"] == "open"
    from fieldkit import overhead
    import fieldkit.refusals as R
    monkeypatch_lost = R.lost
    R.lost = lambda root=None: monkeypatch_lost(tmp_path)
    try:
        assert overhead.main(["causes"]) == 0
    finally:
        R.lost = monkeypatch_lost
    out = capsys.readouterr().out
    assert "hours lost per cause" in out and "ESTIMATED" in out and "ci:" in out


def test_a_red_ci_round_reaches_the_ledger(monkeypatch):
    from fieldkit.method import strikes
    seen = []
    store = {"strikes": {}, "parked": {}}
    monkeypatch.setattr(strikes, "load", lambda p=None: store)
    monkeypatch.setattr(strikes, "save", lambda d, p=None: None)
    monkeypatch.setattr(strikes, "_inbox", lambda *a, **k: None)
    monkeypatch.setattr(refusals, "record", lambda tool, text, **kw: seen.append((tool, text)) or 0)
    strikes.record_failure("feat/x", "abc1234", why="test_y failed")
    strikes.record_failure("feat/x", "abc1234", why="test_y failed")            # the same head twice: one row
    assert seen == [("ci", "CI red: test_y failed (feat/x)")]


def test_rows_from_the_other_computer_are_not_sent_back(tmp_path, me):
    note = refusals.LEDGER1 + __import__("json").dumps([{"tool": "ci", "key": "ci: red", "text": "red", "t": "2026-11-10T01:00:00",
                                                         "session": "1", "machine": "[L15-WIN]"}])
    assert refusals.ingest(tmp_path, [{"n": 1, "text": note}]) == 1
    assert refusals.unsent(tmp_path, "vaio") == []


def test_fieldkit_sync_also_syncs_the_ledger(monkeypatch):
    from fieldkit import cli
    from fieldkit.core import sync
    calls = []
    monkeypatch.setattr(refusals, "sync_quiet", lambda *a, **k: calls.append(1))
    monkeypatch.setattr(sync, "run", lambda **k: [])
    monkeypatch.setattr(sync, "lines", lambda rows: [])
    monkeypatch.setattr(sync, "exit_code", lambda rows: 0)
    import argparse
    assert cli.cmd_sync(argparse.Namespace(apply=False, repo=[], no_fetch=True, json=False)) == 0
    assert calls == [1]


def test_the_real_task_ledger_is_out_of_reach():
    # 2026-11-10: tasks.start in this file wrote two "[TEST-LNX] PLAN ITEM: P23" rows to the real ledger
    from fieldkit.core import settings
    from fieldkit.lifecycle import tasks
    assert not str(tasks.ledger_path()).startswith(str(settings.state_root() / "state"))
def test_a_guard_refusal_of_a_read_only_command_is_recorded_as_read_only(tmp_path, monkeypatch):
    # P23 hook wiring: the v0.9.0 block was the guard refusing `fieldkit ship release-ready`, a read
    import io
    import json as _json
    from fieldkit.autopilot import claude_hook, hardlines
    seen = {}
    monkeypatch.setattr(refusals, "record", lambda tool, why, **kw: seen.update(kw, tool=tool) or 0)
    monkeypatch.setattr(hardlines, "decide", lambda *a, **k: ("deny", "HARD LINE 1 (test)"))
    payload = {"tool_name": "Bash", "tool_input": {"command": "fieldkit ship release-ready v0.9.0"}, "session_id": "s1"}
    claude_hook.main(stdin=io.StringIO(_json.dumps(payload)), stdout=io.StringIO())
    assert seen.get("tool") == "guard" and seen.get("command") == "fieldkit ship release-ready v0.9.0", seen
