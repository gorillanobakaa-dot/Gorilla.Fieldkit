"""CI test selection (.github/ci_tests.py, R7 in docs/HARNESS-PLAN.md): the plan is faked, nothing runs pytest."""
import importlib.util
import json
import os
import sys
import types
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("ci_tests", Path(__file__).resolve().parents[1] / ".github" / "ci_tests.py")
ci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci)


def test_e2e_runs_only_affected_jobs_and_falls_back_for_shared_inputs():
    import yaml

    root = Path(__file__).resolve().parents[1]
    loader = importlib.util.spec_from_file_location("ci_e2e", root / ".github" / "ci_e2e.py")
    e2e = importlib.util.module_from_spec(loader)
    loader.loader.exec_module(e2e)
    picked = e2e.select(["fieldkit/leakgate/gate.py", "tests/test_leakgate_status.py"])
    assert picked["e2e_l15"] == []
    assert set(picked["e2e"]) == set(e2e.LEAK_GATE_JOBS)
    workflow = yaml.safe_load((root / ".github/workflows/e2e.yml").read_text(encoding="utf-8"))
    assert len(picked["e2e"]) < len(workflow["jobs"])
    for name in picked["e2e"]:
        assert f"'{name}'" in workflow["jobs"][name]["if"]
    for shared in (".github/workflows/e2e.yml", ".github/e2e/vaio_batch.py", "fieldkit/cli.py"):
        assert e2e.select(["fieldkit/leakgate/gate.py", shared]) == {"e2e": ["*"], "e2e_l15": ["*"]}
    assert e2e.select(["fieldkit/leakgate/new.py"])["e2e"] == ["*"]
    assert e2e.select(["fieldkit/once.py", "fieldkit/lifecycle/pool.py"]) == {
        "e2e": sorted(e2e.both("once", "pool")), "e2e_l15": []}
    batch = e2e.select(["fieldkit/method/train.py", "tests/test_ci_wait.py"])
    assert batch["e2e"] == [] and len(batch["e2e_l15"]) == 10
    l15 = yaml.safe_load((root / ".github/workflows/e2e-l15.yml").read_text(encoding="utf-8"))
    assert all(f"'{name}'" in l15["jobs"][name]["if"] for name in batch["e2e_l15"])
    assert e2e.select(["fieldkit/once.py", "tests/conftest.py"])["e2e"] == ["*"]
    assert e2e.select(None) == {"e2e": ["*"], "e2e_l15": ["*"]}
    assert e2e.select(["docs/METHODS.md"]) == {"e2e": [], "e2e_l15": []}


def test_required_statuses_depend_on_the_e2e_verdict():
    import yaml

    root = Path(__file__).resolve().parents[1]
    workflow = yaml.safe_load((root / ".github/workflows/tests.yml").read_text(encoding="utf-8"))
    gate = workflow["jobs"]["test"]
    assert {"e2e", "e2e-l15"} <= set(gate["needs"])
    assert gate["name"] == "test (${{ matrix.os }})"
    step = gate["steps"][0]
    for result in ("MAIN_RESULT", "L15_RESULT"):
        assert f'test "${result}" = success || exit 1' in step["run"]


def test_ci_shard_reports_a_stall_and_leaves_other_shards_independent(monkeypatch, tmp_path, capsys):
    from contextlib import nullcontext

    from fieldkit.core import contain, liveness

    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.setenv("RUNNER_OS", "fixture")
    monkeypatch.setattr(liveness, "beacon", lambda *args: nullcontext(None))
    calls = []

    def run(cmd, **kwargs):
        path = kwargs["stdout_path"]
        calls.append(path.name)
        assert kwargs["deadline_s"] >= 600 and kwargs["idle_s"] == 360
        path.write_text("test started\n", encoding="utf-8")
        kwargs["on_tick"]()
        return (contain.Outcome("stalled", 124, seconds=361, why="owned process stopped")
                if len(calls) == 1 else contain.Outcome("ok", 0, seconds=1))

    monkeypatch.setattr(contain, "run", run)
    assert ci.run_tests(["fixture"], ["tests/test_ci_tests.py"], "1/4") == 124
    assert ci.run_tests(["fixture"], ["tests/test_ci_templates.py"], "2/4") == 0
    assert calls == ["fieldkit-pytest-1.log", "fieldkit-pytest-2.log"]
    assert "stalled in 361.0s: owned process stopped" in capsys.readouterr().out


def test_merge_preflight_requires_trusted_success_and_exact_base_head_and_tree(monkeypatch):
    folder = Path(__file__).resolve().parents[1] / ".github"
    monkeypatch.syspath_prepend(str(folder))
    loader = importlib.util.spec_from_file_location("ci_merge_proof", folder / "ci_merge_proof.py")
    proof = importlib.util.module_from_spec(loader)
    loader.loader.exec_module(proof)
    expected = {"schema": 1, "base": "a" * 40, "head": "b" * 40, "tree": "c" * 40}
    run = {"id": 31, "run_attempt": 2, "head_sha": expected["head"], "event": "pull_request",
           "head_repository": {"full_name": "owner/repo"}, "status": "completed", "conclusion": "success",
           "path": ".github/workflows/tests.yml"}
    api = lambda _: {"workflow_runs": [run]}

    def download(run_id, attempt):
        assert (run_id, attempt) == (31, 2)
        return expected

    assert proof.prior_run("owner/repo", expected, api, download) == 31
    for different in (dict(expected, base="e" * 40), dict(expected, tree="e" * 40), None):
        assert proof.prior_run("owner/repo", expected, api, lambda *_: different) is None
    for untrusted in (dict(run, conclusion="failure"), dict(run, event="push"),
                      dict(run, head_repository={"full_name": "fork/repo"})):
        assert proof.prior_run("owner/repo", expected, lambda _: {"workflow_runs": [untrusted]}, download) is None


def test_per_file_release_evidence_refuses_skipped_cases_and_changed_context(tmp_path):
    path = Path(__file__).resolve().parents[1] / ".github" / "ci_file_proof.py"
    loader = importlib.util.spec_from_file_location("ci_file_proof", path)
    proof = importlib.util.module_from_spec(loader)
    loader.loader.exec_module(proof)
    report = tmp_path / "report.xml"
    report.write_text('<testsuite><testcase file="tests\\test_a.py"/><testcase file="tests/test_b.py"/>'
                      '<testcase file="tests/test_b.py"><skipped/></testcase>'
                      '<testcase file="tests/test_c.py"><failure/></testcase></testsuite>', encoding="utf-8")
    selected = ["tests/test_a.py", "tests/test_b.py", "tests/test_c.py", "tests/test_d.py"]
    identity = {"tree": "tree-1", "image": "image-1", "packages": "versions-1"}
    receipt = tmp_path / "proof.json"
    declarations = {selected[0]: ["fixture/shared.json"]}
    inputs = proof.scoped_inputs(selected, declarations, lookup=lambda p: p + "-first")
    proof.write_receipt(receipt, report, selected, identity, inputs)
    prior = json.loads(receipt.read_text(encoding="utf-8"))
    assert proof.missing_files(selected, identity, [prior]) == selected[1:]
    for change in (dict(identity, tree="tree-2"), dict(identity, image="image-2"), dict(identity, packages="versions-2")):
        assert proof.missing_files(selected, change, [prior]) == selected
    moved = dict(identity, tree="tree-2")
    assert proof.missing_files(selected, moved, [prior], inputs) == selected[1:]
    changed_inputs = proof.scoped_inputs(selected, declarations,
                                         lookup=lambda p: p + ("-second" if p == "fixture/shared.json" else "-first"))
    assert proof.missing_files(selected, moved, [prior], changed_inputs) == selected
    assert proof.missing_files(selected, dict(moved, image="other"), [prior], inputs) == selected

    run = {"head_sha": "sha", "head_repository": {"full_name": "owner/fieldkit"}, "status": "completed",
           "conclusion": "success", "event": "pull_request", "path": ".github/workflows/tests.yml"}
    assert proof.trusted_run(run, "owner/fieldkit", "sha")
    assert not proof.trusted_run(dict(run, head_repository={"full_name": "fork/fieldkit"}), "owner/fieldkit", "sha")
    assert not proof.trusted_run(dict(run, conclusion="failure"), "owner/fieldkit", "sha")

    release_file = "tests/test_releasecandidate.py"
    report.write_text('<testsuite><testcase file="' + release_file + '" time="0.042" '
                      'name="test_draft_recovers_an_interrupted_upload_without_changing_latest"/></testsuite>',
                      encoding="utf-8")
    proof.write_receipt(receipt, report, [release_file], identity, {})
    assert json.loads(receipt.read_text(encoding="utf-8"))["fixture_seconds"] == {"release": 0.042}


def test_merge_runs_only_selected_files_without_matching_evidence(monkeypatch):
    selected = ["tests/test_ci_tests.py", "tests/test_releasecandidate.py"]
    monkeypatch.setattr(ci, "selection", lambda *_: (selected, "merge fixture"))
    monkeypatch.setenv("FIELDKIT_REUSE_CI", "1")
    monkeypatch.setenv("FIELDKIT_CI_READ_TOKEN", "fixture")
    monkeypatch.delenv("FIELDKIT_TEST_EVIDENCE", raising=False)

    def reuse(files, token):
        assert files == selected and token == "fixture"
        return selected[1:]

    def run(command):
        assert command[-1:] == selected[1:]
        assert "FIELDKIT_CI_READ_TOKEN" not in os.environ
        return 0

    monkeypatch.setitem(sys.modules, "ci_file_proof", types.SimpleNamespace(reuse=reuse))
    monkeypatch.setattr(ci.subprocess, "call", run)
    assert ci.main(["ci_tests.py", "main", "--merged"]) == 0


def test_shards_use_one_checkout_bound_selection(tmp_path, monkeypatch):
    head = "a" * 40
    manifest = tmp_path / "selection.json"
    selected = ["tests/test_ci_tests.py"]
    monkeypatch.setattr(ci, "checkout_head", lambda: head)
    monkeypatch.setattr(ci, "selection", lambda *_: (selected, "common fixture"))
    assert ci.main(["ci_tests.py", "base", "--write-selection", str(manifest)]) == 0
    assert ci.read_selection(manifest, head) == (selected, "common fixture")
    with pytest.raises(ValueError, match="checked out commit"):
        ci.read_selection(manifest, "b" * 40)
    manifest.write_text(json.dumps({"schema": 1, "head": head, "tests": ["tests/../../other"]}), encoding="utf-8")
    with pytest.raises(ValueError, match="invalid test paths"):
        ci.read_selection(manifest, head)


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


def test_present_drops_a_deleted_test_file(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text("")
    assert ci.present(["tests/test_a.py", "tests/test_gone.py"], root=tmp_path) == ["tests/test_a.py"]


def test_release_reuses_a_green_main_run_and_has_no_probe_job():
    text = (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert "\n  probe:" not in text                                   # F1.5: the hardware probe job is gone
    assert "\n  reuse:" in text and text.count("needs: reuse") == 2    # F1.1: both OS shard sets wait for the lookup
    assert text.count("if: needs.reuse.outputs.run == ''") == 2       # and run only when no green main run exists
    assert "reused green main run" in text                             # the reused run id is logged
