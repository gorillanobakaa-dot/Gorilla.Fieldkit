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


NAME = "journal: no step skipped without a reason"


HARNESS_SKIP = {"event": "auto-accept", "step": "port-x", "kind": "skip", "reviewer": "opus", "why": ["checks passed"]}


def test_a_harness_skip_with_no_reviewer_evidence_still_fails():
    assert not verdicts([dict(HARNESS_SKIP, reviewer=None)])[NAME]
    assert not verdicts([dict(HARNESS_SKIP, why=[])])[NAME]
    assert not verdicts([dict(HARNESS_SKIP, why=["  "])])[NAME]
    assert verdicts([HARNESS_SKIP])[NAME]


def test_an_owner_skip_needs_no_note_the_mandate_is_the_reason():
    # owner, 2026-11-11: "One single approval ... it's all the reason, all the motives you ever need"
    bare = [{"event": "unblock", "step": "port-x", "how": "skip"}]
    assert verdicts(bare)[NAME]
    assert verdicts([{"event": "unblock", "step": "port-x", "how": "skip", "note": "  "}])[NAME]
    assert verdicts([{"event": "unblock", "step": "port-x", "how": "retry"}])[NAME]
    ev = [{"event": "unblock", "step": "p", "how": "skip", "by": "owner", "mandate": "owner-wide"},
          dict(HARNESS_SKIP, step="q")]
    evidence = [e for n, _ok, e in audit.journal_checks(ev) if n == NAME][0]
    assert evidence == "1 skipped by the owner (mandate owner-wide), 1 by the harness with a reviewer"


def test_the_real_160_linux_owner_skip_passes_as_journaled():
    # firefox-160-linux journal line 965 (2026-11-11 12:42:12), byte for byte: no note, no `by`
    ev = [{"t": "2026-11-11 12:42:12", "task": "firefox-160-linux", "event": "unblock", "prev": "f28cb046a8904c24",
           "step": "owner-21.PORT.FIXES.157-028-branding-about-window-logo-rebuilt-from-the-1200px-master-wi",
           "how": "skip"},
          {"t": "2026-11-11 12:42:33", "task": "firefox-160-linux", "event": "unblock", "prev": "28959c601fa550d4",
           "step": "final-checks", "how": "retry"}]
    assert verdicts(ev)[NAME]


def test_a_repeat_is_not_counted_twice_and_the_latest_decision_of_a_step_decides():
    ev = [{"event": "unblock", "step": "port-x", "how": "skip"}] * 3
    evidence = [e for n, _ok, e in audit.journal_checks(ev) if n == NAME][0]
    assert evidence.startswith("1 skipped by the owner")
    harness_then_owner = [dict(HARNESS_SKIP, step="p", reviewer=None), {"event": "unblock", "step": "p", "how": "skip"}]
    assert verdicts(harness_then_owner)[NAME]
    assert not verdicts(harness_then_owner[::-1])[NAME]


def test_done_while_final_checks_were_failing_is_caught_but_a_later_pass_clears_it():
    bad = [{"event": "script-failed", "step": "final-checks"}, {"event": "done"}]
    assert not verdicts(bad)["journal: never 'done' while the final checks were failing"]
    ok = [{"event": "script-failed", "step": "final-checks"}, {"event": "script-done", "step": "final-checks"},
          {"event": "done"}]
    assert verdicts(ok)["journal: never 'done' while the final checks were failing"]


# -- the journal's hash chain (a supervisor must not be able to tidy history) -------------------

import json
import pytest
from fieldkit.buildh import task


@pytest.fixture
def jtask(tmp_path, monkeypatch):
    monkeypatch.setattr(task, "STATE", tmp_path / "state")
    (tmp_path / "state" / "j1").mkdir(parents=True)
    t = {"id": "j1"}
    for i in range(5):
        task.journal(t, "step", n=i)
    return t, tmp_path / "state" / "j1" / "journal.jsonl"


def test_an_untouched_journal_verifies(jtask):
    t, p = jtask
    problems, count, head = task.verify_journal("j1")
    assert problems == [] and count == 5


def test_editing_a_line_breaks_the_chain(jtask):
    t, p = jtask
    lines = p.read_text(encoding="utf-8").splitlines()
    lines[1] = lines[1].replace('"n": 1', '"n": 99')
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    problems, _, _ = task.verify_journal("j1")
    assert problems and "chain broken" in problems[0]


def test_deleting_or_reordering_a_line_breaks_the_chain(jtask):
    t, p = jtask
    lines = p.read_text(encoding="utf-8").splitlines()
    p.write_text("\n".join(lines[:2] + lines[3:]) + "\n", encoding="utf-8")
    assert task.verify_journal("j1")[0]
    p.write_text("\n".join([lines[0], lines[2], lines[1]] + lines[3:]) + "\n", encoding="utf-8")
    assert task.verify_journal("j1")[0]


def test_a_legacy_journal_without_hashes_is_accepted_and_the_chain_continues_from_it(tmp_path, monkeypatch):
    monkeypatch.setattr(task, "STATE", tmp_path / "state")
    d = tmp_path / "state" / "old"
    d.mkdir(parents=True)
    (d / "journal.jsonl").write_text(json.dumps({"event": "start"}) + "\n" + json.dumps({"event": "x"}) + "\n", encoding="utf-8")
    task.journal({"id": "old"}, "new")
    task.journal({"id": "old"}, "newer")
    assert task.verify_journal("old")[0] == []


def test_a_rewrite_that_re_chains_everything_still_fails_against_the_anchor(jtask):
    t, p = jtask
    rows, anchor = audit.chain_checks("j1")
    assert all(ok for _, ok, _ in rows)
    anchors = {"j1": [anchor]}
    # the tidy-up: change line 2 and rebuild the whole chain so it is self-consistent
    ev = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()]
    ev[1]["n"] = 99
    out, prev = [], task.ZERO
    for e in ev:
        e["prev"] = prev
        line = json.dumps(e)
        out.append(line)
        prev = task.line_hash(line)
    p.write_text("\n".join(out) + "\n", encoding="utf-8")
    assert task.verify_journal("j1")[0] == []                       # self-consistent...
    rows, _ = audit.chain_checks("j1", anchors)
    assert not dict((n, ok) for n, ok, _ in rows)["journal: earlier history unchanged since the last audits"]
