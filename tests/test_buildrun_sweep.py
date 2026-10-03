"""The dist/bin sweep removes excised files from dist/bin AND dist/bin/browser (browser/omni.ja's source)."""
from fieldkit.buildh import buildrun


def test_sweep_reaches_the_browser_folder(tmp_path):
    stale = tmp_path / "browser" / "chrome" / "browser" / "content" / "builtin-themes" / "alpenglow"
    stale.mkdir(parents=True)
    (stale / "icon.svg").write_text("<svg/>", encoding="utf-8")
    removed = buildrun.sweep_excised_dist(tmp_path, lambda m: None)
    assert not stale.exists()
    assert any(r.startswith("browser/chrome/browser/content/builtin-themes/alpenglow") for r in removed)
