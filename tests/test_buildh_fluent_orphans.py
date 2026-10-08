"""Fluent (2026-10-08): a message the fork removed that code still names leaves an element with no text (four in
Settings: 08.Look rebuilt preferences.ftl from the 155 file); an id defined twice logs "Attempt to override" on every
page that loads the file. Both were invisible to the shape row, which lists removed ids as deliberate."""
import subprocess

import pytest

from fieldkit.buildh import fluent

pytest.importorskip("fluent.syntax")


def _git(w, *a):
    subprocess.run(["git", "-C", str(w), *a], check=True, capture_output=True)


def test_a_removed_message_that_code_still_names_is_an_orphan(tmp_path):
    w = tmp_path / "tree"
    (w / "l10n").mkdir(parents=True)
    (w / "ui").mkdir()
    (w / "l10n" / "prefs.ftl").write_text("keep-me = Keep\nlost-group =\n    .label = Keyboard shortcuts\n"
                                          "gone-on-purpose = Old feature\n", encoding="utf-8")
    (w / "ui" / "tabs.mjs").write_text('el.dataset.l10nId = "lost-group";\nother("keep-me");\n', encoding="utf-8")
    _git(w, "init", "-q")
    _git(w, "add", ".")
    _git(w, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "pristine")
    base = subprocess.run(["git", "-C", str(w), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    (w / "l10n" / "prefs.ftl").write_text("keep-me = Keep\n", encoding="utf-8")       # the port lost two messages
    r = fluent.orphans(w, base)
    assert r == [("l10n/prefs.ftl", "lost-group", ["ui/tabs.mjs"])]                  # gone-on-purpose: nothing names it


def test_an_id_that_only_contains_another_is_not_a_match(tmp_path):
    w = tmp_path / "tree"
    (w / "l10n").mkdir(parents=True)
    (w / "l10n" / "a.ftl").write_text("button3 = Delete\n", encoding="utf-8")
    (w / "x.mjs").write_text('"containers-remove-button3"\n', encoding="utf-8")
    _git(w, "init", "-q")
    _git(w, "add", ".")
    _git(w, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "p")
    base = subprocess.run(["git", "-C", str(w), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    (w / "l10n" / "a.ftl").write_text("other = x\n", encoding="utf-8")
    assert fluent.orphans(w, base) == []


def test_duplicates_names_ids_defined_twice():
    text = "a = 1\nb =\n    .label = x\n-term = T\na = 1\n-term = T\n"
    assert fluent.duplicates(text) == ["-term", "a"]
    assert fluent.duplicates("a = 1\nb = 2\n") == []


def test_drop_second_copies_keeps_the_first_and_cleans_the_graft():
    lines = ["# Accessible label for the splitter.", "split =", "  .aria-label = Resize", "", "other = x", "",
             "# GORILLA REPAIR: messages required by this Firefox version,", "# grafted verbatim.",
             "split =", "  .aria-label = Resize", "", "# A later comment", "last = y"]
    out, rep = fluent.drop_second_copies(lines)
    assert rep == [("split", 2, 9)]
    assert out == ["# Accessible label for the splitter.", "split =", "  .aria-label = Resize", "", "other = x", "",
                   "# A later comment", "last = y"]
    with pytest.raises(ValueError):
        fluent.drop_second_copies(["a = 1", "a = 2"])
