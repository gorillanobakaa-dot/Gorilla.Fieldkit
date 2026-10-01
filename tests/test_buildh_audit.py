"""The audit's journal rules: each one is a thing the overnight run of 2026-10-01 did."""
from fieldkit.buildh import audit


def verdicts(ev):
    return {n: ok for n, ok, _ in audit.journal_checks(ev)}


def test_a_clean_journal_passes_every_rule():
    ev = [{"event": "auto-done", "step": "a", "notes": ["transplanted 2 lines"]},
          {"event": "script-done", "step": "final-checks"}, {"event": "done"}]
    assert all(verdicts(ev).values())


def test_copying_the_old_source_as_an_answer_key_is_caught():
    ev = [{"event": "auto-done", "step": "port-x", "notes": ["copied answer key from Gorilla.firefox/src"]}]
    assert not verdicts(ev)["journal: no answer key copied into the new source"]


def test_a_skipped_step_is_caught():
    ev = [{"event": "unblock", "step": "port-x", "how": "skip"}]
    assert not verdicts(ev)["journal: no step skipped"]
    assert verdicts([{"event": "unblock", "step": "port-x", "how": "retry"}])["journal: no step skipped"]


def test_done_while_final_checks_were_failing_is_caught_but_a_later_pass_clears_it():
    bad = [{"event": "script-failed", "step": "final-checks"}, {"event": "done"}]
    assert not verdicts(bad)["journal: never 'done' while the final checks were failing"]
    ok = [{"event": "script-failed", "step": "final-checks"}, {"event": "script-done", "step": "final-checks"},
          {"event": "done"}]
    assert verdicts(ok)["journal: never 'done' while the final checks were failing"]
