"""Fluent (.ftl) hunks are ported by message. Built from the real browser.ftl h30 of live run 11 (2026-10-01)."""
import subprocess

import pytest

from fieldkit.buildh import firefox, fluent

H30_LINES = """ urlbar-result-action-open-saved-tabgroup = Open { $group }

 ## Used in the context menu in urlbar view.
-
 urlbar-view-context-menu-open-in-tab =
-  .label = Open in New Tab
-  .accesskey = w
+    .label = Open in New Gorilla Tab
+    .accesskey = w
 urlbar-view-context-menu-open-in-window =
-  .label = Open in New Window
-  .accesskey = N
+    .label = Open in New Gorilla Window
+    .accesskey = N

 ## Labels shown above groups of urlbar results
-
 # A label shown above the "Firefox Suggest" (bookmarks/history) group in the
 # urlbar results.
 urlbar-group-firefox-suggest =
-  .label = { -firefox-suggest-brand-name }
+    .label = { -firefox-suggest-brand-name }

 # A label shown above Quick Actions in the urlbar results.
 urlbar-group-quickactions =
-  .label = Quick Actions
+    .label = Quick Actions

 # The result menu labels shown next to trending results.
-urlbar-result-menu-trending-dont-show2 = Don’t show trending searches
-  .accesskey = D
+urlbar-result-menu-trending-dont-show =
+    .label = Don’t show trending searches
+    .accesskey = D

 ## Reader View toolbar buttons
-
 # This should match menu-view-enter-readerview in menubar.ftl
 reader-view-enter-button =
     .aria-label = Enter Reader View""".splitlines()
H30 = {"header": "@@ -935,60 +919,58 @@", "lines": H30_LINES}

FF157 = """## Used in the menu of a urlbar result.

urlbar-view-context-menu-open-in-tab2 = Open in New Tab
    .accesskey = w
urlbar-view-context-menu-open-in-window2 = Open in New Window
    .accesskey = N

## Labels shown above groups of urlbar results

# A label shown above the "Firefox Suggest" (bookmarks/history) group in the
# urlbar results.
urlbar-group-firefox-suggest =
  .label = { -firefox-suggest-brand-name }

# A label shown above Quick Actions in the urlbar results.
urlbar-group-quickactions =
  .label = Quick Actions

# The result menu labels shown next to trending results.
urlbar-result-menu-trending-dont-show2 = Don’t show trending searches
  .accesskey = D

## Reader View toolbar buttons

# This should match menu-view-enter-readerview in menubar.ftl
reader-view-enter-button =
    .aria-label = Enter Reader View
""".splitlines()


def test_entries_are_parsed_by_message_with_whitespace_ignored():
    e = {x["id"]: x for x in fluent.entries(FF157)}
    assert e["urlbar-view-context-menu-open-in-tab2"]["parts"] == [("value", "Open in New Tab"), (".accesskey", "w")]
    assert e["urlbar-group-quickactions"]["parts"] == [(".label", "Quick Actions")]
    assert e["urlbar-group-quickactions"]["indent"] == "  "


def test_the_real_h30_means_three_wording_changes_and_nothing_else():
    sem = fluent.semantics(H30)
    assert not sem["reformat_only"]
    assert set(sem["changed"]) == {"urlbar-view-context-menu-open-in-tab", "urlbar-view-context-menu-open-in-window"}
    assert sem["changed"]["urlbar-view-context-menu-open-in-tab"] == [(".label", "Open in New Gorilla Tab"), (".accesskey", "w")]
    assert sem["removed"] == ["urlbar-result-menu-trending-dont-show2"]
    assert set(sem["added"]) == {"urlbar-result-menu-trending-dont-show"}
    assert "urlbar-group-quickactions" not in sem["changed"]            # re-indentation is not a change


def test_the_real_h30_is_deferred_to_the_owner_with_the_rename_candidates_named():
    new, notes, gone = fluent.port(list(FF157), H30)
    assert new == FF157 and all("older name" in n for n in notes)
    assert dict(gone)["urlbar-view-context-menu-open-in-tab"] == ["urlbar-view-context-menu-open-in-tab2"]
    assert dict(gone)["urlbar-view-context-menu-open-in-window"] == ["urlbar-view-context-menu-open-in-window2"]


def test_a_reformat_only_hunk_is_a_no_op():
    hunk = {"header": "@@", "lines": [" urlbar-group-quickactions =", "-  .label = Quick Actions", "+    .label = Quick Actions"]}
    new, notes, gone = fluent.port(list(FF157), hunk)
    assert new == FF157 and gone == [] and notes[0].startswith("reformat only")
    assert fluent.check(FF157, FF157, hunk) == []


def test_a_wording_change_on_an_existing_message_is_applied_in_the_files_own_style():
    hunk = {"header": "@@", "lines": [" urlbar-group-quickactions =", "-  .label = Quick Actions", "+    .label = Gorilla Actions"]}
    new, notes, gone = fluent.port(list(FF157), hunk)
    i = new.index("urlbar-group-quickactions =")
    assert new[i + 1] == "  .label = Gorilla Actions" and gone == []       # the file's 2-space indent, not the hunk's 4
    assert fluent.check(FF157, new, hunk) == []
    assert new[:i] == FF157[:i] and new[i + 2:] == FF157[i + 2:]


def test_the_check_catches_a_wrong_message_and_collateral():
    hunk = {"header": "@@", "lines": [" urlbar-group-quickactions =", "-  .label = Quick Actions", "+    .label = Gorilla Actions"]}
    wrong = list(FF157)
    wrong[wrong.index("  .label = Quick Actions")] = "  .label = Something Else"
    assert any("does not read as the patch wants" in w for w in fluent.check(FF157, wrong, hunk))
    collateral = list(FF157)
    collateral[collateral.index("  .label = Quick Actions")] = "  .label = Gorilla Actions"
    collateral[collateral.index("    .accesskey = w")] = "    .accesskey = x"
    assert any("open-in-tab2 changed, but the patch does not touch it" in w for w in fluent.check(FF157, collateral, hunk))


def test_a_hunk_that_cuts_a_message_is_ambiguous():
    hunk = {"header": "@@", "lines": ["-  .label = Quick Actions", "+    .label = Gorilla Actions", " "]}
    with pytest.raises(fluent.Ambiguous):
        fluent.semantics(hunk)


def test_auto_port_transfers_h30_and_applies_a_wording_change_through_the_fluent_tier(tmp_path):
    w = tmp_path / "w"
    (w / "browser").mkdir(parents=True)
    (w / "browser" / "browser.ftl").write_text("\n".join(FF157) + "\n", encoding="utf-8", newline="")
    t = {"workdir": str(w)}
    res = firefox.auto_port(t, {"id": "x"}, "08.Look/b.patch", "browser/browser.ftl", H30)
    assert res["ok"] and any("transferred" in n for n in res["notes"])          # the renamed messages take the wording
    hunk = {"header": "@@", "lines": [" urlbar-group-quickactions =", "-  .label = Quick Actions", "+    .label = Gorilla Actions"]}
    res = firefox.auto_port(t, {"id": "y"}, "08.Look/b.patch", "browser/browser.ftl", hunk)
    assert res["ok"] and res["notes"] == ["ported by message id: 1 changed, 0 added, 0 removed"]
    assert "  .label = Gorilla Actions" in (w / "browser" / "browser.ftl").read_text(encoding="utf-8").splitlines()


# -- the owner's rebranding carried onto messages upstream renamed or restructured ------------------------------

def test_the_real_h30_wording_is_transferred_onto_the_renamed_messages_and_the_drift_pair_is_left_alone():
    new, notes, gone = fluent.port(list(FF157), H30)
    assert gone and dict(gone)["urlbar-view-context-menu-open-in-tab"] == ["urlbar-view-context-menu-open-in-tab2"]
    assert any("older name of urlbar-result-menu-trending-dont-show2" in n for n in notes)      # drift, not rebranding
    new, mapping, tnotes, still = fluent.transfer(new, H30, gone)
    assert still == []
    assert "urlbar-view-context-menu-open-in-tab2 = Open in New Gorilla Tab" in new
    assert "urlbar-view-context-menu-open-in-window2 = Open in New Gorilla Window" in new
    assert new[new.index("urlbar-view-context-menu-open-in-tab2 = Open in New Gorilla Tab") + 1] == "    .accesskey = w"
    assert "urlbar-result-menu-trending-dont-show2 = Don’t show trending searches" in new       # upstream's name kept
    assert "urlbar-result-menu-trending-dont-show =" not in new                                  # the old name NOT added back
    assert mapping["urlbar-view-context-menu-open-in-tab"][0] == "urlbar-view-context-menu-open-in-tab2"
    assert fluent.check(FF157, new, H30, mapping) == []


def test_transfer_applies_the_same_edit_when_upstream_also_changed_other_words():
    hunk = {"header": "@@", "lines": [" urlbar-group-old-name =", "-  .label = Open in New Tab", "+    .label = Open in New Gorilla Tab"]}
    body = list(FF157) + ["urlbar-group-old-name2 =", "    .label = Open in New Tab (beta)"]
    new, notes, gone = fluent.port(body, hunk)
    new, mapping, tnotes, still = fluent.transfer(new, hunk, gone)
    assert still == [] and "    .label = Open in New Gorilla Tab (beta)" in new


def test_transfer_refuses_when_the_edited_fragment_is_not_there_or_the_candidate_is_ambiguous():
    hunk = {"header": "@@", "lines": [" urlbar-group-old-name =", "-  .label = Open in New Tab", "+    .label = Open in New Gorilla Tab"]}
    body = list(FF157) + ["urlbar-group-old-name2 =", "    .label = Something completely different"]
    new, notes, gone = fluent.port(body, hunk)
    new2, mapping, tnotes, still = fluent.transfer(new, hunk, gone)
    assert still == gone and new2 == new and mapping == {}
    body = list(FF157) + ["urlbar-group-old-name2 =", "    .label = Open in New Tab", "urlbar-group-old-name3 =", "    .label = Open in New Tab"]
    new, notes, gone = fluent.port(body, hunk)
    assert len(dict(gone)["urlbar-group-old-name"]) == 2
    assert fluent.transfer(new, hunk, gone)[3] == gone


def test_auto_port_transfers_h30_and_the_check_accepts_the_mapping(tmp_path):
    w = tmp_path / "w"
    (w / "browser").mkdir(parents=True)
    (w / "browser" / "browser.ftl").write_text("\n".join(FF157) + "\n", encoding="utf-8", newline="")
    subprocess.run(["git", "init", "-q", str(w)], check=True)
    subprocess.run(["git", "-C", str(w), "-c", "user.name=t", "-c", "user.email=t@example.com", "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(w), "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "157"], check=True)
    t, s = {"workdir": str(w)}, {"id": "x"}
    res = firefox.auto_port(t, s, "08.Look/b.patch", "browser/browser.ftl", H30)
    assert res["ok"] and "fluent_map" in s, res
    assert "urlbar-view-context-menu-open-in-tab2 = Open in New Gorilla Tab" in (w / "browser" / "browser.ftl").read_text(encoding="utf-8").splitlines()
    assert firefox.check_port(t, s, "08.Look/b.patch", "browser/browser.ftl", H30) == {"ok": True, "why": []}


def test_the_final_re_check_honours_the_transfer_mapping_and_ignores_later_hunks_but_catches_a_real_regression():
    """Live run 15: the literal re-check failed the final checks on browser.ftl h30, which the transfer had ported."""
    before = list(FF157)
    after, mapping, _, still = fluent.transfer(*fluent.port(list(FF157), H30)[:1], H30, fluent.port(list(FF157), H30)[2])
    assert still == []
    step = {"fluent_map": mapping}
    assert firefox.still_holds(step, "browser/browser.ftl", H30, before, after) == []
    later = [l.replace("Quick Actions", "Gorilla Actions") for l in after]            # another hunk's legitimate change
    assert firefox.still_holds(step, "browser/browser.ftl", H30, before, later) == []
    reverted = [l.replace("Open in New Gorilla Tab", "Open in New Tab") for l in after]
    assert any("open-in-tab2" in w for w in firefox.still_holds(step, "browser/browser.ftl", H30, before, reverted))
