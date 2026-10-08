"""The build stamp (D-157-38, owner 2026-10-08): Help > About ends with "built YY:MM:DD:HH:MM:SS" from the build's own
BuildID, and the harness holds the built and the installed browser to the BuildID build-verify recorded."""
import time

from fieldkit.buildh import buildstamp as bs


def _app(tmp_path, bid):
    d = tmp_path / "bin"
    d.mkdir(parents=True, exist_ok=True)
    (d / "application.ini").write_text(f"[App]\nVersion=157.0\nBuildID={bid}\n", encoding="utf-8")
    return d


def test_stamp_reads_the_build_id_as_year_to_second():
    assert bs.stamp("20261008103725") == "built 26:10:08:10:37:25"
    assert bs.stamp("2026100810372") is None and bs.stamp(None) is None


def test_the_about_line_must_end_with_this_folders_stamp(monkeypatch, tmp_path):
    app = _app(tmp_path, "20261008103725")
    lines = ["STAMP|buildid|20261008103725", "STAMP|expected|built 26:10:08:10:37:25",
             "STAMP|shown|157.0 (64-bit) built 26:10:08:10:37:25", "STAMP|verdict|ok|the version line carries this build's stamp"]
    monkeypatch.setattr("fieldkit.buildh.probe.run", lambda *a, **k: {"lines": lines})
    rows = bs.about_rows(app, recorded="20261008103725")
    assert [r["ok"] for r in rows] == [True, True]
    rows = bs.about_rows(app, recorded="20261007110752")              # the install is not the build that was checked
    assert rows[0]["check"].startswith("stamp: this is the build") and not rows[0]["ok"]
    old = ["STAMP|buildid|20261008103725", "STAMP|shown|157.0 (64-bit)", "STAMP|verdict|FAIL|expected the line to end with 'built 26:10:08:10:37:25'"]
    monkeypatch.setattr("fieldkit.buildh.probe.run", lambda *a, **k: {"lines": old})
    r = bs.about_rows(app)
    assert len(r) == 1 and not r[0]["ok"] and "157.0 (64-bit)" in r[0]["evidence"]


def test_built_rows_need_a_build_id_newer_than_the_gate(monkeypatch, tmp_path):
    monkeypatch.setattr(bs, "about_rows", lambda app, recorded=None, say=print: [{"check": "stamp: about", "ok": True, "evidence": ""}])
    _app(tmp_path / "dist", "20261008103725")
    gate = time.mktime(time.strptime("20261008100000", "%Y%m%d%H%M%S"))
    rows, bid = bs.built_rows(tmp_path, gate)
    assert bid == "20261008103725" and rows[0]["ok"]
    late_gate = time.mktime(time.strptime("20261008110000", "%Y%m%d%H%M%S"))      # BuildID older than the gate: stale
    rows, _ = bs.built_rows(tmp_path, late_gate)
    assert not rows[0]["ok"]


def test_the_probe_reads_the_about_window_the_menu_opens():
    js = (bs.Path(bs.__file__).parent / "probes" / "build-stamp.js").read_text(encoding="utf-8")
    assert "openAboutDialog" in js and 'getMostRecentWindow("Browser:About")' in js and "about.close()" in js
