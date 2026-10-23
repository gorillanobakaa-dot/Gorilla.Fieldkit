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


def _flaky_curl(tmp_path, zipfile_path, plan):
    """A curl stand-in on PATH: each call follows the next step of `plan` ('half': write half the bytes and fail
    with 18, a dropped connection; 'none': write nothing and fail; 'all': write the rest and succeed). It honours
    -C - by appending to the existing part file, like curl does."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    state = tmp_path / "curl-calls"
    state.write_text("0")
    fake = bindir / "curl"
    fake.write_text(f"""#!{sys.executable}
import sys, pathlib
state = pathlib.Path({str(state)!r}); n = int(state.read_text()); state.write_text(str(n + 1))
plan = {plan!r}; step = plan[min(n, len(plan) - 1)]
out = pathlib.Path(sys.argv[sys.argv.index("-o") + 1])
data = pathlib.Path({str(zipfile_path)!r}).read_bytes()
have = out.stat().st_size if out.exists() and "-C" in sys.argv else 0
if step == "half":
    with open(out, "ab" if have else "wb") as fh: fh.write(data[have:have + max(1, (len(data) - have) // 2)])
    sys.exit(18)
if step == "none":
    sys.exit(28)
with open(out, "ab" if have else "wb") as fh: fh.write(data[have:])
""")
    fake.chmod(0o755)
    return bindir, state


def test_a_dropped_download_resumes_where_it_stopped(tmp_path):
    z = _zip(tmp_path)
    bindir, calls = _flaky_curl(tmp_path, z, ["half", "half", "all"])
    work = tmp_path / "w"
    work.mkdir()
    r = run(["--yes"], work, FIELDKIT_ZIP_URL="https://example.invalid/fk.zip", FIELDKIT_RETRY_WAIT="0",
            PATH=f"{bindir}:{os.environ['PATH']}")
    out = r.stdout + r.stderr
    assert (work / "Fieldkit" / "fieldkit" / "core" / "doctor.py").is_file(), out[-1500:]
    assert calls.read_text() == "3" and out.count("resuming, try") == 2, out[-1500:]


def test_a_download_that_makes_no_progress_stops_instead_of_looping(tmp_path):
    z = _zip(tmp_path)
    bindir, calls = _flaky_curl(tmp_path, z, ["half", "none"])
    work = tmp_path / "w"
    work.mkdir()
    r = run(["--yes"], work, FIELDKIT_ZIP_URL="https://example.invalid/fk.zip", FIELDKIT_RETRY_WAIT="0",
            PATH=f"{bindir}:{os.environ['PATH']}")
    out = r.stdout + r.stderr
    assert r.returncode == 3 and "No progress since the last try" in out, out[-1500:]
    assert calls.read_text() == "2" and not (work / "Fieldkit").exists()
