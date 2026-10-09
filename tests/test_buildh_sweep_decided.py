"""Pre-packaging sweep of what a decision declares absent (2026-10-09, D-157-40): an incremental build left removed
pages' files in dist/bin, the packager shipped them, and the decision failed after install."""
import yaml

from fieldkit.buildh import buildrun


def test_decided_absent_reads_enforced_omni_absent_checks(tmp_path):
    d = tmp_path / "decisions"
    d.mkdir()
    (d / "PRODUCT-DECISIONS.yaml").write_text(yaml.safe_dump({"decisions": [
        {"id": "D-1", "title": "a", "decided": "2026-10-09", "by": "maintainer", "status": "enforced", "why": "x",
         "verify": [{"omni_absent": ["chrome/browser/content/browser/blockedSite", "chrome/toolkit/content/global/aboutRestricted/"]}]},
        {"id": "D-2", "title": "b", "decided": "2026-10-09", "by": "maintainer", "status": "retired", "why": "x",
         "verify": [{"omni_absent": ["chrome/never/"]}]},
    ]}), encoding="utf-8")
    assert buildrun.decided_absent(tmp_path) == ["chrome/browser/content/browser/blockedSite",
                                                 "chrome/toolkit/content/global/aboutRestricted/"]
    assert buildrun.decided_absent(tmp_path / "nowhere") == []


def test_the_sweep_removes_what_is_declared_absent_and_nothing_else(tmp_path):
    db = tmp_path / "bin"
    content = db / "browser" / "chrome" / "browser" / "content" / "browser"
    content.mkdir(parents=True)
    for n in ("blockedSite.js", "blockedSite.xhtml", "browser.js"):
        (content / n).write_text("x", encoding="utf-8")
    restricted = db / "chrome" / "toolkit" / "content" / "global" / "aboutRestricted"
    restricted.mkdir(parents=True)
    (restricted / "aboutRestricted.html").write_text("x", encoding="utf-8")
    branding = db / "browser" / "chrome" / "browser" / "content" / "branding"
    branding.mkdir(parents=True)
    for n in ("about-logo.png", "about-logo.svg"):
        (branding / n).write_text("x", encoding="utf-8")
    removed = buildrun.sweep_decided_absent(db, ["chrome/browser/content/browser/blockedSite",
                                                 "chrome/toolkit/content/global/aboutRestricted/",
                                                 "chrome/browser/content/branding/about-logo.svg"], say=lambda m: None)
    assert sorted(removed) == ["browser/chrome/browser/content/branding/about-logo.svg",
                               "browser/chrome/browser/content/browser/blockedSite.js",
                               "browser/chrome/browser/content/browser/blockedSite.xhtml",
                               "chrome/toolkit/content/global/aboutRestricted/"]
    assert (content / "browser.js").exists() and (branding / "about-logo.png").exists()     # the rest stays
    assert not restricted.exists()
