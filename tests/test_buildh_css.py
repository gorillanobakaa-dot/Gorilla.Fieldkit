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
