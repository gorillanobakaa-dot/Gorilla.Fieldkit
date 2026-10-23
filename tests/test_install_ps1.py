"""install.ps1: Fieldkit on Windows without git. No network: the zip is a fake one passed as a file:// FIELDKIT_ZIP_URL,
pip is skipped (FIELDKIT_SKIP_PIP=1), and the answers are piped in. Runs in Windows PowerShell 5.1 (powershell.exe),
the one the script promises."""
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "install.ps1"
PS = shutil.which("powershell")

pytestmark = pytest.mark.skipif(sys.platform != "win32" or not PS, reason="install.ps1 is the Windows installer (install.sh is the Linux and macOS one)")


def ps(code, env=None, stdin=""):
    """Run PowerShell 5.1 code with the script's functions dot-sourced; (exit, stdout)."""
    full = f". '{SCRIPT}'\n{code}"
    r = subprocess.run([PS, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", "-"], input=full + "\nexit $LASTEXITCODE\n",
                       capture_output=True, text=True, env=env, timeout=120)
    return r.returncode, r.stdout + r.stderr


def run_script(target, answers, zip_path, extra_env=None):
    env = dict(os.environ, FIELDKIT_ZIP_URL=Path(zip_path).as_uri(), FIELDKIT_SKIP_PIP="1")
    env.update(extra_env or {})
    r = subprocess.run([PS, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SCRIPT), "-Target", str(target)],
                       input="".join(a + "\n" for a in answers), capture_output=True, text=True, env=env, timeout=300)
    return r.returncode, r.stdout + r.stderr


def make_zip(path, entries=None, doctor_exit=0):
    """A small stand-in for the GitHub zip: one top folder, fieldkit/__main__.py, a doctor that exits as told."""
    if entries is None:
        entries = {
            "Gorilla.Fieldkit-main/fieldkit/__init__.py": "",
            "Gorilla.Fieldkit-main/fieldkit/__main__.py": "",
            "Gorilla.Fieldkit-main/fieldkit/core/__init__.py": "",
            "Gorilla.Fieldkit-main/fieldkit/core/doctor.py":
                f"import sys\nprint('LEAN', sys.argv[1:])\nsys.exit({doctor_exit} if '--lean' in sys.argv else 2)\n",
            "Gorilla.Fieldkit-main/fieldkit/core/requirements.json": '{"lean": {"packages": ["pyyaml>=6", "psutil>=5.9"]}}',
            "Gorilla.Fieldkit-main/README.md": "fake\n",
        }
    with zipfile.ZipFile(path, "w") as z:
        for name, text in entries.items():
            z.writestr(name, text)
    return path


def test_parses_in_windows_powershell_5():
    code = ("$e = $null; [void][System.Management.Automation.Language.Parser]::ParseFile("
            f"'{SCRIPT}', [ref]$null, [ref]$e); $PSVersionTable.PSVersion.Major; $e.Count")
    r = subprocess.run([PS, "-NoProfile", "-Command", code], capture_output=True, text=True, timeout=60)
    major, errors = r.stdout.split()
    assert (major, errors) == ("5", "0"), r.stdout + r.stderr
    assert "&&" not in SCRIPT.read_text(encoding="ascii")   # also: the file is pure ASCII (5.1 reads it as ANSI)


def test_target_refusals(tmp_path):
    full = tmp_path / "full"
    full.mkdir()
    (full / "old.txt").write_text("x")
    one = tmp_path / "OneDrive - Work" / "Fieldkit"
    via_env = tmp_path / "synced" / "Fieldkit"
    env = dict(os.environ, OneDrive=str(tmp_path / "synced"))
    code, out = ps(f"""
'NEW=' + (Test-TargetFolder '{tmp_path / "new"}')
'FULL=' + (Test-TargetFolder '{full}')
'NAMED=' + (Test-TargetFolder '{one}')
'ENV=' + (Test-TargetFolder '{via_env}')
'REL=' + (Test-TargetFolder 'Fieldkit')
""", env=env)
    lines = dict(l.split("=", 1) for l in out.splitlines() if "=" in l)
    assert lines["NEW"] == ""
    assert "already has files (old.txt" in lines["FULL"]
    assert "under OneDrive" in lines["NAMED"]
    assert "under OneDrive" in lines["ENV"]
    assert "full path" in lines["REL"]


def test_zip_refusals(tmp_path):
    good = make_zip(tmp_path / "good.zip")
    slip = make_zip(tmp_path / "slip.zip", {"Gorilla.Fieldkit-main/fieldkit/__main__.py": "",
                                             "Gorilla.Fieldkit-main/../../evil.txt": "x"})
    two = make_zip(tmp_path / "two.zip", {"a/fieldkit/__main__.py": "", "b/x": ""})
    other = make_zip(tmp_path / "other.zip", {"Something-main/README.md": ""})
    junk = tmp_path / "junk.zip"
    junk.write_text("not a zip")
    code, out = ps("\n".join(f"'{k}=' + (Test-ZipFile '{p}')" for k, p in
                             [("GOOD", good), ("SLIP", slip), ("TWO", two), ("OTHER", other), ("JUNK", junk)]))
    lines = dict(l.split("=", 1) for l in out.splitlines() if "=" in l)
    assert lines["GOOD"] == ""
    assert "outside the folder" in lines["SLIP"]
    assert "2 top folders" in lines["TWO"]
    assert "not Fieldkit" in lines["OTHER"]
    assert "not a readable zip" in lines["JUNK"]


def test_find_python_skips_store_alias_and_missing(tmp_path):
    alias = tmp_path / "WindowsApps" / "python.exe"
    alias.parent.mkdir()
    shutil.copy(sys.executable, alias)     # a real Python, but in a WindowsApps folder: never used
    code, out = ps(f"(Find-Python @('{alias}', '{tmp_path / 'none.exe'}', '{sys.executable}')).Path")
    assert out.strip().splitlines()[-1] == sys.executable
    code, out = ps(f"'R=' + (Find-Python @('{alias}', '{tmp_path / 'none.exe'}')).Path")
    assert "R=" in out and out.strip().splitlines()[-1] == "R="


def test_a_broken_python_is_a_miss_not_a_stop():
    # Measured 2026-10-23 on the owner's laptop: py.exe pointed at a Python in a deleted user folder; its stderr
    # under ErrorActionPreference Stop ended the whole install at step 3.
    code, out = ps("$ErrorActionPreference = 'Stop'\n"
                   "'R=' + (Invoke-Quiet { & cmd /c 'echo gone 1>&2 & exit 1' })\n"
                   "'W=' + (Invoke-Quiet { & cmd /c 'echo a warning 1>&2 & echo 3.12.1' })\n'ALIVE'")
    # failing: nothing, and the script lives on; succeeding with a warning on stderr: still its answer
    assert out.strip().splitlines()[-3:] == ["R=", "W=3.12.1", "ALIVE"], out


def test_lean_list_read_from_the_zip(tmp_path):
    req = tmp_path / "fieldkit" / "core" / "requirements.json"
    req.parent.mkdir(parents=True)
    req.write_text('{"lean": {"packages": ["pyyaml>=6", "psutil>=5.9", "extra>=1"]}}')
    code, out = ps(f"(Get-LeanPackages '{tmp_path}').Packages -join ','")
    assert out.strip().splitlines()[-1] == "pyyaml>=6,psutil>=5.9,extra>=1"


def test_a_zip_without_a_lean_list_is_not_ready(tmp_path):
    entries = {"Gorilla.Fieldkit-main/fieldkit/__main__.py": "", "Gorilla.Fieldkit-main/README.md": ""}
    target = tmp_path / "fk"
    code, out = run_script(target, ["y", "y", "y"], make_zip(tmp_path / "fk.zip", entries))
    assert code == 3 and "no lean list" in out, out
    assert "Outcome: not ready: the zip has no lean list" in (target / "INSTALLED.txt").read_text(encoding="utf-8-sig")


def test_no_at_step_one_writes_nothing(tmp_path):
    target = tmp_path / "fk"
    code, out = run_script(target, ["n"], make_zip(tmp_path / "fk.zip"))
    assert code == 3, out
    assert "STOPPED at step 1" in out
    assert not target.exists()


def test_end_of_input_is_no(tmp_path):
    target = tmp_path / "fk"
    code, out = run_script(target, [], make_zip(tmp_path / "fk.zip"))
    assert code == 3 and not target.exists(), out


def test_folder_with_files_is_refused_and_untouched(tmp_path):
    target = tmp_path / "fk"
    target.mkdir()
    (target / "mine.txt").write_text("keep")
    code, out = run_script(target, ["y", "y", "y"], make_zip(tmp_path / "fk.zip"))
    assert code == 2 and "REFUSED" in out, out
    assert sorted(p.name for p in target.iterdir()) == ["mine.txt"]


def test_unsafe_zip_is_refused_before_unpacking(tmp_path):
    target = tmp_path / "fk"
    bad = make_zip(tmp_path / "bad.zip", {"Gorilla.Fieldkit-main/fieldkit/__main__.py": "", "Gorilla.Fieldkit-main/../x": ""})
    code, out = run_script(target, ["y", "y", "y"], bad)
    assert code == 3 and "outside the folder" in out, out
    assert not target.exists()


def test_full_run_writes_installed_txt(tmp_path):
    target = tmp_path / "fk"
    zip_path = make_zip(tmp_path / "fk.zip")
    code, out = run_script(target, ["y", "y", "y"], zip_path)
    assert code == 0, out
    assert (target / "fieldkit" / "__main__.py").is_file()
    assert (target / ".venv" / "Scripts" / "python.exe").is_file()
    rec = (target / "INSTALLED.txt").read_text(encoding="utf-8-sig")
    assert "Copied (not downloaded): file:///" in rec
    assert f"({zip_path.stat().st_size} bytes)" in rec and "sha256 " in rec
    assert "Python used: " in rec and "Created: " in rec
    assert "Skipped (FIELDKIT_SKIP_PIP=1): the lean set pyyaml>=6 psutil>=5.9" in rec
    assert "doctor --lean: exit 0" in rec
    assert f"To remove: delete the folder {target}" in rec
    assert rec.rstrip().endswith("Outcome: ready; doctor --lean passed")


def test_doctor_failure_is_not_ready(tmp_path):
    target = tmp_path / "fk"
    code, out = run_script(target, ["y", "y", "y"], make_zip(tmp_path / "fk.zip", doctor_exit=3))
    assert code == 3, out
    rec = (target / "INSTALLED.txt").read_text(encoding="utf-8-sig")
    assert "doctor --lean: exit 3" in rec and "Outcome: not ready" in rec


def test_no_at_venv_stops_with_record(tmp_path):
    target = tmp_path / "fk"
    code, out = run_script(target, ["y", "y", "n"], make_zip(tmp_path / "fk.zip"))
    assert code == 3, out
    assert not (target / ".venv").exists()
    rec = (target / "INSTALLED.txt").read_text(encoding="utf-8-sig")
    assert "Outcome: not ready: at step 4a (your no)" in rec
