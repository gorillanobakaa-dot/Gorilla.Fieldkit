"""D-158-03 (owner, 2026-10-15): closed by default; only what fits no class is a question."""
from fieldkit.migrate import intake


def test_the_classes_the_owner_named_are_cut_without_asking():
    for name in ("browser.ml.chat.enabled", "browser/components/aiwindow/x.sys.mjs", "newtab weather widget",
                 "browser.urlbar.suggest.trending", "nimbus experiment rollout", "remote/marionette/server.sys.mjs",
                 "toolkit.telemetry.glean.ping", "browser.translations.enable", "colorway-theme-picker"):
        assert intake.deny_class(name), name
    assert intake.deny_class("layout.css.grid.enabled") is None


def test_default_cut_items_are_not_open_questions():
    data = {"items": [{"id": "a", "default_cut": "AI / ML", "auto": None}, {"id": "b", "default_cut": None, "auto": None}],
            "clusters": [{"id": "c1", "items": ["a", "b"]}, {"id": "c2", "items": ["a"]}]}
    open_ = intake.open_clusters(data, decided={})
    assert [c["id"] for c in open_] == ["c1"] and open_[0]["open"] == ["b"]
    assert [i["id"] for i in intake.default_cuts(data)] == ["a"]
