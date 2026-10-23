# Version: 1.0.0 · updated 26-10-23-01-35
"""install.sh: Fieldkit on a computer without git (2026-10-23, the 2011 MacBook Air). Run for real, offline: the zip
comes from a file:// URL and FIELDKIT_SKIP_PIP keeps pip off the network."""
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(os.name == "nt" or not shutil.which("sh") or not shutil.which("curl"),
                                reason="install.sh is the macOS/Linux door; Windows gets install.ps1")


def _zip(tmp_path):
    z = tmp_path / "fk.zip"
    with zipfile.ZipFile(z, "w") as zf:
        for rel in ("fieldkit/__init__.py", "pyproject.toml", "install.sh"):
            zf.write(ROOT / rel, f"Gorilla.Fieldkit-main/{rel}")
        for p in (ROOT / "fieldkit" / "core").rglob("*"):
            if p.is_file() and "__pycache__" not in p.parts:
                zf.write(p, f"Gorilla.Fieldkit-main/{p.relative_to(ROOT)}")
    return z


def run(args, cwd, **env):
    """The stranger's case: install.sh downloaded ON ITS OWN into an empty folder (inside a checkout it skips the
    download, by design)."""
    e = dict(os.environ, FIELDKIT_SKIP_PIP="1", **env)
    script = Path(cwd) / "install.sh"
    shutil.copy(ROOT / "install.sh", script)
    return subprocess.run(["sh", str(script), *args], cwd=cwd, env=e, capture_output=True, text=True,
                          timeout=300, stdin=subprocess.DEVNULL)


def test_downloads_the_zip_into_a_new_folder_and_lists_what_it_did(tmp_path):
    work = tmp_path / "w"
    work.mkdir()
    r = run(["--yes"], work, FIELDKIT_ZIP_URL=_zip(tmp_path).as_uri())
    out = r.stdout + r.stderr
    dest = work / "Fieldkit"
    assert (dest / "fieldkit" / "core" / "doctor.py").is_file(), out[-1500:]
    assert (dest / ".venv").is_dir() and "WAY IN:" in out                      # the probe ran
    listing = (dest / "INSTALLED.txt").read_text()
    assert "downloaded" in listing and "fk.zip" in listing and ".venv" in listing and "delete" in listing


def test_never_unzips_into_a_folder_that_has_files(tmp_path):
    work = tmp_path / "w"
    (work / "Fieldkit").mkdir(parents=True)
    (work / "Fieldkit" / "mine.txt").write_text("keep me")
    r = run(["--yes"], work, FIELDKIT_ZIP_URL=_zip(tmp_path).as_uri())
    assert r.returncode == 3 and "already has files" in r.stdout
    assert (work / "Fieldkit" / "mine.txt").read_text() == "keep me"


def test_without_yes_and_no_answer_nothing_is_downloaded(tmp_path):
    work = tmp_path / "w"
    work.mkdir()
    r = run([], work, FIELDKIT_ZIP_URL=_zip(tmp_path).as_uri())
    assert r.returncode == 3 and "Nothing downloaded" in r.stdout and not (work / "Fieldkit").exists()


def test_help_and_bad_option():
    r = subprocess.run(["sh", str(ROOT / "install.sh"), "--help"], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0 and "without git" in r.stdout.lower()
    r = subprocess.run(["sh", str(ROOT / "install.sh"), "--nope"], capture_output=True, text=True, timeout=30)
    assert r.returncode == 2
