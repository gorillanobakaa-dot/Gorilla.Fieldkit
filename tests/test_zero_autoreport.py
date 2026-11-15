"""Auto-report to Zero at every turn end, and the keeper rule silent_child (owner order 2026-11-15)."""
import json
import time

import pytest

from fieldkit.lifecycle import keeper
from fieldkit.zero import autoreport, net

NOW = 1_800_000_000.0
ZERO = "claude:aaaaaaaa@H"
W = "claude:bbbbbbbb@H"
SID = "bbbbbbbb-1111"
ORDER = {"id": "O-1", "objective": "build the watchdog", "area": ["x"], "done": "tests", "at": NOW - 7200}


@pytest.fixture
def env(tmp_path, monkeypatch):
    c = tmp_path / "coord"
    c.mkdir()
    monkeypatch.setenv("FIELDKIT_COORDINATION_DIR", str(c))
    monkeypatch.setattr(net, "machine", lambda: "H")
    monkeypatch.setattr(net, "load_net", lambda: {"net": "open", "zero": {"station": ZERO}})
    tp = tmp_path / "t.jsonl"
    return tp


def transcript(tp, text):
    tp.write_text(json.dumps({"type": "user", "message": {"content": "go"}}) + "\n" +
                  json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}) + "\n",
                  encoding="utf-8")


def station(order=ORDER, meta=None, **rec):
    net.save_stations({"stations": {W: dict({"order": order}, **rec)}, "meta": meta or {}})


def call(tp, sid=SID):
    return {"session_id": sid, "transcript_path": str(tp)}


def test_sends_one_line_with_status_text_and_prs(env):
    station()
    transcript(env, "Opened https://github.com/o/r/pull/42 and waiting for CI. " + "x" * 400)
    sent = []
    line = autoreport.report(call(env), now=NOW, send=lambda: sent.append(1))
    assert sent and line.startswith(f"REPORT {W} [working] order O-1: build the watchdog | PR 42 | Opened")
    assert "\n" not in line and len(line) < 700
    assert net.load_stations()["stations"][W]["report"]["status"] == "working"


def test_debounce_same_status_and_text_sends_nothing(env):
    station()
    transcript(env, "still going")
    assert autoreport.report(call(env), now=NOW, send=lambda: None)
    assert autoreport.report(call(env), now=NOW + 60, send=lambda: None) == ""
    transcript(env, "now different")
    assert autoreport.report(call(env), now=NOW + 120, send=lambda: None)


def test_zero_and_orderless_sessions_send_nothing(env):
    station(order=None)
    transcript(env, "hi")
    assert autoreport.report(call(env), now=NOW, send=lambda: None) == ""
    net.save_stations({"stations": {ZERO: {"order": ORDER}}, "meta": {}})      # Zero itself, even with an order record
    assert autoreport.report(call(env, "aaaaaaaa-1"), now=NOW, send=lambda: None) == ""


def test_rotating_carries_the_successor_brief(env):
    station(meta={"relief": {W: {"ts": NOW, "order": "O-1"}}})
    transcript(env, "handover written")
    line = autoreport.report(call(env), now=NOW, send=lambda: None, brief_of=lambda sid: ("/b/succ.md", 1.0))
    assert "[rotating]" in line and "successor brief: /b/succ.md" in line


@pytest.mark.parametrize("text,status", [("BLOCKED: need a decision", "blocked"), ("DONE. PR merged", "done")])
def test_blocked_and_done(env, text, status):
    station()
    transcript(env, text)
    assert f"[{status}]" in autoreport.report(call(env), now=NOW, send=lambda: None)


def test_a_failing_send_never_raises(env):
    station()
    transcript(env, "x")

    def boom():
        raise RuntimeError("net down")
    assert autoreport.report(call(env), now=NOW, send=boom) == ""


def test_stop_hook_calls_the_report(env, monkeypatch):
    from fieldkit.tokens import rotate
    seen = []
    monkeypatch.setattr(autoreport, "report", lambda c: seen.append(c))
    import io
    rotate.stop_hook(stdin=io.StringIO(json.dumps({"session_id": SID, "transcript_path": str(env)})))
    assert seen and seen[0]["session_id"] == SID


# ---- keeper rule silent_child ----

class P:
    def __init__(self, stations, wait=False):
        self.s, self.wait = stations, wait

    def stations(self): return self.s
    def has_wait(self, st): return self.wait


def ctx():
    return {"now": NOW, "wake": []}


CFG = {"enabled": True, "silent_after_min": 30}


def test_silent_worker_wakes_zero_with_last_words():
    rec = {"order": ORDER, "last": NOW - 3600, "report": {"at": NOW - 3600, "status": "working", "text": "compiling things"}}
    c = ctx()
    keeper.rule_silent_child(P({W: rec}), CFG, {}, c)
    assert len(c["wake"]) == 1 and "silent since" in c["wake"][0]["line"] and "compiling things" in c["wake"][0]["line"]


@pytest.mark.parametrize("rec,wait", [
    ({"order": ORDER, "last": NOW - 600}, False),                                              # recent
    ({"order": ORDER, "last": NOW - 3600, "report": {"at": NOW - 3600, "status": "done"}}, False),   # done
    ({"order": dict(ORDER, closed=True), "last": NOW - 3600}, False),                          # closed
    ({"order": ORDER, "last": NOW - 3600}, True),                                              # live ci-wait
    ({"last": NOW - 3600}, False),                                                             # no order
])
def test_silent_rule_leaves_these_alone(rec, wait):
    c = ctx()
    keeper.rule_silent_child(P({W: dict({"report": {}}, **rec)}, wait), CFG, {}, c)
    assert c["wake"] == []


def test_rotating_report_wakes_at_once_with_the_brief():
    rec = {"order": ORDER, "last": NOW, "report": {"at": NOW, "status": "rotating", "brief": "/b/succ.md"}}
    c = ctx()
    keeper.rule_silent_child(P({W: rec}), CFG, {}, c)
    assert c["wake"][0]["key"].startswith("rotating:") and "/b/succ.md" in c["wake"][0]["next"] and "visible" in c["wake"][0]["next"]


def test_silent_and_rotating_findings_never_start_a_headless_job():
    class F(P):
        def __init__(self):
            super().__init__({})
            self.started = []
            self.repo = "."
        def start(self, *a, **k): self.started.append(a)
        def watch_act(self, *a, **k): pass
    f = F()
    c = {"now": NOW, "stamp": "s", "wake": [{"key": "silent:W:O-1", "state": "SILENT", "line": "l", "next": "n"},
                                          {"key": "rotating:W:O-1", "state": "ROTATING", "line": "l", "next": "n"}],
         "watch": [], "actions": [], "led": {}, "dry": False, "retry": []}
    st = {k: {} for k in ("done", "restarts", "roots", "wake", "woken", "queue", "parked")}
    st["wake"] = {"silent:W:O-1": NOW - 99999, "rotating:W:O-1": NOW - 99999}
    keeper.rule_wake(f, {"headless_after_min": 45, "max_per_day": 3}, st, c)
    assert f.started == []
