"""Live run 16 (2026-10-01): three hand ports the hand check refused for shape, not meaning, and a surplus Fluent copy.

  * Tabbrowser h4: the person replaced a three-line `if (A || B) {` with `if (B) {`; the diff removes `if (` and
    `) {`, token-less lines that go with the run that carries the hunk's tokens.
  * browser-shared.css h7: a removed stylelint comment and `color: inherit;` live in other rules 300 lines away;
    generic lines are judged near the hunk's specific context, comments not at all.
  * `_allowTransparentBrowser` in the added text is `lazy.allowTransparentBrowser` in the file: one name.
  * browser.ftl: the owner moved a message; the tree held it twice, the owner's tree once: the copy that is not
    where the truth has it goes.
"""
from fieldkit.buildh import firefox, fluent
from tests.test_buildh_renamed import R2, H4


def test_hand_port_over_renamed_rewrapped_lines_is_accepted():
    after = R2[:5] + ["    if (lazy.allowTransparentBrowser) {"] + R2[9:]
    assert firefox.hand_port_check(R2, after, H4) == []
    # removing a line that carries none of the hunk's tokens, outside the run, is still damage
    worse = [l for l in after if l.strip() != "Services.obs.notifyObservers("]
    assert any("Services.obs.notifyObservers(" in w for w in firefox.hand_port_check(R2, worse, H4))


CSS_BEFORE = """.other-rule {
  /* stylelint-disable-next-line stylelint-plugin-mozilla/use-design-tokens */
  color: inherit;
}
""".splitlines() + [f".filler-{i} {{ margin: {i}px; }}" for i in range(40)] + """
:root:not([theme-image-in-toolbox]) body,
:root[theme-image-in-toolbox] #navigator-toolbox {
  background-image: var(--toolbox-background-image);
  /* stylelint-disable-next-line stylelint-plugin-mozilla/use-design-tokens */
  background-size: var(--toolbox-background-size);
}

/* stylelint-disable-next-line media-query-no-invalid */
@media -moz-pref("browser.nova.enabled") {
  .chrome-block {
    background-color: var(--toolbox-background-color);
    color: var(--toolbox-text-color);
  }
}
""".splitlines()

H7 = {"lines": [
    " ",
    "-:root:not([theme-image-in-toolbox]) body,",
    "-:root[theme-image-in-toolbox] #navigator-toolbox {",
    "-  background-image: var(--toolbox-background-image);",
    "-  /* stylelint-disable-next-line stylelint-plugin-mozilla/use-design-tokens */",
    "-  background-size: var(--toolbox-background-size);",
    "-}",
    "-",
    "+/* stylelint-disable-next-line media-query-no-invalid */",
    ' @media -moz-pref("browser.nova.enabled") {',
    "-  :root[lwtheme]:not([theme-image-in-toolbox]) #navigator-toolbox {",
    "-    color: inherit;",
    "-    /* stylelint-disable-next-line stylelint-plugin-mozilla/use-design-tokens */",
    "-    background-color: light-dark(rgba(0, 0, 0, 0.05), rgba(255, 255, 255, 0.05));",
    "-  }",
    "-",
    "   .chrome-block {",
    "     background-color: var(--toolbox-background-color);",
    "     color: var(--toolbox-text-color);"]}


def test_generic_removed_lines_elsewhere_do_not_refuse_a_hand_port():
    at = CSS_BEFORE.index(":root:not([theme-image-in-toolbox]) body,")
    after = CSS_BEFORE[:at] + CSS_BEFORE[at + 7:]
    assert firefox.hand_port_check(CSS_BEFORE, after, H7) == []
    # the block still there is caught by its own specific lines
    assert any("theme-image-in-toolbox" in w for w in firefox.hand_port_check(CSS_BEFORE, CSS_BEFORE, H7))


TREE = """urlbar-result-menu-trending-label = Trending

# The result menu labels shown next to trending results.
urlbar-result-menu-trending-dont-show2 = Don’t show trending searches
  .accesskey = D

urlbar-result-menu-tip-get-help2 = Get help
    .accesskey = h
urlbar-result-menu-trending-dont-show2 = Don’t show trending searches
  .accesskey = D

urlbar-result-menu-learn-more = Learn more
""".splitlines()

TRUTH = """urlbar-result-menu-trending-label = Trending

urlbar-result-menu-tip-get-help2 = Get help
    .accesskey = h
urlbar-result-menu-trending-dont-show2 = Don’t show trending searches
  .accesskey = D

urlbar-result-menu-learn-more = Learn more
""".splitlines()


def test_a_surplus_copy_goes_where_the_truth_does_not_have_it(tmp_path):
    new, removed = fluent.dedupe(TREE, TRUTH)
    assert removed == [("urlbar-result-menu-trending-dont-show2", 4)]
    assert new.count("urlbar-result-menu-trending-dont-show2 = Don’t show trending searches") == 1
    assert new.index("urlbar-result-menu-trending-dont-show2 = Don’t show trending searches") > new.index("urlbar-result-menu-tip-get-help2 = Get help")
    # a message only this Firefox has (renamed upstream, the owner's wording transferred onto it) is never surplus
    only_here = TRUTH + ["", "urlbar-view-context-menu-open-in-tab2 = Open in New Gorilla Tab", "    .accesskey = w"]
    assert fluent.dedupe(only_here, TRUTH)[1] == []
    # a message the truth itself holds twice is left alone
    twice = TRUTH + ["", "urlbar-result-menu-learn-more = Learn more"]
    assert fluent.dedupe(twice, twice)[1] == []
    # as a step
    w, tr = tmp_path / "w", tmp_path / "t"
    for root, text in ((w, TREE), (tr, TRUTH)):
        (root / "b").mkdir(parents=True)
        (root / "b/x.ftl").write_text("\n".join(text) + "\n", encoding="utf-8")
    t = {"workdir": str(w)}
    assert not fluent.check_dedupe(t, "b/x.ftl", str(tr))["ok"]
    r = fluent.step_dedupe(t, "b/x.ftl", str(tr))
    assert r["ok"] and "removed surplus urlbar-result-menu-trending-dont-show2 at line 4" in r["summary"]
    assert fluent.check_dedupe(t, "b/x.ftl", str(tr))["ok"]


def test_a_pre_existing_similar_line_is_not_a_rename_of_the_removed_one():
    from fieldkit.buildh import firefox
    before = ["  get SERVER_URL() {", "    return lazy.allowServerURL", "      ? lazy.gServerURL", "      : AppConstants.REMOTE_SETTINGS_SERVER_URLS[0];", "  },", "", "  other() {",
              "    return AppConstants.REMOTE_SETTINGS_SERVER_URLS.includes(this.SERVER_URL) || x;", "  },"]
    after = ["  get SERVER_URL() {", '    return "data:,#remote-settings-dummy/v1";', "  },", "", "  other() {",
             "    return AppConstants.REMOTE_SETTINGS_SERVER_URLS.includes(this.SERVER_URL) || x;", "  },"]
    hunk = {"header": "@@ -1,5 +1,3 @@", "lines": ["   get SERVER_URL() {", "-    return lazy.allowServerURL", "-      ? lazy.gServerURL",
                                                    "-      : AppConstants.REMOTE_SETTINGS_SERVER_URLS[0];", '+    return "data:,#remote-settings-dummy/v1";', "   },"]}
    assert firefox.hand_port_holds(after, hunk, before) == []
