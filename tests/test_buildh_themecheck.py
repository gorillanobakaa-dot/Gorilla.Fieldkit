# Version: 1.0.0 · updated 26-10-18-23-00
"""D-158-19: the Gorilla theme is one constant (decisions/THEME.yaml), checked before and after the build.
2026-10-18: the search-mode list shipped white under cyan text (1.25:1), the hover came from Windows, the app menu cut
a label, and 18 colours in Gorilla's own stylesheet lines were outside the theme."""
import subprocess

import pytest

from fieldkit.buildh import themecheck as tc

SPEC = """version: 1
colours:
  black: "#000000"
  cyan: "#00FFFF"
  highlight: "#65568C"
  pink: "#FFC0CB"
  teal_dark: "#1A4D4D"
pairs:
  - [cyan, black]
  - [cyan, highlight]
  - [black, pink]
hover: highlight
focus: cyan
contrast_floor_from_pairs: true
tolerance: 6
"""


@pytest.fixture
def spec(tmp_path):
    p = tmp_path / "THEME.yaml"
    p.write_text(SPEC, encoding="utf-8")
    return tc.load(p)


def item(surface, text, bg, hover, clipped=False):
    return {"surface": surface, "label": "x", "contrast": tc.contrast(text, bg), "text": text, "background": bg,
            "hover": hover, "clipped": clipped}


def test_the_floor_is_the_weakest_allowed_pair(spec):
    assert 4.9 < spec["floor"] < 5.1                         # cyan on the highlight


def test_the_white_search_list_fails_and_the_black_app_menu_passes(spec):
    white = item("search-mode list", (0, 255, 255), (255, 255, 255), (255, 255, 255))
    black = item("app menu", (0, 255, 255), (0, 0, 0), (101, 86, 140))
    rows = {r["check"]: r for r in tc.judge_popups([white, black], spec)}
    pair = next(r for k, r in rows.items() if "colour pair" in k)
    assert not pair["ok"] and "search-mode list" in pair["evidence"] and "(not in the theme)" in pair["evidence"]
    hover = next(r for k, r in rows.items() if "hover" in k)
    assert not hover["ok"] and "search-mode list" in hover["evidence"]
    assert tc.judge_popups([black], spec)[0]["ok"]


def test_a_cut_label_fails(spec):
    rows = tc.judge_popups([item("app menu", (0, 255, 255), (0, 0, 0), (101, 86, 140), clipped=True)], spec)
    assert not next(r for r in rows if "cut short" in r["check"])["ok"]


def test_no_measurement_is_a_failure_not_a_pass(spec):
    assert not tc.judge_popups([], spec)[0]["ok"]


def git(d, *a):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-C", str(d), *a], check=True,
                   capture_output=True)


def test_static_judges_only_gorilla_lines_and_apply_maps_roles(tmp_path, spec):
    (tmp_path / "t").mkdir()
    css = tmp_path / "t" / "x.css"
    css.write_text("a { color: #123456; }\n", newline="\n")              # upstream's own colour: not judged
    git(tmp_path / "t", "init", "-q")
    git(tmp_path / "t", "add", "-A")
    git(tmp_path / "t", "commit", "-qm", "pristine")
    css.write_text("a { color: #123456; }\n.p { background-color: #20123a !important; }\n.q { color: #b0b0b8; }\n"
                   ".r { border: 1px solid #2b2b2e; }\n.f { --x-fakefocus-border-color: #0080ff; }\n", newline="\n")
    r = tc.static(tmp_path / "t", spec)
    assert not r["ok"] and {c for _, _, c, _ in r["found"]} == {"#20123A", "#B0B0B8", "#2B2B2E", "#0080FF"}
    plan = {c: (new, role) for _, _, c, new, role, _ in tc.apply(tmp_path / "t", spec, dry_run=False)}
    assert plan["#20123A"] == ("#000000", "black") and plan["#B0B0B8"] == ("#00FFFF", "cyan")
    assert plan["#2B2B2E"] == ("#1A4D4D", "teal_dark") and plan["#0080FF"] == ("#00FFFF", "cyan")   # focus is cyan
    assert tc.static(tmp_path / "t", spec)["ok"]
    assert "#123456" in css.read_text()                                   # upstream untouched
