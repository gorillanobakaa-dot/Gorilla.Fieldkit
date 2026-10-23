# Version: 1.0.0 · updated 26-10-23-20-30
"""The static visual layer on Linux (2026-10-23, 158.0-2): Windows-only files are not judged there, and a patch-set
repo that is its own root (gorilla-firefox) resolves {owner}/gorilla-patchset/ masters."""
from fieldkit.visual import allow, static


def test_files_only_another_platform_builds_are_left_out_and_counted():
    items = [{"item": "browser/branding/gorilla/msix/Assets/StoreLogo.scale-200.png"},
             {"item": "browser/branding/gorilla/branding.nsi:39:Mozilla"},
             {"item": "browser/branding/gorilla/firefox.ico#16"}, {"item": "browser/branding/gorilla/default48.png"}]
    keep, n = static.other_platform_items(items, platform="linux")
    assert n == 2 and [i["item"] for i in keep] == ["browser/branding/gorilla/firefox.ico#16",
                                                     "browser/branding/gorilla/default48.png"]
    assert static.other_platform_items(items, platform="windows")[1] == 0


def test_a_patch_set_repo_at_its_own_root_holds_the_masters(tmp_path):
    rules = {"masters": [{"files": "browser/branding/gorilla/**", "master": "{owner}/gorilla-patchset/masters/m.png"}]}
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "patch_policy.json").write_text("{}")
    path, _ = allow.master_for("browser/branding/gorilla/a.png", rules, tmp_path)
    assert path == tmp_path / "masters" / "m.png"
    (tmp_path / "gorilla-patchset").mkdir()                       # the Windows layout keeps its own path
    path, _ = allow.master_for("browser/branding/gorilla/a.png", rules, tmp_path)
    assert path == tmp_path / "gorilla-patchset" / "masters" / "m.png"
