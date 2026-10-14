"""File-backed explanations: the content, and the grey-man rule that guards them.

Written 2026-10-13 during a muster that found this module had NO tests. A
`pytest -k explain` had appeared to cover it, but was matching unrelated buildh
tests whose names merely contain the word. A keyword match is not coverage.
"""
import os

import pytest

from fieldkit import explain


def test_there_are_topics_and_english_is_always_first():
    t = explain.topics()
    assert t, "no topics found at all"
    for name, langs in t.items():
        assert langs[0] == explain.BASE_LANG, name


def test_every_topic_has_an_english_original():
    """The grey-man rule: a topic must NEVER exist in one other language alone."""
    for name in explain.topics():
        assert os.path.exists(explain._path(name)), f"{name} has no English original"


def test_check_passes_and_would_fail_on_an_orphan(tmp_path, monkeypatch):
    assert explain.check()["ok"], explain.check()["problems"]
    monkeypatch.setattr(explain, "TOPIC_DIR", str(tmp_path))
    (tmp_path / "orphan.ro.md").write_text("doar in romana", encoding="utf-8")
    bad = explain.check()
    assert not bad["ok"]
    assert any("grey man" in p for p in bad["problems"])
    assert explain.exit_code(bad) != 0


def test_reading_a_topic_returns_its_text():
    d = explain.explain("wifi-mesh")
    assert d["lang"] == "en" and not d["fell_back"]
    assert "mesh" in d["text"].lower()
    assert explain.exit_code(d) == 0


def test_a_translation_is_returned_when_it_exists():
    d = explain.explain("wifi-mesh", "ro")
    assert d["lang"] == "ro" and not d["fell_back"]
    assert "rețea" in d["text"] or "Mesh" in d["text"]


def test_a_missing_translation_falls_back_to_english_AND_SAYS_SO():
    """Silently serving the wrong language is worse than serving none."""
    d = explain.explain("channel-score", "ro")
    assert d["fell_back"] is True
    assert d["requested"] == "ro" and d["lang"] == "en"
    assert "[no ro version yet" in "\n".join(explain.lines(d))


def test_an_unknown_topic_refuses_and_lists_what_exists():
    d = explain.explain("no-such-topic")
    assert "error" in d
    assert d["available"], "a refusal must say what IS available"
    assert explain.exit_code(d) != 0


def test_the_topics_promised_in_this_session_are_all_present():
    have = explain.topics()
    for name in ("wifi-mesh", "hostile-names", "antenna-numbers",
                 "antenna-connectors", "how-antennas-work", "channel-score"):
        assert name in have, name


def test_no_topic_is_empty_or_a_stub():
    for name in explain.topics():
        text = explain.explain(name)["text"]
        assert len(text) > 400, f"{name} is too short to be an explanation"
        assert text.lstrip().startswith("#"), f"{name} has no heading"


def test_no_em_dashes_anywhere_in_the_topics():
    """House rule: the single most recognisable machine-writing tell."""
    for name, langs in explain.topics().items():
        for lang in langs:
            text = explain.explain(name, None if lang == "en" else lang)["text"]
            assert "—" not in text, f"em-dash in {name}.{lang}"


def test_reading_a_topic_costs_nothing_but_a_file_read():
    """The whole point: no model, no tokens, no variance between runs."""
    a = explain.explain("wifi-mesh")["text"]
    b = explain.explain("wifi-mesh")["text"]
    assert a == b


def test_lines_renders_every_shape_without_crashing():
    for d in (explain.explain("wifi-mesh"), explain.explain("nope"), explain.check()):
        assert explain.lines(d)
