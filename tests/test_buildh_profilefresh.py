# Version: 1.0.0 · updated 26-10-15-22-55
"""profile-fresh: the person's data crosses to a new profile, the old version's settings never do (2026-10-15: a
154-era profile made 158 show Inter, a black new tab and six search engines)."""
import sqlite3

import pytest

from fieldkit.buildh import profilecheck, profilefresh as pf


@pytest.fixture(autouse=True)
def _own_record(tmp_path, monkeypatch):
    # apply() records the intended profile; a test must never write the real record (2026-10-15: one run of this file
    # replaced ~/.local/state/fieldkit/intended-profile with a pytest folder and profile-check judged against it)
    monkeypatch.setattr(profilecheck, "RECORD", tmp_path / "intended-profile")


def _old(tmp_path):
    old = tmp_path / "old"
    old.mkdir()
    with sqlite3.connect(old / "places.sqlite") as c:
        c.execute("create table moz_places (url text)")
        c.execute("insert into moz_places values ('https://example.org/')")
    for f in ("logins.json", "key4.db"):
        (old / f).write_text("x")
    (old / "prefs.js").write_text('user_pref("font.name.serif.x-western", "Inter");\n')
    (old / "user.js").write_text('user_pref("media.gorilla.webcodecs_software_vp8", true);\n'
                                 'user_pref("dom.workers.maxPerDomain", 512);\n')
    (old / "search.json.mozlz4").write_text("x")
    (old / "startupCache").mkdir()
    return old


def test_data_crosses_settings_do_not(tmp_path):
    old = _old(tmp_path)
    new = tmp_path / "new"
    pf.apply(old, new, machine={"media.gorilla.loudness_compressor.enabled": ("false", "this machine has a sink")})
    assert (new / "places.sqlite").is_file() and (new / "logins.json").is_file()
    assert not (new / "prefs.js").exists() and not (new / "search.json.mozlz4").exists()
    assert not (new / "startupCache").exists()
    assert 'user_pref("media.gorilla.loudness_compressor.enabled", false);' in (new / "user.js").read_text()
    assert pf.check(new)["ok"]
    with sqlite3.connect(new / "places.sqlite") as c:
        assert c.execute("select url from moz_places").fetchone()[0] == "https://example.org/"


def test_a_fix_that_lives_only_in_the_profile_is_reported(tmp_path):
    old = _old(tmp_path)
    shipped = {"dom.workers.maxPerDomain": ("512", None)}
    got = pf.stranded(old / "user.js", shipped)
    assert got == [("media.gorilla.webcodecs_software_vp8", "true", None)]


def test_refuses_to_mix_into_a_used_folder(tmp_path):
    old = _old(tmp_path)
    new = tmp_path / "new"
    new.mkdir()
    (new / "prefs.js").write_text("x")
    with pytest.raises(RuntimeError):
        pf.apply(old, new)


def test_a_stale_lock_is_not_a_running_browser(tmp_path):
    old = _old(tmp_path)
    (old / "lock").symlink_to("127.0.1.1:+999999999")
    assert not pf.in_use(old)
