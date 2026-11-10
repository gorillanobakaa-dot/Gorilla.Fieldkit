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
    monkeypatch.setattr(refusals, "record", lambda tool, text, session=None, root=None: seen.append((tool, text)) or 0)
    out = io.StringIO()
    claude_hook.main(io.StringIO(json.dumps({"tool_name": "Bash", "tool_input": {"command": "git push origin main"},
                                             "session_id": "s1"})), out)
    assert "deny" in out.getvalue()
    assert seen and seen[0][0] == "guard" and "HARD LINE 1" in seen[0][1]
    seen.clear()
    claude_hook.main(io.StringIO(json.dumps({"tool_name": "Bash", "tool_input": {"command": "git status"}})), io.StringIO())
    assert seen == []
