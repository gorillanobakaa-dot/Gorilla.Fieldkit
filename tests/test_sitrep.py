"""The sitrep: a report a reader can take in a few seconds at a time, checked against written rules.

Every rule in rules.yaml has a test that breaks it on purpose and sees the check turn red, so a rule that
stops being enforced is caught here and not by the student.
"""
from fieldkit import sitrep


def _report(**over):
    """A passing report, with one section replaced."""
    base = {
        "One line": ["The build is green and the two bugs are fixed."],
        "What I need from you": ["1. Say yes to the release."],
        "Where we started": ["- Two tests were red."],
        "What happened": ["1. Found the cause.", "2. Fixed it.", "3. Ran the suite."],
        "What was achieved": ["- The suite passes."],
        "Where we are now": ["- Waiting on you."],
        "What is left": ["- The release."],
    }
    base.update(over)
    return "\n".join(f"# {k}\n\n" + "\n".join(v) + "\n" for k, v in base.items())


def test_the_example_passes_and_renders_in_every_language():
    assert sitrep.check(sitrep.example())["ok"], sitrep.check(sitrep.example())["problems"]
    for lang in sitrep.LANGS:
        r = sitrep.render(sitrep.example(), lang=lang)
        assert r["ok"], (lang, r["problems"])
        assert sitrep.heading("What I need from you", lang) in r["text"]


def test_every_heading_exists_in_all_six_languages_and_english_is_the_key():
    hs = sitrep.headings()
    for name in sitrep.SECTIONS:
        assert set(hs[name]) == set(sitrep.LANGS), name
        assert hs[name]["en"] == name
    assert sitrep.doctor()["ok"]


def test_ask_first_is_the_default_order_and_classic_puts_asks_last():
    text = _report()
    first = sitrep.render(text)["text"].splitlines()
    classic = sitrep.render(text, order="classic")["text"].splitlines()
    assert first[0] == "One line" and first.index("What I need from you") < first.index("Where we started")
    assert classic.index("What I need from you") > classic.index("What is left")


def test_a_heading_in_another_language_is_understood():
    text = _report().replace("# What happened", "# Ce s-a întâmplat")
    assert sitrep.check(text)["ok"]


def test_missing_section_fails():
    text = _report().replace("# What is left\n\n- The release.\n", "")
    d = sitrep.check(text)
    assert not d["ok"] and any("missing section: 'What is left'" in p for p in d["problems"])
    assert sitrep.exit_code(d) == 3


def test_one_line_must_be_one_short_sentence_with_a_full_stop():
    long = " ".join(["word"] * 26) + "."
    assert any("26 words" in p for p in sitrep.check(_report(**{"One line": [long]}))["problems"])
    assert any("more than one sentence" in p for p in
               sitrep.check(_report(**{"One line": ["Done. Really done."]}))["problems"])
    assert any("full stop" in p for p in sitrep.check(_report(**{"One line": ["Status update"]}))["problems"])
    assert any("has 2 lines" in p for p in sitrep.check(_report(**{"One line": ["A.", "B."]}))["problems"])


def test_more_than_three_asks_fails_and_an_ask_is_one_sentence():
    many = [f"{i}. Do thing {i}." for i in range(1, 5)]
    assert any("4 asks" in p for p in sitrep.check(_report(**{"What I need from you": many}))["problems"])
    two = ["1. Say yes. Then say it again."]
    assert any("one ask, one sentence" in p for p in
               sitrep.check(_report(**{"What I need from you": two}))["problems"])


def test_long_sentence_idiom_abbreviation_emdash_and_italics_each_fail():
    long = "- " + " ".join(["word"] * 30) + "."
    assert any("30-word sentence" in p for p in sitrep.check(_report(**{"What is left": [long]}))["problems"])
    d = sitrep.check(_report(**{"What is left": ["- We will circle back on the rest."]}))
    assert any("idiom" in p and "circle back" in p for p in d["problems"])
    d = sitrep.check(_report(**{"What is left": ["- Three files, e.g. the README."]}))
    assert any("abbreviation" in p for p in d["problems"])
    d = sitrep.check(_report(**{"What is left": ["- The release — tomorrow."]}))
    assert any("banned mark" in p for p in d["problems"])
    d = sitrep.check(_report(**{"What is left": ["- The *release* tomorrow."]}))
    assert any("italics" in p for p in d["problems"])
    d = sitrep.check(_report(**{"What is left": ["- The **release** tomorrow."]}))
    assert d["ok"], d["problems"]


def test_warnings_do_not_fail_but_are_said():
    eight = [f"{i}. Step {i}." for i in range(1, 9)]
    d = sitrep.check(_report(**{"What happened": eight}))
    assert d["ok"] and any("8 steps" in w for w in d["warnings"])
    d = sitrep.check(_report(**{"What is left": ["- Is the release ready?"]}))
    assert d["ok"] and any("question outside the asks" in w for w in d["warnings"])
    d = sitrep.check(_report(**{"What is left": ["- The UEFI setting."]}))
    assert d["ok"] and any("'UEFI' in capitals" in w for w in d["warnings"])
    d = sitrep.check(_report(**{"What is left": ["- The UEFI (the firmware menu) setting."]}))
    assert not d["warnings"], d["warnings"]


def test_a_failing_report_is_not_rendered():
    r = sitrep.render(_report(**{"One line": ["Status"]}))
    assert not r["ok"] and r["text"] == "" and r["problems"]


def test_template_has_the_seven_headings_in_the_asked_language():
    t = sitrep.template("de")
    for name in sitrep.SECTIONS:
        assert f"# {sitrep.heading(name, 'de')}" in t
    assert sitrep.template("en").index("# What I need from you") < sitrep.template("en").index("# Where we started")
    assert sitrep.template("en", "classic").index("# What I need from you") > \
        sitrep.template("en", "classic").index("# What is left")


def test_unknown_language_is_refused_not_guessed():
    r = sitrep.render(_report(), lang="xx")
    assert not r["ok"] and "unknown language" in r["problems"][0]
