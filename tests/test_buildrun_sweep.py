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
