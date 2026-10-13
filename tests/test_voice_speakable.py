"""Fixing text so a synthesiser says what you meant.

Every rule here was found on 2026-10-13 by LISTENING. Each had already shipped
past a code review, because a code review cannot hear.
"""
import pytest

from fieldkit.voice.speakable import (HOMOPHONES, SPOKEN_LIMIT, check, dehyphenate,
                                      homophones_in, speakable)


# ------------------------------------------------- finding 1: hyphens pause
def test_a_hyphen_between_letters_becomes_a_space():
    """The owner: "it can not pronounce yoshie chan when it is joined by a -.
    it sounded halted like Yohsh-eeh-eh cha". espeak-ng treats '-' as a word
    boundary, so the word is spoken as two utterances with a gap between."""
    assert dehyphenate("Yoshie-chan") == "Yoshie chan"
    assert dehyphenate("Hello Yoshie-chan. Pinko-chan speaks.") == \
        "Hello Yoshie chan. Pinko chan speaks."


def test_it_is_not_only_about_names():
    for before, after in [("dual-track", "dual track"), ("pre-commit", "pre commit"),
                          ("read-only", "read only"), ("well-known", "well known")]:
        assert dehyphenate(before) == after


def test_a_minus_sign_is_left_alone():
    """-40 dBm is a measurement, not a compound. Mangling it is a different bug."""
    assert dehyphenate("signal -40 dBm") == "signal -40 dBm"
    assert dehyphenate("RSRP was -87") == "RSRP was -87"


def test_a_command_line_flag_is_left_alone():
    assert dehyphenate("run it with --json") == "run it with --json"
    assert dehyphenate("--devices local") == "--devices local"


def test_hyphens_are_FIXED_not_merely_reported():
    """Removing a hyphen is always right and never changes meaning, so it does
    not need a human. The other two rules do."""
    fixed, problems = speakable("the dual-track docs")
    assert "-" not in fixed
    assert not any("hyphen" in p for p in problems)


# ------------------------------------- finding 2: a spoken line has no spelling
def test_the_suite_sweet_case():
    """The owner heard "the full Fieldkit suite" as "sweet", correctly, because
    that is how the word is pronounced. The synthesiser was right; the word
    was wrong."""
    assert ("suite", "sweet") in homophones_in("the full Fieldkit suite")
    _, problems = speakable("the Fieldkit suite")
    assert any("sweet" in p for p in problems)


def test_won_and_one_in_a_line_full_of_numbers():
    assert ("won", "one") in homophones_in("3 failed out of 1985. Reality won again.")


def test_homophones_are_REPORTED_not_silently_swapped():
    """Only a person can choose a different word that still means the same."""
    fixed, problems = speakable("the Fieldkit suite")
    assert "suite" in fixed, "it must not guess a replacement"
    assert problems


def test_punctuation_does_not_hide_a_homophone():
    assert homophones_in("Reality won.")
    assert homophones_in('He said "won"')


def test_the_list_is_deliberately_small():
    """A general English homophone list would be noise. These are the ones that
    destroy meaning in short, number-heavy lines."""
    assert len(HOMOPHONES) < 20
    for word, sounds_like in HOMOPHONES.items():
        assert word != sounds_like


# --------------------------- finding 3: no visible punctuation, no scrollback
def test_an_over_long_line_is_reported_with_the_count():
    long = "Running one thousand tests against reality and this goes on much too long"
    _, problems = speakable(long)
    assert any(str(len(long)) in p for p in problems)
    assert any("put any number first" in p for p in problems)


def test_a_line_inside_the_limit_is_clean():
    fixed, problems = speakable("Running 1985 tests. Put the kettle on.")
    assert problems == []
    assert fixed == "Running 1985 tests. Put the kettle on."


def test_a_spelled_out_number_is_caught():
    """88 characters with a four-digit number read longhand in the middle: the
    number was gone by the end of the sentence."""
    _, problems = speakable("Running one thousand tests")
    assert any("Use digits" in p for p in problems)
    _, clean = speakable("Running 1985 tests")
    assert not any("digits" in p for p in clean)


def test_the_limit_is_adjustable_for_a_caller_that_knows_better():
    _, problems = speakable("a" * 80, limit=100)
    assert not any("characters" in p for p in problems)


# ------------------------------------------------------------- the whole thing
def test_check_reports_hyphens_too_so_a_test_can_fail_on_them():
    """speakable() FIXES hyphens; check() reports them, for guarding source."""
    assert any("hyphen" in p for p in check("dual-track"))
    assert check("Running 1985 tests.") == []


def test_every_line_the_suite_speaks_is_clean():
    """The real guarantee: what actually ships passes its own rules."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import conftest as c
    for group in (c._STARTING, c._ALL_PASSED, c._SOME_FAILED):
        for template in group:
            line = template.format(n=1985, p=1985, f=3, t=1985)
            assert check(line) == [], f"{line}: {check(line)}"


def test_the_herald_fixes_text_before_speaking_it():
    """Wired in at say(), so every caller benefits without remembering."""
    import os
    from fieldkit.buildh import herald
    spoken = {}
    real = os.environ.get("FIELDKIT_VOICE_SILENT")
    os.environ.pop("FIELDKIT_VOICE_SILENT", None)
    try:
        def fake_run(argv, **k):
            spoken["argv"] = argv
            return type("R", (), {"returncode": 0, "stderr": b""})()

        herald.say("Hello Yoshie-chan",
                   which=lambda n: "/usr/bin/spd-say" if n == "spd-say" else None,
                   run=fake_run)
        assert "Yoshie chan" in " ".join(spoken["argv"]), spoken
        assert "Yoshie-chan" not in " ".join(spoken["argv"])
    finally:
        if real is not None:
            os.environ["FIELDKIT_VOICE_SILENT"] = real


def test_a_broken_speakable_never_stops_the_voice():
    """Speaking imperfectly beats not speaking, especially for an alert."""
    from fieldkit.buildh import herald
    import fieldkit.voice.speakable as sp
    real = sp.speakable
    sp.speakable = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    try:
        out = herald.say("the laptop is offline", which=lambda n: None, out=lambda *a, **k: None)
        assert out == "printed"
    finally:
        sp.speakable = real
