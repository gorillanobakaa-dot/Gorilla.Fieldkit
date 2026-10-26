"""fieldkit testcost: a passed tree is never re-tested, the plan maps changes to tests, nothing weakens by default."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from fieldkit import testcost

ROOT = Path(__file__).resolve().parents[1]


def git(repo, *a):
    return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, check=True).stdout


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_TESTCOST_DIR", str(tmp_path / "state"))
    r = tmp_path / "r"
    r.mkdir()
    git(r, "init", "-q")
    git(r, "config", "user.email", "t@t")
    git(r, "config", "user.name", "t")
    (r / "fieldkit" / "a").mkdir(parents=True)
    (r / "fieldkit" / "__init__.py").write_text("")
    (r / "fieldkit" / "a" / "__init__.py").write_text("")
    (r / "fieldkit" / "a" / "b.py").write_text("X = 1\n")
    (r / "fieldkit" / "lonely.py").write_text("Y = 2\n")
    (r / "tests").mkdir()
    (r / "tests" / "test_b.py").write_text("from fieldkit.a import b\n\ndef test_b():\n    assert b.X == 1\n")
    (r / "tests" / "test_other.py").write_text("import fieldkit\n\ndef test_o():\n    assert True\n")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "init")
    return r


def test_the_key_is_the_tree_the_tests_see_and_leaves_the_index_alone(repo):
    k1 = testcost.tree_key(repo)
    assert k1 == testcost.tree_key(repo)
    (repo / "new.txt").write_text("untracked counts: the tests can read it")
    k2 = testcost.tree_key(repo)
    assert k2 != k1
    assert git(repo, "status", "--porcelain") == "?? new.txt\n"   # the real index did not take the file


def test_only_a_pass_on_the_same_tree_lets_a_check_skip(repo):
    k = testcost.tree_key(repo)
    assert testcost.seen("full-suite", k) is None
    testcost.record("full-suite", "fail", 600, k)
    assert testcost.seen("full-suite", k) is None
    testcost.record("full-suite", "pass", 600, "some-other-tree")
    assert testcost.seen("full-suite", k) is None
    testcost.record("full-suite", "pass", 590, k)
    assert testcost.seen("full-suite", k)["seconds"] == 590
    assert testcost.seen("full-suite", "") is None


def test_the_budget_is_the_median_of_passes_and_needs_three(repo):
    for s in (100, 300):
        testcost.record("pipeline", "pass", s)
    testcost.record("pipeline", "fail", 9999)
    assert testcost.budget("pipeline") is None
    testcost.record("pipeline", "pass", 200)
    assert testcost.budget("pipeline") == 200


def test_every_gate_tier_is_off_unless_the_owner_set_it_true(tmp_path, monkeypatch):
    monkeypatch.setattr(testcost, "ROOT", tmp_path)
    assert not any(testcost.flags().values())
    (tmp_path / "fieldkit.local.json").write_text(json.dumps(
        {"testcost": {"docs_only_privacy_only": True, "leak_gate_once_per_release": "yes"}}))
    f = testcost.flags()
    assert f["docs_only_privacy_only"] is True
    assert f["leak_gate_once_per_release"] is False      # only a real True counts


def test_documentation_only_means_no_test_can_read_it():
    assert testcost.is_docs_only(["README.md", "docs/guide.md", "Changelogs/x.txt"])
    assert not testcost.is_docs_only(["skills/fieldkit-hog/SKILL.md"])    # skill tests read skills
    assert not testcost.is_docs_only(["README.md", "fieldkit/a/b.py"])
    assert not testcost.is_docs_only(["docs/groups.yaml"])
    assert not testcost.is_docs_only([])


def test_the_plan_maps_a_module_to_the_tests_that_import_it(repo):
    pl = testcost.plan(repo, paths=["fieldkit/a/b.py"])
    assert [Path(t).name for t in pl["tests"]] == ["test_b.py"]          # `import fieldkit` alone covers nothing
    pl = testcost.plan(repo, paths=["fieldkit/lonely.py"])
    assert pl["tests"] == [] and pl["unmapped"] == ["fieldkit/lonely.py"]
    pl = testcost.plan(repo, paths=["tests/test_other.py", "README.md"])
    assert [Path(t).name for t in pl["tests"]] == ["test_other.py"] and pl["unmapped"] == []


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_the_hook_does_not_run_the_suite_twice_on_the_same_tree(repo, tmp_path):
    hooks = repo / "hooks"
    hooks.mkdir()
    shutil.copyfile(ROOT / "hooks" / "pre-commit", hooks / "pre-commit")
    git(repo, "config", "core.hooksPath", "hooks")
    # the repo's own stand-in package must not shadow the real fieldkit the hook calls
    shutil.rmtree(repo / "fieldkit")
    (repo / "tests" / "test_b.py").unlink()
    git(repo, "add", "-A")
    env = dict(os.environ, PYTHONPATH=str(ROOT), FIELDKIT_TESTCOST_DIR=str(tmp_path / "state"),
               VIRTUAL_ENV=str(Path(sys.executable).parent.parent), PYTEST_ADDOPTS="")
    r1 = subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "one"], env=env, capture_output=True, text=True)
    assert r1.returncode == 0, r1.stdout + r1.stderr
    r2 = subprocess.run(["git", "-C", str(repo), "commit", "-q", "--amend", "-m", "one, retried"], env=env,
                        capture_output=True, text=True)
    assert r2.returncode == 0, r2.stdout + r2.stderr
    runs = [json.loads(x) for x in (tmp_path / "state" / "runs.jsonl").read_text().splitlines()]
    assert [x["status"] for x in runs if x["check"] == "full-suite"] == ["pass", "skipped"]
    assert "passed on this exact tree" in r2.stdout + r2.stderr


def test_the_run_guard_gets_the_budget_line_and_never_a_stop(repo):
    assert testcost.preflight_check({"label": "pre-commit", "kind": "test"}) == ("ok", "")
    for s in (500, 600, 700):
        testcost.record("pre-commit", "pass", s)
    st, line = testcost.preflight_check({"label": "pre-commit", "kind": "test"})
    assert st == "ok" and "10.0 min" in line
    from fieldkit.core import preflight
    assert "fieldkit.testcost:preflight_check" in preflight.STEPS
