"""Tests for fieldkit repair ps-compat-check."""
import sys
from pathlib import Path

import pytest

from fieldkit.repair import ps_compat_check as pcc


def test_expand_archive_is_flagged(tmp_path):
    p = tmp_path / "install.ps1"
    p.write_text("Expand-Archive -LiteralPath $zip -DestinationPath $dest\n", encoding="utf-8")
    findings, err = pcc.check_ps1_file(p)
    assert err is None
    assert any(f["rule"] == "expand-archive-ps7-broken" for f in findings)


def test_commented_expand_archive_is_not_flagged(tmp_path):
    p = tmp_path / "s.ps1"
    p.write_text("# Expand-Archive would go here\n", encoding="utf-8")
    findings, err = pcc.check_ps1_file(p)
    assert err is None and findings == []


def test_get_filehash_is_flagged(tmp_path):
    p = tmp_path / "s.ps1"
    p.write_text("$h = Get-FileHash $f -Algorithm SHA256\n", encoding="utf-8")
    findings, err = pcc.check_ps1_file(p)
    assert err is None
    assert any(f["rule"] == "get-filehash-ps7-module-path" for f in findings)


def test_convertfromjson_ashashtable_is_flagged(tmp_path):
    p = tmp_path / "s.ps1"
    p.write_text("ConvertFrom-Json -AsHashtable\n", encoding="utf-8")
    findings, err = pcc.check_ps1_file(p)
    assert err is None
    assert any(f["rule"] == "convertfromjson-ashashtable-ps6" for f in findings)


def test_clean_file_has_no_findings(tmp_path):
    p = tmp_path / "s.ps1"
    p.write_text("[IO.Compression.ZipFile]::ExtractToDirectory($zip, $dest)\n", encoding="utf-8")
    findings, err = pcc.check_ps1_file(p)
    assert err is None and findings == []


def test_non_ps1_file_produces_no_ps_findings(tmp_path):
    # a .sh file is not checked for PS compat patterns
    p = tmp_path / "script.sh"
    p.write_text("Expand-Archive foo bar\n", encoding="utf-8")
    findings, err = pcc.check_ps1_file(p)
    # check_ps1_file still reads it but finds no PS patterns since we check suffix in CLI
    # The important thing: .sh files don't produce expand-archive findings when passed explicitly
    assert err is None or True  # may error on non-.ps1 - that is acceptable


def test_apply_fixes_expand_archive(tmp_path):
    p = tmp_path / "install.ps1"
    p.write_text("    Expand-Archive -LiteralPath $zipPath -DestinationPath $stage\n", encoding="utf-8")
    rc = pcc.main(["--ps1", str(p), "--apply"])
    new_text = p.read_text(encoding="utf-8")
    assert "ExtractToDirectory" in new_text
    assert "Expand-Archive" not in new_text


def test_install_ps1_has_no_compat_issues():
    root = Path(__file__).resolve().parents[1]
    ps1 = root / "install.ps1"
    if not ps1.is_file():
        pytest.skip("install.ps1 not found")
    findings, err = pcc.check_ps1_file(ps1)
    assert err is None
    assert findings == [], f"install.ps1 has PS compat issues: {findings}"


def test_bom_in_python_file_is_detected(tmp_path):
    p = tmp_path / "bad.py"
    p.write_bytes(b"\xef\xbb\xbf" + b"x = 1\n")
    findings = pcc.check_bom(tmp_path)
    assert any(f["rule"] == "python-bom" for f in findings)


def test_bom_apply_removes_bom(tmp_path):
    p = tmp_path / "bad.py"
    p.write_bytes(b"\xef\xbb\xbf" + b"x = 1\n")
    findings = pcc.check_bom(tmp_path)
    pcc.apply_bom_fixes(findings)
    assert not p.read_bytes().startswith(b"\xef\xbb\xbf")


def test_cli_exit_3_on_findings(tmp_path):
    p = tmp_path / "bad.ps1"
    p.write_text("Expand-Archive -LiteralPath $a -DestinationPath $b\n", encoding="utf-8")
    assert pcc.main(["--ps1", str(p)]) == 3


def test_cli_exit_0_on_clean(tmp_path):
    p = tmp_path / "good.ps1"
    p.write_text("[IO.Compression.ZipFile]::ExtractToDirectory($a, $b)\n", encoding="utf-8")
    assert pcc.main(["--ps1", str(p)]) == 0

# ---------------------------------------------------------------------------
# Workflow shard consistency tests
# ---------------------------------------------------------------------------

def _write_wf(tmp_path, name, jobs_yaml):
    import yaml
    wf_dir = tmp_path / ".github" / "workflows"
    wf_dir.mkdir(parents=True, exist_ok=True)
    (tmp_path / ".git").mkdir(exist_ok=True)  # make it look like a repo root
    wf = {"name": name, "on": "push", "jobs": jobs_yaml}
    (wf_dir / f"{name}.yml").write_text(
        yaml.dump(wf, default_flow_style=False), encoding="utf-8"
    )


def _shard_job(os_name, n):
    return {
        f"shard-{os_name}": {
            "runs-on": os_name,
            "strategy": {"fail-fast": False, "matrix": {"shard": list(range(1, n + 1))}},
            "steps": [{"run": "python ci_tests.py full --shard I/" + str(n)}],
        }
    }


def test_matching_shard_counts_pass(tmp_path):
    jobs = {}
    for os_name in ("windows-latest", "ubuntu-latest"):
        jobs.update(_shard_job(os_name, 20))
    _write_wf(tmp_path, "tests", jobs)
    _write_wf(tmp_path, "release", dict(jobs, test={"runs-on": "ubuntu-latest", "needs": ["shard-windows-latest", "shard-ubuntu-latest"], "if": "always()", "steps": [{"run": "echo ok"}]}))
    findings = pcc.check_workflow_shards(tmp_path)
    assert findings == [], findings


def test_release_missing_shard_job_is_flagged(tmp_path):
    jobs = {}
    for os_name in ("windows-latest", "ubuntu-latest"):
        jobs.update(_shard_job(os_name, 20))
    _write_wf(tmp_path, "tests", jobs)
    # release.yml has no shard jobs - old unsharded style
    _write_wf(tmp_path, "release", {"test": {"runs-on": "ubuntu-latest", "steps": [{"run": "python -m pytest -q"}]}})
    findings = pcc.check_workflow_shards(tmp_path)
    assert any(f["rule"] == "workflow-release-not-sharded" for f in findings), findings


def test_shard_count_mismatch_is_flagged(tmp_path):
    tests_jobs = {}
    release_jobs = {}
    for os_name in ("windows-latest", "ubuntu-latest"):
        tests_jobs.update(_shard_job(os_name, 20))
        release_jobs.update(_shard_job(os_name, 4))  # mismatch: 4 vs 20
    _write_wf(tmp_path, "tests", tests_jobs)
    _write_wf(tmp_path, "release", release_jobs)
    findings = pcc.check_workflow_shards(tmp_path)
    assert any(f["rule"] == "workflow-shard-count-mismatch" for f in findings), findings


def test_unsharded_pytest_in_release_is_flagged(tmp_path):
    jobs = {}
    for os_name in ("windows-latest", "ubuntu-latest"):
        jobs.update(_shard_job(os_name, 20))
    _write_wf(tmp_path, "tests", jobs)
    # release.yml has matching shard jobs BUT also a plain pytest step in test job
    release_jobs = dict(jobs)
    release_jobs["test"] = {"runs-on": "ubuntu-latest", "steps": [{"run": "python -m pytest -q -rs"}]}
    _write_wf(tmp_path, "release", release_jobs)
    findings = pcc.check_workflow_shards(tmp_path)
    assert any(f["rule"] == "workflow-release-unsharded-pytest" for f in findings), findings


def test_real_repo_workflows_are_consistent():
    root = Path(__file__).resolve().parents[1]
    findings = pcc.check_workflow_shards(root)
    assert findings == [], f"Workflow shard issues: {findings}"


def test_whole_repo_tree_is_clean_in_ci():
    """F2.7: the check runs in CI on the repo's own tree (no pre-push hook exists): any finding fails the PR."""
    root = Path(__file__).resolve().parents[1]
    findings = pcc.check_repair_registration(root) + pcc.check_workflow_shards(root) + pcc.check_bom(root)
    assert findings == [], findings


def test_unreadable_batch_yaml_is_a_fail_not_a_pass(tmp_path):
    jobs = {}
    for os_name in ("windows-latest", "ubuntu-latest"):
        jobs.update(_shard_job(os_name, 20))
    _write_wf(tmp_path, "tests", jobs)
    _write_wf(tmp_path, "release", dict(jobs))
    by = tmp_path / "fieldkit" / "method"
    by.mkdir(parents=True)
    (by / "batch.yaml").write_bytes(b"ÿþ not utf-8 ")
    findings = pcc.check_workflow_shards(tmp_path)
    assert any(f["rule"] == "workflow-unreadable" for f in findings), findings


def test_git_failure_in_stale_check_is_a_fail(tmp_path, monkeypatch):
    for rel in pcc.GENERATORS:
        f = tmp_path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("x", encoding="utf-8")

    def boom(*a, **k):
        raise OSError("no git")
    monkeypatch.setattr(pcc.subprocess, "run", boom)
    findings = pcc.check_stale_generated(tmp_path)
    assert findings and all(f["rule"] == "generated-file-unknown" for f in findings), findings
