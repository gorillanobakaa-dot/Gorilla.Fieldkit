import json
import os
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from fieldkit.method import installproof


@pytest.fixture
def run_inputs(tmp_path):
    def git(*args):
        return subprocess.check_output(["git", "-C", str(tmp_path), *args], text=True).strip()

    git("init", "-q")
    dot = tmp_path / ".github"
    dot.mkdir()
    (dot / "fieldkit-release.yaml").write_text(
        "schema: 1\ndistribution: installers\ntargets:\n  mac:\n"
        "    platform: macos\n    arch: x86_64\n    minimum_os: '10.13'\n"
        "    installer: 'browser-{version}\\.pkg'\n    workflow: .github/workflows/candidate.yml\n",
        encoding="utf-8",
    )
    git("add", ".github")
    git("-c", "user.name=Test", "-c", "user.email=t@t", "commit", "-qm", "Policy")
    package = tmp_path / "browser-1.0.pkg"
    package.write_bytes(b"fixture only")
    return {
        "repo": tmp_path, "target": "mac", "version": "1.0", "installer": package, "assets": (),
        "output": tmp_path / "proof.json", "command": [sys.executable, "-c", "import sys; sys.exit(1)"],
        "env": {**os.environ, "GITHUB_ACTIONS": "true", "GITHUB_EVENT_NAME": "workflow_dispatch",
                "GITHUB_SHA": git("rev-parse", "HEAD"), "GITHUB_REPOSITORY": "owner/repo",
                "GITHUB_WORKFLOW_REF": "owner/repo/.github/workflows/candidate.yml@refs/tags/v1.0"},
        "observe": lambda: ("macos", None, "x86_64", "10.13.6"),
    }


def test_proof_records_only_checked_candidate(run_inputs):
    with pytest.raises(subprocess.CalledProcessError):
        installproof.record(**run_inputs)
    assert not run_inputs["output"].exists()
    run_inputs["command"] = [sys.executable, "-c", "import os; assert os.environ['FIELDKIT_VERSION'] == '1.0'"]
    proof = installproof.record(**run_inputs)
    assert proof == json.loads(run_inputs["output"].read_text(encoding="utf-8"))
    assert proof["installed"] is True
    assert proof["commit"] == run_inputs["env"]["GITHUB_SHA"]
    package = run_inputs["installer"]
    assert proof["assets"] == {package.name: installproof.candidate.file_digest(package)}


def test_refuses_wrong_runner_and_replaced_bytes(run_inputs):
    with pytest.raises(installproof.candidate.Refused, match="runner reports"):
        installproof.record(**{**run_inputs, "observe": lambda: ("macos", None, "x86_64", "14.0")})

    def replace(command, *, cwd, env, check, timeout):
        run_inputs["installer"].write_bytes(b"changed after checking")

    with pytest.raises(installproof.candidate.Refused, match="bytes changed"):
        installproof.record(**run_inputs, run=replace)
    assert not run_inputs["output"].exists()


def test_zip_proof_runs_only_the_declared_installer_and_hashes_the_outer_asset(run_inputs, monkeypatch):
    archive = run_inputs["repo"] / "browser-1.0.zip"
    member = "packages/Install Browser.pkg"
    policy = installproof.candidate.policy_at(run_inputs["repo"], "HEAD")
    policy["targets"]["mac"].update(installer=r"browser-{version}\.zip", installer_member=member)
    monkeypatch.setattr(installproof.candidate, "policy_at", lambda *_: policy)
    run_inputs["installer"] = archive
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr(member, b"declared installer fixture")
        bundle.writestr("../unrelated.pkg", b"do not extract")

    def check(command, *, cwd, env, check, timeout):
        extracted = Path(env["FIELDKIT_INSTALLER"])
        assert extracted.read_bytes() == b"declared installer fixture"
        assert [child.name for child in extracted.parent.iterdir()] == ["installer.pkg"]

    proof = installproof.record(**run_inputs, run=check)
    assert proof["installer_member"] == member
    assert proof["assets"] == {archive.name: installproof.candidate.file_digest(archive)}


def test_zip_refuses_a_member_that_is_a_link(tmp_path):
    archive = tmp_path / "candidate.zip"
    link = zipfile.ZipInfo("installer.pkg")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr(link, "outside.pkg")
    with pytest.raises(installproof.candidate.Refused, match="regular installer"):
        with installproof.runnable_installer(archive, link.filename):
            pytest.fail("symlink reached the installer command")
