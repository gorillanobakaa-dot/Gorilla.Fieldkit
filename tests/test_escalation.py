"""Plan P9, escalation ladder: ladder steps, own-vs-not attempts, success table, strikes integration (no network, no model)."""
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


def test_escalation_off_by_default_and_climbs_when_on(monkeypatch):
    monkeypatch.setenv("FIELDKIT_ATTEMPT_MODEL", "sonnet")
    for h in ("h1", "h2"):
        strikes.record_failure("b", h, inbox=lambda *a: None)
    assert strikes.escalation("b") is None
    d = models.load()
    d["escalation"]["enabled"] = True
    monkeypatch.setattr(models, "load", lambda path=None: d)
    assert strikes.escalation("b") == "opus"


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
