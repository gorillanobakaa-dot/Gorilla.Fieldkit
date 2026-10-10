"""Prove once, on the package that ships (2026-10-09): build-verify runs the package checks on the unpacked zip and
records them with the zip's file hashes; post-install carries them only when the install is that zip, byte for byte."""
import json
import zipfile

from fieldkit.buildh import pkgproof as pp


def _zip(tmp_path, files):
    z = tmp_path / "firefox-157.0.en-US.win64.zip"
    with zipfile.ZipFile(z, "w") as zf:
        for rel, data in files.items():
            zf.writestr("firefox/" + rel, data)
    return z


FILES = {"application.ini": "[App]\nBuildID=20261009184728\n", "omni.ja": "x" * 100, "browser/omni.ja": "y" * 50}


def test_prove_runs_every_group_on_the_unpacked_package_and_records_hashes(tmp_path):
    z = _zip(tmp_path, FILES)
    seen = []

    def run(g, t, app, say):
        seen.append((g, (app / "application.ini").is_file()))
        return [{"check": f"{g}: fine", "ok": True, "evidence": "e"}]
    got = pp.prove({}, z, say=lambda m: None, run=run)
    assert [g for g, _ in seen] == list(pp.PACKAGE_GROUPS) and all(ok for _, ok in seen)
    assert set(got["files"]) == set(FILES) and set(got["seconds"]) == set(pp.PACKAGE_GROUPS)
    assert len(got["rows"]) == len(pp.PACKAGE_GROUPS)


def _install(tmp_path, files, marker=True):
    d = tmp_path / "Gorilla Unleashed"
    for rel, data in files.items():
        (d / rel).parent.mkdir(parents=True, exist_ok=True)
        (d / rel).write_text(data, encoding="utf-8", newline="")
    if marker:
        (d / pp.MARKER).write_text("{}", encoding="utf-8")
    return d


def _record(tmp_path, z):
    st = tmp_path / "state"
    st.mkdir()
    proven = {g: [{"check": f"{g}: fine", "ok": True, "evidence": "e"}] for g in pp.PACKAGE_GROUPS}
    (st / "build-result.json").write_text(json.dumps({"verified_at": "2026-10-09 19:16:00", "package": {
        "files": pp.zip_files(z), "proven": proven, "seconds": {}}}), encoding="utf-8")
    return st


def test_an_identical_install_carries_the_package_rows(tmp_path):
    z = _zip(tmp_path, FILES)
    row, carry = pp.carried(_record(tmp_path, z), _install(tmp_path, FILES))
    assert row["ok"] and "3 file(s)" in row["evidence"]
    assert set(carry) == set(pp.PACKAGE_GROUPS) and carry["about"][0]["evidence"].startswith(pp.CARRIED)


def test_a_changed_missing_or_extra_file_carries_nothing_and_fails(tmp_path):
    z = _zip(tmp_path, FILES)
    st = _record(tmp_path, z)
    for change in ({"omni.ja": "z" * 100}, {"defaults/pref/evil.js": "pref('x', 1);"}):
        d = _install(tmp_path, {**FILES, **change})
        row, carry = pp.carried(st, d)
        assert not row["ok"] and carry == {}, change
        for p in d.rglob("*"):
            if p.is_file():
                p.unlink()
    d = _install(tmp_path, {k: v for k, v in FILES.items() if k != "browser/omni.ja"})
    row, carry = pp.carried(st, d)
    assert not row["ok"] and "missing" in row["evidence"] and carry == {}


def test_no_record_means_every_check_runs(tmp_path):
    assert pp.carried(tmp_path / "nothing", tmp_path) == (None, {})


def test_post_install_carries_package_groups_and_runs_the_rest(tmp_path, monkeypatch):
    from fieldkit.buildh import install as inst, task, proof, leaks
    z = _zip(tmp_path, FILES)
    st = tmp_path / "state" / "t1"
    st.mkdir(parents=True)
    proven = {g: [{"check": f"{g}: proven on the package", "ok": True, "evidence": "e"}] for g in pp.PACKAGE_GROUPS}
    (st / "build-result.json").write_text(json.dumps({"build_id": "20261009184728", "verified_at": "x", "package": {
        "files": pp.zip_files(z), "proven": proven, "seconds": {}}}), encoding="utf-8")
    target = _install(tmp_path, FILES)

    def never(*a, **k):
        raise AssertionError("a package check ran again on identical files")
    for name in ("_visual_row", "_ui_rows", "_about_rows", "_satellite_rows"):
        monkeypatch.setattr(inst, name, never)
    from fieldkit.buildh import buildstamp
    monkeypatch.setattr(buildstamp, "about_rows", never)
    monkeypatch.setattr(proof, "rows", lambda w, d, deleted=(), which=(), truth_root=None:
                        [{"check": f"{which[0]}: ran", "ok": True, "evidence": ""}])
    monkeypatch.setattr(inst, "caches_row", lambda build_id=None: {"check": "profiles: ran", "ok": True, "evidence": ""})
    monkeypatch.setattr(leaks, "rows", lambda d, seconds=45: [{"check": "leaks: ran", "ok": True, "evidence": ""}])
    monkeypatch.setattr(inst, "_decisions_row", lambda t, target: [{"check": "decisions: ran", "ok": True, "evidence": ""}])
    monkeypatch.setattr(inst, "_claims_row", lambda t, target: [{"check": "claims: ran", "ok": True, "evidence": ""}])
    monkeypatch.setattr(inst, "running", lambda d: [])
    monkeypatch.setattr(task, "STATE", tmp_path / "state")
    monkeypatch.setattr(task, "load", lambda tid: {"id": tid, "meta": {}, "workdir": str(tmp_path)})
    monkeypatch.setattr(task, "journal", lambda t, ev, **kw: None)
    said = []
    r = inst.post_install("t1", install_dir=target, only=inst.PROOF_CHECKS, say=said.append)
    names = [x["name"] for x in r["results"]]
    assert r["ok"], said
    assert names[0] == "carried" and "egress" in names and "claims" in names and "satellite" in names
    assert any("stamp: this is the build" in m and "20261009184728" in m for m in said)     # read here, not carried
    assert any(m.startswith("  timings (seconds):") for m in said)
