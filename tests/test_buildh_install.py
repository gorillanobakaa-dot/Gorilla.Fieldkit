"""Install with a backup first and a way back: zip the install dir and the profiles into Documents, install the
verified build silently into the same per-user directory, verify by application.ini and firefox.exe --version,
restore from the backup when asked (hash-checked)."""
import json
import zipfile

import pytest

from fieldkit.buildh import install as inst, task


def _fake_install(d, version="155.0.1"):
    d.mkdir(parents=True)
    (d / "application.ini").write_text(f"[App]\nVendor=Mozilla\nName=Firefox\nCodeName=Gorilla Unleashed\nVersion={version}\nBuildID=20260914\n", encoding="utf-8")
    (d / "firefox.exe").write_bytes(b"MZ fake")
    (d / "omni.ja").write_bytes(b"x" * 1000)
    return d


def test_backup_zips_install_and_profiles_with_a_manifest_and_restore_puts_it_back(tmp_path, monkeypatch):
    inst_dir = _fake_install(tmp_path / "Gorilla Unleashed")
    prof = tmp_path / "Profiles"
    (prof / "abc.default").mkdir(parents=True)
    (prof / "abc.default" / "prefs.js").write_text('user_pref("x", 1);\n', encoding="utf-8")
    monkeypatch.setattr(inst, "running", lambda d: [])
    monkeypatch.setattr(inst, "any_firefox_running", lambda: False)
    monkeypatch.setattr(inst, "profiles_dir", lambda: prof)          # never the real profiles folder
    said = []
    bdir = inst.backup(inst_dir, profiles=prof, dest_root=tmp_path / "Documents" / "Gorilla.Firefox.Backups", say=said.append)
    man = json.loads((bdir / "manifest.json").read_text(encoding="utf-8"))
    assert bdir.name.startswith("155.0.1-") and man["installed"]["version"] == "155.0.1"
    assert man["files"]["install"]["entries"] == 3 and man["files"]["profiles"]["entries"] == 1
    assert inst._sha(bdir / man["files"]["install"]["zip"]) == man["files"]["install"]["sha256"]
    # a new build lands, then the backup goes back
    (inst_dir / "application.ini").write_text("[App]\nVersion=157.0\nCodeName=Gorilla Unleashed\n", encoding="utf-8")
    assert inst.installed(inst_dir)["version"] == "157.0"
    info = inst.restore(bdir, inst_dir, say=said.append)
    assert info["version"] == "155.0.1" and (inst_dir / "omni.ja").stat().st_size == 1000
    # a damaged backup is refused
    (bdir / man["files"]["install"]["zip"]).write_bytes(b"damaged")
    with pytest.raises(task.Refused, match="damaged backup"):
        inst.restore(bdir, inst_dir)


def test_verify_rows_read_the_installed_version(tmp_path, monkeypatch):
    d = _fake_install(tmp_path / "G", version="157.0")
    monkeypatch.setattr(inst.subprocess, "run", lambda *a, **k: type("R", (), {"stdout": "Mozilla Firefox 157.0", "returncode": 0})())
    monkeypatch.setattr(inst, "find_install", lambda: d)
    rows = {r["check"]: r["ok"] for r in inst.verify(d, "157.0")}
    assert all(rows.values()), rows
    rows = {r["check"]: r["ok"] for r in inst.verify(d, "158.0")}
    assert not rows["application.ini says the pinned version"]


def test_install_refuses_while_the_browser_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(inst, "running", lambda d: ["C:/x/firefox.exe"])
    with pytest.raises(task.Refused, match="running"):
        inst.install(tmp_path / "setup.exe", tmp_path / "G")


def test_fresh_install_removes_the_old_directory_first(tmp_path, monkeypatch):
    d = _fake_install(tmp_path / "G")
    (d / "leftover-from-build-2.dll").write_bytes(b"x")
    monkeypatch.setattr(inst, "running", lambda d: [])
    monkeypatch.setattr(inst.subprocess, "run", lambda *a, **k: type("R", (), {"returncode": 0})())
    monkeypatch.setattr(inst.time, "sleep", lambda s: None)
    rc, _ = inst.install(tmp_path / "setup.exe", d, say=lambda m: None)
    assert rc == 0 and not (d / "leftover-from-build-2.dll").exists()


def test_startup_caches_are_cleared_and_proven_empty(tmp_path, monkeypatch):
    p1 = tmp_path / "local/Profiles/abc.default"
    (p1 / "startupCache").mkdir(parents=True)
    (p1 / "startupCache/scriptCache-current.bin").write_bytes(b"stale")
    monkeypatch.setattr(inst, "local_profiles", lambda: [p1])
    monkeypatch.setattr(inst.subprocess, "run", lambda *a, **k: type("R", (), {"stdout": "", "returncode": 0})())
    assert not inst.caches_row()["ok"]
    cleared = inst.clear_startup_caches(say=lambda m: None)
    assert len(cleared) == 1 and "1 files" in cleared[0] and inst.caches_row()["ok"]
    monkeypatch.setattr(inst.subprocess, "run", lambda *a, **k: type("R", (), {"stdout": "firefox", "returncode": 0})())
    with pytest.raises(task.Refused, match="running"):
        inst.clear_startup_caches()


def test_post_install_runs_each_owner_script_with_the_install_dir_and_keeps_logs(tmp_path, monkeypatch):
    sd = tmp_path / "working scripts"
    sd.mkdir()
    for name, _ in inst.POST_INSTALL:
        (sd / f"{name}.py").write_text("import sys; print('args', sys.argv[1:]); sys.exit(0 if 'verify_no' not in sys.argv[0] else 2)\n", encoding="utf-8")
    monkeypatch.setattr(inst, "scripts_dir", lambda: sd)
    from fieldkit.buildh import proof
    monkeypatch.setattr(proof, "rows", lambda w, d, deleted=(), which=(), truth_root=None: [{"check": "startup: headless", "ok": True, "evidence": "", "bad": []}] if "startup" in which else [])
    monkeypatch.setattr(inst, "caches_row", lambda build_id=None: {"check": "profiles: no stale startup cache", "ok": True, "evidence": ""})
    from fieldkit.buildh import leaks
    monkeypatch.setattr(leaks, "rows", lambda d, seconds=45: [])
    monkeypatch.setattr(inst, "_decisions_row", lambda t, target: [])
    monkeypatch.setattr(inst, "_claims_row", lambda t, target: [])
    monkeypatch.setattr(inst, "_visual_row", lambda t, target, say=print: [])
    monkeypatch.setattr(inst, "_ui_rows", lambda t, target, say=print: [])
    monkeypatch.setattr(inst, "_about_rows", lambda t, target, say=print: [])      # its own tests: test_buildh_aboutpages.py
    monkeypatch.setattr(inst, "_stamp_rows", lambda task_id, target, say=print, carried=None: [])  # its own tests: test_buildh_buildstamp.py
    monkeypatch.setattr(inst, "_satellite_rows", lambda target, say=print: [])     # its own tests: test_buildh_satellite.py
    monkeypatch.setattr(inst, "running", lambda d: [])
    monkeypatch.setattr(task, "STATE", tmp_path / "state")
    monkeypatch.setattr(task, "load", lambda tid: {"id": tid, "meta": {}, "workdir": str(tmp_path)})
    journal = []
    monkeypatch.setattr(task, "journal", lambda t, ev, **kw: journal.append((ev, kw)))
    said = []
    r = inst.post_install("t1", install_dir=tmp_path / "G", say=said.append)
    # the keyboard-taking check is skipped unless asked, and the skip is said out loud
    assert not r["ok"] and [(x["name"], x["rc"]) for x in r["results"]] == [("profiles", 0), ("startup", 0),
        ("verify_installed_build", 0), ("verify_no_phone_home", 2), ("webrtc_selftest", 0), ("verify_address_bar", None)]
    assert [x["status"] for x in r["results"]] == ["ok", "ok", "ok", "FAIL", "ok", "SKIPPED"]
    assert any("takes the keyboard" in m for m in said)
    slept = []
    r = inst.post_install("t1", install_dir=tmp_path / "G", only={"verify_address_bar"}, say=said.append, drive=True, sleep=slept.append)
    assert r["ok"] and r["results"][0]["rc"] == 0 and sum(slept) == inst.DRIVE_COUNTDOWN and any("KEYBOARD" in m for m in said)
    assert "--install" in (tmp_path / "state" / "t1" / "post-install" / "webrtc_selftest.log").read_text() and "--yes" in \
        (tmp_path / "state" / "t1" / "post-install" / "webrtc_selftest.log").read_text()
    assert journal[0][0] == "post_install" and journal[0][1]["ok"] is False
    r = inst.post_install("t1", install_dir=tmp_path / "G", only={"webrtc_selftest"}, say=lambda m: None)
    assert r["ok"] and len(r["results"]) == 1


def test_syntax_problems_catch_a_js_module_that_does_not_parse(tmp_path):
    from fieldkit.buildh import firefox
    (tmp_path / "ok.sys.mjs").write_text("export const A = { parent: {}, child: { events: {} } };", encoding="utf-8")
    (tmp_path / "bad.sys.mjs").write_text("export const A = {" + chr(10) + "  parent: {" + chr(10) + "  }," + chr(10)
                                          + "  child: {" + chr(10) + '    "moz-src:///x.mjs",' + chr(10) + "  }," + chr(10) + "};", encoding="utf-8")
    (tmp_path / "ok.min.js").write_text("this is not checked", encoding="utf-8")
    out = firefox.syntax_problems(tmp_path, ["ok.sys.mjs", "bad.sys.mjs", "ok.min.js"])
    assert len(out) == 1 and out[0].startswith("bad.sys.mjs: line 5") and "SyntaxError" in out[0], out


def test_install_from_zip_empties_the_directory_and_unpacks_the_top_folder(tmp_path, monkeypatch):
    zp = tmp_path / "firefox-157.0.en-US.win64.zip"
    with zipfile.ZipFile(zp, "w") as z:
        z.writestr("firefox/firefox.exe", "MZ")
        z.writestr("firefox/application.ini", "[App]\nVersion=157.0\n")
        z.writestr("firefox/browser/omni.ja", "x")
    d = _fake_install(tmp_path / "G")
    (d / "stale.dll").write_bytes(b"old")
    monkeypatch.setattr(inst, "running", lambda d: [])
    rc, _ = inst.install_from_zip(zp, d, say=lambda m: None)
    assert rc == 0 and not (d / "stale.dll").exists() and (d / "browser/omni.ja").read_text() == "x"
    assert inst.installed(d)["version"] == "157.0"


# ---------------------------------------------------------------- security review 2026-10-02

def test_find_install_never_picks_ordinary_firefox_and_refuses_ambiguity(tmp_path, monkeypatch):
    moz = _fake_install(tmp_path / "Mozilla Firefox")
    gor = _fake_install(tmp_path / "Gorilla Unleashed")
    gor2 = _fake_install(tmp_path / "Other" / "GorillaBuild")
    monkeypatch.setattr(inst, "_uninstall_entries", lambda: [("Mozilla Firefox (x64 en-GB)", str(moz))])
    assert inst.gorilla_installs() == [] and inst.find_install() is None
    # a path that says gorilla only above the install folder (the account name) is still ordinary Firefox
    assert inst.gorilla_installs([("Mozilla Firefox", "C:/Users/gorillafan/AppData/Local/Mozilla Firefox")]) == []  # privacy-scan: allow (fake)
    r = inst.no_target()
    assert not r["ok"] and "only says Firefox is never picked" in r["why"] and "--install-dir" in r["next"]
    # the first entry is ordinary Firefox: it used to win because its name contains "Firefox"
    monkeypatch.setattr(inst, "_uninstall_entries", lambda: [("Mozilla Firefox (x64 en-GB)", str(moz)),
                                                             ("Gorilla Unleashed 157.0", str(gor)), ("Gorilla Unleashed 157.0", str(gor))])
    assert inst.find_install() == gor
    # the location alone may say Gorilla; two different Gorilla installs -> no guess
    monkeypatch.setattr(inst, "_uninstall_entries", lambda: [("Firefox", str(gor)), ("Firefox Nightly", str(gor2))])
    assert inst.gorilla_installs() == [gor, gor2] and inst.find_install() is None
    assert "2 Gorilla installs" in inst.no_target()["why"]


def test_install_run_refuses_when_only_ordinary_firefox_is_registered(tmp_path, monkeypatch):
    moz = _fake_install(tmp_path / "Mozilla Firefox")
    setup = tmp_path / "setup.exe"
    setup.write_bytes(b"MZ installer")
    (tmp_path / "state" / "t1").mkdir(parents=True)
    (tmp_path / "state" / "t1" / "build-result.json").write_text(json.dumps(
        {"artifacts": {"installer": {"file": str(setup), "sha256": inst._sha(setup)}}}), encoding="utf-8")
    monkeypatch.setattr(task, "STATE", tmp_path / "state")
    monkeypatch.setattr(task, "load", lambda tid: {"id": tid, "meta": {"upstream": {"version": "157.0"}}})
    monkeypatch.setattr(inst, "_uninstall_entries", lambda: [("Mozilla Firefox (x64 en-GB)", str(moz))])
    monkeypatch.setattr(inst, "backup", lambda *a, **k: pytest.fail("nothing may be backed up or replaced"))
    r = inst.run("t1", say=lambda m: None)
    assert not r["ok"] and r["next"] == "NEXT: pass --install-dir <the Gorilla install folder>"
    assert (moz / "firefox.exe").read_bytes() == b"MZ fake"


def _zip(tmp_path, entries):
    zp = tmp_path / "firefox-157.0.en-US.win64.zip"
    with zipfile.ZipFile(zp, "w") as z:
        for name, data in entries.items():
            z.writestr(name, data)
    return zp


def test_zip_install_is_verified_by_its_own_marker_not_by_a_stale_uninstall_entry(tmp_path, monkeypatch):
    zp = _zip(tmp_path, {"firefox/firefox.exe": "MZ", "firefox/application.ini": "[App]\nVersion=157.0\n"})
    d = tmp_path / "G"
    monkeypatch.setattr(inst, "running", lambda d: [])
    monkeypatch.setattr(inst.subprocess, "run", lambda *a, **k: type("R", (), {"stdout": "Mozilla Firefox 157.0", "returncode": 0})())
    # a stale entry of an earlier installer points here: it must not be what passes the row
    monkeypatch.setattr(inst, "find_install", lambda: d)
    marker = inst.new_marker(zp, inst._sha(zp), "157.0", "t1")
    inst.install_from_zip(zp, d, say=lambda m: None, marker=marker)
    rows = inst.verify(d, "157.0", marker=marker)
    assert rows[-1]["check"].startswith("this install's own marker") and all(r["ok"] for r in rows), rows
    # another run's marker (or none) fails, whatever the registry says
    other = dict(marker, nonce="0" * 32)
    assert not inst.verify(d, "157.0", marker=other)[-1]["ok"]
    (d / inst.MARKER).unlink()
    row = inst.verify(d, "157.0", marker=marker)[-1]
    assert not row["ok"] and "missing" in row["evidence"]


def test_zip_install_refuses_an_entry_that_escapes_the_install_dir(tmp_path, monkeypatch):
    zp = _zip(tmp_path, {"firefox/firefox.exe": "MZ", "firefox/../../escaped.txt": "x"})
    monkeypatch.setattr(inst, "running", lambda d: [])
    with pytest.raises(task.Refused, match="outside"):
        inst.install_from_zip(zp, tmp_path / "sub" / "G", say=lambda m: None)
    assert not (tmp_path / "escaped.txt").exists()


def _post_install_world(tmp_path, monkeypatch, scripts=None):
    sd = tmp_path / "working scripts"
    sd.mkdir()
    for name, _ in inst.POST_INSTALL:
        if scripts is None or name in scripts:
            (sd / f"{name}.py").write_text("import sys; sys.exit(0)\n", encoding="utf-8")
    monkeypatch.setattr(inst, "scripts_dir", lambda: sd)
    from fieldkit.buildh import proof, leaks
    monkeypatch.setattr(proof, "rows", lambda w, d, deleted=(), which=(), truth_root=None: [{"check": "startup: headless", "ok": True, "evidence": "", "bad": []}] if "startup" in which else [])
    monkeypatch.setattr(inst, "caches_row", lambda build_id=None: {"check": "profiles: no stale startup cache", "ok": True, "evidence": ""})
    monkeypatch.setattr(leaks, "rows", lambda d, seconds=45: [])
    monkeypatch.setattr(inst, "_decisions_row", lambda t, target: [])
    monkeypatch.setattr(inst, "_claims_row", lambda t, target: [])
    monkeypatch.setattr(inst, "_visual_row", lambda t, target, say=print: [])
    monkeypatch.setattr(inst, "_ui_rows", lambda t, target, say=print: [])
    monkeypatch.setattr(inst, "_about_rows", lambda t, target, say=print: [])      # its own tests: test_buildh_aboutpages.py
    monkeypatch.setattr(inst, "_stamp_rows", lambda task_id, target, say=print, carried=None: [])  # its own tests: test_buildh_buildstamp.py
    monkeypatch.setattr(inst, "_satellite_rows", lambda target, say=print: [])     # its own tests: test_buildh_satellite.py
    monkeypatch.setattr(inst, "running", lambda d: [])
    monkeypatch.setattr(task, "STATE", tmp_path / "state")
    monkeypatch.setattr(task, "load", lambda tid: {"id": tid, "meta": {}, "workdir": str(tmp_path)})
    monkeypatch.setattr(task, "journal", lambda t, ev, **kw: None)


def test_post_install_is_not_ok_while_a_check_is_skipped_or_missing(tmp_path, monkeypatch):
    _post_install_world(tmp_path, monkeypatch, scripts={"verify_installed_build", "webrtc_selftest", "verify_address_bar"})
    said = []
    r = inst.post_install("t1", install_dir=tmp_path / "G", say=said.append)
    status = {x["name"]: x["status"] for x in r["results"]}
    # every check that ran passed; one script is missing and one needs --drive: SKIPPED, and the verdict is NOT OK
    assert status["verify_installed_build"] == "ok" and status["verify_no_phone_home"] == "SKIPPED"
    assert status["verify_address_bar"] == "SKIPPED" and not r["ok"]
    assert sorted(r["skipped"]) == ["verify_address_bar", "verify_no_phone_home"]
    assert any(m.startswith("  [SKIPPED] verify_no_phone_home") for m in said)
    # --only still works: what was asked for ran and passed
    r = inst.post_install("t1", install_dir=tmp_path / "G", only={"webrtc_selftest"}, say=lambda m: None)
    assert r["ok"] and [x["name"] for x in r["results"]] == ["webrtc_selftest"]
    # --only naming a missing script, or a name that is no check, is never OK
    r = inst.post_install("t1", install_dir=tmp_path / "G", only={"verify_no_phone_home"}, say=lambda m: None)
    assert not r["ok"] and r["skipped"] == ["verify_no_phone_home"]
    r = inst.post_install("t1", install_dir=tmp_path / "G", only={"verfy_installed_build"}, say=lambda m: None)
    assert not r["ok"] and r["results"][0]["why"] == "no check has this name"


def test_post_install_refuses_without_a_gorilla_target(tmp_path, monkeypatch):
    _post_install_world(tmp_path, monkeypatch)
    monkeypatch.setattr(inst, "_uninstall_entries", lambda: [])
    r = inst.post_install("t1", say=lambda m: None)
    assert not r["ok"] and "--install-dir" in r["next"]


def test_restore_puts_the_profiles_back_only_into_their_own_folder(tmp_path, monkeypatch):
    inst_dir = _fake_install(tmp_path / "Gorilla Unleashed")
    prof = tmp_path / "Profiles"
    (prof / "abc.default").mkdir(parents=True)
    (prof / "abc.default" / "prefs.js").write_text("old", encoding="utf-8")
    monkeypatch.setattr(inst, "running", lambda d: [])
    monkeypatch.setattr(inst, "any_firefox_running", lambda: False)
    bdir = inst.backup(inst_dir, profiles=prof, dest_root=tmp_path / "B", say=lambda m: None)
    # a different profiles folder (the real one, by default) is refused before anything is touched
    (inst_dir / "omni.ja").write_bytes(b"new build")
    elsewhere = tmp_path / "RealProfiles"
    with pytest.raises(task.Refused, match="unzip"):
        inst.restore(bdir, inst_dir, say=lambda m: None, profiles=elsewhere)
    assert (inst_dir / "omni.ja").read_bytes() == b"new build" and not elsewhere.exists()
    # a running Firefox (any) holds the profiles: refused
    monkeypatch.setattr(inst, "any_firefox_running", lambda: True)
    with pytest.raises(task.Refused, match="close every Firefox"):
        inst.restore(bdir, inst_dir, say=lambda m: None, profiles=prof)
    monkeypatch.setattr(inst, "any_firefox_running", lambda: False)
    # a broken profiles.zip with a matching hash: the half-written folder goes, the current profiles come back
    man = json.loads((bdir / "manifest.json").read_text(encoding="utf-8"))
    good = (bdir / "profiles.zip").read_bytes()
    (bdir / "profiles.zip").write_bytes(b"not a zip")
    man["files"]["profiles"]["sha256"] = inst._sha(bdir / "profiles.zip")
    (bdir / "manifest.json").write_text(json.dumps(man), encoding="utf-8")
    with pytest.raises(zipfile.BadZipFile):
        inst.restore(bdir, inst_dir, say=lambda m: None, profiles=prof)
    assert (prof / "abc.default" / "prefs.js").read_text(encoding="utf-8") == "old"
    assert not list(tmp_path.glob("Profiles.before-restore-*"))
    (bdir / "profiles.zip").write_bytes(good)
    man["files"]["profiles"]["sha256"] = inst._sha(bdir / "profiles.zip")
    (bdir / "manifest.json").write_text(json.dumps(man), encoding="utf-8")
    # the restore: install dir and profiles from the backup, the current profiles kept aside, never deleted
    (prof / "abc.default" / "prefs.js").write_text("changed since", encoding="utf-8")
    said = []
    inst.restore(bdir, inst_dir, say=said.append, profiles=prof)
    assert (inst_dir / "omni.ja").stat().st_size == 1000
    assert (prof / "abc.default" / "prefs.js").read_text(encoding="utf-8") == "old"
    aside = list(tmp_path.glob("Profiles.before-restore-*"))
    assert len(aside) == 1 and (aside[0] / "abc.default" / "prefs.js").read_text(encoding="utf-8") == "changed since"
    assert any("current profiles kept as" in m for m in said)


def test_restore_refuses_without_a_target():
    with pytest.raises(task.Refused, match="--install-dir"):
        inst.restore("nowhere", None)


def test_throwaway_firefox_copies_in_temp_do_not_block_the_cache_clear(tmp_path):
    from fieldkit.buildh import install as ins
    temp = tmp_path / "Temp"
    bench = str(temp / "gnetbench_x" / "browser" / "firefox.exe")
    real = str(tmp_path / "Gorilla Unleashed" / "firefox.exe")
    assert ins.blocking_firefox([bench, ""], temp=temp) == []
    assert ins.blocking_firefox([bench, real], temp=temp) == [real]


def test_desktop_icons_row_fails_without_iconkit_and_reads_its_verdict(tmp_path, monkeypatch):
    from fieldkit.buildh import install as ins
    monkeypatch.setattr(ins.os, "name", "nt")
    monkeypatch.setattr(ins, "ICONKIT", tmp_path / "missing.py")
    assert not ins.desktop_icons_row(apply=False)["ok"]
    kit = tmp_path / "iconkit.py"
    kit.write_text('print("  ok   A.lnk: largest frame 256 px")\nprint("  FILE B.lnk: largest frame 32 px")\n',
                   encoding="utf-8")
    monkeypatch.setattr(ins, "ICONKIT", kit)
    row = ins.desktop_icons_row(apply=False)
    assert not row["ok"] and "B.lnk" in row["evidence"]


def test_a_startup_cache_is_stale_only_when_another_build_wrote_it(tmp_path, monkeypatch):
    # 2026-10-10: the owner opened build 30 after its install and the row failed on build 30's own cache
    local = tmp_path / "Local" / "Profiles" / "abc.default"
    (local / "startupCache").mkdir(parents=True)
    (local / "startupCache" / "scriptCache-child.bin").write_bytes(b"x")
    roaming = tmp_path / "Roaming"
    (roaming / "Profiles" / "abc.default").mkdir(parents=True)
    (roaming / "Profiles" / "abc.default" / "compatibility.ini").write_text(
        "[Compatibility]\nLastVersion=157.0_20261010064555/20261010064555\n", encoding="utf-8")
    monkeypatch.setattr(inst, "local_profiles", lambda: [local])
    monkeypatch.setattr(inst, "profiles_dir", lambda: roaming)
    assert inst.caches_row("20261010064555")["ok"]
    assert not inst.caches_row("20261009184728")["ok"] and not inst.caches_row()["ok"]


def test_the_window_gets_every_option_as_typed(monkeypatch):
    # 2026-10-10: --only and --drive were swallowed by the launcher; the keyboard check was skipped twice
    import sys
    from fieldkit.buildh import window, cli as bcli
    got = {}
    monkeypatch.setattr(window, "launch", lambda args: got.setdefault("args", args) and {"pid": 1, "log": "x"})
    monkeypatch.setattr(sys, "argv", ["fieldkit", "build-harness", "window", "post-install", "t", "--only",
                                      "verify_address_bar", "--drive"])
    from fieldkit import cli
    cli.main(sys.argv[1:])
    assert got["args"] == ["post-install", "t", "--only", "verify_address_bar", "--drive"]
