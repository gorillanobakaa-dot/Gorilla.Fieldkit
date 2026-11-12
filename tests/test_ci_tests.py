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
    assert ci.selection("0" * 40)[0] is ci.REFUSED
    assert ci.selection("")[0] is ci.REFUSED


def test_only_the_covering_tests(monkeypatch):
    fake(monkeypatch, {"tests": ["tests\\test_b.py", "tests/test_a.py"], "unmapped": [], "docs_only": False})
    assert ci.selection("origin/main")[0] == ["tests/test_a.py", "tests/test_b.py"]


def test_docs_only_runs_nothing(monkeypatch):
    fake(monkeypatch, {"tests": [], "unmapped": [], "docs_only": True})
    assert ci.selection("origin/main") == ([], "no code changed - no tests")


def test_no_code_runs_nothing_even_when_docs_only_is_false(monkeypatch):
    fake(monkeypatch, {"tests": [], "unmapped": [], "docs_only": False, "no_code": True})
    assert ci.selection("origin/main") == ([], "no code changed - no tests")


def test_unmapped_refuses_and_never_runs_the_whole_suite_but_mutation_plans_select_normally(monkeypatch):
    fake(monkeypatch, {"tests": ["tests/test_a.py"], "unmapped": ["fieldkit/x.py"], "docs_only": False})
    tests, why = ci.selection("origin/main")
    assert tests is ci.REFUSED and "fieldkit/x.py" in why and "testmap.yaml" in why
    fake(monkeypatch, {"tests": ["tests/test_a.py"], "unmapped": ["tests/x_mutations.yaml"], "docs_only": False})
    assert ci.selection("origin/main")[0] == ["tests/test_a.py"]


def test_no_answer_from_testcost_refuses_never_the_whole_suite(monkeypatch):
    fake(monkeypatch, out="Traceback ...")
    assert ci.selection("origin/main")[0] is ci.REFUSED


def test_a_refused_selection_fails_ci_in_seconds_and_runs_no_pytest(monkeypatch, capsys):
    fake(monkeypatch, {"tests": [], "unmapped": ["fieldkit/x.py"], "docs_only": False})
    called = []
    monkeypatch.setattr(ci.subprocess, "call", lambda *a, **k: called.append(a) or 0)
    assert ci.main(["ci_tests.py", "origin/main"]) == 1 and not called
    assert "REFUSED - no test was run" in capsys.readouterr().out


def test_only_full_runs_the_whole_suite():
    assert ci.selection("full")[0] is None


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


# -- P9 (2026-11-11): desktop-file-validate without Ubuntu's archive (its DNS was down for hours) --------------------
GH = Path(__file__).resolve().parents[1] / ".github"
DFU = GH / "desktop-file-utils.sh"


def _pinned():
    import re
    t = DFU.read_text(encoding="utf-8")
    return (GH / re.search(r'DEB="\$\(dirname "\$0"\)/(\S+)"', t).group(1)), re.search(r'SHA256="([0-9a-f]{64})"', t).group(1)


def test_the_pinned_package_matches_its_hash_and_both_workflows_use_it_with_a_time_cap():
    import hashlib
    import yaml
    deb, sha = _pinned()
    assert hashlib.sha256(deb.read_bytes()).hexdigest() == sha
    for wf in ("tests.yml", "release.yml"):
        jobs = yaml.safe_load((GH / "workflows" / wf).read_text(encoding="utf-8"))["jobs"]
        steps = (jobs.get("shard-ubuntu-latest") or jobs["test"])["steps"]   # tests.yml: one job per shard
        step = [s for s in steps if "desktop-file-validate" in s.get("name", "")]
        assert len(step) == 1 and step[0]["run"].strip() == "bash .github/desktop-file-utils.sh", wf
        assert 0 < step[0]["timeout-minutes"] <= 10, wf                         # a hung step never eats an hour
        assert not any("apt-get" in str(s.get("run", "")) for s in steps), wf   # apt only inside the script, as fallback


@pytest.mark.skipif(not __import__("shutil").which("sha256sum") or __import__("sys").platform == "win32",
                    reason="the script runs on the Linux runner (bash + sha256sum)")
def test_a_wrong_hash_fails_and_installs_nothing(tmp_path):
    import shutil
    import subprocess
    deb, _ = _pinned()
    (tmp_path / "vendor").mkdir()
    shutil.copy(DFU, tmp_path / DFU.name)
    (tmp_path / "vendor" / deb.name).write_bytes(deb.read_bytes() + b"x")
    log = tmp_path / "calls"
    fake = tmp_path / "fake-sudo"
    fake.write_text(f'#!/bin/sh\necho "$@" >> "{log}"\n', encoding="utf-8")
    fake.chmod(0o755)
    r = subprocess.run(["bash", str(tmp_path / DFU.name)], capture_output=True, text=True,
                       env={"PATH": "/usr/bin:/bin", "SUDO": str(fake)})
    assert r.returncode == 1 and "WRONG HASH" in r.stdout, r.stdout + r.stderr
    assert not log.exists()                                                     # no dpkg, no apt fallback


def test_a_batch_pr_runs_the_selection_in_its_body_and_a_normal_pr_does_not(tmp_path):
    ev = tmp_path / "e.json"
    ev.write_text(json.dumps({"pull_request": {"body": 'x\n<!-- fieldkit-batch {"tests": ["tests\\\\a.py", "tests/b.py"]} -->'}}))
    assert ci.batch_selection(str(ev), "night/batch-20261112-0900") == ["tests/a.py", "tests/b.py"]
    assert ci.batch_selection(str(ev), "feat/x") is None            # only a batch branch carries a selection
    ev.write_text(json.dumps({"pull_request": {"body": 'no marker'}}))
    assert ci.batch_selection(str(ev), "night/batch-1") is None     # no marker: the normal selection
    ev.write_text(json.dumps({"pull_request": {"body": '<!-- fieldkit-batch {"tests": null} -->'}}))
    assert ci.batch_selection(str(ev), "night/batch-1") is None     # the tool could not select: normal, never fewer
