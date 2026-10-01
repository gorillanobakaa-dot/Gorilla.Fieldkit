"""Hunks in a CSS file where long-but-common lines occur everywhere. From the real browser-shared.css h7 and h11
of live run 12 (2026-10-01): the span was pulled onto the wrong block, a one-line substitution went to the model,
and a correct answer was refused for a `color: inherit;` that lives elsewhere."""
import pytest

from fieldkit.buildh import firefox

H11 = {"header": "@@ -826,7 +814,7 @@ menupopup::part(drop-indicator) {", "lines": [
    " ",
    "   :root[lwtheme] & {",
    "     background-color: var(--lwt-accent-color); /* Known opaque */",
    "-    background-image: image(var(--toolbar-background-color)), var(--toolbox-background-image);",
    "+    background-image: linear-gradient(var(--toolbar-background-color)), var(--toolbox-background-image);",
    "     background-repeat: no-repeat, var(--toolbox-background-repeat);",
    "     /* stylelint-disable-next-line stylelint-plugin-mozilla/use-design-tokens */",
    "     background-position:"]}

CSS157_H11 = """  color-scheme: var(--toolbar-color-scheme);
  /* Variables defined in browser-colors.css will be updated to use design tokens, see bug 1952566. */
  /* stylelint-disable-next-line stylelint-plugin-mozilla/use-design-tokens */
  border-top-color: var(--chrome-content-separator-color);

  :root[lwtheme] & {
    background-color: var(--lwt-accent-color); /* Known opaque */
    background-image: image(var(--toolbar-background-color)), var(--toolbox-background-image);
    background-repeat: no-repeat, var(--toolbox-background-repeat);
    /* stylelint-disable-next-line stylelint-plugin-mozilla/use-design-tokens */
    background-position:
      top left,
      var(--toolbox-background-position);
    /* stylelint-disable-next-line stylelint-plugin-mozilla/use-design-tokens */
    background-size: auto, var(--toolbox-background-size);
  }
""".splitlines()

H7 = {"header": "@@ -310,22 +309,8 @@ body {", "lines": [
    "   }",
    " }",
    " ",
    "-:root:not([theme-image-in-toolbox]) body,",
    "-:root[theme-image-in-toolbox] #navigator-toolbox {",
    "-  background-image: var(--toolbox-background-image);",
    "-  background-repeat: var(--toolbox-background-repeat);",
    "-  background-position: var(--toolbox-background-position);",
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

CSS157_H7 = """  }
}

:root:not([theme-image-in-toolbox]) body,
:root[theme-image-in-toolbox] #navigator-toolbox {
  background-image: var(--toolbox-background-image);
  background-repeat: var(--toolbox-background-repeat);
  background-position: var(--toolbox-background-position);
  /* stylelint-disable-next-line stylelint-plugin-mozilla/use-design-tokens */
  background-size: var(--toolbox-background-size);
}

.browser-toolbar {
  appearance: none;
  /* Reset linux padding */
  padding: 0;
  border-style: none;
  background-color: transparent;
  color: inherit;
  &:not(.browser-titlebar) {
    background-color: var(--toolbar-background-color);
    color: var(--toolbar-text-color);
  }
}

/* as in the real file, these lines occur all over the place */
.urlbar-input { color: inherit; }
.tab-label {
  color: inherit;
  /* stylelint-disable-next-line stylelint-plugin-mozilla/use-design-tokens */
  background-size: var(--toolbox-background-size);
}
.sidebar-title {
  color: inherit;
  /* stylelint-disable-next-line stylelint-plugin-mozilla/use-design-tokens */
}
""".splitlines()


def test_h11_a_one_line_substitution_is_merged_despite_the_common_comment_line_above():
    body = list(CSS157_H11)
    notes = []
    new = firefox.auto_merge(body, H11, notes)
    assert "    background-image: linear-gradient(var(--toolbar-background-color)), var(--toolbox-background-image);" in new
    assert "    background-image: image(var(--toolbar-background-color)), var(--toolbox-background-image);" not in new
    assert len(new) == len(body) and notes == ["merged by key: removed 1 line(s), inserted 1"]
    assert firefox.hunk_problems(body, new, H11) == [] and firefox.collateral(body, new, H11) == []


def test_the_bounds_follow_the_hunk_in_order_not_the_first_common_line():
    body = list(CSS157_H11)
    at = firefox._anchor(body, [l[1:] for l in H11["lines"] if l[:1] in (" ", "-")])
    lo, hi = firefox._bounds(body, H11, at)
    assert body[lo] == "  :root[lwtheme] & {" and body[hi] == "    background-position:"


def test_h7_the_block_that_exists_is_removed_but_the_lint_comment_for_a_gone_media_query_is_the_owners_call():
    body = list(CSS157_H7)
    with pytest.raises(firefox.PlacementGone, match="no longer exist"):
        firefox.auto_merge(body, H7)


def test_h7_a_correct_answer_is_not_refused_for_a_color_inherit_that_lives_elsewhere():
    body = list(CSS157_H7)
    after = body[:3] + body[12:]                                        # the existing block removed (lines 4-11 + blank)
    after.insert(3, "/* stylelint-disable-next-line media-query-no-invalid */")
    assert not any("should be gone" in w for w in firefox.hunk_problems(body, after, H7))


H17 = {"header": "@@ -1829,7 +1833,13 @@ popupnotificationcontent {", "lines": [
    "   }",
    " ",
    '   @media not -moz-pref("browser.nova.enabled") {',
    "+    /* stylelint-disable-next-line media-query-no-invalid */",
    '+    @media -moz-pref("browser.nova.enabled") {',
    '+      list-style-image: url("chrome://browser/skin/smart-window-nova.svg");',
    "+    }",
    "+",
    "     > .toolbarbutton-icon {",
    "+      /* stylelint-disable-next-line media-query-no-invalid */",
    "       /* Smart Window icon has additional 0.125rem padding per side, to be slightly smaller than Classic Window icon when used in Switcher */",
    "       padding: var(--space-xxsmall);",
    "     }"]}

CSS157_H17 = """    display: none;
  }

  @media not -moz-pref("browser.nova.enabled") {
    > .toolbarbutton-icon {
      /* Smart Window icon has additional 0.125rem padding per side, to be slightly smaller than Classic Window icon when used in Switcher */
      padding: var(--space-xxsmall);
    }
  }
}

#appMenu-new-ai-window-button > moz-badge {
  margin-inline-start: auto;
}
""".splitlines()


def test_h17_two_added_blocks_in_one_hunk_are_each_placed_by_their_own_neighbour():
    body = list(CSS157_H17)
    new = firefox.auto_merge(body, H17)
    i = new.index('  @media not -moz-pref("browser.nova.enabled") {')
    assert new[i + 1:i + 6] == ["    /* stylelint-disable-next-line media-query-no-invalid */",
                                '    @media -moz-pref("browser.nova.enabled") {',
                                '      list-style-image: url("chrome://browser/skin/smart-window-nova.svg");', "    }", ""]
    j = new.index("    > .toolbarbutton-icon {")
    assert new[j + 1] == "      /* stylelint-disable-next-line media-query-no-invalid */"
    assert new[j + 2].startswith("      /* Smart Window icon")
    assert firefox.hunk_problems(body, new, H17) == [] and firefox.collateral(body, new, H17) == []


def test_a_done_removal_is_recognised_as_already_in_place_even_when_its_lines_live_elsewhere():
    """Live run 15: h15/h16 were merged, then reopened, then parked because tier 0 looked file-wide."""
    hunk = {"header": "@@", "lines": [" .toolbox-top-unique-selector {", "-  color: inherit;", "-  background-color: var(--toolbox-background-color);",
                                      "-  padding-inline-end: var(--toolbar-padding-inline);", " }", " ", " .another-unique-selector-after {"]}
    done = [".toolbox-top-unique-selector {", "}", "", ".another-unique-selector-after {", "}", "", ".other-rule {", "  color: inherit;",
            "  background-color: var(--toolbox-background-color);", "  padding-inline-end: var(--toolbar-padding-inline);", "}"]
    assert firefox.already_upstream(done, hunk)
    not_done = done[:1] + ["  color: inherit;", "  background-color: var(--toolbox-background-color);",
                           "  padding-inline-end: var(--toolbar-padding-inline);"] + done[1:]
    assert not firefox.already_upstream(not_done, hunk)


# tokens-platform.css h2: design-token files repeat the same declaration in every colour scheme, and upstream
# added two declarations inside the span
TOKENS = {"header": "@@", "lines": [
    "     --button-background-color: color-mix(in srgb, currentColor 13%, transparent);",
    "     --button-background-color-hover: color-mix(in srgb, currentColor 17%, transparent);",
    "     --button-background-color-active: color-mix(in srgb, currentColor 30%, transparent);",
    "-    --button-background-color-ghost: transparent;",
    "     --button-text-color: currentColor;",
    "-    --button-text-color-menu-selected: currentColor;",
    "     --button-text-color-primary: AccentColorText;", " ", "     /** color **/"]}
TOKENS_157 = """  @media (prefers-contrast) {
    --button-text-color: currentColor;
    --button-text-color-menu-selected: currentColor;
    --button-text-color-primary: AccentColorText;
  }
  @media (forced-colors) {
    --button-text-color: currentColor;
    --button-text-color-menu-selected: currentColor;
    --button-text-color-primary: AccentColorText;
  }
  :root {
    /* TODO Bug 1821203 - Gray use needs to be consolidated */
    --button-background-color: color-mix(in srgb, currentColor 13%, transparent);
    --button-background-color-hover: color-mix(in srgb, currentColor 17%, transparent);
    --button-background-color-active: color-mix(in srgb, currentColor 30%, transparent);
    --button-background-color-ghost: transparent;
    --button-background-color-ghost-hover: color-mix(in srgb, currentColor 17%, transparent);
    --button-background-color-ghost-active: color-mix(in srgb, currentColor 30%, transparent);
    --button-text-color: currentColor;
    --button-text-color-menu-selected: currentColor;
    --button-text-color-primary: AccentColorText;

    /** color **/
    --color-accent-attention: AccentColor;
  }
  @media (prefers-color-scheme: dark) {
    --button-text-color: currentColor;
    --button-text-color-menu-selected: currentColor;
    --button-text-color-primary: AccentColorText;
  }
""".splitlines()


def test_a_repeated_declaration_may_extend_the_span_in_order_and_the_two_new_lines_become_questions():
    body = list(TOKENS_157)
    at = firefox._anchor(body, [l[1:] for l in TOKENS["lines"] if l[:1] in (" ", "-")])
    auto_rm, uncertain = firefox.identify_questions(body, TOKENS, at)
    assert [body[n - 1].strip() for n in auto_rm] == ["--button-background-color-ghost: transparent;", "--button-text-color-menu-selected: currentColor;"]
    assert [body[n - 1].strip()[:38] for n in uncertain] == ["--button-background-color-ghost-hover:", "--button-background-color-ghost-active"]
