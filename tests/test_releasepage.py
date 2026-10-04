"""The release page: both tracks on the page, plain language first, no link away.

Each case below is a page that a writer really produced or would produce by
copying the last one. The first is the page that was published on 2026-10-04.
"""
import json

from fieldkit import cli
from fieldkit.core import settings
from fieldkit.gdocs import releasepage as RP

PARSER = cli.build_parser()

LAYMAN = """# Product v1.2.3 - A plain title

**Date:** 2026-01-01
**Previous version:** v1.2.2

---

## Why This Release Exists

Think of it as a receipt from a till: the shopkeeper does not write it by hand.

## How to Install

**Step 1:** Close the program.

```
# this line starts with a hash and is NOT a heading
product install
```
"""

DEVELOPER = """# v1.2.3: technical title

## Summary

Root cause, fix, scope.
"""

OPENING = """# Product 1.2.3

**One line.**

## What is this program?

A program you talk to by typing.

## Should you download this version?

Yes if you use scripts. No need otherwise.

## Why this matters to you

A till prints a receipt.
"""


def test_the_page_that_was_published_is_refused():
    published = ("# Product 1.2.3\n\n## Why this release exists\n\nA summary.\n\n## Privacy\n\nNone.\n\n"
                 "## Full notes\n\n- [`v1.2.3-release-notes.layman.md`](Changelogs/v1.2.3-release-notes.layman.md) "
                 "— plain English\n")
    findings = RP.check(published, LAYMAN, DEVELOPER)
    text = "\n".join(findings)
    for want in ("plain-language track is not on the page", "developer track is not on the page",
                 "Full notes", "sends the reader to the notes file", "whether to download it",
                 "why it matters", "what it is"):
        assert want in text, want


def test_a_composed_page_passes_and_keeps_the_order():
    page = RP.compose(OPENING, LAYMAN, DEVELOPER, extra="# What the program printed\n\n```\nreal output\n```")
    assert RP.check(page, LAYMAN, DEVELOPER) == []
    order = [page.index(s) for s in ("## What is this program?", "## Should you download", "## Why this matters",
                                     "# In plain language", "### Why This Release Exists",
                                     "# What the program printed", "# For developers", "### Summary")]
    assert order == sorted(order), "the page is not in the fixed order"
    # One title: the tracks' own title blocks are dropped.
    assert "A plain title" not in page and "technical title" not in page
    # A '#' line inside a code block is a comment, not a heading, and is left alone.
    assert "\n# this line starts with a hash and is NOT a heading\n" in page


def test_an_opening_that_does_not_say_whether_to_bother_is_refused():
    opening = OPENING.replace("## Should you download this version?", "## Notes")
    findings = RP.check(RP.compose(opening, LAYMAN, DEVELOPER), LAYMAN, DEVELOPER)
    assert any("whether to download it" in f for f in findings)


def test_what_the_program_is_must_open_the_page():
    late = "# Product\n\n" + "filler line\n" * 60 + OPENING.split("\n", 1)[1]
    findings = RP.check(RP.compose(late, LAYMAN, DEVELOPER), LAYMAN, DEVELOPER)
    assert any("within the first" in f for f in findings)


def test_a_track_cut_short_is_not_the_track():
    page = RP.compose(OPENING, LAYMAN, DEVELOPER).replace("the shopkeeper does not write it by hand", "(see the notes)")
    assert any("plain-language track is not on the page in full" in f for f in RP.check(page, LAYMAN, DEVELOPER))


def test_relative_links_and_images_are_refused_and_absolute_ones_are_not():
    page = RP.compose(OPENING + "\n[guide](docs/GUIDE.md)\n\n![a picture](shots/a.png)\n"
                      "\n[site](https://example.org/x) [top](#install)\n", LAYMAN, DEVELOPER)
    findings = RP.check(page, LAYMAN, DEVELOPER)
    assert sum("is relative" in f for f in findings) == 2
    assert not any("example.org" in f or "#install" in f for f in findings)


def test_developer_part_before_plain_language_is_refused():
    page = RP.compose(OPENING, LAYMAN, DEVELOPER)
    a, b = page.index("# In plain language"), page.index("# For developers")
    swapped = page[:a] + page[b:] + "\n\n" + page[a:b]
    assert any("comes before" in f for f in RP.check(swapped))


def test_compose_command_writes_only_a_page_that_passes(tmp_path, capsys):
    files = {}
    for name, text in (("opening", OPENING), ("layman", LAYMAN), ("developer", DEVELOPER)):
        files[name] = tmp_path / f"{name}.md"
        files[name].write_text(text, encoding="utf-8")
    out = tmp_path / "page.md"
    args = PARSER.parse_args(["release-page", "compose", "--opening", str(files["opening"]),
                              "--layman", str(files["layman"]), "--developer", str(files["developer"]),
                              "--out", str(out)])
    assert args.fn(args) == 0 and out.is_file()
    assert "NEXT:" in capsys.readouterr().out

    args = PARSER.parse_args(["release-page", "check", str(out), "--layman", str(files["layman"]),
                              "--developer", str(files["developer"]), "--json"])
    assert args.fn(args) == 0
    assert json.loads(capsys.readouterr().out)["findings"] == []

    # A bad opening: nothing is written, every reason is printed, exit 3.
    files["opening"].write_text("# Product\n\nA changelog.\n", encoding="utf-8")
    bad = tmp_path / "bad.md"
    args = PARSER.parse_args(["release-page", "compose", "--opening", str(files["opening"]),
                              "--layman", str(files["layman"]), "--developer", str(files["developer"]),
                              "--out", str(bad)])
    assert args.fn(args) == 3 and not bad.exists()
    printed = capsys.readouterr().out
    assert printed.count("REFUSED:") >= 3 and "NEXT:" in printed


def test_the_philosophy_ships_with_the_package_and_is_printed(capsys):
    assert RP.PHILOSOPHY == settings.ROOT / "fieldkit" / "gdocs" / "PHILOSOPHY.md"
    text = RP.philosophy_text()
    for line in ("Open source gave the world the recipe. It forgot to teach people how to cook.",
                 "Education is not optional. It is the product.",
                 "No one should have to trust a summary they cannot verify"):
        assert line in text
    args = PARSER.parse_args(["docs", "philosophy"])
    assert args.fn(args) == 0
    out = capsys.readouterr().out
    assert "Gorilla Open Source Philosophy" in out and "NEXT: fieldkit docs guide" in out
