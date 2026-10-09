"""tab outline (2026-10-09, D-157-37): the installed browser outlines every inactive tab in cyan, and no rule changes
it under the mouse; a rule scan that saw nothing proves nothing."""
from fieldkit.buildh import tabborders as tb

RULE = ("TAB|rule|master-redirect.css|.tabbrowser-tab:not([selected]) .tab-background { background-color: transparent "
        "!important; outline: rgb(0, 255, 255) solid 1px !important; }")
GOOD = ["TAB|inactive|outline-style|solid", "TAB|inactive|outline-width|1px", "TAB|inactive|outline-color|rgb(0, 255, 255)",
        RULE, "TAB|sheets|walked|93"]


def test_the_build_as_shipped_passes():
    rows = tb.judge(*tb.parse(GOOD))
    assert all(r["ok"] for r in rows), rows
    assert "93 sheets" in rows[1]["evidence"]


def test_a_missing_or_transparent_outline_fails():
    for bad in (["TAB|inactive|outline-style|none"], ["TAB|inactive|outline-color|rgba(0, 0, 0, 0)"],
                ["TAB|inactive|outline-width|0px"]):
        lines = [l for l in GOOD if l.split("|")[2] != bad[0].split("|")[2]] + bad
        assert not tb.judge(*tb.parse(lines))[0]["ok"], bad


def test_a_hover_outline_fails_and_a_blind_scan_fails():
    hover = "TAB|rule|master-redirect.css|.tabbrowser-tab:not([selected]):hover .tab-background { outline: cyan solid 1px; }"
    assert not tb.judge(*tb.parse(GOOD + [hover]))[1]["ok"]
    blind = [l for l in GOOD if "|rule|" not in l]
    row = tb.judge(*tb.parse(blind))[1]
    assert not row["ok"] and "blind" in row["evidence"]


def test_no_probe_output_fails_closed():
    assert not tb.judge(*tb.parse([]))[0]["ok"]
    assert tb.cyan("rgb(0, 255, 255)") and not tb.cyan("rgb(255, 192, 203)") and not tb.cyan("rgba(0, 255, 255, 0)")
