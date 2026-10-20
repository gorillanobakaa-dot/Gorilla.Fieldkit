"""The stranger test (2026-10-20): a fresh clone, venv and HOME; every step a row; the first broken step stops it."""
from pathlib import Path
from types import SimpleNamespace

from fieldkit.method import stranger as st


def test_a_stranger_has_a_fresh_home_and_no_pointer_to_the_owners_fieldkit():
    env = st.stranger_env("/tmp/h", base={"HOME": "/u", "USERPROFILE": "C:/u", "PYTHONPATH": "/fk", "FIELDKIT_ROOT": "/fk",
                                           "PFIND_CHROMA_PYTHON": "/py", "GH_TOKEN": "x", "PATH": "/bin"})
    assert env["HOME"] == env["USERPROFILE"] == str(Path("/tmp/h")) and env["PATH"] == "/bin"
    for leaked in ("PYTHONPATH", "FIELDKIT_ROOT", "PFIND_CHROMA_PYTHON", "GH_TOKEN"):     # an allowlist, not a denylist
        assert leaked not in env
    assert env["LOCALAPPDATA"].startswith(str(Path("/tmp/h"))) and env["XDG_CONFIG_HOME"].startswith(str(Path("/tmp/h")))


def test_failing_tests_are_named():
    out = "x\nFAILED tests/a.py::test_one - boom\nFAILED tests/b.py::test_two\n2 failed, 9 passed\n"
    assert st.failing_tests(out) == ["tests/a.py::test_one", "tests/b.py::test_two"]


def _runner(calls, pytest_rc=0, clone_rc=0):
    def run(cmd, **kw):
        calls.append((cmd, kw.get("env")))
        c = " ".join(map(str, cmd))
        if "clone" in c:
            Path(cmd[-1]).mkdir(parents=True, exist_ok=True)
            return SimpleNamespace(returncode=clone_rc, stdout="", stderr="fatal: no such ref" if clone_rc else "")
        if "-m pytest" in c:
            out = "FAILED tests/t.py::test_x - e\n1 failed, 5 passed in 1s\n" if pytest_rc else "6 passed in 1s\n"
            return SimpleNamespace(returncode=pytest_rc, stdout=out, stderr="")
        return SimpleNamespace(returncode=0, stdout="main\n", stderr="")
    return run


def test_every_step_is_a_row_and_the_suite_runs_with_the_fresh_home(tmp_path):
    calls = []
    r = st.run(tmp_path / "repo", ref="main", keep=True, say=lambda m: None, runner=_runner(calls), work=tmp_path / "w")
    assert r["ok"] and [x["step"] for x in r["rows"]][:3] == ["fresh clone", "fresh virtual environment", 'pip install -e ".[test]"']
    env = next(e for c, e in calls if "-m pytest" in " ".join(map(str, c)))
    assert env["HOME"] == str(tmp_path / "w" / "home") and (tmp_path / "w" / "home" / "Documents").is_dir()


def test_a_failing_suite_fails_the_test_and_names_the_tests(tmp_path):
    r = st.run(tmp_path / "repo", ref="main", keep=True, say=lambda m: None, runner=_runner([], pytest_rc=1),
               work=tmp_path / "w")
    suite = next(x for x in r["rows"] if x["step"].startswith("test suite"))
    assert not r["ok"] and not suite["ok"] and "tests/t.py::test_x" in suite["evidence"]


def test_a_failed_clone_stops_before_anything_else(tmp_path):
    calls = []
    r = st.run(tmp_path / "repo", ref="nope", keep=True, say=lambda m: None, runner=_runner(calls, clone_rc=1),
               work=tmp_path / "w")
    assert not r["ok"] and len(r["rows"]) == 1 and not any("pip" in " ".join(map(str, c)) for c, _ in calls)
