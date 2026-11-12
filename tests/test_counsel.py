"""P9 counsel: one module for every stronger-model call (advise, review, tiebreak, climb), one rung choice, one journal."""
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from fieldkit.lifecycle import counsel, models

ROOT = Path(__file__).resolve().parents[1]
DATA = {"escalation": {"enabled": True, "ladder": ["haiku", "sonnet", "opus", "fable"], "own_limit": 2},
        "availability": {"cooldown_minutes": 120, "no_verdict_cooldown_minutes": 30, "patterns": ["usage limit"]},
        "cheap": ["sonnet", "haiku"], "review": ["opus"], "premium": ["fable"], "default": "sonnet", "ledger": "l.jsonl",
        "counsel": {"advisor": {"enabled": True, "never": ["haiku"]}, "journal": "counsel.jsonl"}}
ALL = lambda r: {"ok": True}                                     # noqa: E731
NO_FABLE = lambda r: {"ok": r != "fable"}                        # noqa: E731


@pytest.fixture(autouse=True)
def _state(tmp_path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_LIFECYCLE_DIR", str(tmp_path / "lc"))
    monkeypatch.delenv("CLAUDE_CODE_DISABLE_ADVISOR_TOOL", raising=False)
    monkeypatch.delenv("FIELDKIT_REVIEWER", raising=False)


# ---- the advisor rung -------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("worker,check,want", [
    ("claude-sonnet-5-5", ALL, "opus"),          # the NEXT rung above, not the top
    ("haiku", ALL, "sonnet"),                    # Haiku calls an advisor (Sonnet) ...
    ("claude-opus-5-5", ALL, "fable"),
    ("claude-opus-5-5", NO_FABLE, None),         # Fable without a mandate: no advisor, never an error
    ("fable", ALL, None),                        # the top rung has nothing above
    ("qwen-7b", ALL, None),                      # a worker off the ladder gets no guess
])
def test_the_advisor_is_the_next_usable_rung_above_the_worker(worker, check, want):
    assert counsel.advisor_for(worker, DATA, check, env={})["advisor"] == want


def test_the_advisor_is_never_below_or_equal_to_the_worker():
    rungs = DATA["escalation"]["ladder"]
    for i, w in enumerate(rungs):
        a = counsel.advisor_for(w, DATA, ALL, env={})["advisor"]
        assert a is None or rungs.index(a) > i, (w, a)


def test_haiku_is_never_an_advisor_even_when_it_is_the_rung_above():
    d = dict(DATA, escalation=dict(DATA["escalation"], ladder=["local", "haiku", "sonnet"]))
    r = counsel.advisor_for("local", d, ALL, env={})
    assert r["advisor"] is None and "never" in r["why"]


def test_a_worker_off_the_ladder_gets_no_advisor_even_with_no_never_list():
    d = dict(DATA, counsel={"advisor": {"enabled": True, "never": []}})
    assert counsel.advisor_for("qwen-7b", d, ALL, env={})["advisor"] is None
    assert counsel.advisor_for("", d, ALL, env={})["advisor"] is None


def test_an_unavailable_rung_is_skipped_for_the_advisor(monkeypatch):
    monkeypatch.setattr(models, "availability", lambda r, now=None, data=None: (r != "opus", "opus unavailable"))
    assert counsel.advisor_for("sonnet", DATA, ALL, env={})["advisor"] == "fable"


@pytest.mark.parametrize("env,data", [({"CLAUDE_CODE_DISABLE_ADVISOR_TOOL": "1"}, DATA), ({"FIELDKIT_REVIEWER": "1"}, DATA),
                                      ({}, dict(DATA, counsel={"advisor": {"enabled": False}}))])
def test_the_advisor_can_be_turned_off(env, data):
    assert counsel.advisor_for("sonnet", data, ALL, env=env)["advisor"] is None


def test_launch_args_put_the_advisor_before_the_model_and_respect_one_already_given():
    argv, adv, _ = counsel.launch_args(["claude", "-p", "--model", "sonnet"], env={}, data=DATA, check=ALL)
    assert argv == ["claude", "-p", "--advisor", "opus", "--model", "sonnet"] and adv == "opus"
    own = ["claude", "-p", "--advisor", "fable", "--model", "sonnet"]
    assert counsel.launch_args(own, env={}, data=DATA, check=ALL)[:2] == (own, None)


def test_one_rung_choice_review_and_advisor_agree():
    """The advisor and the reviewer take the same walk (pick how="next"): the same rung for the same author."""
    pol = {"reviewer": {"unknown_author": "top"}}
    for w in ("haiku", "sonnet", "claude-opus-5-5"):
        a = counsel.advisor_for(w, DATA, NO_FABLE, env={})["advisor"]
        r = counsel.pick_reviewer(w, pol, DATA, NO_FABLE)
        if a:
            assert r["reviewer"] == a and not r.get("same_rung")
        else:
            assert r.get("same_rung") or r.get("reauthor") or not r["reviewer"]


def test_the_tiebreaker_is_the_highest_usable_rung():
    assert counsel.pick_tiebreaker("haiku", data=DATA, check=ALL)["reviewer"] == "fable"
    assert counsel.pick_tiebreaker("haiku", data=DATA, check=NO_FABLE)["reviewer"] == "opus"


# ---- the stream log and a refused flag ----------------------------------------------------------------------------------
ADVISOR_LINE = json.dumps({"type": "assistant", "message": {"content": [
    {"type": "server_tool_use", "id": "srvtoolu_1", "name": "advisor", "input": {}}]}}, separators=(",", ":"))


def test_advisor_calls_are_counted_from_the_stream_log():
    other = json.dumps({"type": "assistant", "message": {"content": [{"type": "server_tool_use", "name": "web_search"},
                                                                      {"type": "tool_use", "name": "advisor"}]}})
    assert counsel.advisor_calls("\n".join([ADVISOR_LINE, other, ADVISOR_LINE, "not json advisor server_tool_use"])) == 2
    assert counsel.advisor_calls("") == 0


def test_advisor_rejected_only_when_the_run_died_on_the_flag():
    assert counsel.advisor_rejected(1, "", "error: unknown option '--advisor'")
    assert not counsel.advisor_rejected(0, "", "advisor")
    assert not counsel.advisor_rejected(1, '{"type":"result","subtype":"error"} advisor', "")
    assert not counsel.advisor_rejected(1, "", "some other failure")


def _fake_contain(monkeypatch, outcomes):
    from fieldkit.autopilot import run
    from fieldkit.core import contain
    seen = []
    monkeypatch.setattr(run, "claude_cmd", lambda: ["claude"])

    def fake(args, **kw):
        seen.append(list(args))
        rc, out, err = outcomes[len(seen) - 1]
        return SimpleNamespace(rc=rc, out=out, err=err, timed_out=False, stalled=False, tail_file=None, leftovers=None,
                               why="")
    monkeypatch.setattr(contain, "run", fake)
    monkeypatch.setattr(counsel, "advisor_for", lambda w, data=None, check=None, env=None:
                        {"advisor": "opus", "why": "test"})
    return run, seen


# I10 (2026-11-11): the headless launcher calls counsel.launch_args; these two ran skipped until it did.
def test_a_worker_launch_carries_the_advisor_and_journals_its_calls(monkeypatch):
    run, seen = _fake_contain(monkeypatch, [(0, ADVISOR_LINE + "\n" + '{"type":"result","result":"ok"}', "")])
    r = run.claude("hi")
    assert "--advisor" in seen[0] and seen[0][seen[0].index("--advisor") + 1] == "opus"
    assert r["advisor"] == "opus" and r["advisor_calls"] == 1
    row = counsel.rows()[-1]
    assert row["path"] == "advise" and row["model"] == "opus" and row["calls"] == 1 and row["author"] == "sonnet"


def test_a_refused_advisor_flag_is_retried_once_without_it(monkeypatch):
    run, seen = _fake_contain(monkeypatch, [(1, "", "error: unknown option '--advisor'"),
                                            (0, '{"type":"result","result":"ok"}', "")])
    r = run.claude("hi")
    assert len(seen) == 2 and "--advisor" in seen[0] and "--advisor" not in seen[1]
    assert r["ok"] and r["advisor"] is None
    assert [x["outcome"] for x in counsel.rows()] == ["rejected", "off"]


def test_every_launch_the_lint_allows_carries_the_advisor():
    """I10: each headless launcher in LAUNCH_OK (but the guard itself) goes through counsel's advisor path."""
    for rel in LAUNCH_OK:
        if rel != "fieldkit/lifecycle/models.py":
            txt = (ROOT / rel).read_text(encoding="utf-8")
            assert "counsel.launch_args" in txt or "counsel.run_advised" in txt, rel


def test_run_advised_adds_the_advisor_retries_once_without_it_and_journals(monkeypatch):
    monkeypatch.setattr(counsel, "advisor_for", lambda w, data=None, check=None, env=None: {"advisor": "opus", "why": "t"})
    seen = []

    def run(a):
        seen.append(list(a))
        return (1, "", "error: unknown option '--advisor'") if len(seen) == 1 else (0, "fine", "")
    assert counsel.run_advised(["claude", "-p", "--model", "sonnet"], {}, run) == (0, "fine", "")
    assert seen == [["claude", "-p", "--advisor", "opus", "--model", "sonnet"], ["claude", "-p", "--model", "sonnet"]]
    assert [(x["outcome"], x["author"]) for x in counsel.rows()] == [("rejected", "sonnet"), ("off", "sonnet")]
    seen.clear()
    assert counsel.run_advised(["claude", "-p", "--model", "sonnet"], {}, lambda a: seen.append(a) or (0, ADVISOR_LINE, ""))[0] == 0
    assert len(seen) == 1 and counsel.rows()[-1]["outcome"] == "on" and counsel.rows()[-1]["calls"] == 1


def test_drop_advisor_removes_both_spellings():
    assert counsel.drop_advisor(["c", "--advisor", "opus", "--advisor=x", "--model", "s"]) == ["c", "--model", "s"]


def test_the_reviewer_never_gets_an_advisor_and_is_journaled():
    got = {}

    def fake_run(cmd, **kw):
        got.update(cmd=cmd, env=kw["env"])
        return SimpleNamespace(returncode=0, stdout="fine\nVERDICT: YES", stderr="")
    pol = {"reviewer": {"cmd": ["claude", "-p", "--model", "{model}"]}}
    ans = counsel.ask("opus", "q", "review", pol, fake_run)
    assert ans["yes"] and "--advisor" not in got["cmd"] and got["env"]["CLAUDE_CODE_DISABLE_ADVISOR_TOOL"] == "1"
    assert counsel.rows()[-1]["path"] == "review" and counsel.rows()[-1]["outcome"] == "yes"


def test_one_journal_feeds_per_job_and_the_review(monkeypatch):
    counsel.journal("advise", model="opus", author="sonnet", outcome="on", job="T-1", calls=3)
    counsel.journal("review", model="opus", outcome="no", job="T-1")
    counsel.journal("climb", model="opus", author="sonnet", outcome="climbed", job="T-1")
    j = {r["job"]: r for r in counsel.per_job()}["T-1"]
    assert (j["advisor"], j["advisor_calls"], j["reviews"], j["climbs"]) == ("opus", 3, 1, 1)
    ls = counsel.lines()
    assert ls[1].split()[0] == "advise" and "3 time(s)" in ls[1]
    from fieldkit import review
    assert review.counsel_lines() == ls


# ---- lint: no second place to ask a stronger model ----------------------------------------------------------------------
# Every existing place, with why it may stay. A new entry needs a reason that is not "a stronger model".
LAUNCH_OK = {
    "fieldkit/lifecycle/models.py": "defines guard_launch",
    "fieldkit/autopilot/run.py": "THE headless worker launcher; it calls counsel.launch_args",
    "fieldkit/capture/recipes.py": "one cheap-tier naming call (Sonnet, --tools ''); it goes through counsel.run_advised",
}
RUNG_OK = {
    "fieldkit/lifecycle/counsel.py": "the one place",
    "fieldkit/lifecycle/models.py": "next_model / climb are thin calls into counsel; record_rung writes the journal",
    "fieldkit/lifecycle/autoaccept.py": "_rung / pick_reviewer / pick_tiebreaker are thin calls into counsel",
    "fieldkit/lifecycle/routing.py": "_rung_of reads the owner's routing ceiling (a start rung, not a stronger model)",
    "fieldkit/method/strikes.py": "_rung journals a strike-driven rung change; the choice is models.climb",
    "fieldkit/peers/link_heal.py": "rung() is a link-repair step, nothing to do with models",
}
PICKER = re.compile(r"^def\s+(\w*rung\w*|climb\w*|next_model\w*|pick_\w*(?:model|reviewer|tiebreak|advisor)\w*|"
                    r"\w*advisor\w*)\s*\(", re.M)
CLAUDE_MODEL = re.compile(r"""["']claude["']\s*,\s*["']-p["'][^\]\n]*["']--model""")


def _py():
    for p in sorted((ROOT / "fieldkit").rglob("*.py")):
        yield p.relative_to(ROOT).as_posix(), p.read_text(encoding="utf-8", errors="replace")


def test_no_new_claude_model_launch_outside_counsel():
    bad = []
    for rel, txt in _py():
        if rel == "fieldkit/lifecycle/counsel.py":
            continue
        if (CLAUDE_MODEL.search(txt) or "guard_launch(" in txt) and rel not in LAUNCH_OK:
            bad.append(rel)
    assert not bad, f"a new `claude -p --model` launch outside lifecycle/counsel.py: {bad}"


def test_no_new_rung_picking_function_outside_counsel():
    bad = [f"{rel}: {m.group(1)}" for rel, txt in _py() for m in PICKER.finditer(txt) if rel not in RUNG_OK]
    assert not bad, f"a new rung-picking function outside lifecycle/counsel.py: {bad}"


def test_the_lint_can_fail():
    assert CLAUDE_MODEL.search('argv = ["claude", "-p", "--model", m]')
    assert PICKER.search("def pick_stronger_model(x):\n") and PICKER.search("def choose_rung(x):\n")
