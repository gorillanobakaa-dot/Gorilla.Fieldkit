"""I-04 core: a unit of work of ANY kind is accepted by the harness only when every check passes AND an independent
reviewer on a higher ladder rung says yes. The work here is an essay section (no code, no patch), to prove the core
assumes neither; the build-harness plug-in is tested in test_buildh_autoport.py."""
import json
import sys

import pytest

from fieldkit.lifecycle import autoaccept as core

LADDER = {"escalation": {"ladder": ["haiku", "sonnet", "opus", "fable"]}}
ON = {"enabled": True, "kinds": ["section", "skip"], "reviewer": {"unknown_author": "top"}, "ledger": "aa.jsonl"}


@pytest.fixture(autouse=True)
def _state(tmp_path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_LIFECYCLE_DIR", str(tmp_path / "lc"))


def essay_checks(text, words=(80, 400), cites=1):
    """The project's own checks on an essay section (as its YAML would list them): word count and citations."""
    n = len(text.split())
    c = text.count("(")
    return [("word count within the limit", words[0] <= n <= words[1], f"{n} words"),
            (f"at least {cites} citation(s)", c >= cites, f"{c} found")]


GOOD = "The study shows a clear effect (Smith, 2020). " * 12
BAD = "Too short and no source."


def yes(model, prompt):
    yes.asked = (model, prompt)
    return {"yes": True, "why": "the section answers the question and cites its source"}


def no(model, prompt):
    return {"yes": False, "why": "the claim is not supported by the cited source"}


def allow_all(r):
    return {"ok": True}


def pick(author, pol):
    return core.pick_reviewer(author, pol, data=LADDER, check=allow_all)


def test_an_essay_section_that_passes_its_checks_and_the_reviewer_is_accepted_and_journaled():
    rec = core.decide("section", "essay.md#method", "claude-sonnet-5-5", essay_checks(GOOD), "Is the section sound?",
                      evidence=GOOD, rewind="git checkout HEAD~1 -- essay.md", project="essay", pol=ON, asker=yes,
                      pick=pick)
    assert rec["accepted"] and rec["reviewer"] == "opus" and rec["author"] == "claude-sonnet-5-5"
    assert yes.asked[0] == "opus" and "word count within the limit: PASS" in yes.asked[1]
    rs = core.unseen(ON)
    assert [r["id"] for r in rs] == [rec["id"]] and rs[0]["rewind"].startswith("git checkout")
    core.mark_seen([rec["id"]], ON)
    assert core.unseen(ON) == []


def test_an_essay_section_failing_a_check_is_refused_and_no_reviewer_is_asked():
    def never(m, p):
        raise AssertionError("the reviewer must not be asked when a check fails")
    rec = core.decide("section", "essay.md#intro", "claude-sonnet-5-5", essay_checks(BAD), "?", pol=ON, asker=never,
                      pick=pick)
    assert not rec["accepted"] and any("word count" in w for w in rec["why"]) and not rec.get("wait_owner")


def test_the_reviewer_saying_no_is_not_accepted():
    rec = core.decide("section", "s", "claude-sonnet-5-5", essay_checks(GOOD), "?", pol=ON, asker=no, pick=pick)
    assert not rec["accepted"] and "said no" in rec["why"][0]


def test_no_checks_is_not_accepted_fail_closed():
    rec = core.decide("section", "s", "claude-sonnet-5-5", [], "?", pol=ON, asker=yes, pick=pick)
    assert not rec["accepted"] and "no deterministic check" in rec["why"][0]


def test_the_reviewer_is_never_the_authors_own_model():
    rec = core.decide("section", "s", "claude-opus-5-5", essay_checks(GOOD), "?", pol=ON, asker=yes, reviewer="opus")
    assert not rec["accepted"] and rec.get("wait_owner") and "not independent" in rec["why"][0]


def test_the_ladder_picks_the_lowest_allowed_rung_above_the_author_and_skips_a_refused_one():
    pk = core.pick_reviewer("claude-sonnet-5-5", ON, data=LADDER, check=lambda r: {"ok": r != "opus"})
    assert pk["reviewer"] == "fable" and "opus" in pk["why"]
    top = core.pick_reviewer("claude-fable-5-1", ON, data=LADDER, check=allow_all)
    assert top["reviewer"] is None, "nothing is above the top rung: the owner reviews"
    unknown = core.pick_reviewer("", ON, data=LADDER, check=allow_all)
    assert unknown["reviewer"] == "fable"
    refuse = core.pick_reviewer("", dict(ON, reviewer={"unknown_author": "refuse"}), data=LADDER, check=allow_all)
    assert refuse["reviewer"] is None


def test_policy_off_or_kind_not_listed_waits_for_the_owner():
    off = core.decide("section", "s", "claude-sonnet-5-5", essay_checks(GOOD), "?", pol=dict(ON, enabled=False),
                      asker=yes, pick=pick)
    assert not off["accepted"] and off["wait_owner"]
    other = core.decide("video-cut", "s", "claude-sonnet-5-5", essay_checks(GOOD), "?", pol=ON, asker=yes, pick=pick)
    assert not other["accepted"] and other["wait_owner"]


def test_an_unreadable_policy_turns_it_off(tmp_path):
    assert core.policy(tmp_path / "missing.yaml")["enabled"] is False


def test_the_shipped_policy_is_on_by_owner_order():
    pol = core.policy(core.DATA)
    assert pol["enabled"] is True and {"adapted", "skip", "retry"} <= set(pol["kinds"])


def test_ask_reviewer_reads_the_last_verdict_and_fails_closed(monkeypatch):
    pol = dict(ON)
    monkeypatch.setenv("FIELDKIT_REVIEWER_CMD", json.dumps([sys.executable, "-c", "print('fine\\nVERDICT: YES')"]))
    assert core.ask_reviewer("opus", "q", pol)["yes"] is True
    monkeypatch.setenv("FIELDKIT_REVIEWER_CMD", json.dumps([sys.executable, "-c",
                                                            "print('VERDICT: YES\\nno wait\\nVERDICT: NO')"]))
    assert core.ask_reviewer("opus", "q", pol)["yes"] is False
    monkeypatch.setenv("FIELDKIT_REVIEWER_CMD", json.dumps([sys.executable, "-c", "print('looks good to me')"]))
    r = core.ask_reviewer("opus", "q", pol)
    assert r["yes"] is False and "no verdict" in r["why"]
    monkeypatch.setenv("FIELDKIT_REVIEWER_CMD", json.dumps([sys.executable, "-c",
                                                            "import sys; print('VERDICT: YES'); sys.exit(1)"]))
    assert core.ask_reviewer("opus", "q", pol)["yes"] is False


def test_seen_is_refused_without_the_owner(monkeypatch, capsys):
    monkeypatch.setenv("CLAUDECODE", "1")
    rec = core.decide("skip", "s", "claude-sonnet-5-5", essay_checks(GOOD), "?", pol=ON, asker=yes, pick=pick)
    monkeypatch.setattr(core, "policy", lambda path=None: ON)
    assert core.main(["seen", "--all"]) == 1
    assert [r["id"] for r in core.unseen(ON)] == [rec["id"]]
