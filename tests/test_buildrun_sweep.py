"""The dist/bin sweep removes excised files from dist/bin AND dist/bin/browser (browser/omni.ja's source)."""
from fieldkit.buildh import buildrun


def test_sweep_reaches_the_browser_folder(tmp_path):
    stale = tmp_path / "browser" / "chrome" / "browser" / "content" / "builtin-themes" / "alpenglow"
    stale.mkdir(parents=True)
    (stale / "icon.svg").write_text("<svg/>", encoding="utf-8")
    removed = buildrun.sweep_excised_dist(tmp_path, lambda m: None)
    assert not stale.exists()
    assert any(r.startswith("browser/chrome/browser/content/builtin-themes/alpenglow") for r in removed)


def test_a_pref_the_patch_added_counts_when_a_decision_locked_it():
    from fieldkit.buildh import verify
    have = {'pref("a.b", false, locked);'}
    assert verify._locked_variant('pref("a.b", false);', have)
    assert not verify._locked_variant('pref("a.b", true);', have)
    assert not verify._locked_variant('pref("a.b", false, locked);', {'pref("a.b", false);'})


def test_an_unsorted_mozbuild_list_is_found_sorted_and_comments_travel(tmp_path):
    from fieldkit.buildh import mozbuild_rules as m
    p = tmp_path / "moz.build"
    p.write_text('EXTRA_JS_MODULES += [\n    "AboutNewTab.sys.mjs",\n    # mode\n    "Gorilla.sys.mjs",\n'
                 '    "AIWindowStub.sys.mjs",\n]\nSOURCES["a.c"].flags += [\n    "-z",\n    "-a",\n]\n', encoding="utf-8")
    assert [v for _, _, v in m.unsorted_lists(p.read_text(encoding="utf-8"))] == ["AIWindowStub.sys.mjs"]
    assert m.fix_unsorted_lists(p) == ["EXTRA_JS_MODULES"]
    t = p.read_text(encoding="utf-8")
    assert t.index("AboutNewTab") < t.index("AIWindowStub") < t.index("# mode") < t.index("Gorilla.sys.mjs")
    assert m.unsorted_lists(t) == [] and '"-z",\n    "-a"' in t


def test_a_hot_laptop_cools_before_any_load():
    from fieldkit.buildh import buildrun
    temps = iter([86.0, 80.0, 70.0])
    waited = []
    assert buildrun.cool_down(lambda m: None, sleep=waited.append, read=lambda: next(temps)) == 70.0
    assert waited == [30, 30]


def test_a_compiled_resource_older_than_the_icons_is_removed(tmp_path):
    import os
    import time
    from fieldkit.buildh import buildrun
    od, br = tmp_path / "obj", tmp_path / "branding"
    (od / "browser" / "app").mkdir(parents=True)
    br.mkdir()
    res = od / "browser" / "app" / "firefox.exe.res"
    res.write_bytes(b"old")
    old = time.time() - 3600
    os.utime(res, (old, old))
    (br / "newtab.ico").write_bytes(b"new")
    assert buildrun.stale_resources(od, br, lambda m: None) == [str(res)]
    assert not res.exists()
