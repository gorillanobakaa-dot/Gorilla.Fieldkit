"""Mixed hunks (removals AND additions) are merged by the harness; the model is asked only REMOVE/KEEP, never to
write code. Built from the real firefox.js h32 of live run 8 (2026-10-01): Gemma failed it 3 times with line
operations; upstream 157 had collapsed a 5-line #ifdef into one line.
"""
from pathlib import Path

import subprocess

import pytest

from fieldkit.buildh import firefox

H32 = {"header": "@@ -2218,11 +2209,13 @@", "lines": [
    " ",
    " // Is the sidebar positioned ahead of the content browser",
    ' pref("sidebar.position_start", true);',
    "-#ifdef NIGHTLY_BUILD",
    '-pref("sidebar.revamp", true);',
    "-#else",
    '-pref("sidebar.revamp", false);',
    "-#endif",
    '+pref("sidebar.revamp", false, locked); // GORILLA: the 136 revamp sidebar (dumping ground) locked off',
    "+// Gorilla Unleashed: Force accept languages and disable multilingual / trending",
    '+pref("intl.multilingual.enabled", false);',
    '+pref("intl.multilingual.downloadEnabled", false);',
    '+pref("intl.accept_languages", "en-US, en");',
    '+pref("browser.urlbar.suggest.trending", false);',
    '+pref("browser.urlbar.trending.featureGate", false);',
    ' pref("sidebar.revamp.round-content-area", true);',
    ' pref("sidebar.animation.enabled", true);',
    ' pref("sidebar.animation.duration-ms", 200);']}

FF157 = """// Try to convert PDFs sent as octet-stream
pref("pdfjs.handleOctetStream", true);

// Is the sidebar positioned ahead of the content browser
pref("sidebar.position_start", true);
pref("sidebar.revamp", true);
pref("sidebar.animation.enabled", true);
pref("sidebar.animation.duration-ms", 200);
pref("sidebar.animation.expand-on-hover.duration-ms", 400);
pref("sidebar.animation.expand-on-hover.delay-duration-ms", 200);

// This pref is used to store user customized tools in the sidebar launcher and shouldn't be changed.
pref("sidebar.main.tools", "");
""".splitlines()

OWNER_155 = """// Try to convert PDFs sent as octet-stream
pref("pdfjs.handleOctetStream", true);

// Is the sidebar positioned ahead of the content browser
pref("sidebar.position_start", true);
pref("sidebar.revamp", false, locked); // GORILLA: the 136 revamp sidebar (dumping ground) locked off
// Gorilla Unleashed: Force accept languages and disable multilingual / trending
pref("intl.multilingual.enabled", false);
pref("intl.multilingual.downloadEnabled", false);
pref("intl.accept_languages", "en-US, en");
pref("browser.urlbar.suggest.trending", false);
pref("browser.urlbar.trending.featureGate", false);
pref("sidebar.animation.enabled", true);
pref("sidebar.animation.duration-ms", 200);
pref("sidebar.animation.expand-on-hover.duration-ms", 400);
pref("sidebar.animation.expand-on-hover.delay-duration-ms", 200);

// This pref is used to store user customized tools in the sidebar launcher and shouldn't be changed.
pref("sidebar.main.tools", "");
""".splitlines()


def test_the_real_h32_is_merged_by_the_harness_with_no_model_and_matches_the_owners_own_port():
    notes = []
    assert firefox.auto_merge(FF157, H32, notes) == OWNER_155
    assert notes == ["merged by key: removed 1 line(s), inserted 7"]


def test_the_old_tiers_still_give_up_on_h32_so_the_merge_is_what_rescues_it():
    with pytest.raises(firefox.Ambiguous):
        firefox.transplant(list(FF157), H32)
    with pytest.raises(firefox.Ambiguous):
        firefox.auto_substitute(list(FF157), H32)


def test_merge_refuses_when_an_upstream_line_needs_a_decision():
    body = list(FF157)
    body.insert(6, 'pref("sidebar.revamp.somethingNew", true);')          # inside the span, unknown to the hunk
    with pytest.raises(firefox.Ambiguous, match="need a decision"):
        firefox.auto_merge(body, H32)


def test_merge_refuses_when_nothing_of_the_old_text_is_there():
    body = [l for l in FF157 if "sidebar.revamp" not in l]
    with pytest.raises(firefox.Ambiguous, match="none of the removed lines"):
        firefox.auto_merge(body, H32)


def test_merge_refuses_when_the_context_line_is_ambiguous():
    body = list(FF157)
    body.insert(5, 'pref("sidebar.position_start", true);')                # the context line twice INSIDE the span
    with pytest.raises(firefox.Ambiguous, match="could not be fixed"):
        firefox.auto_merge(body, H32)


def test_a_plus_block_that_opens_the_hunk_goes_before_the_first_context_line():
    hunk = {"header": "@@", "lines": ["+// new first", "+pref(\"n.e.w\", 1);", ' pref("sidebar.position_start", true);']}
    new = firefox.auto_merge(list(FF157), hunk)
    i = new.index('pref("sidebar.position_start", true);')
    assert new[i - 2:i] == ["// new first", 'pref("n.e.w", 1);']


def test_question_answers_also_insert_the_plus_block(tmp_path):
    body = list(FF157)
    body.insert(6, 'pref("sidebar.revamp.somethingNew", true);')
    target = tmp_path / "firefox.js"
    target.write_text("\n".join(body) + "\n", encoding="utf-8", newline="")
    at = firefox._anchor(body, [l[1:] for l in H32["lines"] if l[:1] in (" ", "-")])
    auto_rm, uncertain = firefox.identify_questions(body, H32, at)
    assert uncertain == [7] and auto_rm == [6]
    count, summary, removed = firefox.apply_question_answers(target, [(7, "keep")], H32, auto_rm)
    out = target.read_text(encoding="utf-8").splitlines()
    assert "inserted 7 line(s) by the harness" in summary and count == 8
    assert out[5].startswith('pref("sidebar.revamp", false, locked)') and 'pref("sidebar.revamp.somethingNew", true);' in out
    assert 'pref("sidebar.revamp", true);' not in out


def test_the_packet_asks_questions_for_a_mixed_hunk_and_says_the_harness_inserts(tmp_path, monkeypatch):
    from fieldkit.buildh import task
    monkeypatch.setattr(task, "STATE", tmp_path / "state")
    w = tmp_path / "work"
    w.mkdir()
    body = list(FF157)
    body.insert(6, 'pref("sidebar.revamp.somethingNew", true);')
    (w / "firefox.js").write_text("\n".join(body) + "\n", encoding="utf-8", newline="")
    t = {"workdir": str(w)}
    text = firefox.packet_port(t, {"id": "x"}, 8000, "05.PREFS/x.patch", "firefox.js", H32, answer_mode=True)
    assert "insert the 7 new line(s) itself" in text and "REMOVE" in text and "DELETE" not in text.split("REMOVE")[0][-200:]


FF157_WITH_STRAY_ENDIF = """#if defined(XP_WIN)
  pref("toolkit.winRegisterApplicationRestart", true);
#endif

// The values of preferredAction and alwaysAskBeforeHandling before pdf.js
// became the default.
pref("pdfjs.previousHandler.preferredAction", 0);
pref("pdfjs.previousHandler.alwaysAskBeforeHandling", false);

""".splitlines() + FF157


def test_a_generic_line_like_endif_never_bounds_the_span_the_real_file_had_one_eleven_lines_early():
    """The real 157 firefox.js: the anchor landed 11 lines early and a stray #endif started the span."""
    body = list(FF157_WITH_STRAY_ENDIF)
    at = firefox._anchor(body, [l[1:] for l in H32["lines"] if l[:1] in (" ", "-")])
    auto_rm, uncertain = firefox.identify_questions(body, H32, at)
    assert uncertain == [] and [body[n - 1] for n in auto_rm] == ['pref("sidebar.revamp", true);']
    assert firefox.auto_merge(body, H32)[9:] == OWNER_155


FF157_WITH_OTHER_IFDEFS = """#ifdef NIGHTLY_BUILD
pref("some.other.nightly.thing", true);
#else
pref("some.other.nightly.thing", false);
#endif
""".splitlines() + FF157_WITH_STRAY_ENDIF + """
#ifdef NIGHTLY_BUILD
pref("yet.another.nightly.thing", true);
#else
pref("yet.another.nightly.thing", false);
#endif
""".splitlines()


def test_the_check_judges_the_span_not_the_whole_file_a_removed_line_upstream_already_dropped_cannot_be_demanded():
    """The real h33: fourteen other #ifdef NIGHTLY_BUILD blocks in firefox.js made a correct merge 'wrong'."""
    before = list(FF157_WITH_OTHER_IFDEFS)
    after = firefox.auto_merge(before, H32)
    assert firefox.hunk_problems(before, after, H32) == []
    assert firefox.collateral(before, after, H32) == []


def test_the_check_still_catches_a_removal_that_did_not_happen_inside_the_span():
    before = list(FF157_WITH_OTHER_IFDEFS)
    after = list(before)
    i = after.index('pref("sidebar.position_start", true);')
    after[i + 1:i + 1] = [l[1:] for l in H32["lines"] if l.startswith("+")]      # added, but the old line kept
    assert any("should be gone" in w for w in firefox.hunk_problems(before, after, H32))


def test_the_check_still_catches_a_missing_added_line_inside_the_span():
    before = list(FF157_WITH_OTHER_IFDEFS)
    after = firefox.auto_merge(before, H32)
    after.remove('pref("browser.urlbar.suggest.trending", false);')
    assert any("missing added line" in w for w in firefox.hunk_problems(before, after, H32))


def test_the_span_starts_at_the_hunks_first_specific_line_not_five_lines_before_the_anchor():
    """The real file: an unrelated #if defined(XP_WIN) ... #endif block sits just above the span."""
    before = list(FF157_WITH_STRAY_ENDIF)
    lo, hi = firefox._span(before, H32)
    assert before[lo] == "// Is the sidebar positioned ahead of the content browser"
    assert before[hi - 1] == 'pref("sidebar.animation.duration-ms", 200);'
    after = firefox.auto_merge(before, H32)
    assert firefox.hunk_problems(before, after, H32) == []


def test_a_hunk_already_in_place_is_done_by_tier_zero_not_sent_to_a_model(tmp_path):
    w = tmp_path / "w"
    w.mkdir()
    (w / "firefox.js").write_text("\n".join(OWNER_155) + "\n", encoding="utf-8", newline="")
    res = firefox.auto_port({"workdir": str(w)}, {"id": "x"}, "05.PREFS/x.patch", "firefox.js", H32)
    assert res["ok"] and res["notes"] == ["already in place: the added lines are there and the removed ones gone"]
    assert (w / "firefox.js").read_text(encoding="utf-8").splitlines() == OWNER_155


def test_the_check_passes_a_hunk_that_is_already_in_place_and_untouched(tmp_path):
    """Live run 10: tier 0 said 'already in place', then the net-count check called the present lines missing."""
    w = tmp_path / "w"
    w.mkdir()
    (w / "firefox.js").write_text("\n".join(OWNER_155) + "\n", encoding="utf-8", newline="")
    subprocess.run(["git", "init", "-q", str(w)], check=True)
    subprocess.run(["git", "-C", str(w), "-c", "user.name=t", "-c", "user.email=t@example.com", "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(w), "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "done earlier"], check=True)
    res = firefox.check_port({"workdir": str(w)}, {"id": "x"}, "05.PREFS/x.patch", "firefox.js", H32)
    assert res == {"ok": True, "why": []}
    # but a file that merely did not change, with the hunk NOT in it, still fails
    (w / "firefox.js").write_text("\n".join(FF157) + "\n", encoding="utf-8", newline="")
    subprocess.run(["git", "-C", str(w), "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qam", "undone"], check=True)
    assert not firefox.check_port({"workdir": str(w)}, {"id": "x"}, "05.PREFS/x.patch", "firefox.js", H32)["ok"]
