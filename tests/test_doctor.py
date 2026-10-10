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
