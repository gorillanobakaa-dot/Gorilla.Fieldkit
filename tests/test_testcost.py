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
    """The case the cache exists for: a commit refused AFTER the suite (privacy scan, a message typo) is tried again on
    the same tree. Whether the commit itself goes through depends on the scan, so only the log is asserted."""
    hooks = repo / "hooks"
    hooks.mkdir()
    shutil.copy2(ROOT / "hooks" / "pre-commit", hooks / "pre-commit")   # copy2 keeps the mode: Linux git skips a
    os.chmod(hooks / "pre-commit", 0o755)                                # hook that is not executable
    git(repo, "config", "core.hooksPath", "hooks")
    # the repo's own stand-in package must not shadow the real fieldkit the hook calls
    shutil.rmtree(repo / "fieldkit")
    (repo / "tests" / "test_b.py").unlink()
    # like the real repo: bytecode the suite writes is ignored (CI 2026-10-26: without it the second attempt saw new
    # __pycache__ files, a different tree, and on Windows the privacy scan read paths inside the .pyc files)
    (repo / ".gitignore").write_text("__pycache__/\n*.pyc\n")
    git(repo, "add", "-A")
    env = dict(os.environ, PYTHONPATH=str(ROOT), FIELDKIT_TESTCOST_DIR=str(tmp_path / "state"),
               VIRTUAL_ENV=str(Path(sys.executable).parent.parent), PYTEST_ADDOPTS="", FIELDKIT_PREFLIGHT="0",
               PYTHONDONTWRITEBYTECODE="1", FIELDKIT_FULL_SUITE="1")   # the full-suite cache (affected: own test)
    outs = []
    for _ in range(2):
        r = subprocess.run(["git", "-C", str(repo), "commit", "-q", "--allow-empty", "-m", "one"], env=env,
                           capture_output=True, text=True)
        outs.append(r.stdout + r.stderr)
        if r.returncode == 0:      # it went through: undo it, so the second attempt sees the same tree again
            git(repo, "reset", "-q", "--soft", "HEAD~1") if git(repo, "rev-list", "--count", "HEAD").strip() != "1"                 else None
    log = tmp_path / "state" / "runs.jsonl"
    assert log.exists(), "the hook never ran: " + " | ".join(outs)
    runs = [json.loads(x) for x in log.read_text().splitlines()]
    assert [x["status"] for x in runs if x["check"] == "full-suite"] == ["pass", "skipped"], outs
    assert "passed on this exact tree" in outs[1]


def test_the_run_guard_gets_the_budget_line_and_never_a_stop(repo):
    assert testcost.preflight_check({"label": "pre-commit", "kind": "test"}) == ("ok", "")
    for s in (500, 600, 700):
        testcost.record("pre-commit", "pass", s)
    st, line = testcost.preflight_check({"label": "pre-commit", "kind": "test"})
    assert st == "ok" and "10.0 min" in line
    from fieldkit.core import preflight
    assert "fieldkit.testcost:preflight_check" in preflight.STEPS


def test_run_fast_does_not_repeat_the_same_tests_on_the_same_tree(repo, monkeypatch, capsys):
    (repo / "fieldkit" / "a" / "b.py").write_text("X = 1\n# edit\n")
    calls = []
    monkeypatch.setattr(testcost.subprocess, "call", lambda cmd, **kw: calls.append(cmd) or 0)
    assert testcost.main(["run", "--fast", "--base", "HEAD", "--repo", str(repo)]) == 0
    assert testcost.main(["run", "--fast", "--base", "HEAD", "--repo", str(repo)]) == 0
    assert len(calls) == 1, "the second run on the identical tree must be skipped"
    assert "not run again" in capsys.readouterr().out
    (repo / "fieldkit" / "a" / "b.py").write_text("X = 1\n# edit 2\n")      # a changed tree runs again
    assert testcost.main(["run", "--fast", "--base", "HEAD", "--repo", str(repo)]) == 0
    assert len(calls) == 2


VERSION_BUMP = ["-version = \"0.6.0\"", "+version = \"0.6.1\""]


def _plan(paths, diffs):
    return testcost.plan(".", paths=paths, diff_lines=lambda p: diffs.get(p, []))


def test_a_version_only_diff_is_no_code_and_selects_nothing():
    pl = _plan(["pyproject.toml", "fieldkit/__init__.py"],
               {"pyproject.toml": VERSION_BUMP, "fieldkit/__init__.py": ["-__version__ = \"0.6.0\"", "+__version__ = \"0.6.1\""]})
    assert pl["no_code"] and pl["tests"] == [] and pl["unmapped"] == []


def test_a_version_file_with_another_changed_line_is_code():
    pl = _plan(["pyproject.toml"], {"pyproject.toml": VERSION_BUMP + ["+name = \"x\""]})
    assert not pl["no_code"] and (pl["tests"] or pl["unmapped"])


def test_a_version_file_with_no_readable_diff_is_code():
    assert not _plan(["pyproject.toml"], {})["no_code"]


def test_a_file_that_is_not_a_version_file_is_never_version_only():
    assert not _plan(["fieldkit/testcost.py"], {"fieldkit/testcost.py": VERSION_BUMP})["no_code"]


def test_docs_state_and_version_together_are_no_code_but_one_code_file_is_not():
    paths = ["docs/HARNESS-PLAN.md", "state/coordination/PLAN.md", "pyproject.toml"]
    assert _plan(paths, {"pyproject.toml": VERSION_BUMP})["no_code"]
    assert not _plan(paths + ["fieldkit/testcost.py"], {"pyproject.toml": VERSION_BUMP})["no_code"]


def test_only_the_named_version_files_can_be_version_only():
    assert testcost.is_version_only("pyproject.toml", VERSION_BUMP)
    assert not testcost.is_version_only("setup.cfg", VERSION_BUMP)


def test_a_file_under_state_selects_no_test_whatever_its_suffix():
    pl = _plan(["state/coordination/run.py"], {})
    assert pl["no_code"] and pl["tests"] == [] and pl["unmapped"] == []
