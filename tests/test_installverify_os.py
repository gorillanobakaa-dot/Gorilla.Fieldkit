"""fieldkit install verify on Windows and macOS, the peer/host runs, the align hook and the release-cycle stage."""
import json
import subprocess
from pathlib import Path

from fieldkit import parity
from fieldkit.method import installverify as iv
from fieldkit.method import installverify_os as ox
from fieldkit.method import releasecycle as rc


def test_self_test_catches_every_windows_and_macos_break():
    res = ox.self_test()
    assert len(res) == len(ox.WIN_BREAKS) + len(ox.MAC_BREAKS) + 2
    assert [r for r in res if not r["ok"]] == []


def test_a_correct_windows_install_passes(tmp_path):
    assert [r["check"] for r in ox.WinFake(tmp_path).verify() if not r["ok"]] == []


def test_a_shortcut_icon_in_another_folder_is_fine_when_it_holds_the_releases_frames(tmp_path):
    f = ox.WinFake(tmp_path)
    ox._w_lnk_good_other_icon(f)
    assert [r["check"] for r in f.verify() if not r["ok"]] == []


def test_a_frame_the_installed_ico_lacks_is_named(tmp_path):
    f = ox.WinFake(tmp_path)
    ox._w_ico_missing_size(f)
    bad = [r for r in f.verify() if not r["ok"]]
    assert [r["check"] for r in bad] == ["shortcut.desktop", "shortcut.startmenu", "icon.ico:48"]   # the shortcuts use that .ico
    assert "install" in bad[-1]["fix"]


def test_lnk_target_through_conhost_is_the_quoted_argument():
    assert ox.lnk_exe({"target": "C:\\Windows\\System32\\conhost.exe", "args": '"C:\\a b\\x.exe"'}) == "C:\\a b\\x.exe"
    assert ox.lnk_exe({"target": "C:\\a\\x.exe", "args": ""}) == "C:\\a\\x.exe"


def test_a_correct_macos_install_passes_and_a_release_without_a_bundle_is_info(tmp_path):
    f = ox.MacFake(tmp_path)
    assert [r["check"] for r in f.verify() if not r["ok"]] == []
    rows = ox.verify_macos(dict(f.ref, app=None), f.env)
    assert all(r["ok"] for r in rows) and any(r.get("info") for r in rows)


def test_ssh_run_returns_the_rows_the_mac_printed():
    class P:
        returncode = 0
        stdout = "noise\n" + json.dumps([{"check": "binary.path", "ok": True, "line": "x", "fix": ""}])
        stderr = ""
    rows, why = ox.ssh_run("macair", {"binary_sha": "a"}, ssh=lambda ref: ["ssh"], run=lambda *a, **k: P())
    assert rows[0]["check"] == "binary.path" and why == ""


def test_ssh_run_without_python_is_a_failure_with_a_reason():
    class P:
        returncode = 127
        stdout = ""
        stderr = "python3: command not found"
    rows, why = ox.ssh_run("macair", {}, ssh=lambda ref: ["ssh"], run=lambda *a, **k: P())
    assert rows is None and "python3" in why


def test_peer_and_host_failures_exit_3(monkeypatch):
    monkeypatch.setattr(iv, "_remote", lambda a: ("peer", None, "the peer did not answer: down"))
    assert iv.main(["verify", "gorilla-opencode", "--peer"]) == 3
    monkeypatch.setattr(iv, "_remote", lambda a: ("macair", [{"check": "x", "ok": False, "line": "l", "fix": "f"}], ""))
    assert iv.main(["verify", "gorilla-opencode", "--host", "macair"]) == 3
    monkeypatch.setattr(iv, "_remote", lambda a: ("macair", [{"check": "x", "ok": True, "line": "l", "fix": ""}], ""))
    assert iv.main(["verify", "gorilla-opencode", "--host", "macair"]) == 0


def test_the_link_probe_is_registered_and_runs_the_verify(monkeypatch):
    from fieldkit.peers import link_probe
    monkeypatch.setattr(iv, "probe_lines", lambda product: (["OK  x: y"], {"ok": True, "os": "linux", "rows": []}))
    r = link_probe.run("install-verify", "gorilla-opencode")
    assert r["ok"] and r["data"]["ok"] and "install-verify" in link_probe.PROBES
    assert not link_probe.run("install-verify", "x; y")["ok"]


def test_the_peer_asks_the_install_verify_probe(monkeypatch):
    from fieldkit.peers import link
    seen = {}

    def call(req, timeout=15, device=None):
        seen.update(req)
        return {"ok": True, "host": "vaio", "data": {"rows": [{"check": "c", "ok": True, "line": "", "fix": ""}]}}
    monkeypatch.setattr(link, "call", call)
    a = type("A", (), {"host": None, "product": "gorilla-opencode", "tag": None, "ref": None})()
    label, rows, why = iv._remote(a)
    assert seen["name"] == "install-verify" and label == "peer vaio" and rows and not why


def test_self_test_exit_code_and_skip_note(capsys):
    assert iv.main(["self-test", "--os", "windows"]) == 0
    assert "cases" in capsys.readouterr().out


# ---- parity align runs the proof ------------------------------------------------------------------------------------
def test_install_verify_names_the_first_failing_line_and_its_fix():
    out = "OK   a: fine\nFAIL icon.64x64: gone\n     FIX: run install\n"
    r = parity.install_verify("v1", run=lambda cmd, timeout=30: (3, out, ""))
    assert not r["ok"] and "icon.64x64" in r["why"] and "run install" in r["why"]
    assert parity.install_verify("v1", run=lambda cmd, timeout=30: (0, "", ""))["ok"]


def test_align_fails_on_windows_when_the_proof_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(parity, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(parity, "opencode_path", lambda: Path(tmp_path / "gorilla-opencode.exe"))
    monkeypatch.setattr(parity, "pin", lambda path=None: {"gorilla-opencode": {"version": "v0.1.160", "repo": "o/r"}})
    ran = []

    def run(cmd, timeout=30):
        ran.append(cmd)
        return 0, "v0.1.160", ""
    f = tmp_path / "dl.exe"
    f.write_bytes(b"x")
    kw = dict(apply=True, sysname="windows", run=run, fetch_fn=lambda *a, **k: (f, "abc"),
              install_fn=lambda *a, **k: {"ok": True})
    bad = parity.align(verify_fn=lambda tag, run: {"ok": False, "why": "install verify exit 3: FAIL x"}, **kw)
    assert bad["state"] == "FAILED" and "install verify" in bad["why"]
    assert any(c[-1] == "install" for c in ran)                   # the program's own install ran before the proof
    assert parity.align(verify_fn=lambda tag, run: {"ok": True}, **kw)["state"] in ("DONE", "ALIGNED")


# ---- the release cycle is not done until the proof passes -----------------------------------------------------------
class Ctx:
    def __init__(self, tmp, project="gorilla-opencode"):
        self.vars = {"version": "v0.2.0", "project": project, "release_file": str(tmp / "r.yaml")}
        self.state_dir = tmp


def test_release_cycle_installed_stage_needs_both_computers(tmp_path, monkeypatch):
    monkeypatch.setattr(rc, "_r", lambda ctx: {"project": "gorilla-opencode"})
    ctx = Ctx(tmp_path)
    calls = []

    def run(cmd, **k):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 3 if "--peer" in cmd else 0, "FAIL binary.path: x\n", "")
    r = rc.installed(ctx, run=run)
    assert not r["ok"] and "--peer" in " ".join(calls[1]) and "install verify --peer" in r["detail"]
    assert not rc.verify_installed(ctx)["ok"]
    r = rc.installed(ctx, run=lambda cmd, **k: subprocess.CompletedProcess(cmd, 0, "", ""))
    assert r["ok"] and rc.verify_installed(ctx)["ok"]
    assert "installed" in rc.STAGES and any(s["id"] == "installed" for s in rc.spec("gorilla-opencode", "v1")["stages"])


def test_release_cycle_installed_stage_skips_a_project_with_no_install(tmp_path, monkeypatch):
    monkeypatch.setattr(rc, "_r", lambda ctx: {"project": "app"})
    assert rc.installed(Ctx(tmp_path, "app"), run=lambda *a, **k: 1 / 0)["ok"]
