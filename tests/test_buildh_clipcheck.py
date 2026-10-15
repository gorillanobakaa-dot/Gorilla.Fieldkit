# Version: 1.0.0 · updated 26-10-15-16-03
"""clip-check: cut dialog text is measured, repeated, compared with stock, and a swap convicts only against a control."""
import subprocess

from fieldkit.buildh import clipcheck as cc

CUT = ["CLIP|text|leave-page|infoBody|40|8|This page is asking you to confirm",
       "CLIP|frame|leave-page|156|124", "CLIP|surface|leave-page|406x124|3",
       "CLIP|surface|alert|406x150|2"]
FITS = ["CLIP|surface|leave-page|406x156|3", "CLIP|surface|alert|406x150|2"]


def test_parse_reads_the_owner_screenshot_numbers():
    p = cc.parse(["noise"] + CUT + ["CLIP|error|prompt|no dialog opened"])
    s = p["surfaces"]["leave-page"]
    assert s["texts"][0] == {"element": "infoBody", "need": 40, "got": 8, "text": "This page is asking you to confirm"}
    assert s["frame"] == [156, 124] and s["size"] == "406x124"
    assert cc.clipped(p) == {"leave-page": True, "alert": False}
    assert p["errors"] == ["prompt: no dialog opened"]


def test_a_surface_that_never_reported_its_size_is_not_judged():
    # a run that died before measuring must not count as "fits"
    assert cc.clipped(cc.parse(["CLIP|text|alert|x|30|10|t"])) == {}


def _fake(sequence):
    """probe_run stand-in: returns the next canned run each call."""
    it = iter(sequence)
    return lambda install, js, **k: {"lines": next(it), "done": True}


def test_ours_when_stock_never_clips_and_the_race_is_reported():
    r = cc.run("/inst", repeats=3, stock_dir="/stock", out=lambda *a: None,
               probe_run=_fake([CUT, FITS, FITS, FITS, CUT, FITS]))
    row = next(x for x in r["rows"] if x["surface"] == "leave-page")
    assert row["verdict"] == "OURS" and row["ours"] == "2/3" and row["stock"] == "0/3"
    assert row["how"] == "2 of 3 runs" and "40" in row["worst"]
    assert not r["ok"]


def test_upstream_when_stock_clips_too_and_clean_when_nothing_clips():
    r = cc.run("/inst", repeats=1, stock_dir="/stock", out=lambda *a: None, probe_run=_fake([CUT, CUT]))
    assert {x["surface"]: x["verdict"] for x in r["rows"]} == {"alert": "CLEAN", "leave-page": "UPSTREAM"}
    assert r["ok"]


def test_one_good_run_never_convicts_a_file():
    # 2026-10-15: one run with the pristine file gave 156 px; the next two gave 124 px. The control must clip
    # every time and the change must fix every time.
    seq = lambda *v: (lambda it: lambda changed: next(it))(iter(v))
    assert cc.confirm(seq(True, False, True, True, True, True), 3)["confirmed"] is False
    assert cc.confirm(seq(True, False, True, False, True, False), 3)["confirmed"] is True
    assert cc.confirm(seq(False, False, True, False, True, False), 3)["confirmed"] is False


def test_swap_uses_the_pristine_file_and_an_identical_control(tmp_path):
    w = tmp_path / "src"
    (w / "toolkit").mkdir(parents=True)
    g = lambda *a: subprocess.run(["git", "-C", str(w), "-c", "user.name=t", "-c", "user.email=t@t", *a],
                                  check=True, capture_output=True)
    (w / "toolkit/D.mjs").write_text("pristine\n")
    g("init", "-q")
    g("add", "-A")
    g("commit", "-qm", "pristine")
    (w / "toolkit/D.mjs").write_text("ours\n")
    inst = tmp_path / "inst" / "modules"
    inst.mkdir(parents=True)
    (inst / "D.mjs").write_text("ours\n")
    seen = []

    def probe_run(install, js, files=None, **k):
        if files:
            text = files["modules/D.mjs"].read_text()
            seen.append(text)
            return {"lines": FITS if text == "pristine\n" else CUT, "done": True}
        return {"lines": CUT, "done": True}

    r = cc.run(tmp_path / "inst", workdir=w, repeats=2, swaps={"modules/D.mjs": "toolkit/D.mjs"},
               out=lambda *a: None, probe_run=probe_run)
    assert seen == ["ours\n", "pristine\n", "ours\n", "pristine\n"]   # control and change alternate
    assert r["tests"][0]["confirmed"] is True
    assert "CONFIRMED" in r["detail"]


def test_a_swap_that_cannot_be_made_is_said_not_silently_skipped(tmp_path):
    r = cc.run(tmp_path, workdir=tmp_path, repeats=1, swaps={"omni.ja": "x"}, out=lambda *a: None,
               probe_run=_fake([FITS]))
    assert r["tests"][0]["confirmed"] is False and "not tested" in r["detail"]


def test_pref_ab_sets_the_pref_only_on_the_changed_side():
    import os
    seen = []

    def probe_run(install, js, **k):
        seen.append(os.environ.get("GPROBE_PREFS"))
        return {"lines": FITS if os.environ.get("GPROBE_PREFS") else CUT, "done": True}

    r = cc.run("/inst", repeats=2, prefs={"ui.prefersReducedMotion": 0}, out=lambda *a: None, probe_run=probe_run)
    assert seen[2:] == ["", "ui.prefersReducedMotion=0", "", "ui.prefersReducedMotion=0"]
    assert r["tests"][0]["confirmed"] is True


# The real lines of 2026-10-15 (Gorilla 158 installed, and stock Firefox 158), trimmed to one surface.
REAL_OURS = [
    "CLIP|text|leave-page|description#infoBody|40|8|This page is asking you to confirm",
    "CLIP|box|leave-page|measure|/window#commonDialogWindow/dialog#commonDialog|124px|0px/0px|0px/0px",
    "CLIP|moved|leave-page|dialog#commonDialog.sizeDetermined|padding-top|0px|16px",
    "CLIP|rule|leave-page|dialog#commonDialog.sizeDetermined|padding-top|chrome://global/content/commonDialog.css:91"
    "|& dialog|var(--grid-padding) calc(var(--grid-padding) - 4px)",
    "CLIP|anim|leave-page|dialog#commonDialog|CSSTransition|padding-top|0.009999000000000001",
    "CLIP|rule|leave-page|dialog#commonDialog.sizeDetermined|transition|chrome://browser/skin/master-redirect.css:67"
    "|*, ::before, ::after|0.01ms 0.01ms",
    "CLIP|surface|leave-page|406x124|2",
]
REAL_STOCK = [
    "CLIP|box|leave-page|measure|/window#commonDialogWindow/dialog#commonDialog|156px|16px/16px|0px/0px",
    "CLIP|surface|leave-page|406x156|2",
]


def test_the_real_case_is_explained_down_to_the_rule_and_line():
    r = cc.run("/inst", repeats=1, stock_dir="/stock", out=lambda *a: None, probe_run=_fake([REAL_OURS, REAL_STOCK]))
    row = r["rows"][0]
    assert row["verdict"] == "OURS"
    why = "\n".join(row["why"])
    assert "padding-top was 0px when the dialog height was measured, 16px after" in why
    assert "CSSTransition on padding-top (0.01 ms) was running" in why
    assert "commonDialog.css:91" in why and "master-redirect.css:67" in why
    assert "ours padding 0px/0px" in row["vs_stock"][0] and "stock padding 16px/16px" in row["vs_stock"][0]
    assert "why:" in r["detail"] and "differs from stock" in r["detail"]


def test_try_a_candidate_fix_against_the_shipped_file(tmp_path):
    inst = tmp_path / "inst" / "skin"
    inst.mkdir(parents=True)
    (inst / "m.css").write_text("shipped\n")
    fix = tmp_path / "fixed.css"
    fix.write_text("fixed\n")

    def probe_run(install, js, files=None, **k):
        return {"lines": FITS if files and files["skin/m.css"].read_text() == "fixed\n" else CUT, "done": True}

    r = cc.run(tmp_path / "inst", repeats=2, stock_dir=None, tries={"skin/m.css": str(fix)}, out=lambda *a: None,
               probe_run=probe_run)
    assert r["tests"][0]["confirmed"] is True and "try skin/m.css" in r["tests"][0]["test"]
