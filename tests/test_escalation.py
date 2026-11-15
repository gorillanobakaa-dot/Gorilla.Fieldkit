"""Plan P9, escalation ladder: ladder steps, own-vs-not attempts, success table, strikes integration (no network, no model)."""
import json

import pytest

from fieldkit.lifecycle import models
from fieldkit.method import strikes


@pytest.fixture(autouse=True)
def iso(tmp_path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_LIFECYCLE_DIR", str(tmp_path / "lc"))
    monkeypatch.setenv("FIELDKIT_PARKED_FILE", str(tmp_path / "parked.json"))


def test_next_model_retries_then_climbs():
    assert models.next_model("claude-haiku-5-5", 1) == "claude-haiku-5-5"
    assert models.next_model("claude-haiku-5-5", 2) == "sonnet"
    assert models.next_model("claude-sonnet-5-5", 2) == "opus"
    assert models.next_model("claude-opus-5-5", 2) == "fable"
    assert models.next_model("claude-fable-5-1", 2) is None
    assert models.next_model("gpt-9", 5) is None


def test_plan_review_only_for_risky_work():
    assert models.needs_plan_review("release pipeline") and models.needs_plan_review("fan control")
    assert not models.needs_plan_review("docs typo")


def test_table_ignores_failures_not_the_branchs():
    models.record_attempt("b", "docs", "sonnet", "pass")
    models.record_attempt("b", "docs", "sonnet", "fail", own=False)
    models.record_attempt("b", "docs", "sonnet", "fail", own=True)
    c = models.success_table()[("sonnet", "docs")]
    assert (c["tries"], c["passed"], c["own_failed"], c["rate"]) == (2, 1, 1, 0.5)


def test_best_model_needs_history_and_rate():
    for _ in range(3):
        models.record_attempt("b", "docs", "haiku", "pass")
    models.record_attempt("b", "ci", "haiku", "fail")
    assert models.best_model("docs") == "haiku"
    assert models.best_model("ci") is None
    assert models.best_model("none") is None


def test_bad_outcome_refused():
    with pytest.raises(ValueError):
        models.record_attempt("b", "j", "m", "maybe")


def test_strikes_record_model_and_attempt(monkeypatch):
    monkeypatch.setenv("FIELDKIT_ATTEMPT_MODEL", "sonnet")
    kind = lambda *a: {"t::a": "pass"}
    r = strikes.count_red("b", "h1", ["FAILED t::a"], "base", job="ci", runner=kind, inbox=lambda *a: None)
    assert r["kind"] == "own"
    assert strikes.load()["strikes"]["b"]["rounds"][0]["model"] == "sonnet"
    assert models.read_attempts()[0]["own"] is True
    mains = lambda *a: {"t::a": "fail"}
    strikes.count_red("b", "h2", ["FAILED t::a"], "base", job="ci", runner=mains, inbox=lambda *a: None)
    assert models.read_attempts()[1]["own"] is False


def _ladder(monkeypatch, on=True, mandate=False):
    d = models.load()
    d["escalation"]["enabled"] = on
    monkeypatch.setattr(models, "load", lambda path=None: d)
    monkeypatch.setattr(models, "mandate_allows",
                        lambda m, today=None: (True, "mandate") if mandate else (False, "no mandate"))


def test_ladder_is_on_in_the_shipped_config():
    assert models.ladder_cfg()["enabled"] is True


def test_ladder_off_parks_after_two(monkeypatch):
    _ladder(monkeypatch, on=False)
    monkeypatch.setenv("FIELDKIT_ATTEMPT_MODEL", "sonnet")
    for h in ("h1", "h2"):
        r = strikes.record_failure("b", h, inbox=lambda *a: None)
    assert r["parked"] and strikes.escalation("b") is None
    assert strikes.ladder_step([{"model": "sonnet"}, {"model": "sonnet"}]) is None


def test_sonnet_climbs_to_opus_then_parks_fable_skipped(monkeypatch):
    _ladder(monkeypatch)
    inbox = []
    add = lambda *a: inbox.append(a)
    monkeypatch.setenv("FIELDKIT_ATTEMPT_MODEL", "sonnet")
    for h in ("h1", "h2"):
        r = strikes.record_failure("b", h, inbox=add)
    assert not r["parked"] and r["escalate_to"] == "opus" and strikes.escalation("b") == "opus"
    monkeypatch.setenv("FIELDKIT_ATTEMPT_MODEL", "opus")
    r = strikes.record_failure("b", "h3", inbox=add)
    assert not r["parked"]                                  # opus retries once
    r = strikes.record_failure("b", "h4", inbox=add)
    assert r["parked"] and r["new"]                         # fable refused (no mandate): opus was the top
    assert "sonnet, opus" in inbox[0][1]                    # the parked note says which models tried
    rows = [json.loads(l) for l in models.attempts_path().read_text().splitlines()]
    assert [(x["from"], x["to"]) for x in rows if x.get("event") == "rung"] == [("sonnet", "opus"), ("opus", "PARK")]
    assert rows[-1]["skipped"] == ["fable"]


def test_fable_used_with_a_mandate(monkeypatch):
    _ladder(monkeypatch, mandate=True)
    assert models.climb("opus", 2)["next"] == "fable"
    _ladder(monkeypatch)
    assert models.climb("opus", 2) == {"next": None, "same": False, "skipped": ["fable"]}


def test_unknown_model_still_parks(monkeypatch):
    _ladder(monkeypatch)
    monkeypatch.setenv("FIELDKIT_ATTEMPT_MODEL", "local-qwen")
    for h in ("h1", "h2"):
        r = strikes.record_failure("b", h, inbox=lambda *a: None)
    assert r["parked"]


def test_build_harness_failed_step_asks_the_ladder(monkeypatch):
    from fieldkit.buildh import task
    _ladder(monkeypatch)
    monkeypatch.setenv("FIELDKIT_ATTEMPT_MODEL", "sonnet")
    journal = []
    monkeypatch.setattr(task, "journal", lambda t, ev, **kw: journal.append((ev, kw)))
    t = {"id": "T1"}
    s = {"id": "s1", "attempts": 3, "max_attempts": 3}
    assert task.next_status(t, s) == "failed" and s["model"] == "opus" and s["attempts"] == 0
    assert journal[0][0] == "rung"
    s["attempts"] = 3
    assert task.next_status(t, s) == "blocked"              # opus is the top allowed rung
    assert "sonnet, opus" in s["last_why"][-1]
    assert task.next_status(t, {"id": "x", "attempts": 3, "max_attempts": 3}, by="cli") == "blocked"
    assert task.next_status(t, {"id": "x", "attempts": 1, "max_attempts": 3}) == "failed"


def test_low_rate_never_delegated():
    models.record_attempt("b", "x", "haiku", "pass")
    for _ in range(2):
        models.record_attempt("b", "x", "haiku", "fail")
    assert models.best_model("x") is None


def test_unproven_red_past_the_limit_is_not_the_models_own(monkeypatch):
    monkeypatch.setenv("FIELDKIT_ATTEMPT_MODEL", "sonnet")
    none = lambda *a: {}
    for h in ("h1", "h2", "h3"):
        r = strikes.count_red("b", h, ["FAILED t::a"], "base", job="ci", runner=none, inbox=lambda *a: None)
    assert r["kind"] == "unproven" and r["counted"]
    assert [a["own"] for a in models.read_attempts()] == [False]
