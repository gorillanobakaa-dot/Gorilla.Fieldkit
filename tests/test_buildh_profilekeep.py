# Version: 1.0.0 · updated 26-10-16-21-45
"""D-157-28: an install must open the profile with the saved logins (profilekeep.py). 2026-10-16: 155 in Program
Files and 157 in the user folder opened two profiles; the logins "disappeared" while closing the browser kept them."""
import json
import sqlite3

from fieldkit.buildh import profilekeep


def _profile(root, rel, platform_dir, version, logins=0, cookies=0):
    p = root / rel
    p.mkdir(parents=True)
    (p / "compatibility.ini").write_text(f"[Compatibility]\nLastVersion={version}_1/1\nLastPlatformDir={platform_dir}\n")
    if logins:
        (p / "logins.json").write_text(json.dumps({"logins": [{"id": i} for i in range(logins)]}))
    c = sqlite3.connect(p / "cookies.sqlite")
    c.execute("create table moz_cookies (id integer)")
    c.executemany("insert into moz_cookies values (?)", [(i,) for i in range(cookies)])
    c.commit()
    c.close()


def _world(tmp_path):
    root = tmp_path / "Firefox"
    _profile(root, "Profiles/a.default-default-1", str(tmp_path / "Program Files" / "Gorilla"), "155.0.1", 0, 19)
    _profile(root, "Profiles/b.default-default-2", str(tmp_path / "user" / "Gorilla"), "157.0", 60, 147)
    (root / "profiles.ini").write_text("[Profile0]\nName=default-default-1\nIsRelative=1\nPath=Profiles/a.default-default-1\n"
                                       "[Profile1]\nName=default-default-2\nIsRelative=1\nPath=Profiles/b.default-default-2\n")
    return root


def test_the_folder_of_the_profile_with_the_logins_passes(tmp_path):
    r = profilekeep.check(tmp_path / "user" / "Gorilla", _world(tmp_path))
    assert r["ok"] and r["keeper"]["logins"] == 60 and "default-default-2" in r["evidence"]


def test_another_folder_is_refused_and_named(tmp_path):
    root = _world(tmp_path)
    r = profilekeep.check(tmp_path / "Program Files" / "Gorilla", root)
    assert not r["ok"] and "60 saved logins" in r["evidence"] and "default-default-1 (0 logins" in r["evidence"]
    r = profilekeep.check(tmp_path / "elsewhere", root)
    assert not r["ok"] and "NEW, empty profile" in r["evidence"]


def test_nothing_to_lose_passes(tmp_path):
    (tmp_path / "profiles.ini").write_text("")
    assert profilekeep.check(tmp_path / "x", tmp_path)["ok"]
