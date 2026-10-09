"""The commit hook refuses only a failure that reproduces (2026-10-09: a timing test failed once under load and passed
3 of 3 alone). A failure that does not reproduce is logged in state/flaky-tests.log; one that does refuses."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[1] / "hooks" / "pre-commit"
SH = shutil.which("sh")


def _repo(tmp_path, test_body):
    r = tmp_path / "repo"
    (r / "hooks").mkdir(parents=True)
    (r / "tests").mkdir()
    shutil.copy(HOOK, r / "hooks" / "pre-commit")
    (r / "tests" / "test_x.py").write_text(test_body, encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
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
