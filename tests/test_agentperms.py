"""agentperms + cirunner (owner 2026-11-23): allow-everything agent settings are found and tightened; CI workers follow RAM."""
import json

from fieldkit.method import agentperms, cirunner


def _home(tmp_path):
    (tmp_path / ".kiro").mkdir()
    (tmp_path / ".kiro" / "permissions.yaml").write_text(
        "﻿rules:\n  - capability: all\n    effect: allow\npolicies:\n  - auto-approve", encoding="utf-8")
    (tmp_path / ".gemini" / "antigravity-cli").mkdir(parents=True)
    (tmp_path / ".gemini" / "antigravity-cli" / "settings.json").write_text(
        json.dumps({"trustedWorkspaces": [str(tmp_path), str(tmp_path / "proj")], "apiKey": "allow"}), encoding="utf-8")
    (tmp_path / "opencode.json").write_text(json.dumps({"permission": {"bash": "allow", "read": "ask"}}), encoding="utf-8")
    return tmp_path


def test_audit_finds_kiro_antigravity_and_opencode(tmp_path):
    findings, _ = agentperms.collect(_home(tmp_path))
    text = "\n".join(findings)
    assert "every capability" in text and "auto-approve" in text
    assert "whole home" in text and "permission.bash" in text
    assert "apiKey" not in text


def test_fix_tightens_and_keeps_narrow_folders(tmp_path):
    home = _home(tmp_path)
    assert agentperms.main(["audit", "--home", str(home)]) == 3
    assert agentperms.main(["fix", "--home", str(home), "--apply"]) == 0
    assert (home / ".kiro" / "permissions.yaml.fieldkit-bak").exists()
    assert agentperms.main(["audit", "--home", str(home)]) == 0
    ag = json.loads((home / ".gemini" / "antigravity-cli" / "settings.json").read_text(encoding="utf-8"))
    assert ag["trustedWorkspaces"] == [str(home / "proj")]
    assert json.loads((home / "opencode.json").read_text(encoding="utf-8"))["permission"] == {"bash": "ask", "read": "ask"}


def test_runner_workers_follow_ram_and_cpus():
    assert cirunner.workers({"cpus": 4, "ram_mb": 7000}) == 4
    assert cirunner.workers({"cpus": 16, "ram_mb": 7000}) == 4
    assert cirunner.workers({"cpus": 4, "ram_mb": 3000}) == 2
    assert cirunner.workers({"cpus": 4, "ram_mb": 100}) == 1
    assert cirunner.problems({"cpus": 4, "ram_mb": 2000})
    assert not cirunner.problems({"cpus": 4, "ram_mb": 7000})
