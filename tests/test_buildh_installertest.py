"""installer-test (2026-10-19): the shipped installer is installed and uninstalled for real and the machine is left as
it was; every installed file must match the proved zip, and an installer that exits 0 having installed nothing fails
(2026-10-02)."""
import hashlib
import json
import zipfile
from types import SimpleNamespace

from fieldkit.buildh import installertest as it, task


def _setup(tmp_path, monkeypatch):
    zp = tmp_path / "pkg.zip"
    with zipfile.ZipFile(zp, "w") as z:
        z.writestr("firefox/firefox.exe", b"exe")
        z.writestr("firefox/application.ini", b"[App]\nBuildID=20261018195930\n")
    inst = tmp_path / "setup.exe"
    inst.write_bytes(b"installer")
    st = tmp_path / "state"
    (st / "t").mkdir(parents=True)
    (st / "t" / "build-result.json").write_text(json.dumps({"build_id": "20261018195930", "artifacts": {
        "installer": {"file": str(inst), "sha256": hashlib.sha256(b"installer").hexdigest()},
        "zip": {"file": str(zp)}}}), encoding="utf-8")
    monkeypatch.setattr(task, "STATE", st)
    monkeypatch.setattr(task, "load", lambda tid: {"meta": {"upstream": {"version": "158.0"}}})
    monkeypatch.setattr(task, "journal", lambda *a, **k: None)
    monkeypatch.setattr(it, "snapshot", lambda: {"uninstall": ["A"], "shortcuts": []})
    return zp


def _runner(target_files, uninstall_ok=True):
    def run(cmd, **kw):
        if cmd[1] == "/S" and cmd[0].endswith("setup.exe"):
            d = __import__("pathlib").Path(cmd[2].split("=", 1)[1]).parent / "Gorilla Unleashed"
            for rel, data in target_files.items():
                (d / rel).parent.mkdir(parents=True, exist_ok=True)
                (d / rel).write_bytes(data)
            return SimpleNamespace(returncode=0, stdout="")
        if cmd[0].endswith("firefox.exe"):
            return SimpleNamespace(returncode=0, stdout="Mozilla Firefox 158.0")
        if cmd[0].endswith("helper.exe"):
            if uninstall_ok:
                (__import__("pathlib").Path(cmd[0]).parents[1] / "firefox.exe").unlink()
            return SimpleNamespace(returncode=0, stdout="")
        return SimpleNamespace(returncode=0, stdout="")             # the recycle
    return run


def test_a_good_install_and_uninstall_passes(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    files = {"firefox.exe": b"exe", "application.ini": b"[App]\nBuildID=20261018195930\n", "uninstall/helper.exe": b"h"}
    r = it.run("t", say=lambda m: None, runner=_runner(files), work=tmp_path / "w")
    assert r["ok"], [x for x in r["rows"] if not x["ok"]]


def test_an_installer_that_installs_nothing_fails(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    r = it.run("t", say=lambda m: None, runner=_runner({}), work=tmp_path / "w")
    bad = {x["check"] for x in r["rows"] if not x["ok"]}
    assert not r["ok"] and "every file of the package is installed, byte-identical" in bad


def test_a_changed_file_or_a_leftover_entry_fails(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    files = {"firefox.exe": b"EXE", "application.ini": b"[App]\nBuildID=20261018195930\n", "uninstall/helper.exe": b"h"}
    snaps = iter([{"uninstall": ["A"], "shortcuts": []}, {"uninstall": ["A", "Mozilla Firefox 158.0"], "shortcuts": []}])
    monkeypatch.setattr(it, "snapshot", lambda: next(snaps))
    r = it.run("t", say=lambda m: None, runner=_runner(files), work=tmp_path / "w")
    bad = {x["check"] for x in r["rows"] if not x["ok"]}
    assert "every file of the package is installed, byte-identical" in bad and "the uninstall entries are as before" in bad


def test_the_ini_asks_for_no_shortcuts_service_or_agent():
    text = it.INI.format(dir="X")
    for k in ("DesktopShortcut", "StartMenuShortcuts", "TaskbarShortcut", "MaintenanceService", "RegisterDefaultAgent"):
        assert f"{k}=false" in text
