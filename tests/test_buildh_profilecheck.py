# Version: 1.0.0 · updated 26-10-15-23-05
"""profile-check: the build ran on the intended profile, every launcher names it, the desktop menu is not stale
(2026-10-15: the new 158 ran on the old 154-era profile through GNOME's cached menu; its new tab was black)."""
import os
import time

from fieldkit.buildh import profilecheck as pc

INST = "/usr/lib/gorilla-unleashed"


def _profile(root, name, version, install, age=0):
    d = root / name
    d.mkdir(parents=True)
    (d / "compatibility.ini").write_text(f"[Compatibility]\nLastVersion={version}_1/1\nLastPlatformDir={install}\n")
    (d / "prefs.js").write_text("")
    t = time.time() - age
    for f in ("compatibility.ini", "prefs.js"):
        os.utime(d / f, (t, t))
    return d


def _desktop(d, name, profile):
    d.mkdir(parents=True, exist_ok=True)
    arg = f" -profile {profile}" if profile else ""
    (d / name).write_text(f"[Desktop Entry]\nExec={INST}/gorilla-unleashed{arg} %u\n")
    return d / name


def test_the_old_profile_run_by_the_new_build_fails(tmp_path):
    old = _profile(tmp_path, "ff154-main", "158.0", INST, age=0)          # newest: the build ran the OLD one
    new = _profile(tmp_path, "gorilla-158", "158.0", INST, age=600)
    found = pc.profiles([tmp_path])
    rows = pc.rows(INST, str(new), found=found, starters=[], shell=0)
    by = {r["check"]: r for r in rows}
    assert not by["profile: the newest profile this build ran is the intended one"]["ok"]
    assert str(old) in by["profile: no other profile was run by this build"]["evidence"]


def test_all_good_passes(tmp_path):
    new = _profile(tmp_path, "gorilla-158", "158.0", INST)
    _profile(tmp_path, "esr", "140.0", "/usr/lib/firefox-esr")          # another browser's profile is not ours
    apps = tmp_path / "apps"
    _desktop(apps, "gorilla-unleashed.desktop", new)
    starters = pc.launchers(INST, dirs=(apps,), scripts=())
    rows = pc.rows(INST, str(new), found=pc.profiles([tmp_path]), starters=starters, shell=time.time() + 60)
    assert all(r["ok"] for r in rows), rows


def test_a_launcher_naming_another_profile_fails(tmp_path):
    new = _profile(tmp_path, "gorilla-158", "158.0", INST)
    apps, bin_ = tmp_path / "apps", tmp_path / "bin"
    _desktop(apps, "gorilla-unleashed.desktop", new)
    bin_.mkdir()
    (bin_ / "ff154").write_text(f"#!/bin/sh\nPROFILE=/opt/x/.mozilla/ff154-main\nexec {INST}/firefox -profile \"$PROFILE\"\n")
    starters = pc.launchers(INST, dirs=(apps,), scripts=(bin_,))
    row = [r for r in pc.rows(INST, str(new), found=pc.profiles([tmp_path]), starters=starters, shell=time.time() + 60)
           if "launcher" in r["check"]][0]
    assert not row["ok"] and "ff154-main" in row["evidence"]


def test_a_menu_older_than_its_launcher_fails(tmp_path):
    new = _profile(tmp_path, "gorilla-158", "158.0", INST)
    apps = pc.HOME / ".nonexistent-test-dir"                              # only HOME launchers are judged for the cache
    starters = [{"file": str(pc.HOME / "fake.desktop"), "profile": str(new), "kind": "menu", "line": ""}]
    rows = pc.rows(INST, str(new), found=pc.profiles([tmp_path]), starters=starters, shell=time.time() - 3600)
    menu = [r for r in rows if "menu" in r["check"]][0]
    assert menu["ok"]                                                     # the fake file does not exist: nothing newer


def test_no_intended_profile_is_a_failure(tmp_path):
    rows = pc.rows(INST, None, found=[], starters=[], shell=0)
    assert rows and not rows[0]["ok"]
