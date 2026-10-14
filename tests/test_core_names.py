"""Hostile network names: what they break, and the promise that nothing crashes.

Written 2026-10-13 during a muster that found this module shipped with no tests
at all. Its whole job is to survive input chosen by somebody else, so an
untested version of it is a contradiction.

The worked example throughout is the owner's own SSID, `:(`, which is three
characters attacking three different layers: a field separator, and two shell
and regex metacharacters.
"""
import pytest

from fieldkit.core import names


# ----------------------------------------------------------- never raises
@pytest.mark.parametrize("value", [
    None, "", " ", "HomeWiFi", ":(", "_|_", "a:b", "x;rm -rf ~", "-starts-with-dash",
    "trailing ", "\x07bell", "quote'd", 'double"d', "back\\slash", "💀", "a" * 300,
])
def test_classify_never_raises_and_always_returns_the_same_keys(value):
    r = names.classify(value)
    assert set(r) == {"name", "problems", "safe", "worst", "advice"}
    assert isinstance(r["problems"], list)
    assert isinstance(r["safe"], bool)


def test_an_ordinary_name_is_clean():
    r = names.classify("HomeWiFi")
    assert r["safe"] and not r["problems"] and r["advice"] == ""


# ------------------------------------------------- the owner's own SSID
def test_the_colon_is_reported_as_a_field_separator():
    r = names.classify(":(")
    assert not r["safe"]
    joined = " ".join(r["problems"])
    assert "separate fields" in joined
    assert "shift along" in joined


def test_the_brackets_are_reported_as_an_instruction():
    r = names.classify(":(")
    assert r["worst"] == "instruction", "a shell metacharacter outranks a separator"
    assert "becomes a command" in " ".join(r["problems"])
    assert "own argv element" in r["advice"]


def test_the_pipe_is_the_dangerous_one():
    r = names.classify("_|_")
    assert r["worst"] == "instruction"
    assert not names.safe_for_shell("_|_")


# --------------------------------------------------------- other traps
def test_a_leading_dash_is_read_as_an_option():
    assert "reads it as an option" in " ".join(names.classify("-rf").problems
                                               if hasattr(names.classify("-rf"), "problems")
                                               else names.classify("-rf")["problems"])


def test_invisible_whitespace_is_caught():
    assert any("whitespace" in p for p in names.classify("home ")["problems"])


def test_a_control_character_is_treated_as_an_instruction():
    r = names.classify("evil\x1b[2Kname")
    assert r["worst"] == "instruction"
    assert any("control character" in p for p in r["problems"])


def test_a_backslash_is_flagged_because_it_is_also_the_escape_marker():
    r = names.classify("back\\slash")
    assert any("backslash" in p for p in r["problems"])


def test_empty_and_none_are_problems_not_silent_passes():
    for v in (None, ""):
        r = names.classify(v)
        assert not r["safe"] and r["problems"]


# ------------------------------------------------------- the actual fixes
def test_unescape_undoes_what_a_tool_escaped():
    assert names.unescape(r"we\:ird") == "we:ird"
    assert names.unescape(r"back\\slash") == "back\\slash".replace("\\\\", "\\")


def test_split_from_right_keeps_the_name_whole():
    """The bug this exists for: splitting left to right shifts every field.

    It takes the count of TRAILING fixed fields and returns (name, fields),
    because the name is the only part whose length is unknown.
    """
    line = r"\:(:13:2472 MHz:100:WPA2"
    name, fields = names.split_from_right(line, 4)
    assert name == r"\:("
    assert fields == ["13", "2472 MHz", "100", "WPA2"]
    assert names.unescape(name) == ":("


def test_split_from_right_ignores_an_ESCAPED_separator():
    name, fields = names.split_from_right(r"we\:ird:6:2437", 2)
    assert name == r"we\:ird", "the escaped colon belongs to the name"
    assert fields == ["6", "2437"]


def test_split_from_right_refuses_a_line_that_is_too_short():
    name, fields = names.split_from_right("only:two", 5)
    assert name is None and fields == []


def test_a_name_made_ENTIRELY_of_separators_still_parses():
    name, fields = names.split_from_right(r"\:\:\::6:2437", 2)
    assert names.unescape(name) == ":::"
    assert fields == ["6", "2437"]


def test_safe_for_shell_refuses_rather_than_hoping():
    assert names.safe_for_shell("HomeWiFi")
    for bad in (":(", "_|_", "a;b", "a`b", "a$b", "a'b", 'a"b', ""):
        assert not names.safe_for_shell(bad), bad


def test_advice_is_given_for_every_severity_it_can_report():
    for worst in ("separator", "instruction", "escaping"):
        assert names.advice(worst), worst
    assert names.advice(None) == ""
