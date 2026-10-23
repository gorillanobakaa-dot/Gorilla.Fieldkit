"""fieldkit doctor (2026-10-10): what a fresh machine still needs, with the line for THIS system; stdlib only."""
import json
import subprocess
import sys

from fieldkit.core import doctor

DEB = {"system": "Linux", "is_windows": False, "is_linux": True, "python": "3.12.3", "distro": "ubuntu",
       "distro_like": ["debian"], "cpus": 4}
WIN = dict(DEB, system="Windows", is_windows=True, is_linux=False, distro=None, distro_like=[])


def _files(tmp_path):
    pp = tmp_path / "pyproject.toml"
    pp.write_text('[project]\nrequires-python = ">=3.11"\ndependencies = ["pyyaml>=6", "lxml>=5"]\n'
                  '[project.optional-dependencies]\ntest = ["pytest>=8"]\n', encoding="utf-8")
    req = tmp_path / "req.json"
    req.write_text(json.dumps({"tools": {
        "git": {"why": "w", "for": ["core"], "install": {"debian": "sudo apt-get install -y git",
                                                       "windows": "winget install --id Git.Git -e"}},
        "rpmbuild": {"why": "rpm", "for": ["debian-kernel"], "install": {"debian": "sudo apt-get install -y rpm"}}}}))
    return pp, req


def test_a_ready_machine_says_ready_and_points_at_the_pipeline(tmp_path, monkeypatch):
    pp, req = _files(tmp_path)
    monkeypatch.setattr(doctor, "is_debian_family", lambda: True)
    r = doctor.check("debian-kernel", pp, req, have_dist=lambda n: "1.0", which=lambda n: f"/usr/bin/{n}", h=DEB)
    assert r["ok"] and r["install"] == [] and r["next"] == "fieldkit pipeline run debian-kernel --only deps"
    assert {x["name"] for x in r["rows"]} == {"python", "pyyaml", "lxml", "pytest", "git", "rpmbuild"}


def test_missing_things_get_one_install_line_each_for_this_system(tmp_path, monkeypatch):
    pp, req = _files(tmp_path)
    monkeypatch.setattr(doctor, "is_debian_family", lambda: True)
    r = doctor.check("debian-kernel", pp, req, have_dist=lambda n: None if n in ("lxml", "pytest") else "1",
                     which=lambda n: None, h=DEB)
    assert not r["ok"] and r["family"] == "debian"
    assert len([l for l in r["install"] if "pip install -e" in l]) == 1          # one line covers lxml and pytest
    assert "sudo apt-get install -y git" in r["install"] and "sudo apt-get install -y rpm" in r["install"]
    assert "NOT READY - 4 missing" in doctor.lines(r)[-2] and r["next"].startswith("run these")


def test_a_pipeline_tool_is_only_asked_for_that_pipeline_and_windows_gets_windows_lines(tmp_path, monkeypatch):
    pp, req = _files(tmp_path)
    monkeypatch.setattr(doctor, "is_debian_family", lambda: False)
    r = doctor.check(None, pp, req, have_dist=lambda n: "1", which=lambda n: None, h=WIN)
    assert [x["name"] for x in r["rows"] if x["kind"] == "tool"] == ["git"]
    assert r["install"] == ["winget install --id Git.Git -e"]


def test_an_old_python_is_a_finding(tmp_path, monkeypatch):
    pp, req = _files(tmp_path)
    monkeypatch.setattr(doctor, "is_debian_family", lambda: True)
    r = doctor.check(None, pp, req, have_dist=lambda n: "1", which=lambda n: "/x", h=dict(DEB, python="3.9.2"))
    assert not r["ok"] and "Python 3.11 or later" in r["install"][0]


def test_it_runs_before_fieldkit_is_installed():
    """Standard library only: on a fresh machine PyYAML is not there yet, and doctor is what says so."""
    code = "import sys; import fieldkit.core.doctor as d; d.check(); print(sorted(m for m in ('yaml','lxml','psutil') if m in sys.modules))"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert out.returncode == 0 and out.stdout.strip() == "[]", out.stderr[-400:]


def test_the_real_requirements_file_is_complete():
    req = json.loads(doctor.REQUIREMENTS.read_text(encoding="utf-8"))
    for name, t in req["tools"].items():
        assert t.get("why") and t.get("for") and t.get("install", {}).get("debian"), name


def test_a_tool_of_the_other_system_is_not_asked_for(tmp_path, monkeypatch):
    pp, req = _files(tmp_path)
    data = json.loads(req.read_text())
    data["tools"]["nft"] = {"why": "w", "for": ["leakgate"], "only": "linux", "install": {"debian": "apt nftables"}}
    req.write_text(json.dumps(data))
    monkeypatch.setattr(doctor, "is_debian_family", lambda: False)
    win = doctor.check("leakgate", pp, req, have_dist=lambda n: "1", which=lambda n: None, h=WIN)
    assert "nft" not in [r["name"] for r in win["rows"]]
    monkeypatch.setattr(doctor, "is_debian_family", lambda: True)
    deb = doctor.check("leakgate", pp, req, have_dist=lambda n: "1", which=lambda n: None, h=DEB)
    assert "nft" in [r["name"] for r in deb["rows"]] and "apt nftables" in deb["install"]


def test_a_ready_machine_for_a_command_points_at_the_command(tmp_path, monkeypatch):
    """--for audio: audio is a command, not a pipeline; NEXT must be a line that runs."""
    pp, req = _files(tmp_path)
    monkeypatch.setattr(doctor, "is_debian_family", lambda: True)
    r = doctor.check("audio", pp, req, have_dist=lambda n: "1.0", which=lambda n: f"/usr/bin/{n}", h=DEB)
    assert r["ok"] and r["next"] == "fieldkit audio"


# ---- 2026-10-23: the first Mac (2011 MacBook Air, macOS 10.13, 4 GB, no Homebrew, Xcode or git) ------------------
MAC = dict(DEB, system="Darwin", is_linux=False, is_mac=True, distro=None, distro_like=[])
STUB = "xcode-select: error: no developer tools were found at '/Applications/Xcode.app', and no install could be requested"


def test_apples_git_stub_is_not_git():
    assert doctor.git_state("/usr/bin/git", run=lambda argv, **k: (1, STUB), system="darwin") == "stub"
    assert doctor.git_state("/usr/bin/git", run=lambda argv, **k: (0, "git version 2.47.3"), system="linux") == "real"
    assert doctor.git_state("/nowhere/git", run=lambda argv, **k: (127, "not found"), system="linux") == "missing"


def test_a_mac_is_a_family_with_its_own_lines_and_the_stub_counts_as_missing(tmp_path, monkeypatch):
    pp, req = _files(tmp_path)
    data = json.loads(req.read_text())
    data["tools"]["git"]["install"]["mac"] = "not needed: install Fieldkit from the zip"
    data["tools"]["git"]["optional_when"] = "zip"
    req.write_text(json.dumps(data))
    monkeypatch.setattr(doctor, "git_state", lambda path=None, **k: "stub")
    r = doctor.check(pyproject=pp, requirements=req, have_dist=lambda n: "1.0", which=lambda n: "/usr/bin/git", h=MAC)
    git = next(x for x in r["rows"] if x["name"] == "git")
    assert r["family"] == "mac" and not git["ok"] and "zip" in git["install"]
    r = doctor.check(pyproject=pp, requirements=req, have_dist=lambda n: "1.0", which=lambda n: "/usr/bin/git", h=MAC,
                     way="zip")
    assert r["ok"] and "not needed" in next(x for x in r["rows"] if x["name"] == "git")["detail"]


def test_lean_asks_only_for_the_lean_packages(tmp_path):
    pp, req = _files(tmp_path)
    data = json.loads(req.read_text())
    data["lean"] = {"packages": ["pyyaml>=6"]}
    req.write_text(json.dumps(data))
    r = doctor.check(pyproject=pp, requirements=req, have_dist=lambda n: None, which=lambda n: "/x", h=DEB, lean=True)
    names = [x["name"] for x in r["rows"] if x["kind"] == "python-package"]
    assert names == ["pyyaml"] and "--no-deps" in r["install"][0]          # lxml (a full dependency) is not asked for


def test_the_real_lean_set_is_what_the_core_was_measured_with():
    req = json.loads(doctor.REQUIREMENTS.read_text(encoding="utf-8"))
    assert [doctor._dist_name(s) for s in req["lean"]["packages"]] == ["pyyaml", "psutil"]


def test_python2_does_not_break_the_search(tmp_path, monkeypatch):
    # macOS 10.13's /usr/bin/python is Python 2: print(a, b) gives "(2, 7)" and broke int() (2026-10-23, on the Air)
    py2, py3 = tmp_path / "python", tmp_path / "python3"
    for p in (py2, py3):
        p.write_text("#!/bin/sh\n")
    outs = {str(py2): (0, "(2, 7)\n"), str(py3): (0, "3 13\n")}
    monkeypatch.setattr(doctor, "_run", lambda argv, **k: outs[argv[0]])
    monkeypatch.setattr(doctor.shutil, "which", lambda n: None)
    found = doctor.pythons([str(py2), str(py3)])
    assert [v for _, v in found] == [(3, 13), (2, 7)]


def test_probe_picks_zip_and_lean_on_the_air(monkeypatch):
    monkeypatch.setattr(doctor, "pythons", lambda places=None: [("/usr/local/bin/python3", (3, 13)), ("/usr/bin/python", (2, 7))])
    monkeypatch.setattr(doctor, "memory_gb", lambda: 4.0)
    monkeypatch.setattr(doctor.shutil, "which", lambda n: {"git": "/usr/bin/git", "cc": "/usr/bin/cc", "curl": "/usr/bin/curl",
                                                         "unzip": "/usr/bin/unzip"}.get(n))
    run = lambda argv, **k: (0, "SIP status: disabled.") if argv[0] == "csrutil" else (1, STUB)
    r = doctor.probe(run=run, h=MAC)
    assert r["way"] == "zip" and r["lean"] and r["facts"]["git"] == "stub" and not r["facts"]["compiler"]
    assert r["facts"]["python_to_use"] == "/usr/local/bin/python3" and r["facts"]["sip"] == "disabled"
    assert any("full path" in w for w in r["why"])                      # python3 is not on this PATH
    assert doctor.probe_lines(r)[-1].endswith("-m fieldkit.core.doctor --lean")


def test_a_linux_check_never_runs_this_machines_git(tmp_path, monkeypatch):
    # run on the 2011 Air (which has Apple's git stub) these Linux-shaped tests failed: the stub check ran for real
    pp, req = _files(tmp_path)
    def boom(*a, **k):
        raise AssertionError("git_state must not run for a Linux host")
    monkeypatch.setattr(doctor, "git_state", boom)
    r = doctor.check(pyproject=pp, requirements=req, have_dist=lambda n: "1.0", which=lambda n: "/usr/bin/" + n, h=DEB)
    assert r["ok"]
