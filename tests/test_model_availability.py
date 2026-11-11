"""P9: a model that is out of budget is skipped by every choice, and the harness works with what is left."""
import subprocess
import time
from types import SimpleNamespace

import pytest

from fieldkit.lifecycle import autoaccept as core
from fieldkit.lifecycle import models, routing

LADDER = {"escalation": {"enabled": True, "ladder": ["haiku", "sonnet", "opus", "fable"], "own_limit": 2},
          "availability": {"cooldown_minutes": 120, "no_verdict_cooldown_minutes": 30,
                           "patterns": ["usage limit", "max turns", "quota"]},
          "cheap": ["sonnet", "haiku"], "review": ["opus"], "premium": ["fable"], "default": "sonnet", "ledger": "l"}
ON = {"enabled": True, "kinds": ["skip"], "reviewer": {"unknown_author": "top"}, "ledger": "aa.jsonl"}
CHECKS = [("a check", True, "")]


@pytest.fixture(autouse=True)
def _state(tmp_path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_LIFECYCLE_DIR", str(tmp_path / "lc"))
    monkeypatch.setattr(models, "mandate_allows", lambda m, today=None: (True, "test"))   # every rung is allowed
    # the owner's real mandate store is not read here: an owner-wide mandate opens the owner_steps door (P9, 2026-11-11)
    monkeypatch.setattr(core, "_mandate_decide", lambda **kw: ("ask", "no mandate in this test", None))


def pick(author, pol):
    return core.pick_reviewer(author, pol, data=LADDER)


def test_a_marked_model_is_unavailable_until_its_retry_time():
    models.mark_unavailable("fable", "limit reached", until=time.time() + 600, data=LADDER)
    up, why = models.availability("claude-fable-5-1", data=LADDER)
    assert not up and "fable unavailable until" in why and "limit reached" in why
    models.mark_unavailable("fable", "old", until=time.time() - 5, data=LADDER)
    assert models.is_available("fable", data=LADDER)               # the retry time passed: back by itself


def test_the_owner_can_pin_a_model_in_yaml_until_a_date():
    pinned = dict(LADDER, availability=dict(LADDER["availability"], unavailable={"fable": "until 2999-01-01"}))
    assert not models.is_available("fable", data=pinned)
    over = dict(LADDER, availability=dict(LADDER["availability"], unavailable={"fable": "until 2000-01-01"}))
    assert models.is_available("fable", data=over)
    assert "owner" in models.unavailable_lines(data=pinned)[0]


def test_a_budget_error_names_its_reset_time_and_other_text_marks_nothing():
    now = time.time()
    hit = models.classify_failure(f"Claude usage limit reached|{int(now) + 7200}", 1, now=now, data=LADDER)
    assert hit and abs(hit["until"] - (int(now) + 7200)) < 1
    hit = models.classify_failure("Error: Reached max turns (1)", 1, now=now, data=LADDER)
    assert hit and 1700 < hit["until"] - now < 1900             # the 30 minute cooldown
    assert models.classify_failure("VERDICT: YES fine", 0, data=LADDER) is None


def test_the_ladder_climb_skips_an_unavailable_rung_and_says_so():
    models.mark_unavailable("fable", "limit", time.time() + 600, data=LADDER)
    models.mark_unavailable("opus", "limit", time.time() + 600, data=LADDER)
    step = models.climb("sonnet", 2, LADDER)
    assert step["next"] is None and len(step["skipped"]) == 2 and "unavailable" in step["skipped"][0]
    models.clear_unavailable("opus", LADDER)
    assert models.climb("sonnet", 2, LADDER)["next"] == "opus"


def test_the_reviewer_skips_an_unavailable_rung():
    models.mark_unavailable("opus", "limit", time.time() + 600, data=LADDER)
    pk = pick("sonnet", ON)
    assert pk["reviewer"] == "fable" and "opus unavailable" in pk["why"]


def test_author_on_the_top_available_rung_is_reviewed_by_its_own_rung_never_refused():
    models.mark_unavailable("fable", "limit", time.time() + 600, data=LADDER)
    pk = pick("opus", ON)
    assert pk["reviewer"] == "opus" and pk["same_rung"] and "reauthor" not in pk
    assert "same-rung review" in pk["why"] and "fable unavailable" in pk["why"]
    seen = []
    rec = core.decide("skip", "s1", "opus", CHECKS, "ok?", pol=ON, pick=pick,
                      asker=lambda m, p: seen.append(m) or {"yes": True, "why": "ok", "answer": "VERDICT: YES"})
    assert seen == ["opus"] and rec["accepted"] and rec["same_rung"] and "same-rung" in rec["reviewer_why"]


def test_reauthor_is_the_other_setting_for_the_unavailable_top():
    models.mark_unavailable("fable", "limit", time.time() + 600, data=LADDER)
    pol = dict(ON, reviewer={"unknown_author": "top", "when_top_unavailable": "reauthor"})
    pk = pick("opus", pol)
    assert pk["reviewer"] == "opus" and pk["reauthor"] == "sonnet" and "same_rung" not in pk
    rec = core.decide("skip", "s1", "opus", CHECKS, "ok?", pol=pol, pick=pick,
                      asker=lambda m, p: pytest.fail("no reviewer is asked: the step is re-authored first"))
    assert rec["reauthor"] == "sonnet" and not rec["accepted"] and not rec.get("wait_owner")


def test_when_the_author_is_cheaper_the_unavailable_top_changes_nothing():
    models.mark_unavailable("fable", "limit", time.time() + 600, data=LADDER)
    assert pick("sonnet", ON)["reviewer"] == "opus" and "reauthor" not in pick("sonnet", ON)


def test_nothing_available_parks_with_a_retry_time_and_is_not_a_no():
    back = time.time() + 900
    for m in ("opus", "fable"):
        models.mark_unavailable(m, "limit", back, data=LADDER)
    models.mark_unavailable("sonnet", "limit", back, data=LADDER)
    models.mark_unavailable("haiku", "limit", back, data=LADDER)
    rec = core.decide("skip", "s1", "sonnet", CHECKS, "ok?", pol=ON, pick=pick, asker=lambda m, p: pytest.fail("asked"))
    assert rec["wait_owner"] and abs(rec["retry_at"] - back) < 1 and rec["why"][0].startswith("parked, not refused")
    assert not rec["accepted"]


def test_a_reviewer_call_that_hits_its_limit_is_marked_and_replaced():
    calls = []

    def run(cmd, **kw):
        calls.append(cmd[cmd.index("--model") + 1] if "--model" in cmd else kw["env"]["ANTHROPIC_MODEL"])
        if kw["env"]["ANTHROPIC_MODEL"] == "opus":
            return SimpleNamespace(returncode=1, stdout="", stderr="Error: Reached max turns (1)")
        return SimpleNamespace(returncode=0, stdout="fine\nVERDICT: YES", stderr="")

    ask = lambda m, p: core.ask_reviewer(m, p, dict(ON, reviewer={"cmd": ["claude", "--model", "{model}"]}), run=run)
    rec = core.decide("skip", "s1", "sonnet", CHECKS, "ok?", pol=dict(ON, reviewer={"unknown_author": "top"}),
                      pick=lambda a, p: core.pick_reviewer(a, p, data=LADDER), asker=ask)
    assert rec["accepted"] and rec["reviewer"] == "fable"        # opus was found unavailable, fable took it
    assert not models.is_available("opus") and "max turns" in models.availability("opus")[1]


def test_a_real_answer_without_a_verdict_line_is_only_a_no():
    run = lambda cmd, **kw: SimpleNamespace(returncode=0, stdout="I am unsure.", stderr="")
    r = core.ask_reviewer("opus", "q", dict(ON, reviewer={"cmd": ["x"]}), run=run)
    assert not r["yes"] and not r.get("unavailable") and models.is_available("opus", data=LADDER)


def test_a_silent_nonzero_exit_marks_the_model_unavailable():
    run = lambda cmd, **kw: SimpleNamespace(returncode=1, stdout="", stderr="boom")
    r = core.ask_reviewer("opus", "q", dict(ON, reviewer={"cmd": ["x"]}), run=run)
    assert r["unavailable"] and not models.is_available("opus", data=LADDER)


def test_the_tiebreaker_is_never_an_unavailable_rung():
    models.mark_unavailable("fable", "limit", time.time() + 600, data=LADDER)
    assert core.pick_tiebreaker("sonnet", ON, data=LADDER)["reviewer"] == "opus"


def test_task_routing_steps_down_to_an_available_model_or_parks(monkeypatch):
    d = routing.load()
    monkeypatch.setattr(models, "load", lambda path=None: LADDER)
    text = "write a note about the weather, test it"
    ch = {"mode": "solo", "ceiling": "opus", "accepted": "", "fresh": True}
    assert routing.route(text, requested="opus", data=d, ch=ch)["model"] in ("sonnet", "opus")
    models.mark_unavailable("sonnet", "limit", time.time() + 600, data=LADDER)
    r = routing.route(text, requested="sonnet", data=d, ch=ch)
    assert r["action"] == "park" and "sonnet unavailable" in r["why"] and "retry after" in r["why"]


def test_the_build_harness_re_authors_a_step_once_then_parks(monkeypatch):
    from fieldkit.buildh import autoport, task
    monkeypatch.setattr(task, "journal", lambda *a, **k: None)
    rec = {"reauthor": "sonnet", "author": "opus", "reviewer": "opus", "why": ["fable unavailable"]}
    s = {"id": "p1", "model": "opus", "attempts": 2, "status": "proposed"}
    assert autoport._reauthor({}, s, rec) and s["model"] == "sonnet" and s["status"] == "failed" and s["attempts"] == 0
    s["model"], s["status"] = "opus", "proposed"                 # it came back from sonnet and the top is still out
    assert not autoport._reauthor({}, s, rec) and s["status"] == "blocked" and s["model"] == "opus"
