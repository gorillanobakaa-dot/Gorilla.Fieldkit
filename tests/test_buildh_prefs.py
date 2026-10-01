"""Pref files ported by pref name. Shapes taken from the real firefox.js h34/h35/h39 of 2026-10-01: the owner sets
values (often `false, locked`) on prefs upstream has moved, re-wrapped in #ifdef, or removed."""
from fieldkit.buildh import prefs

HUNK = {"header": "@@", "lines": [
    " // Smart window",
    '-pref("browser.ml.enable", true);',
    '+pref("browser.ml.enable", false, locked); // GORILLA: no on-device ML',
    '-pref("browser.ml.chat.enabled", true);',
    '+pref("browser.ml.chat.enabled", false, locked);',
    '-pref("extensions.ml.enabled", true);',
    '+pref("extensions.ml.enabled", false, locked);',
    '+pref("intl.accept_languages", "en-US, en");',
    '-pref("browser.ml.linkPreview.longPress", true);',
    " "]}

FF157 = """// elsewhere, upstream moved it and wrapped it
#ifdef NIGHTLY_BUILD
  pref("browser.ml.enable", true);
#else
  pref("browser.ml.enable", false);
#endif
pref("browser.ml.chat.enabled", true);
pref("browser.ml.linkPreview.longPress", true);
pref("browser.ml.linkPreview.enabled", true);
""".splitlines()


def test_changes_are_read_by_name_comments_ignored():
    ch = prefs.changes(HUNK)
    assert set(ch["set"]) == {"browser.ml.enable", "browser.ml.chat.enabled", "extensions.ml.enabled"}
    assert ch["drop"] == ["browser.ml.linkPreview.longPress"] and ch["add"] == ['pref("intl.accept_languages", "en-US, en");']


def test_a_pref_defined_more_than_once_is_the_owners_call_never_a_guess():
    new, notes, gone = prefs.port(list(FF157), HUNK)
    assert new == FF157
    assert dict(gone)["browser.ml.enable"][0].startswith("defined 2 times")
    assert dict(gone)["extensions.ml.enabled"] == []                 # removed upstream, no candidate


def test_values_are_set_in_place_by_name_wherever_the_pref_lives_and_drops_happen():
    hunk = {"header": "@@", "lines": ['-pref("browser.ml.chat.enabled", true);', '+pref("browser.ml.chat.enabled", false, locked);',
                                      '-pref("browser.ml.linkPreview.longPress", true);']}
    new, notes, gone = prefs.port(list(FF157), hunk)
    assert gone == []
    assert 'pref("browser.ml.chat.enabled", false, locked);' in new and 'pref("browser.ml.linkPreview.longPress", true);' not in new
    assert 'pref("browser.ml.linkPreview.enabled", true);' in new
    assert prefs.check(FF157, new, hunk) == []
    assert notes == ["ported by pref name: 1 set, 1 dropped"]


def test_the_indent_of_the_moved_pref_is_kept():
    body = ["#ifdef X", '  pref("a.b.c", true);', "#endif"]
    hunk = {"header": "@@", "lines": ['-pref("a.b.c", true);', '+pref("a.b.c", false, locked);']}
    new, _, _ = prefs.port(body, hunk)
    assert new[1] == '  pref("a.b.c", false, locked);'


def test_the_check_catches_wrong_values_and_collateral():
    hunk = {"header": "@@", "lines": ['-pref("browser.ml.chat.enabled", true);', '+pref("browser.ml.chat.enabled", false, locked);']}
    wrong = [l.replace('pref("browser.ml.chat.enabled", true);', 'pref("browser.ml.chat.enabled", false);') for l in FF157]
    assert any("does not read as the patch wants" in w for w in prefs.check(FF157, wrong, hunk))
    collateral = [l.replace('pref("browser.ml.linkPreview.enabled", true);', 'pref("browser.ml.linkPreview.enabled", false);') for l in FF157]
    assert any("linkPreview.enabled changed, but the patch does not touch it" in w for w in prefs.check(FF157, collateral, hunk))


def test_candidates_keep_most_of_the_dotted_path():
    names = {"toolkit.telemetry.updatePing.enable", "toolkit.telemetry.enabled", "browser.altClickSave", "a.b.c.other"}
    assert prefs.candidates("toolkit.telemetry.updatePing.enabled", names) == ["toolkit.telemetry.updatePing.enable"]
    assert prefs.candidates("browser.smartwindow.model", names) == []


def test_auto_port_sets_prefs_by_name_and_defers_a_removed_one(tmp_path):
    from fieldkit.buildh import firefox
    import subprocess
    w = tmp_path / "w"
    (w / "browser" / "app" / "profile").mkdir(parents=True)
    f = w / "browser" / "app" / "profile" / "firefox.js"
    f.write_text("\n".join(FF157) + "\n", encoding="utf-8", newline="")
    subprocess.run(["git", "init", "-q", str(w)], check=True)
    subprocess.run(["git", "-C", str(w), "-c", "user.name=t", "-c", "user.email=t@example.com", "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(w), "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "157"], check=True)
    t = {"workdir": str(w)}
    hunk = {"header": "@@", "lines": ['-pref("browser.ml.chat.enabled", true);', '+pref("browser.ml.chat.enabled", false, locked);']}
    res = firefox.auto_port(t, {"id": "x"}, "05.PREFS/p.patch", "browser/app/profile/firefox.js", hunk)
    assert res["ok"] and res["notes"] == ["ported by pref name: 1 set, 0 dropped"]
    assert firefox.check_port(t, {"id": "x"}, "05.PREFS/p.patch", "browser/app/profile/firefox.js", hunk) == {"ok": True, "why": []}
    gone = {"header": "@@", "lines": ['-pref("extensions.ml.enabled", true);', '+pref("extensions.ml.enabled", false, locked);']}
    res = firefox.auto_port(t, {"id": "y"}, "05.PREFS/p.patch", "browser/app/profile/firefox.js", gone)
    assert res.get("defer") and "extensions.ml.enabled" in res["why"][0]
