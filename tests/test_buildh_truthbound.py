"""Every port change must be explained by the 155 truth delta or by a recorded step."""
from fieldkit.buildh import truthbound as tb

P155 = ["a", "b", "c"]
T155 = ["a", "b-gorilla", "c"]           # the owner changed b
P157 = ["a", "b", "c", "d"]


def test_no_truth_delta_but_changed_is_unexplained_unless_stepped():
    assert tb.judge("x.js", P157, ["a", "b", "c"], P155, P155, set()) == ("no-truth", 1, 0)
    assert tb.judge("x.js", P157, ["a", "b", "c"], P155, P155, {"x.js"}) is None


def test_within_truth_passes_and_beyond_truth_is_unexplained():
    assert tb.judge("x.js", P157, ["a", "b-gorilla", "c", "d"], P155, T155, set()) is None
    now = ["a", "b-gorilla", "c"] + ["x%d();" % i for i in range(5)]
    assert tb.judge("x.js", P157, now, P155, T155, set()) == ("beyond", 1, 5)
    assert tb.judge("x.ftl", P157, now, P155, T155, set()) is None     # Fluent is judged by its own rows
    assert tb.judge("x.js", P157, now, P155, T155, {"x.js"}) is None


def test_missing_sides_are_not_judged():
    assert tb.judge("x.js", None, ["a"], P155, T155, set()) is None
    assert tb.judge("x.js", P157, ["a"], None, None, set()) is None
