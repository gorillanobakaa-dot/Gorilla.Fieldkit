"""P28 C: rotation is compaction-aware (fieldkit/tokens/rotate.py, budget.py, rotate_stop.py).

A Claude Code session that compacts itself is never blocked for context (WARN, not ROTATE); dollars still rotate it;
the context lines are expressed against the model's real window (the lower of the absolute line and its share).
"""
import json

import pytest

from fieldkit.tokens import budget, rotate, rotate_stop, session_cost as sc

CFG = dict(rotate.DEFAULTS)


@pytest.fixture(autouse=True)
def iso(tmp_path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_ROTATE_DIR", str(tmp_path / "rot"))
    monkeypatch.setenv("FIELDKIT_COORDINATION_DIR", str(tmp_path / "coord"))
    monkeypatch.setenv("FIELDKIT_CIWAIT_DIR", str(tmp_path / "ciw"))
    for k in ("FIELDKIT_JOB_DIR", "FIELDKIT_ROTATE", "DISABLE_AUTO_COMPACT", "DISABLE_COMPACT"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(rotate, "config", lambda: dict(CFG))
    monkeypatch.setattr(rotate_stop, "permanent_zero", lambda sid: False)


def _rows(read=400_000, model="claude-opus-5-5", compactions=(97_789, 120_000)):
    base = {"timestamp": "2026-11-16T10:00:00Z", "cwd": "C:/work/repo", "gitBranch": "feat/x",
            "entrypoint": "claude-desktop"}
    rows = [{**base, "type": "user", "message": {"content": "Build it."}}]
    for pre in compactions:
        rows.append({**base, "type": "system", "subtype": "compact_boundary", "compactMetadata": {"preTokens": pre}})
    rows.append({**base, "type": "assistant", "message": {
        "id": "m1", "model": model, "stop_reason": "end_turn", "content": [],
        "usage": {"input_tokens": 0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": read,
                  "output_tokens": 1_000}}})
    return rows


def _transcript(tmp_path, name="sess-compact", **kw):
    p = tmp_path / "projects" / "proj" / f"{name}.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(json.dumps(r) + "\n" for r in _rows(**kw)), encoding="utf-8")
    return p


def test_verdict_compacting_session_is_warn_not_rotate():
    level, why = rotate.verdict(400_000, 0, CFG, window=200_000, compact=True, compactions=3)
    assert level == "WARN"
    assert "compacts itself (compacted 3 time(s)): WARN, not a rotation" in why[0]
    assert rotate.verdict(400_000, 0, CFG, window=200_000)[0] == "ROTATE"        # no compaction: still ROTATE


def test_dollars_still_rotate_a_compacting_session():
    level, why = rotate.verdict(400_000, 80.0, CFG, window=200_000, compact=True)
    assert level == "ROTATE" and why[-1] == "session dollars $80.00 >= ROTATE $75.00"


def test_measure_counts_compactions_and_judged_does_not_rotate(tmp_path):
    p = _transcript(tmp_path)
    facts = rotate.measure(sc.ClaudeCode(tmp_path / "projects"), p)
    assert (facts["compactions"], facts["compact_at"], facts["model"]) == (2, 120_000, "claude-opus-5-5")
    r = rotate.judged(facts, CFG)
    assert r["level"] == "WARN" and r["compactable"] is True and r["window"] == 200_000


def test_auto_compact_off_rotates_again(tmp_path, monkeypatch):
    monkeypatch.setenv("DISABLE_AUTO_COMPACT", "1")
    p = _transcript(tmp_path)
    r = rotate.judged(rotate.measure(sc.ClaudeCode(tmp_path / "projects"), p), CFG)
    assert r["level"] == "ROTATE" and r["compactable"] is False


def test_can_compact_only_claude_code_and_env_switch():
    assert rotate.can_compact({"source": "claude-code"}, env={}) is True
    assert rotate.can_compact({"source": "claude-code"}, env={"DISABLE_AUTO_COMPACT": "0"}) is True
    assert rotate.can_compact({"source": "claude-code"}, env={"DISABLE_COMPACT": "true"}) is False
    assert rotate.can_compact({"source": "codex"}, env={}) is False


def test_window_of_one_million_and_config_override():
    assert rotate.window_of("claude-opus-5-5[1m]", CFG) == 1_000_000
    assert rotate.window_of("claude-opus-5-5", CFG) == 200_000
    assert rotate.window_of("x", {**CFG, "windows": {"x": 64_000}}) == 64_000


def test_lines_against_the_real_window():
    assert rotate.ctx_lines(100_000, CFG) == (60_000, 75_000)
    assert rotate.ctx_lines(1_000_000, CFG) == (120_000, 150_000)                 # the absolute line is lower
    assert rotate.ctx_lines(None, CFG) == (120_000, 150_000)
    level, why = rotate.verdict(80_000, 0, CFG, window=100_000)
    assert level == "ROTATE" and why == ["context per call 80k (80% of the 100k window) >= ROTATE 75k"]


def test_budget_fraction_ignores_context_when_compactable():
    assert budget.fraction({"context_last": 400_000, "usd": 0.0, "compactable": True}, CFG) == 0.0
    assert budget.fraction({"context_last": 400_000, "usd": 0.0}, CFG) == pytest.approx(400 / 150)
    assert budget.fraction({"context_last": 60_000, "usd": 0.0, "window": 100_000}, CFG) == pytest.approx(0.8)


def test_hook_marks_warn_so_the_stop_gate_does_not_block(tmp_path):
    p = _transcript(tmp_path)
    call = {"transcript_path": str(p), "session_id": p.stem, "hook_event_name": "UserPromptSubmit"}
    rotate.hook_line(call, "UserPromptSubmit")
    said = json.loads((tmp_path / "rot" / "said" / (p.stem + ".json")).read_text(encoding="utf-8"))
    assert said["level"] == "WARN"
    pre = {"transcript_path": str(p), "tool_name": "Bash", "tool_input": {"command": "git status"},
           "hook_event_name": "PreToolUse"}
    assert rotate_stop.stop_refusal(pre) is None
