"""fieldkit teach: a session transcript becomes a log of what was run, in order, with what came back."""
import json

from fieldkit import teach


def _transcript(tmp_path):
    p = tmp_path / "sess.jsonl"
    rows = [
        {"type": "assistant", "timestamp": "2026-10-14T14:00:00Z", "message": {"content": [
            {"type": "text", "text": "I will look."},
            {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "git status --short", "description": "Show status"}}]}},
        {"type": "user", "timestamp": "2026-10-14T14:00:01Z", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": " M a.py\n"}]}},
        {"type": "assistant", "timestamp": "2026-10-14T14:00:02Z", "message": {"content": [
            {"type": "tool_use", "id": "t2", "name": "Read", "input": {"file_path": "/x/y"}},
            {"type": "tool_use", "id": "t3", "name": "Bash", "input": {"command": "false"}}]}},
        {"type": "user", "timestamp": "2026-10-14T14:00:03Z", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t2", "content": "file text"},
            {"type": "tool_result", "tool_use_id": "t3", "content": [{"type": "text", "text": "exit 1"}], "is_error": True}]}},
    ]
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return p


def test_only_shell_commands_in_order_with_their_results(tmp_path):
    recs = teach.calls(str(_transcript(tmp_path)))
    assert [r["command"] for r in recs] == ["git status --short", "false"]
    assert recs[0]["result"].strip() == "M a.py" and not recs[0]["error"]
    assert recs[1]["result"] == "exit 1" and recs[1]["error"]


def test_since_drops_earlier_commands(tmp_path):
    recs = teach.calls(str(_transcript(tmp_path)), since="2026-10-14T14:00:02")
    assert [r["command"] for r in recs] == ["false"]


def test_write_makes_the_log_and_leaves_the_narration_out(tmp_path, monkeypatch):
    src = _transcript(tmp_path)
    monkeypatch.setattr(teach, "resolve", lambda s: str(src))
    out = tmp_path / "log.md"
    r = teach.write("any", str(out), title="Teaching log: test")
    assert r["ok"] and r["commands"] == 2 and r["failed"] == 1
    text = out.read_text(encoding="utf-8")
    assert "git status --short" in text and "FAILED" in text and "I will look." not in text
    assert "## Summary" in text


def test_long_output_is_cut_and_says_so(tmp_path, monkeypatch):
    recs = [{"time": "", "tool": "Bash", "command": "yes | head", "description": "", "result": "y\n" * 5000, "error": False}]
    text = teach.render(recs, "t", "s.jsonl", max_chars=100)
    assert "more characters not shown" in text


def test_a_missing_transcript_is_refused_not_invented(tmp_path, monkeypatch):
    monkeypatch.setattr(teach, "resolve", lambda s: None)
    r = teach.write("nope", str(tmp_path / "log.md"))
    assert not r["ok"] and "no transcript" in r["why"] and not (tmp_path / "log.md").exists()
