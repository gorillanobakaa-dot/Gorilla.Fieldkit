"""fieldkit antigravity: the thin wrapper (binary found, absent -> one NEXT line, argument mapping) and, when the
Rust agy-tools is built (CI builds it first), the real checks on fixtures made here."""
import json
import os
import stat
import sys

import pytest

from fieldkit import antigravity as ag


def _fake_binary(tmp_path, monkeypatch):
    """A stub that prints its argv, so the mapping from the Fieldkit command to the binary is visible."""
    if sys.platform == "win32":
        exe = tmp_path / "fake.cmd"
        exe.write_text("@echo off\r\necho ARGV %*\r\nexit /b 0\r\n")
    else:
        exe = tmp_path / "fake"
        exe.write_text('#!/bin/sh\necho "ARGV $*"\nexit 0\n')
        exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("FIELDKIT_AGY_TOOLS", str(exe))
    return exe


def test_absent_binary_prints_one_next_line_and_exits_1(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FIELDKIT_AGY_TOOLS", str(tmp_path / "nope"))
    monkeypatch.setattr(ag, "ROOT", tmp_path)          # no rust/agy-tools/target here either
    assert ag.main(["doors"]) == 1
    out = capsys.readouterr().out
    assert out.strip().splitlines()[-1] == "NEXT: fieldkit emerge run agy-tools"


def test_commands_map_to_the_binary(tmp_path, monkeypatch, capfd):
    _fake_binary(tmp_path, monkeypatch)
    assert ag.main(["check", "hooks", "h.json"]) == 0
    assert "ARGV run hooks h.json" in capfd.readouterr().out
    assert ag.main(["doors", "a.json", "--check-bins"]) == 0
    assert "ARGV run doors a.json --check-bins" in capfd.readouterr().out
    assert ag.main(["migrate-workflows", "proj", "--apply"]) == 0
    assert "ARGV run workflow-migrate proj --mode apply" in capfd.readouterr().out
    assert ag.main(["migrate-workflows"]) == 0
    assert "run workflow-migrate . --mode preview" in capfd.readouterr().out


def test_bad_usage_is_exit_2(capsys):
    assert ag.main(["check"]) == 2
    assert ag.main(["check", "nonsense", "x"]) == 2
    assert ag.main(["bogus"]) == 2


real = pytest.mark.skipif(ag.binary() is None, reason="agy-tools not built (fieldkit emerge run agy-tools)")


@real
def test_hooks_valid_and_invalid(tmp_path, capfd):
    good = tmp_path / "hooks.json"
    good.write_text(json.dumps({"guard": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "true"}]}]}}))
    assert ag.main(["check", "hooks", str(good)]) == 0
    unknown = tmp_path / "unknown.json"
    unknown.write_text(json.dumps({"guard": {"OnWhatever": []}}))
    assert ag.main(["check", "hooks", str(unknown)]) == 3
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert ag.main(["check", "hooks", str(bad)]) == 3


@real
def test_mcp_check_flags_legacy_sse_and_mixing(tmp_path, capfd):
    f = tmp_path / "mcp_config.json"
    f.write_text(json.dumps({"mcpServers": {"ok": {"url": "https://e.org/mcp"}}}))
    assert ag.main(["check", "mcp", str(f)]) == 0
    f.write_text(json.dumps({"mcpServers": {"old": {"url": "https://e.org/sse"}}}))
    assert ag.main(["check", "mcp", str(f)]) == 3
    f.write_text(json.dumps({"mcpServers": {"both": {"command": "x", "url": "https://e.org/mcp"}}}))
    assert ag.main(["check", "mcp", str(f)]) == 3


@real
def test_doors_lists_all_agents_and_fails_on_findings(tmp_path, capfd):
    claude = tmp_path / ".mcp.json"
    claude.write_text(json.dumps({"mcpServers": {"r": {"type": "http", "url": "https://" + "u" + ":" + "tok" + "@e.org/mcp?k=1"}}}))
    codex = tmp_path / "config.toml"
    codex.write_text('[mcp_servers.t]\ncommand = "definitely-not-a-binary-xyz"\n')
    gemini = tmp_path / "settings.json"
    gemini.write_text(json.dumps({"mcpServers": {"g": {"httpUrl": "https://g.org/mcp"}}}))
    assert ag.main(["doors", str(claude), str(gemini)]) == 0
    out = capfd.readouterr().out
    assert "remote_doors: 2" in out and "tok" not in out and "k=1" not in out
    assert ag.main(["doors", str(claude), str(codex), "--check-bins"]) == 3
    assert "MISSING-BIN" in capfd.readouterr().out
    legacy = tmp_path / "old.json"
    legacy.write_text(json.dumps({"mcpServers": {"s": {"url": "https://e.org/sse"}}}))
    assert ag.main(["doors", str(legacy)]) == 3


@real
def test_migrate_preview_apply_verify_undo_never_deletes(tmp_path, capfd):
    wf = tmp_path / ".agent" / "workflows"
    wf.mkdir(parents=True)
    src = wf / "demo-flow.md"
    src.write_text("---\ndescription: demo flow\n---\n# Demo\nstep\n")
    assert ag.main(["migrate-workflows", str(tmp_path)]) == 0
    assert src.is_file() and not (tmp_path / ".agents").exists(), "preview changes nothing"
    assert ag.main(["migrate-workflows", str(tmp_path), "--apply"]) == 0
    assert (tmp_path / ".agents" / "skills" / "demo-flow" / "SKILL.md").is_file() and not src.exists()
    assert ag.main(["migrate-workflows", str(tmp_path), "--verify"]) == 0
    assert ag.main(["migrate-workflows", str(tmp_path), "--undo"]) == 0
    assert src.is_file()
    kept = list((tmp_path / ".agent" / "workflows.undone").rglob("SKILL.md"))
    assert len(kept) == 1, "undo moved the generated skill aside instead of deleting it"
    assert not (tmp_path / ".agents" / "skills" / "demo-flow" / "SKILL.md").exists()
