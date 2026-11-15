"""P34: Zero's read-run counter and tokens per model."""
import json

import pytest

from fieldkit.lifecycle import modelmix, readrun


@pytest.fixture(autouse=True)
def _state(tmp_path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_LIFECYCLE_DIR", str(tmp_path / "lc"))


def bash(cmd, sid="z"):
    return {"session_id": sid, "tool_name": "Bash", "tool_input": {"command": cmd}}


C = {"max_run": 4, "every": 5}


@pytest.mark.parametrize("cmd,want", [
    ("git status", True), ("ls -la && cat a.txt | head -5", True), ("cd /x && grep -n foo bar.py", True),
    ("fieldkit task status", True), ("git commit -m x", False), ("python - <<EOF\nprint(1)\nEOF", False),
    ("ls > out.txt", False), ("cat $(whoami)", False), ("ls; rm -rf x", False), ("", False)])
def test_what_counts_as_read_only(cmd, want):
    assert readrun.is_read_only(cmd) is want


def test_the_fifth_read_in_a_row_says_delegate_and_the_tenth_says_it_again():
    said = [readrun.note(bash(f"ls {i}"), zero=True, c=C) for i in range(1, 11)]
    assert said[:4] == [""] * 4
    assert "haiku subagent" in said[4] and "5 read-only" in said[4]
    assert said[5:9] == [""] * 4 and "10 read-only" in said[9]          # the text changes, so the room never swallows it


def test_a_write_or_an_agent_call_ends_the_run():
    for i in range(4):
        readrun.note(bash(f"ls {i}"), zero=True, c=C)
    readrun.note({"session_id": "z", "tool_name": "Agent", "tool_input": {}}, zero=True, c=C)
    assert readrun.note(bash("ls 5"), zero=True, c=C) == ""              # the count started again
    for i in range(3):
        assert readrun.note(bash(f"ls {i}"), zero=True, c=C) == ""
    assert readrun.note(bash("ls 9"), zero=True, c=C) != ""


def test_read_and_grep_tools_are_neutral():
    for i in range(4):
        readrun.note(bash(f"ls {i}"), zero=True, c=C)
    assert readrun.note({"session_id": "z", "tool_name": "Read", "tool_input": {}}, zero=True, c=C) == ""
    assert readrun.note(bash("ls x"), zero=True, c=C) != ""


def test_only_zero_is_counted():
    assert all(readrun.note(bash("ls"), zero=False, c=C) == "" for _ in range(9))


def test_the_limit_is_data(monkeypatch):
    assert readrun.cfg()["max_run"] == 4


def test_a_broken_state_never_breaks_the_hook(monkeypatch):
    monkeypatch.setattr(readrun, "_load", lambda: 1 / 0)
    assert readrun.note(bash("ls"), zero=True, c=C) == ""


def line(mid, model, i, o, ts="2026-11-12T01:00:00Z", cr=0):
    return json.dumps({"type": "assistant", "timestamp": ts, "uuid": mid + model,
                       "message": {"id": mid, "model": model, "usage": {"input_tokens": i, "output_tokens": o,
                                                                         "cache_read_input_tokens": cr}}})


def test_tokens_per_model_count_each_message_once_and_take_the_model_from_the_message(tmp_path):
    proj = tmp_path / "-home-x-wt-job-T-20261112-030627-4e014d"
    (proj / "sess1" / "subagents").mkdir(parents=True)
    (proj / "sess1.jsonl").write_text("\n".join([
        line("m1", "claude-opus-5-5", 100, 10), line("m1", "claude-opus-5-5", 100, 30),      # same id: counted once
        line("m2", "claude-opus-5-5", 50, 5, cr=900)]), encoding="utf-8")
    (proj / "sess1" / "subagents" / "agent-1.jsonl").write_text(line("s1", "claude-haiku-5-5", 7, 3), encoding="utf-8")
    rs = modelmix.rows(tmp_path, zero_session="sess")
    by = {r["model"]: r for r in rs}
    assert by["claude-opus-5-5"]["calls"] == 2 and by["claude-opus-5-5"]["output"] == 35
    assert by["claude-opus-5-5"]["cache_read"] == 900 and by["claude-opus-5-5"]["tier"] == "expensive"
    assert by["claude-haiku-5-5"]["tier"] == "cheap" and by["claude-haiku-5-5"]["zero"]   # the subagent is in sess1
    assert {r["group"] for r in rs} == {"T-20261112-030627-4e014d"}
    assert modelmix.totals(rs)["claude-opus-5-5"]["tokens"] == 150 + 35 + 900
    assert modelmix.rows(tmp_path, since="2027-01-01") == []


def test_zero_is_recognised_by_its_station_name(monkeypatch):
    from fieldkit.zero import net
    monkeypatch.setattr(net, "load_net", lambda: {})
    monkeypatch.setattr(net, "zero_of", lambda n: "claude:c67eb841@host")
    monkeypatch.setattr(net, "hook_station", lambda sid, host=None: f"claude:{sid[:8]}@host")
    assert readrun.is_zero("c67eb841-3b92-4f5f") and not readrun.is_zero("aaaaaaaa-1")
