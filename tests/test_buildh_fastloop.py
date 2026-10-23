"""The fast loop as a gate (2026-10-18): a chrome change is tried on the last build's unpacked dist/bin before the
next compile, and judged by build-verify's own rows."""
import subprocess

import pytest

from fieldkit.buildh import fastloop, probe, themecheck

# the theme spec the gate judges by, as a fixture: the owner's real THEME.yaml lives in Gorilla.firefox, which a fresh
# download (and GitHub CI) does not have (2026-10-20: both CI systems failed here; the laptop passed because it has it)
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


@pytest.fixture(autouse=True)
def _spec(tmp_path, monkeypatch):
    p = tmp_path / "THEME.yaml"
    p.write_text(SPEC, encoding="utf-8")
    monkeypatch.setattr(themecheck, "SPEC_PLACES", [str(p)])


def _tree(tmp_path):
    w = tmp_path / "src"
    (w / "t").mkdir(parents=True)
    g = lambda *a: subprocess.run(["git", "-C", str(w), "-c", "user.name=t", "-c", "user.email=t@t", *a], check=True,
                                  capture_output=True, text=True).stdout.strip()
    (w / "t" / "m.css").write_bytes(b"menupopup { background: Menu; }\n")
    g("init", "-q")
    g("add", "-A")
    g("commit", "-qm", "built")
    head = g("rev-parse", "HEAD")
    (w / "t" / "m.css").write_bytes(b"menupopup { background: #000; }\n")
    g("commit", "-qam", "theme")
    return w, head


def _dist(tmp_path):
    d = tmp_path / "obj" / "dist" / "bin" / "browser" / "chrome" / "browser" / "skin"
    d.mkdir(parents=True)
    (d / "m.css").write_bytes(b"menupopup { background: Menu; }\n")
    return tmp_path / "obj"


GOOD = ["UI|menu|gorilla-satellite-button|Off|16.75|rgb(0,255,255)|rgb(0,0,0)",
        "UI|popup|app menu|New Tab|16.75|rgb(0,255,255)|rgb(0,0,0)|rgb(101,86,140)|0|0",
        "UI|surface|app menu|1|0"]
BAD = ["UI|popup|tab right-click menu|Reload|18.31|rgb(21,20,26)|rgb(255,255,255)|rgb(255,255,255)|0|0",
       "UI|surface|tab right-click menu|1|0"]


def test_the_changed_css_is_tried_on_the_unpacked_build_and_a_bad_menu_fails_the_gate(tmp_path):
    w, head = _tree(tmp_path)
    obj = _dist(tmp_path)
    seen = {}

    def run(dist, js, files=None, **kw):
        seen.update(files or {})
        return {"done": True, "lines": BAD}
    rows = fastloop.rows("t", {"workdir": str(w)}, say=lambda m: None, run=run,
                         record={"head": head, "objdir": str(obj)})
    assert list(seen) == ["browser/chrome/browser/skin/m.css"]           # found by the OLD version's bytes
    assert open(seen["browser/chrome/browser/skin/m.css"], "rb").read() == b"menupopup { background: #000; }\n"
    assert not all(r["ok"] for r in rows)


def test_a_good_menu_passes_and_no_earlier_build_says_so(tmp_path):
    w, head = _tree(tmp_path)
    obj = _dist(tmp_path)
    rows = fastloop.rows("t", {"workdir": str(w)}, say=lambda m: None, run=lambda *a, **k: {"done": True, "lines": GOOD},
                         record={"head": head, "objdir": str(obj)})
    assert all(r["ok"] for r in rows), rows
    first = fastloop.rows("t", {"workdir": str(w)}, record={"head": head, "objdir": str(tmp_path / "none")})
    assert first[0]["ok"] and "no earlier unpacked build" in first[0]["evidence"]


def test_tree_since_maps_an_unpacked_build_by_content(tmp_path):
    w, head = _tree(tmp_path)
    obj = _dist(tmp_path)
    got, skipped = probe.tree_since(w, head, obj / "dist" / "bin", tmp_path / "out")
    assert list(got) == ["@browser/chrome/browser/skin/m.css"] and skipped == []


def test_the_theme_spec_is_found_in_the_patch_set_repo_named_locally(tmp_path, monkeypatch):
    # 2026-10-23, Linux: the spec lives in <firefox.root>/decisions/THEME.yaml, not in the Windows Gorilla.firefox folder
    from fieldkit.buildh import themecheck
    from fieldkit.core import settings
    spec = tmp_path / "decisions" / "THEME.yaml"
    spec.parent.mkdir()
    spec.write_text("colours: {}\n")
    monkeypatch.setattr(themecheck, "SPEC_PLACES", [str(tmp_path / "absent.yaml")])
    monkeypatch.setattr(settings, "local_settings", lambda: {"firefox": {"root": str(tmp_path)}})
    assert themecheck.find_spec() == spec
