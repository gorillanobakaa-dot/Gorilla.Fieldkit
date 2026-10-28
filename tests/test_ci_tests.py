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
    monkeypatch.setattr(ci, "selection", lambda base: (tests, "none"))
    monkeypatch.setattr(ci.subprocess, "call", lambda cmd: pytest.fail("pytest must not run"))
    assert ci.main(["x", "origin/main"]) == code
