"""Session budget (fieldkit/tokens/budget.py): the half-budget line, the 90% handover, the session names.

Every number below is worked out by hand: the budget is the ROTATE line (rotate.DEFAULTS: 150k tokens per call,
$75 a session), so 50% is 75k tokens or $37.50 and 90% is 135k tokens or $67.50.
"""
import json
import time
from pathlib import Path

import pytest

from fieldkit.peers import machine
from fieldkit.tokens import budget, rotate
from fieldkit.zero import net

TAG = "[L15-WIN]"


@pytest.fixture(autouse=True)
def iso(tmp_path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_ROTATE_DIR", str(tmp_path / "rot"))
    monkeypatch.setenv("FIELDKIT_COORDINATION_DIR", str(tmp_path / "coord"))
    monkeypatch.setenv("FIELDKIT_CIWAIT_DIR", str(tmp_path / "ciw"))
    monkeypatch.delenv("FIELDKIT_JOB_DIR", raising=False)
    monkeypatch.delenv("FIELDKIT_ROTATE", raising=False)
    monkeypatch.setattr(rotate, "config", lambda: dict(rotate.DEFAULTS))
    monkeypatch.setattr(machine, "current", lambda *a, **k: TAG)


def row(ctx=0, usd=0.0, sid="aaaaaaaa-1111", **extra):
    return {"session": sid, "context_last": ctx, "usd": usd, "why": ["context per call 80k >= WARN 120k"],
            "task": "Build the budget tool", "title": "", **extra}


def transcript(tmp_path, name):
    t = tmp_path / f"{name}.jsonl"
    t.write_text("", encoding="utf-8")
    return str(t)


@pytest.mark.parametrize("ctx,usd,level", [
    (74_999, 0, None), (75_000, 0, "HALF"), (0, 37.5, "HALF"), (134_999, 67.49, "HALF"),
    (135_000, 0, "STOP"), (0, 67.5, "STOP"), (150_000, 75.0, "STOP")])
def test_the_budget_is_the_worse_meter(ctx, usd, level):
    assert budget.level(budget.fraction(row(ctx, usd))) == level


def test_half_is_said_once_per_session(tmp_path):
    t = transcript(tmp_path, "s1")
    text = budget.lines(row(ctx=80_000, sid="s1"), t)
    assert text.startswith("BUDGET 53%: you are at half the budget")
    assert budget.lines(row(ctx=80_000, sid="s1"), t) == ""


def test_stop_writes_the_handover_names_the_successor_and_lists_archives(tmp_path, monkeypatch):
    calls = []

    def fake_handover(transcript, title, next_, done="", not_verified="", **k):
        calls.append(title)
        return {"note": str(tmp_path / "n.md"), "plan_written": True, "brief": "BRIEF TEXT", "row": {}}

    monkeypatch.setattr(rotate, "handover", fake_handover)
    monkeypatch.setattr(budget, "archive_candidates", lambda sid, now=None, cfg=None: [{"id": "bbbbbbbb-2"}])
    t = transcript(tmp_path, "s2")
    text = budget.lines(row(ctx=140_000, sid="s2", entrypoint="claude-desktop"), t)
    assert "you used 140,000 tokens, stop writing: I am preparing the handover and starting a new VISIBLE session " \
           "automatically" in text
    assert "start_session" in text and 'archive_session "bbbbbbbb-2"' in text
    assert calls == [f"{TAG} #01 {time.strftime('%H:%M')} Build the budget tool"]
    assert Path(rotate.state_dir() / "briefs" / "s2.md").read_text(encoding="utf-8") == "BRIEF TEXT"
    assert budget.handed_over("s2") is True
    assert budget.lines(row(ctx=140_000, sid="s2"), t) == ""           # said once


def test_numbers_increase_and_carry_the_tag_and_the_start_time():
    now = 1_762_000_000
    a = budget.numbered_title("Handover job", now)
    b = budget.numbered_title("Handover job", now + 5)
    hhmm = time.strftime("%H:%M", time.localtime(now))
    assert a == f"{TAG} #01 {hhmm} Handover job"
    assert b == f"{TAG} #02 {hhmm} Handover job"


def test_such_a_title_passes_the_session_tag_check():
    title = f"{TAG} #07 14:05 budget tool"
    assert machine.tagged(title, TAG)
    assert machine.untagged([{"id": "x", "title": title}], TAG, "here") == []
    assert machine.untagged([{"id": "y", "title": "budget tool"}], TAG, "here")     # an untagged one is named


def test_archive_list_never_has_the_rotated_session_or_zero(monkeypatch):
    monkeypatch.setattr(net, "load_net", lambda: {})
    monkeypatch.setattr(net, "zero_of", lambda n: "claude:cccccccc@HOST")
    done = [{"session": "bbbbbbbb-2", "title": "old", "idle_minutes": 90},
            {"session": "cccccccc-3", "title": "zero", "idle_minutes": 90},
            {"session": "aaaaaaaa-1", "title": "rotated", "idle_minutes": 90}]
    monkeypatch.setattr(rotate, "finished", lambda **k: (done, []))
    assert [x["id"] for x in budget.archive_candidates("aaaaaaaa-1")] == ["bbbbbbbb-2"]


def test_an_unreadable_zero_archives_nothing(monkeypatch):
    def boom():
        raise OSError("no net file")
    monkeypatch.setattr(net, "load_net", boom)
    monkeypatch.setattr(rotate, "finished", lambda **k: ([{"session": "bbbbbbbb-2"}], []))
    assert budget.archive_candidates("aaaaaaaa-1") == []


def test_rotate_after_the_stop_says_the_handover_is_done(tmp_path, monkeypatch):
    monkeypatch.setattr(rotate, "handover", lambda *a, **k: {"note": "n", "plan_written": True, "brief": "b",
                                                              "row": {}})
    monkeypatch.setattr(budget, "archive_candidates", lambda *a, **k: [])
    budget.lines(row(ctx=140_000, sid="s3"), transcript(tmp_path, "s3"))
    assert budget.handed_over("s3")
    assert "already written" in rotate.rotation_done_line(row(ctx=160_000, sid="s3"))


# A visible successor the app refused (owner 2026-11-03): never a silent headless or Haiku fallback.
def _boom(*a):
    raise RuntimeError("no net")


def _log():
    return [json.loads(x) for x in (rotate.state_dir() / "successors.jsonl").read_text().splitlines()]


def test_refused_successor_goes_to_zero_not_headless():
    sent, ran = [], []
    r = budget.start_successor("B.md", "[L15-WIN] #02 01:50 job", "session",
                               send=lambda t: sent.append(t) or "claude:zero@H", headless=lambda b: ran.append(b))
    assert r["step"] == "zero" and r["line"].startswith("OWNER:") and not ran
    assert "user_asked" in sent[0] and "B.md" in sent[0]
    assert [x["step"] for x in _log()] == ["refused", "zero"]       # the owner-visible line is written first


def test_headless_sonnet_only_when_zero_is_unreachable():
    ran = []
    r = budget.start_successor("B.md", "T", "session", send=_boom, headless=lambda b: ran.append(b) or "T-1")
    assert r["step"] == "headless-sonnet" and ran == ["B.md"] and "Sonnet" in r["line"]


def test_headless_start_pins_sonnet(monkeypatch):
    seen = {}

    class R:
        returncode, stdout, stderr = 0, "started T-1", ""

    monkeypatch.setattr("subprocess.run", lambda argv, **k: seen.setdefault("argv", argv) and R())
    budget._headless_sonnet("B.md")
    assert seen["argv"][-3:] == ["--model", "sonnet", "--pin"]


def test_a_user_started_turn_starts_it_itself():
    r = budget.start_successor("B.md", "T", "user", send=_boom, headless=_boom)
    assert r["step"] == "self" and "user_asked" in r["line"]


def test_every_step_failing_is_reported_not_swallowed():
    r = budget.start_successor("B.md", "T", "session", send=_boom, headless=_boom)
    assert r["step"] == "failed" and "inbox" in r["line"]
