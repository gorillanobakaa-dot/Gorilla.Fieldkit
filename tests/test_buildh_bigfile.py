"""A data file with thousands of hand steps (2026-10-09: the refreshed HSTS list, 170,000 lines, 2,441 steps) is
verified without redoing per-file work for every step: the per-file facts are built once, the line key is remembered,
and the step check (hand_port_holds) runs no whole-file diff it would throw away."""
import difflib

from fieldkit.buildh import firefox


def _world(n=4000, steps=60):
    before = [f"host{i}.example, 1" for i in range(n)]
    after = list(before)
    hunks = []
    for s in range(steps):
        i = 10 + s * (n // steps)
        after[i] = f"new{s}.example, 1"
        hunks.append({"header": f"@@ -{i - 2},5 +{i - 2},5 @@",
                      "lines": [" " + before[i - 2], " " + before[i - 1], "-" + before[i], "+" + after[i],
                                " " + before[i + 1], " " + before[i + 2]]})
    return before, after, hunks


def test_every_step_holds_and_no_whole_file_diff_runs(monkeypatch):
    before, after, hunks = _world()

    def no_diff(*a, **k):
        raise AssertionError("hand_port_holds ran a whole-file diff it throws away")
    monkeypatch.setattr(difflib, "SequenceMatcher", no_diff)
    firefox._FACTS.clear()
    assert all(not firefox.hand_port_holds(after, h, before, ()) for h in hunks)
    assert len(firefox._FACTS) <= 4                     # per file, not per step


def test_a_step_that_did_not_happen_is_still_caught():
    before, after, hunks = _world(steps=5)
    after[10] = before[10]                               # the first change undone
    assert firefox.hand_port_holds(after, hunks[0], before, ())
    assert not firefox.hand_port_holds(after, hunks[1], before, ())
