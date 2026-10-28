"""CI test selection (.github/ci_tests.py, R7 in docs/HARNESS-PLAN.md): the plan is faked, nothing runs pytest."""
import importlib.util
import json
import types
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("ci_tests", Path(__file__).resolve().parents[1] / ".github" / "ci_tests.py")
ci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci)


def fake(monkeypatch, plan=None, out=None):
    def run(cmd, **kw):
        return types.SimpleNamespace(returncode=0, stdout=out if out is not None else json.dumps(plan))
    monkeypatch.setattr(ci.subprocess, "run", run)


def test_full_when_asked_or_no_before():
    assert ci.selection("full")[0] is None
    assert ci.selection("0" * 40)[0] is None
    assert ci.selection("")[0] is None


def test_only_the_covering_tests(monkeypatch):
    fake(monkeypatch, {"tests": ["tests\\test_b.py", "tests/test_a.py"], "unmapped": [], "docs_only": False})
    assert ci.selection("origin/main")[0] == ["tests/test_a.py", "tests/test_b.py"]


def test_docs_only_runs_nothing(monkeypatch):
    fake(monkeypatch, {"tests": [], "unmapped": [], "docs_only": True})
    assert ci.selection("origin/main") == ([], "docs only: no test covers the change")


def test_unmapped_runs_the_whole_suite_but_mutation_plans_do_not(monkeypatch):
    fake(monkeypatch, {"tests": ["tests/test_a.py"], "unmapped": ["fieldkit/x.py"], "docs_only": False})
    assert ci.selection("origin/main")[0] is None
    fake(monkeypatch, {"tests": ["tests/test_a.py"], "unmapped": ["tests/x_mutations.yaml"], "docs_only": False})
    assert ci.selection("origin/main")[0] == ["tests/test_a.py"]


def test_no_answer_from_testcost_is_the_whole_suite(monkeypatch):
    fake(monkeypatch, out="Traceback ...")
    assert ci.selection("origin/main")[0] is None


@pytest.mark.parametrize("tests,code", [([], 0)])
def test_main_runs_nothing_for_an_empty_selection(monkeypatch, tests, code):
    monkeypatch.setattr(ci, "selection", lambda base, merged=False: (tests, "none"))
    monkeypatch.setattr(ci.subprocess, "call", lambda cmd: pytest.fail("pytest must not run"))
    assert ci.main(["x", "origin/main"]) == code


def _git(repo, *a):
    import subprocess
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *a],
                          capture_output=True, text=True, check=True).stdout


def _merge_repo(tmp_path, main_moved):
    _git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "a").write_text("1")
    _git(tmp_path, "add", "-A"); _git(tmp_path, "commit", "-qm", "base")
    _git(tmp_path, "checkout", "-qb", "pr")
    (tmp_path / "b").write_text("2")
    _git(tmp_path, "add", "-A"); _git(tmp_path, "commit", "-qm", "pr")
    _git(tmp_path, "checkout", "-q", "main")
    if main_moved:
        (tmp_path / "c").write_text("3")
        _git(tmp_path, "add", "-A"); _git(tmp_path, "commit", "-qm", "main moved")
    _git(tmp_path, "merge", "-q", "--no-ff", "-m", "merge", "pr")


def test_merge_of_an_unmoved_main_is_already_tested(tmp_path, monkeypatch):
    _merge_repo(tmp_path, main_moved=False)
    monkeypatch.chdir(tmp_path)
    assert ci.merged_tree_already_tested() is True
    assert ci.selection("origin/main", merged=True)[0] == []


def test_merge_after_main_moved_is_tested(tmp_path, monkeypatch):
    _merge_repo(tmp_path, main_moved=True)
    monkeypatch.chdir(tmp_path)
    assert ci.merged_tree_already_tested() is False


def test_merged_flag_is_off_for_pull_requests(monkeypatch):
    monkeypatch.setattr(ci, "merged_tree_already_tested", lambda head="HEAD": pytest.fail("must not be asked"))
    fake(monkeypatch, {"tests": ["tests/test_a.py"], "unmapped": [], "docs_only": False})
    assert ci.selection("origin/main")[0] == ["tests/test_a.py"]
