"""The commit hook refuses only a failure that reproduces (2026-10-09: a timing test failed once under load and passed
3 of 3 alone). A failure that does not reproduce is logged in state/flaky-tests.log; one that does refuses."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[1] / "hooks" / "pre-commit"
SH = shutil.which("sh")


def _expose(tmp_path, dirname, names):
    """Put the TEST interpreter on PATH under the given names.

    TRAP: a SYMLINK does not work here. A venv interpreter finds its pyvenv.cfg
    beside its own executable path, so a symlink from somewhere else silently
    falls back to the system interpreter, which has no pytest. The hook then
    correctly reports "no interpreter with pytest" and the test looks like a
    hook bug. Use a wrapper that execs the real path instead.
    """
    import sys
    d = tmp_path / dirname
    d.mkdir(exist_ok=True)
    for name in names:
        w = d / name
        w.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n', encoding="utf-8")
        w.chmod(0o755)
    return d


def _bin_with_python(tmp_path):
    return _expose(tmp_path, "bin", ("python", "python3"))


def _repo(tmp_path, test_body):
    r = tmp_path / "repo"
    (r / "hooks").mkdir(parents=True)
    (r / "tests").mkdir()
    shutil.copy(HOOK, r / "hooks" / "pre-commit")
    (r / "tests" / "test_x.py").write_text(test_body, encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["PATH"] = f"{_bin_with_python(tmp_path)}:{env.get('PATH', '')}"
    env.pop("VIRTUAL_ENV", None)
    # the temp repo is not fieldkit, so the privacy scan needs the real package
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    env["FIELDKIT_FULL_SUITE"] = "1"        # the reproduce logic of a suite run is under test, not the selection
    subprocess.run(["git", "init", "-q", str(r)], check=True, env=env)
    return r, env


def _run(r, env):
    p = subprocess.run([SH, "hooks/pre-commit"], cwd=r, capture_output=True, text=True, env=env, timeout=300)
    return p.returncode, p.stdout + p.stderr


FLAKY = '''from pathlib import Path
def test_once():
    m = Path(__file__).with_name("ran-once")
    if not m.exists():
        m.write_text("x")
        assert False, "first run fails"
'''


@pytest.mark.skipif(not SH, reason="no sh")
def test_a_failure_that_does_not_reproduce_is_logged_not_refused(tmp_path):
    r, env = _repo(tmp_path, FLAKY)
    rc, out = _run(r, env)
    assert "tests failed" not in out, out
    log = (r / "state" / "flaky-tests.log").read_text(encoding="utf-8")
    assert "tests/test_x.py::test_once" in log and "WARNING" in out


@pytest.mark.skipif(not SH, reason="no sh")
def test_a_failure_that_reproduces_refuses_the_commit(tmp_path):
    r, env = _repo(tmp_path, "def test_always():\n    assert False\n")
    rc, out = _run(r, env)
    assert rc == 1 and "commit refused" in out and "test_always" in out
    assert not (r / "state" / "flaky-tests.log").exists()


@pytest.mark.skipif(not SH, reason="no sh")
def test_a_collection_error_refuses_the_commit(tmp_path):
    r, env = _repo(tmp_path, "import no_such_module_anywhere\n")
    rc, out = _run(r, env)
    assert rc == 1 and "commit refused" in out


# ---------------------------------------------------------------------------
# 2026-10-13: the hook called a bare `python`, which does not exist on Debian
# (only python3). It therefore printed "tests failed - commit refused" when the
# tests had never run at all. Refusing was right; the stated reason was false,
# and a false reason sends somebody hunting a failure that does not exist.
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not SH, reason="no sh")
def test_the_hook_finds_an_interpreter_even_without_a_bare_python(tmp_path):
    """PATH deliberately has python3 but NO `python`, which is normal Debian."""
    r, env = _repo(tmp_path, "def test_fine():\n    assert True\n")
    # python3 ONLY, no `python` alias: the normal Debian shape.
    bin_ = _expose(tmp_path, "onlypython3", ("python3",))
    env = dict(env, PATH=f"{bin_}:/usr/bin:/bin")
    env.pop("VIRTUAL_ENV", None)
    rc, out = _run(r, env)
    assert "COULD NOT RUN" not in out, out
    assert "tests failed" not in out, out


@pytest.mark.skipif(not SH, reason="no sh")
def test_no_interpreter_with_pytest_says_so_instead_of_blaming_the_tests(tmp_path):
    """The distinction that matters: 'nothing was verified' is not 'tests failed'."""
    r, env = _repo(tmp_path, "def test_fine():\n    assert True\n")
    # git stays reachable (a wrapper), every Python does not. 2026-10-16: with PATH holding only an empty folder, the
    # hook run by Git for Windows' sh (as in a real commit) died at its first line, "git: command not found", before
    # it looked for an interpreter, so the test failed only inside the commit hook.
    empty = tmp_path / "nointerp"
    empty.mkdir()
    g = empty / "git"
    g.write_text(f'#!/bin/sh\nexec "{Path(shutil.which("git")).as_posix()}" "$@"\n', encoding="utf-8", newline="\n")
    g.chmod(0o755)
    env = dict(env, PATH=str(empty))
    env.pop("VIRTUAL_ENV", None)
    rc, out = _run(r, env)
    assert rc == 1
    assert "COULD NOT RUN THE TESTS" in out
    assert "NOT a test failure" in out
    assert "tests failed - commit refused" not in out
