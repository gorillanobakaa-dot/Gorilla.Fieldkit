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


def test_post_install_runs_each_owner_script_with_the_install_dir_and_keeps_logs(tmp_path, monkeypatch):
    sd = tmp_path / "working scripts"
    sd.mkdir()
    for name, _ in inst.POST_INSTALL:
        (sd / f"{name}.py").write_text("import sys; print('args', sys.argv[1:]); sys.exit(0 if 'verify_no' not in sys.argv[0] else 2)\n", encoding="utf-8")
    monkeypatch.setattr(inst, "scripts_dir", lambda: sd)
    from fieldkit.buildh import proof
    monkeypatch.setattr(proof, "rows", lambda w, d, deleted=(), which=(): [{"check": "startup: headless", "ok": True, "evidence": "", "bad": []}])
    monkeypatch.setattr(inst, "running", lambda d: [])
    monkeypatch.setattr(task, "STATE", tmp_path / "state")
    monkeypatch.setattr(task, "load", lambda tid: {"id": tid, "meta": {}, "workdir": str(tmp_path)})
    journal = []
    monkeypatch.setattr(task, "journal", lambda t, ev, **kw: journal.append((ev, kw)))
    said = []
    r = inst.post_install("t1", install_dir=tmp_path / "G", say=said.append)
    # the keyboard-taking check is skipped unless asked, and the skip is said out loud
    assert not r["ok"] and [(x["name"], x["rc"]) for x in r["results"]] == [("startup", 0),
        ("verify_installed_build", 0), ("verify_no_phone_home", 2), ("webrtc_selftest", 0), ("verify_address_bar", None)]
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
