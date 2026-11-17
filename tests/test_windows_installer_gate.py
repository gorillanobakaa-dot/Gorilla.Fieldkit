"""Every Windows binary release must ship a click-to-install .exe/.msi and pass installer-test."""
import hashlib
import json
from types import SimpleNamespace

from fieldkit.buildh import releasepublish as rp, task
from fieldkit.method import releasegate, ship


def _sums(tmp_path, files):
    lines = [f"{hashlib.sha256(f.read_bytes()).hexdigest()}  {f.name}\n" for f in files]
    p = tmp_path / "SHA256SUMS"
    p.write_text("".join(lines), encoding="utf-8")
    return p


def test_windows_installer_blocker_across_versions_and_platforms():
    # Batch or Windows-tagged releases with only a .zip (or raw app .exe) are blocked
    assert releasegate.windows_installer_blocker("v160.0.0", ["Gorilla-Unleashed-160.0-win64.zip", "SHA256SUMS"])
    assert releasegate.windows_installer_blocker("v161.0.0", ["Gorilla-Unleashed-161.0-win64.zip", "firefox.exe"])
    assert releasegate.windows_installer_blocker("v162.0.0-windows", ["SHA256SUMS"])

    # Any version with an actual installer (.installer.exe, setup.exe, .msi) passes
    assert releasegate.windows_installer_blocker(
        "v160.0.0", ["Gorilla-Unleashed-160.0-win64.zip", "firefox-160.0.en-US.win64.installer.exe", "SHA256SUMS"]
    ) == ""
    assert releasegate.windows_installer_blocker("v2.1.0-windows", ["app-2.1.0-windows-amd64.zip", "app-setup.exe"]) == ""
    assert releasegate.windows_installer_blocker("v3.0.0-win64", ["app.msi"]) == ""

    # Non-Windows releases are unaffected
    assert releasegate.windows_installer_blocker("v160.0.0-linux", ["gorilla-unleashed_160.0_amd64.deb"]) == ""
    assert releasegate.windows_installer_blocker("v157.0.0-mac", ["Gorilla-Unleashed-157.0-mac-x86_64.zip"]) == ""


def _fake_runner(cmd, **kw):
    joined = " ".join(map(str, cmd))
    if "rev-parse --abbrev-ref" in joined:
        return SimpleNamespace(returncode=0, stdout="feat/rel\n", stderr="")
    if "rev-parse HEAD" in joined:
        return SimpleNamespace(returncode=0, stdout="deadbeef1234\n", stderr="")
    if "rev-list --count" in joined:
        return SimpleNamespace(returncode=0, stdout="1\n", stderr="")
    if cmd[:3] == ["gh", "release", "view"]:
        return SimpleNamespace(returncode=1, stdout="", stderr="not found")
    return SimpleNamespace(returncode=0, stdout="ok\n", stderr="")


def test_releasepublish_requires_installer_and_installer_test(tmp_path, monkeypatch):
    monkeypatch.setattr(task, "STATE", tmp_path / "state" / "build-harness")
    monkeypatch.setattr(rp, "PLAN_DIR", tmp_path / "state" / "publish")
    tdir = task.STATE / "firefox-161-windows"
    tdir.mkdir(parents=True)
    zip_f = tmp_path / "Gorilla-Unleashed-161.0-win64.zip"
    exe_f = tmp_path / "firefox-161.0.en-US.win64.installer.exe"
    zip_f.write_bytes(b"zip")
    exe_f.write_bytes(b"exe")
    (tdir / "build-result.json").write_text(json.dumps({
        "artifacts": {
            "zip": {"file": str(zip_f), "sha256": hashlib.sha256(b"zip").hexdigest()},
            "installer": {"file": str(exe_f), "sha256": hashlib.sha256(b"exe").hexdigest()},
        }
    }), encoding="utf-8")
    notes = tmp_path / "notes.md"
    notes.write_text("notes", encoding="utf-8")

    # 1. Zip alone without installer.exe is refused
    sums_zip = _sums(tmp_path, [zip_f])
    r1 = rp.prepare("firefox-161-windows", tmp_path, "v161.0.0-windows", "Title", notes, [zip_f, sums_zip],
                    gh_repo="o/r", runner=_fake_runner, say=lambda m: None)
    assert not r1["ok"]

    # 2. Installer present, but installer-test has not run yet -> refused
    sums_both = _sums(tmp_path, [zip_f, exe_f])
    r2 = rp.prepare("firefox-161-windows", tmp_path, "v161.0.0-windows", "Title", notes, [zip_f, exe_f, sums_both],
                    gh_repo="o/r", runner=_fake_runner, say=lambda m: None)
    assert not r2["ok"]
    assert any("installer-test" in row["check"] and not row["ok"] for row in r2["rows"])

    # 3. Failing installer-test in journal -> refused
    (tdir / "journal.jsonl").write_text(json.dumps({"event": "installer-test", "ok": False}) + "\n", encoding="utf-8")
    r3 = rp.prepare("firefox-161-windows", tmp_path, "v161.0.0-windows", "Title", notes, [zip_f, exe_f, sums_both],
                    gh_repo="o/r", runner=_fake_runner, say=lambda m: None)
    assert not r3["ok"]

    # 4. Passing installer-test in journal + installer present -> allowed
    (tdir / "journal.jsonl").write_text(json.dumps({"event": "installer-test", "ok": True}) + "\n", encoding="utf-8")
    r4 = rp.prepare("firefox-161-windows", tmp_path, "v161.0.0-windows", "Title", notes, [zip_f, exe_f, sums_both],
                    gh_repo="o/r", runner=_fake_runner, say=lambda m: None)
    assert r4["ok"]


def test_ship_gh_release_blocks_windows_zip_without_installer(tmp_path, monkeypatch):
    from fieldkit.core import sync
    monkeypatch.setattr(sync, "_slug", lambda r: "o/r")
    zip_f = tmp_path / "Gorilla-Unleashed-161.0-win64.zip"
    exe_f = tmp_path / "firefox-161.0.en-US.win64.installer.exe"
    zip_f.write_bytes(b"zip")
    exe_f.write_bytes(b"exe")
    page = tmp_path / "page.md"
    page.write_text("| File | Bytes | SHA-256 |\n|---|---|---|\n| x | 1 | abc |\n", encoding="utf-8")
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout="https://github.com/o/r/releases/tag/v161.0.0\n", stderr="")

    r_bad = ship.gh_release(
        tmp_path, "v161.0.0", [zip_f.name, "--notes-file", page.name],
        mandate=lambda *a, **k: ("allow", "ok", None),
        gate=lambda *a, **k: {"ok": True, "state": "gate-open"},
        run=run,
    )
    assert not r_bad["ok"] and r_bad["state"] == "bad-input"
    assert not calls

    r_ok = ship.gh_release(
        tmp_path, "v161.0.0", [zip_f.name, exe_f.name, "--notes-file", page.name],
        mandate=lambda *a, **k: ("allow", "ok", None),
        gate=lambda *a, **k: {"ok": True, "state": "gate-open"},
        run=run,
    )
    assert r_ok["ok"] and len(calls) == 1
