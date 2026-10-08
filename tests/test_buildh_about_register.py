"""about_register (2026-10-08): every about: page the tree can register is in the owner's reviewed register, and no
page it marks "remove" is registered - a new upstream page stops the build until someone has looked at it."""
import subprocess

from fieldkit.buildh import decisions


def _tree(tmp_path, names):
    w = tmp_path / "tree"
    (w / "docshell" / "build").mkdir(parents=True)
    (w / "docshell" / "build" / "components.conf").write_text(
        "about_pages = [\n" + "".join(f"    '{n}',\n" for n in names) + "]\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(w), "init", "-q"], check=True)
    return w


def _reg(tmp_path, rows):
    p = tmp_path / "ABOUT-PAGES.yaml"
    p.write_text("pages:\n" + "".join(f"  - {{name: {n}, verdict: {v}}}\n" for n, v in rows), encoding="utf-8")
    return p


def test_all_reviewed_and_removed_ones_gone_passes(tmp_path):
    ok, ev = decisions.about_register(_reg(tmp_path, [("about", "keep"), ("glean", "remove")]), _tree(tmp_path, ["about"]))
    assert ok and "1 registered page(s), all reviewed (1 kept)" in ev


def test_a_new_page_or_a_removed_page_back_fails(tmp_path):
    w = _tree(tmp_path, ["about", "shiny-new", "glean"])
    ok, ev = decisions.about_register(_reg(tmp_path, [("about", "keep"), ("glean", "remove")]), w)
    assert not ok and "not reviewed: about:shiny-new" in ev and "marked remove but registered: about:glean" in ev


def test_a_verdict_must_be_keep_or_remove(tmp_path):
    ok, ev = decisions.about_register(_reg(tmp_path, [("about", "maybe")]), _tree(tmp_path, ["about"]))
    assert not ok and "verdict must be keep or remove: about" in ev
