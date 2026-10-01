"""Keyed files (.properties / .dtd / .ini) ported by key, the same shape as the Fluent and prefs tiers."""
from fieldkit.buildh import keyed

PROPS_157 = """# License header
# comment

brandShortName = Firefox
tabs.closeTab = Close Tab
tabs.closeTabs = Close Tabs
longEntry = first part \\
    second part
newThing = Added upstream
""".splitlines()

HUNK = {"header": "@@", "lines": [" # comment", " ", "-brandShortName = Firefox", "+brandShortName = Gorilla Unleashed",
                                  " tabs.closeTab = Close Tab", "-tabs.closeTabs = Close Tabs", "+tabs.closeTabs = Close Gorilla Tabs"]}


def test_entries_fold_continuations_and_skip_comments():
    e = {x["key"]: x for x in keyed.entries(PROPS_157, "a.properties")}
    assert e["longEntry"]["value"] == "first part second part" and e["longEntry"]["end"] - e["longEntry"]["start"] == 2
    assert "# comment" not in e and e["brandShortName"]["value"] == "Firefox"


def test_values_are_set_by_key_in_the_files_own_style_and_checked():
    new, notes, gone = keyed.port(list(PROPS_157), HUNK, "a.properties")
    assert gone == [] and "brandShortName = Gorilla Unleashed" in new and "tabs.closeTabs = Close Gorilla Tabs" in new
    assert new.index("brandShortName = Gorilla Unleashed") == PROPS_157.index("brandShortName = Firefox")
    assert keyed.check(PROPS_157, new, HUNK, "a.properties") == []
    assert notes == ["ported by key: 2 set, 0 dropped"]


def test_a_key_upstream_removed_goes_to_the_owner_with_candidates():
    body = [l.replace("tabs.closeTabs = Close Tabs", "tabs.closeTabs2 = Close Tabs") for l in PROPS_157]
    new, notes, gone = keyed.port(body, HUNK, "a.properties")
    assert new == body and dict(gone)["tabs.closeTabs"] == ["tabs.closeTabs2"]


def test_a_new_key_is_not_placed_by_this_tier():
    hunk = {"header": "@@", "lines": [" tabs.closeTab = Close Tab", "+gorilla.newKey = Hello"]}
    new, notes, gone = keyed.port(list(PROPS_157), hunk, "a.properties")
    assert new == PROPS_157 and "new keys need a place" in gone[0][0]


def test_dtd_entities_are_keys_too():
    dtd = ['<!ENTITY brandShortName "Firefox">', '<!ENTITY other "Thing">']
    hunk = {"header": "@@", "lines": ['-<!ENTITY brandShortName "Firefox">', '+<!ENTITY brandShortName "Gorilla">', ' <!ENTITY other "Thing">']}
    new, notes, gone = keyed.port(dtd, hunk, "brand.dtd")
    assert new == ['<!ENTITY brandShortName "Gorilla">', '<!ENTITY other "Thing">'] and keyed.check(dtd, new, hunk, "brand.dtd") == []


def test_ini_keys_are_qualified_by_section():
    ini = ["[App]", "Name=Firefox", "[Other]", "Name=Thing"]
    hunk = {"header": "@@", "lines": [" [App]", "-Name=Firefox", "+Name=Gorilla Unleashed", " [Other]"]}
    new, notes, gone = keyed.port(ini, hunk, "application.ini")
    assert new == ["[App]", "Name=Gorilla Unleashed", "[Other]", "Name=Thing"]


def test_the_check_catches_wrong_values_and_collateral_but_not_in_the_final_re_check():
    new, _, _ = keyed.port(list(PROPS_157), HUNK, "a.properties")
    wrong = [l.replace("Gorilla Unleashed", "Something") for l in new]
    assert any("does not read as the patch wants" in w for w in keyed.check(PROPS_157, wrong, HUNK, "a.properties"))
    later = [l.replace("Added upstream", "changed by a later hunk") for l in new]
    assert any("changed, but the patch does not touch it" in w for w in keyed.check(PROPS_157, later, HUNK, "a.properties"))
    assert keyed.check(PROPS_157, later, HUNK, "a.properties", collateral=False) == []


def test_auto_port_and_check_port_use_the_keyed_tier(tmp_path):
    import subprocess
    from fieldkit.buildh import firefox
    w = tmp_path / "w"
    (w / "chrome").mkdir(parents=True)
    f = w / "chrome" / "browser.properties"
    f.write_text("\n".join(PROPS_157) + "\n", encoding="utf-8", newline="")
    subprocess.run(["git", "init", "-q", str(w)], check=True)
    subprocess.run(["git", "-C", str(w), "-c", "user.name=t", "-c", "user.email=t@example.com", "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(w), "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "157"], check=True)
    t = {"workdir": str(w)}
    res = firefox.auto_port(t, {"id": "x"}, "08.Look/p.patch", "chrome/browser.properties", HUNK)
    assert res["ok"] and res["notes"] == ["ported by key: 2 set, 0 dropped"]
    assert firefox.check_port(t, {"id": "x"}, "08.Look/p.patch", "chrome/browser.properties", HUNK) == {"ok": True, "why": []}
    gone = {"header": "@@", "lines": ["-tabs.closeTabs = Close Tabs", "+tabs.closeTabs = Close Gorilla Tabs"]}
    f.write_text("\n".join(l.replace("tabs.closeTabs = Close Tabs", "tabs.closeTabs2 = Close Tabs") for l in PROPS_157) + "\n", encoding="utf-8", newline="")
    res = firefox.auto_port(t, {"id": "y"}, "08.Look/p.patch", "chrome/browser.properties", gone)
    assert res.get("defer") and "tabs.closeTabs2" in res["why"][0]
