"""Calibration (2026-10-09, proactive programme item 2): a check counts only when the known-good browser passes it and
a copy broken in the way it guards against fails it; a calibrated check that changes must be calibrated again."""
import json
import zipfile

from fieldkit.buildh import calibrate as cal, task


def _world(tmp_path, monkeypatch, proven_ok=True):
    monkeypatch.setattr(task, "STATE", tmp_path / "state")
    monkeypatch.setattr(task, "load", lambda tid: {"id": tid, "workdir": str(tmp_path)})
    z = tmp_path / "firefox.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("firefox/application.ini", "[App]\nBuildID=20261009184728\n")
    (tmp_path / "state" / "t1").mkdir(parents=True, exist_ok=True)
    (tmp_path / "state" / "t1" / "build-result.json").write_text(json.dumps({
        "build_id": "20261009184728", "artifacts": {"zip": {"file": str(z)}},
        "package": {"proven": {"ui": [{"check": "tab outline: x", "ok": proven_ok, "evidence": "e"}]}}}), encoding="utf-8")


def _fault(i, rows, made_of=("calibrate.py",)):
    return {"id": i, "what": i, "change": None, "run": lambda t, app, b: rows, "must_fail": "guard:", "made_of": list(made_of)}


def test_a_caught_fault_passes_and_a_blind_or_missing_one_fails():
    assert cal.judge_fault(_fault("a", []), [{"check": "guard: x", "ok": False, "evidence": "outline none"}])[0]
    caught, ev = cal.judge_fault(_fault("a", []), [{"check": "guard: x", "ok": True, "evidence": "fine"}])
    assert not caught and ev.startswith("BLIND")
    assert not cal.judge_fault(_fault("a", []), [{"check": "other", "ok": False, "evidence": ""}])[0]


def test_run_needs_a_good_known_good_and_records_every_fault(tmp_path, monkeypatch):
    _world(tmp_path, monkeypatch)
    faults = [_fault("caught", [{"check": "guard: x", "ok": False, "evidence": "red"}]),
              _fault("blind", [{"check": "guard: x", "ok": True, "evidence": "green"}])]
    r = cal.run("t1", say=lambda m: None, faults=faults)
    assert r["rows"][0]["ok"] and [x["caught"] for x in r["results"]] == [True, False]
    rec = json.loads((tmp_path / "state" / "t1" / "calibration.json").read_text(encoding="utf-8"))
    assert "calibrate.py" in rec["made_of"] and len(rec["results"]) == 2
    row = cal.status_row("t1", faults=faults)
    assert not row["ok"] and "blind" in row["evidence"]
    _world(tmp_path, monkeypatch, proven_ok=False)
    assert not cal.run("t1", say=lambda m: None, faults=faults)["rows"][0]["ok"]


def test_status_fails_when_never_calibrated_or_a_check_changed(tmp_path, monkeypatch):
    _world(tmp_path, monkeypatch)
    faults = [_fault("caught", [{"check": "guard: x", "ok": False, "evidence": "red"}])]
    assert not cal.status_row("t1", faults=faults)["ok"]
    cal.run("t1", say=lambda m: None, faults=faults)
    assert cal.status_row("t1", faults=faults)["ok"]
    monkeypatch.setattr(cal, "made_of_hashes", lambda faults=None: {"calibrate.py": "changed"})
    row = cal.status_row("t1", faults=faults)
    assert not row["ok"] and "changed since calibration" in row["evidence"]


def test_every_real_fault_names_files_that_exist():
    for f in cal.FAULTS:
        for rel in f["made_of"]:
            assert (cal.HERE / rel).is_file(), (f["id"], rel)
    assert len({f["id"] for f in cal.FAULTS}) == len(cal.FAULTS)
