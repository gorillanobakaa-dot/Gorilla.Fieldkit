"""Production proof reads the shipped artefacts: pref intent vs shipped defaults (last file wins, #if not judged),
excised modules absent from the packaged archives."""
import subprocess
import zipfile

from fieldkit.buildh import proof


def _ship(install, grep="", ffx=""):
    install.mkdir(parents=True, exist_ok=True)
    (install / "browser").mkdir(exist_ok=True)
    with zipfile.ZipFile(install / "omni.ja", "w") as z:
        z.writestr("greprefs.js", grep)
        z.writestr("modules/Kept.sys.mjs", "")
    with zipfile.ZipFile(install / "browser/omni.ja", "w") as z:
        z.writestr("defaults/preferences/firefox.js", ffx)


def _tree(tmp_path, all_js, ffx_js):
    w = tmp_path / "tree"
    (w / "modules/libpref/init").mkdir(parents=True)
    (w / "browser/app/profile").mkdir(parents=True)
    (w / "modules/libpref/init/all.js").write_text('pref("a.one", 1);\npref("b.two", true);\n', encoding="utf-8")
    (w / "browser/app/profile/firefox.js").write_text('pref("c.three", "x");\npref("s.level", 9);\n', encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(w)], check=True)
    subprocess.run(["git", "-C", str(w), "-c", "user.email=a@b", "-c", "user.name=t", "add", "."], check=True)
    subprocess.run(["git", "-C", str(w), "-c", "user.email=a@b", "-c", "user.name=t", "commit", "-qm", "pristine"], check=True)
    (w / "modules/libpref/init/all.js").write_text(all_js, encoding="utf-8")
    (w / "browser/app/profile/firefox.js").write_text(ffx_js, encoding="utf-8")
    return w


def test_pref_lines_track_last_wins_flags_and_conditionals():
    got = proof.pref_lines('pref("x", 1);\n#ifdef XP_WIN\npref("y", "w");\n#endif\npref("x", 2, locked); // c\n')
    assert got == {"x": ("2", "locked", False), "y": ("w", "", True)}


def test_prefs_row_reports_absent_overridden_and_skips_conditionals(tmp_path):
    w = _tree(tmp_path,
              'pref("a.one", 1);\npref("b.two", false);\npref("s.level", 4);\n#ifdef XP_MACOSX\npref("m.only", 1);\n#endif\npref("gone", 7);\n',
              'pref("c.three", "x");\npref("s.level", 9);\n')
    _ship(tmp_path / "inst", grep='pref("a.one", 1);\npref("b.two", false);\npref("s.level", 4);\n', ffx='pref("c.three", "x");\npref("s.level", 9);\n')
    r = proof.prefs_row(w, tmp_path / "inst")
    assert not r["ok"]
    assert any(b.startswith("gone: want 7, shipped ABSENT") for b in r["bad"])
    assert any(b.startswith("s.level: want 4, shipped 9 (overridden") for b in r["bad"])
    assert "1 under #if not judged" in r["evidence"] and len(r["bad"]) == 2


def test_excised_row_names_packaged_removed_modules(tmp_path):
    _ship(tmp_path / "inst")
    with zipfile.ZipFile(tmp_path / "inst" / "omni.ja", "a") as z:
        z.writestr("chrome/toolkit/content/global/ml/MLEngine.worker.mjs", "")
        z.writestr("modules/TelemetryUtils.sys.mjs", "")
    r = proof.excised_row(tmp_path / "inst", ["toolkit/components/telemetry/TelemetryUtils.sys.mjs", "x/Other.sys.mjs"])
    assert not r["ok"] and sorted(r["bad"]) == ["omni.ja:chrome/toolkit/content/global/ml/MLEngine.worker.mjs", "omni.ja:modules/TelemetryUtils.sys.mjs"]
    r = proof.excised_row(tmp_path / "inst", ["x/Other.sys.mjs"])
    assert not r["ok"] and r["bad"] == ["omni.ja:chrome/toolkit/content/global/ml/MLEngine.worker.mjs"]


def test_egress_judges_hosts_from_the_browsers_own_log():
    log = ("x uri=https://firefox.settings.services.mozilla.com/v2/ y\n"
           "x uri=https://firefox.settings.services.mozilla.com/v2/buckets/main z\n"
           "x uri=https://www.anthropic.com/legal/archive/abc q\n"
           "x uri=https://cdn.anthropic.com/img.png q\n"
           "x uri=https://cdn.jsdelivr.net/gh/uBlockOrigin/x.txt q\n"
           "x uri=http://firefox-portal-detection.com/success.txt?ipv4 q\n"
           "x uri=https://push.services.mozilla.com/ q\n"
           "x uri=https://unknown-tracker.example/beacon q\n")
    hosts = proof.http_hosts(log)
    assert hosts["firefox.settings.services.mozilla.com"][0] == 2
    vendor, unknown = proof.judge_hosts(hosts, "www.anthropic.com")
    assert set(vendor) == {"firefox.settings.services.mozilla.com", "firefox-portal-detection.com", "push.services.mozilla.com"}
    assert set(unknown) == {"unknown-tracker.example"}
