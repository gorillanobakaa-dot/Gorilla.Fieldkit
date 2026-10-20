# Version: 1.0.0 · updated 26-10-20-19-00
"""gtk-theme check: the three mistakes of 2026-10-20 (Black Gorilla Theme, Files sidebar grey on Alt+Tab) must each be
caught. The synthetic rules below are copied from libadwaita 1.7.6 / GTK 3.24.49 as this machine had them; the
last tests read the real libraries when they are installed and are skipped elsewhere."""
import os

import pytest

from fieldkit.gtktheme import gtkcheck as g

ADW = {
    (":root", "--sidebar-backdrop-color: @sidebar_backdrop_color; --headerbar-backdrop-color: @headerbar_backdrop_color"),
    ("headerbar:backdrop > windowhandle", "filter: opacity(0.5); transition: filter 200ms ease-out;"),
    ("searchbar > revealer > box:backdrop > *", "filter: opacity(0.5);"),
    ("headerbar", "filter: opacity(0.5);"),                      # not a backdrop rule: never reported
}
GTK3 = {
    ("@define-color", "theme_unfocused_bg_color #353535"),
    ("@define-color", "unfocused_borders #202020"),
    (".background:backdrop", "color: #919190; background-color: #353535;"),
    ("button:backdrop, notebook > header > tabs > arrow:backdrop", "color: #919190;"),
    ("label:backdrop", "color: transparent;"),                   # not a text colour: never reported
}

# The block as released 26.08.15-08-17: focused names only, no cancels.
OLD = """@define-color sidebar_bg_color #000000; @define-color headerbar_bg_color #000000;
@define-color theme_unfocused_bg_color #000000;"""
# The block as released 26.10.20: backdrop names, cancels, cyan text in both states.
NEW = """@define-color sidebar_backdrop_color #000000; @define-color headerbar_backdrop_color #000000;
@define-color theme_unfocused_bg_color #000000; @define-color unfocused_borders #00FFFF;
/* a comment { with braces } is not a rule */
headerbar:backdrop > windowhandle,
searchbar > revealer > box:backdrop > * { filter: none; }
.background, .background:backdrop { color: #00FFFF; }
button:not(:disabled):not(.suggested-action), notebook > header > tabs > arrow:not(:disabled) { color: inherit; }"""


def test_old_theme_is_caught_on_every_count():
    assert g.check_names(ADW, OLD, "libadwaita") == ["headerbar_backdrop_color", "sidebar_backdrop_color"]
    assert g.check_fades(ADW, OLD) == ["headerbar:backdrop > windowhandle", "searchbar > revealer > box:backdrop > *"]
    assert g.check_names(GTK3, OLD, "gtk3") == ["unfocused_borders"]
    root, others = g.check_greys(GTK3, OLD)
    assert root == [".background:backdrop"]
    assert others == ["button:backdrop", "notebook > header > tabs > arrow:backdrop"]


def test_fixed_theme_is_clean():
    assert g.check_names(ADW, NEW, "libadwaita") == []
    assert g.check_fades(ADW, NEW) == []
    assert g.check_names(GTK3, NEW, "gtk3") == []
    assert g.check_greys(GTK3, NEW) == ([], [])


def test_base_drops_state_and_exclusions_only():
    assert g.base("button:backdrop:not(:disabled):not(.suggested-action)") == "button"
    assert g.base("menu menuitem:backdrop") == "menu menuitem"
    assert g.base("row:selected button.flat:not(:active):backdrop") == "row:selected button.flat"


def test_selector_lists_split_outside_brackets_only():
    assert g._parts("a:not(.x, .y), b > c") == ["a:not(.x, .y)", "b > c"]


def test_comments_do_not_define_names():
    assert g.defined_names("/* @define-color sidebar_backdrop_color #000; */") == set()


def test_block_is_found_between_its_markers():
    t = "x\n/* BEGIN black-gorilla-theme */\na{}\n/* END black-gorilla-theme */\ny"
    assert g._block(t).startswith("/* BEGIN") and g._block(t).endswith("theme */")
    assert g._block("no theme here") is None


@pytest.mark.skipif(not g.find_lib("libadwaita-1.so.0"), reason="libadwaita not installed")
def test_real_libadwaita_old_theme_misses_the_sidebar():
    rules = g.embedded_rules(g.find_lib("libadwaita-1.so.0"))
    assert "sidebar_backdrop_color" in g.check_names(rules, OLD, "libadwaita")
    assert "headerbar:backdrop > windowhandle" in g.check_fades(rules, OLD)


@pytest.mark.skipif(not g.find_lib("libgtk-3.so.0"), reason="GTK3 not installed")
def test_real_gtk3_definitions_are_read():
    # the first version read rules only and never saw GTK3's @define-color lines: this name check could not fire
    rules = g.embedded_rules(g.find_lib("libgtk-3.so.0"))
    assert {"theme_unfocused_selected_bg_color", "wm_unfocused_title"} <= set(g.check_names(rules, "", "gtk3"))


@pytest.mark.skipif(not os.path.isdir("/proc/self"), reason="no /proc")
def test_stale_apps_never_lists_this_process():
    assert all(a["pid"] != os.getpid() for a in g.stale_apps())
