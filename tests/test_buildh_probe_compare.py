"""probe-compare: two outputs of the same probe, before and after a change (2026-10-07: some 40 design tokens lost
among 2,000 TOK lines; nobody finds that by eye)."""
from fieldkit import cli
from fieldkit.buildh import probe_compare as pc

BEFORE = """  probe: design-tokens.js in a copy of C:/x
  TOK-NAMES|812 from the loaded token files|0 more
  TOK|frame|--color-gray-0|#fff|#fff
  TOK|frame|--button-bg|light-dark(#eee, #222)|#eee
  TOK|settings|--focus-outline|2px solid AccentColor|2px solid AccentColor
  TOK|frame|--old-only|(unset)|(unset)
PROBE DONE in 30.1 s
"""
AFTER = """  TOK|frame|--color-gray-0|(unset)|(unset)
  TOK|frame|--button-bg|light-dark(#ddd, #222)|#ddd
  TOK|settings|--focus-outline|2px solid AccentColor|2px solid AccentColor
  TOK|frame|--new-token|4px|4px
  TOK|frame|--new-token|5px|5px
  SERVER [echo-ua 127.0.0.1:8765] GET /x
"""


def test_tokens_lost_changed_and_newly_set_are_told_apart():
    a, da = pc.load(BEFORE)
    b, db = pc.load(AFTER)
    assert ("frame", "--color-gray-0") in a and da == 0 and db == 1          # a repeated key: the last one is kept
    assert b[("frame", "--new-token")] == "5px"
    r = pc.compare(a, b)
    assert [k for k, _, _ in r["LOST"]] == [("frame", "--color-gray-0")]
    assert [(k, x, y) for k, x, y in r["changed"]] == [(("frame", "--button-bg"), "#eee", "#ddd")]
    assert [k for k, _, _ in r["now-set"]] == [("frame", "--new-token")]
    assert r["same"] == 2                                                     # focus-outline, and old-only unset both


def test_other_probe_formats_by_key_width_and_whole_value():
    a, _ = pc.load("IMG|about:home|parent|logo.png|512x512|1048576\n", prefix="IMG", keys=3, value="all")
    b, _ = pc.load("IMG|about:home|parent|logo.png|128x128|65536\n", prefix="IMG", keys=3, value="all")
    [(k, x, y)] = pc.compare(a, b)["changed"]
    assert k == ("about:home", "parent", "logo.png") and x == "512x512|1048576" and y == "128x128|65536"


def test_the_command_exits_3_when_anything_is_lost(tmp_path, capsys):
    fa, fb = tmp_path / "before.txt", tmp_path / "after.txt"
    fa.write_text(BEFORE, encoding="utf-8")
    fb.write_text(AFTER, encoding="utf-8")
    assert cli.main(["build-harness", "probe-compare", str(fa), str(fb)]) == 3
    out = capsys.readouterr().out
    assert "== LOST: 1" in out and "== changed: 1" in out and "== now-set: 1" in out and "--color-gray-0" in out
    assert cli.main(["build-harness", "probe-compare", str(fa), str(fa)]) == 0
    assert cli.main(["build-harness", "probe-compare", str(fa)]) == 2                    # one file: refused
    assert cli.main(["build-harness", "probe-compare", str(fa), str(fb), "prefix=NOPE"]) == 1   # nothing to compare
