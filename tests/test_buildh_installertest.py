"""installer-test (2026-10-19): the shipped installer is installed and uninstalled for real and the machine is left as
it was; every installed file must match the proved zip, and an installer that exits 0 having installed nothing fails
(2026-10-02)."""
import hashlib
import json
import zipfile
from types import SimpleNamespace

from fieldkit.buildh import installertest as it, task

import pytest


@pytest.fixture(autouse=True)
def _the_deb_rows_run_here(request, monkeypatch):
    # the .deb round trip rows are Linux rows (host.os_row); these tests fake a Linux runner, so on the Windows CI
    # they must run as on Linux, not report not-this-platform
    if "deb" in request.node.name:
        from fieldkit.core import host
        monkeypatch.setattr(host, "platform_ok", lambda platforms: True)


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
    r = it.run_exe("t", say=lambda m: None, runner=_runner(files), work=tmp_path / "w")
    assert r["ok"], [x for x in r["rows"] if not x["ok"]]


def test_an_installer_that_installs_nothing_fails(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    r = it.run_exe("t", say=lambda m: None, runner=_runner({}), work=tmp_path / "w")
    bad = {x["check"] for x in r["rows"] if not x["ok"]}
    assert not r["ok"] and "every file of the package is installed, byte-identical" in bad


def test_a_changed_file_or_a_leftover_entry_fails(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    files = {"firefox.exe": b"EXE", "application.ini": b"[App]\nBuildID=20261018195930\n", "uninstall/helper.exe": b"h"}
    snaps = iter([{"uninstall": ["A"], "shortcuts": []}, {"uninstall": ["A", "Mozilla Firefox 158.0"], "shortcuts": []}])
    monkeypatch.setattr(it, "snapshot", lambda: next(snaps))
    r = it.run_exe("t", say=lambda m: None, runner=_runner(files), work=tmp_path / "w")
    bad = {x["check"] for x in r["rows"] if not x["ok"]}
    assert "every file of the package is installed, byte-identical" in bad and "the uninstall entries are as before" in bad


def test_the_ini_asks_for_no_shortcuts_service_or_agent():
    text = it.INI.format(dir="X")
    for k in ("DesktopShortcut", "StartMenuShortcuts", "TaskbarShortcut", "MaintenanceService", "RegisterDefaultAgent"):
        assert f"{k}=false" in text


# ---- Linux: the .deb round trip (P9, owner 2026-11-12) ------------------------------------------------------------------
import pathlib


class _Deb:
    """A fake dpkg/apt: the package's files live under root; install copies them to `dest`, purge removes them."""

    def __init__(self, tmp_path, files, sudo=True, purge_ok=True, installed_before=False, install_ok=True):
        self.root, self.dest, self.files = tmp_path / "pkg", tmp_path / "sys", files
        self.sudo, self.purge_ok, self.install_ok = sudo, purge_ok, install_ok
        self.installed = installed_before
        self.calls = []
        for rel, data in files.items():
            (self.root / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.root / rel).write_bytes(data)

    def path(self, rel):
        return str(self.dest / rel)

    def listing(self):
        return "\n".join(f"-rw-r--r-- root/root 1 2026-11-12 10:00 ./{rel}" for rel in self.files) + "\ndrwxr-xr-x root/root 0 x ./usr/\n"

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        out = SimpleNamespace(returncode=0, stdout="")
        if cmd[:3] == ["sudo", "-n", "true"]:
            out.returncode = 0 if self.sudo else 1
        elif cmd[0] == "dpkg-deb" and cmd[1] == "-c":
            out.stdout = self.listing()
        elif cmd[0] == "dpkg-deb" and cmd[1] == "-f":
            out.stdout = "158.0-1\n"
        elif cmd[0] == "dpkg-deb" and cmd[1] == "-x":
            pass
        elif cmd[0] == "dpkg-query" and "-f=${Version}" in cmd:
            out.stdout = "158.0-1" if self.installed else ""
        elif cmd[0] == "dpkg-query":
            out.returncode = 0 if self.installed else 1
            out.stdout = "install ok installed|158.0-1" if self.installed else ""
        elif "install" in cmd and "apt-get" in " ".join(cmd):
            out.returncode = 0 if self.install_ok else 100
            if self.install_ok:
                self.installed = True
        elif "purge" in cmd:
            out.returncode = 0 if self.purge_ok else 100
            if self.purge_ok:
                self.installed = False
        return out


def _deb_setup(tmp_path, monkeypatch, files, **kw):
    st = tmp_path / "state"
    (st / "t").mkdir(parents=True)
    deb = tmp_path / "gorilla-unleashed_158.0-1_amd64.deb"
    deb.write_bytes(b"deb")
    (st / "t" / "build-result.json").write_text(json.dumps({"artifacts": {
        "deb": {"file": str(deb), "sha256": hashlib.sha256(b"deb").hexdigest()}}}), encoding="utf-8")
    monkeypatch.setattr(task, "STATE", st)
    monkeypatch.setattr(task, "load", lambda tid: {"meta": {"upstream": {"version": "158.0"}}})
    monkeypatch.setattr(task, "journal", lambda *a, **k: None)
    fake = _Deb(tmp_path, files, **kw)
    # the fake's files are "installed" at absolute paths under tmp: rewrite the dpkg listing to those paths
    monkeypatch.setattr(it, "deb_files", lambda d, r=None: [fake.path(rel) for rel in files])
    state = {"launchers": []}
    monkeypatch.setattr(it, "deb_snapshot", lambda r=None: {"installed": fake.installed, "version": "", **state})
    return fake, state


def _linux_runner(fake, files, leave=()):
    base = fake

    def run(cmd, **kw):
        out = base(cmd, **kw)
        if "install" in cmd and "apt-get" in " ".join(cmd) and base.install_ok:
            for rel, data in files.items():
                p = base.dest / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(data)
        if "purge" in cmd and base.purge_ok:
            for rel in files:
                if rel not in leave:
                    (base.dest / rel).unlink(missing_ok=True)
        if cmd[0].endswith("/gorilla-unleashed") and "--version" in cmd:
            out.stdout = "Gorilla Unleashed 158.0"
        if cmd[0].endswith("dpkg-deb") and cmd[1] == "-x":
            for rel, data in files.items():
                (pathlib.Path(cmd[3]) / rel).parent.mkdir(parents=True, exist_ok=True)
                (pathlib.Path(cmd[3]) / rel).write_bytes(data)
        return out
    return run


FILES = {"usr/lib/gorilla-unleashed/gorilla-unleashed": b"bin", "usr/share/applications/gorilla-unleashed.desktop": b"d"}


def test_deb_good_install_and_purge_passes(tmp_path, monkeypatch):
    fake, _ = _deb_setup(tmp_path, monkeypatch, FILES)
    r = it.run_deb("t", say=lambda m: None, runner=_linux_runner(fake, FILES), work=tmp_path / "w")
    assert r["ok"], [x for x in r["rows"] if not x["ok"]]
    assert any(c[:4] == ["sudo", "-n", "env", "DEBIAN_FRONTEND=noninteractive"] for c in fake.calls)   # never a password


def test_deb_that_installs_nothing_fails(tmp_path, monkeypatch):
    fake, _ = _deb_setup(tmp_path, monkeypatch, FILES, install_ok=False)
    r = it.run_deb("t", say=lambda m: None, runner=_linux_runner(fake, FILES), work=tmp_path / "w")
    bad = {x["check"] for x in r["rows"] if not x["ok"]}
    assert not r["ok"] and "every file of the package is installed, byte-identical" in bad


def test_deb_a_file_left_behind_by_the_purge_fails(tmp_path, monkeypatch):
    fake, _ = _deb_setup(tmp_path, monkeypatch, FILES)
    left = "usr/share/applications/gorilla-unleashed.desktop"
    r = it.run_deb("t", say=lambda m: None, runner=_linux_runner(fake, FILES, leave=(left,)), work=tmp_path / "w")
    assert {x["check"] for x in r["rows"] if not x["ok"]} >= {"no file of the package is left"}


def test_deb_refuses_when_sudo_would_ask_for_a_password(tmp_path, monkeypatch):
    fake, _ = _deb_setup(tmp_path, monkeypatch, FILES, sudo=False)
    r = it.run_deb("t", say=lambda m: None, runner=_linux_runner(fake, FILES), work=tmp_path / "w")
    assert not r["ok"] and r["rows"][-1]["check"].startswith("sudo -n") and not any("apt-get" in " ".join(c) for c in fake.calls)


def test_deb_never_removes_an_installed_package_unasked(tmp_path, monkeypatch):
    fake, _ = _deb_setup(tmp_path, monkeypatch, FILES, installed_before=True)
    r = it.run_deb("t", say=lambda m: None, runner=_linux_runner(fake, FILES), work=tmp_path / "w")
    assert not r["ok"] and r["rows"][-1]["check"] == "the package is not installed yet"
    assert not any("apt-get" in " ".join(c) for c in fake.calls)


def test_deb_a_stale_hash_is_refused(tmp_path, monkeypatch):
    fake, _ = _deb_setup(tmp_path, monkeypatch, FILES)
    (tmp_path / "gorilla-unleashed_158.0-1_amd64.deb").write_bytes(b"other")
    r = it.run_deb("t", say=lambda m: None, runner=_linux_runner(fake, FILES), work=tmp_path / "w")
    assert not r["ok"] and len(r["rows"]) == 1


def test_run_picks_the_deb_off_windows(monkeypatch):
    seen = []
    monkeypatch.setattr(it, "run_deb", lambda *a, **k: seen.append("deb") or {"ok": True})
    monkeypatch.setattr(it, "run_exe", lambda *a, **k: seen.append("exe") or {"ok": True})
    monkeypatch.setattr(it.sys, "platform", "linux")
    it.run("t")
    monkeypatch.setattr(it.sys, "platform", "win32")
    it.run("t")
    assert seen == ["deb", "exe"]
