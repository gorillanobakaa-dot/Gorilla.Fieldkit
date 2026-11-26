# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for fieldkit handoff. No network, no real agents, no real home folder."""
import json
import subprocess

import pytest

try:
    from fieldkit import handoff
except ImportError:  # standalone use: handoff.py next to this file
    import handoff

UID = "11111111-1111-4111-8111-111111111111"
FLAGS = ["--provider", "claude", "--model", "Sonnet 5.5", "--thinking", "medium", "--economy", "--json"]


def fake_key():
    return "sk-" + "a" * 24


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    proj = home / ".claude" / "projects" / "-home-me-fieldkit"
    proj.mkdir(parents=True)
    (proj / f"{UID}.jsonl").write_text(
        json.dumps({"type": "user", "cwd": str(tmp_path), "message": {"role": "user", "content":
                    "fieldkit has a wifi tool that can check the wifi connections. what can it do?"}}) + "\n" +
        json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": f"Scans SSIDs. key {fake_key()} in {home}/x.py for Alice Example"},
            {"type": "tool_use", "name": "Read", "input": {"file_path": "wifi.py"}}]}}) + "\n",
        encoding="utf-8")
    local = tmp_path / "fieldkit.local.json"
    local.write_text(json.dumps({"privacy": {"terms": ["Alice Example"]}}), encoding="utf-8")
    monkeypatch.setattr(handoff, "HOME", home)
    monkeypatch.setattr(handoff, "CARD_DIR", home / ".agent-handoff" / "cards")
    monkeypatch.setattr(handoff, "CONFIG_PATH", home / ".agent-handoff" / "config.json")
    monkeypatch.setattr(handoff, "find_local_json", lambda: local)
    monkeypatch.setattr(handoff, "_TERMS_RE", handoff._UNLOADED)
    monkeypatch.setattr(handoff, "fieldkit_scan", lambda p: ("clean", ""))
    monkeypatch.setattr(handoff, "lmstudio_models", lambda: ["local-model"])

    def no_launch(*a, **k):
        raise AssertionError("an agent was launched")
    monkeypatch.setattr(subprocess, "run", no_launch)
    return home


def test_find_by_uuid(env):
    assert handoff.find_sessions(UID, None)[0][1].stem == UID


def test_find_by_title_with_typo_and_project(env):
    q = "fielkit has a wifi tool that can check the wifi connections. what exactly can it do?"
    assert handoff.find_sessions(q, "fieldkit")[0][1].stem == UID


def test_parses_nested_claude_records(env):
    parsed = handoff.parse_file(next((env / ".claude").rglob("*.jsonl")))
    assert parsed["goal"].startswith("fieldkit has a wifi tool")
    assert any(t.startswith("Read(") for t in parsed["tools"])
    assert parsed["cwd"]


def test_redaction_keys_home_and_private_terms(env):
    out = handoff.redact(f"{fake_key()} {env}/a.py Alice Example")
    assert "sk-" not in out and str(env) not in out and "Alice" not in out
    assert "~/a.py" in out and "[PRIVATE]" in out


def _card(prov, model, light, eff="medium", eco=True):
    return handoff.HandoffCard("c", "", "a", "p", "s", "t", "pr", "", "", [], [], {}, prov, model, eff, eco,
                               "/x.md", light_model=light)


def test_claude_command_enforces_effort_and_economy(env):
    reg = handoff.build_registry({})
    cmd, e = handoff.build_command(_card("claude", "Sonnet 5.5", "Haiku 5.5", "high"), reg)
    assert cmd[0] == "claude" and cmd[2:4] == ["--model", "claude-sonnet-5-5"]
    assert cmd[-3:] == ["--disallowedTools", "Task", "Agent"]          # zero subagents
    assert e["MAX_THINKING_TOKENS"] == "31999"
    assert e["CLAUDE_CODE_SUBAGENT_MODEL"] == "claude-haiku-5-5"      # helpers on the light model
    assert "ZERO swarms" in cmd[1]
    cmd, e = handoff.build_command(_card("claude", "Opus 5.5", "", eco=False), reg)
    assert "--disallowedTools" not in cmd and "CLAUDE_CODE_SUBAGENT_MODEL" not in e


def test_codex_effort_flag(env):
    cmd, _ = handoff.build_command(_card("codex", "GPT-6.2 Sol", "GPT-6 Luna", "low"), handoff.build_registry({}))
    assert 'model_reasoning_effort="low"' in cmd


def test_economy_is_default_without_asking(env, capsys):
    assert handoff.main([UID, "--provider", "claude", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    t = out["target"]
    assert (t["economy"], t["model"], t["light_model"], t["thinking"]) == (True, "Sonnet 5.5", "Haiku 5.5", "medium")
    assert any("subagent" in x for x in out["policy"]["enforced"])


def test_other_providers_are_honest_about_advisory(env, capsys):
    handoff.main([UID, "--provider", "opencode", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert any("subagents" in x for x in out["policy"]["advisory"])


def test_standard_mode_needs_a_model_when_not_interactive(env):
    assert handoff.main([UID, "--provider", "claude", "--no-economy", "--json"]) == 1
    assert handoff.main([UID, "--provider", "claude", "--no-economy", "--model", "Opus 5.5", "--json"]) == 0


def test_economy_forbids_orchestrator_model(env):
    assert handoff.main([UID, "--provider", "antigravity", "--model", "Orchestrator (Gemini 4 Neon High)", "--json"]) == 1


def test_config_can_fix_a_guessed_economy_model(env, tmp_path, capsys):
    handoff.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    handoff.CONFIG_PATH.write_text(json.dumps({"economy_models": {"codex": {"main": "GPT-6 Sol"}}}))
    handoff.main([UID, "--provider", "codex", "--json"])
    assert json.loads(capsys.readouterr().out)["target"]["model"] == "GPT-6 Sol"


def test_validate_rejects_bad_thinking(env):
    hook = handoff.make_validate_hook(handoff.build_registry({}))
    c = handoff.HandoffCard("c", "", "a", "p", "s", "t", "pr", "", "", [], [], {}, "claude", "Sonnet 5.5", "extreme", True)
    with pytest.raises(SystemExit):
        hook(c)


def test_no_approve_is_preview_and_never_launches(env, capsys):
    assert handoff.main([UID, *FLAGS]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "PREVIEW" and out["approved"] is False


def test_card_file_is_clean_of_home_and_secrets(env, capsys):
    handoff.main([UID, *FLAGS])
    card = json.loads(capsys.readouterr().out)["card"]
    text = open(card, encoding="utf-8").read()
    assert str(env) not in text and "sk-abc" not in text and "Alice" not in text


def test_privacy_findings_block_with_exit_3(env, monkeypatch, capsys):
    monkeypatch.setattr(handoff, "fieldkit_scan", lambda p: ("findings", "1 email"))
    assert handoff.main([UID, *FLAGS, "--approve"]) == 3
    assert json.loads(capsys.readouterr().out)["status"] == "BLOCKED_PRIVACY"


def test_not_scanned_blocks_launch(env, monkeypatch, capsys):
    monkeypatch.setattr(handoff, "fieldkit_scan", lambda p: ("not-scanned", "no fieldkit"))
    assert handoff.main([UID, *FLAGS]) == 3
    capsys.readouterr()
    assert handoff.main([UID, *FLAGS, "--approve"]) == 3


def test_local_model_skips_gate(env, monkeypatch, capsys):
    def boom(p):
        raise AssertionError("scan should not run for a local model")
    monkeypatch.setattr(handoff, "fieldkit_scan", boom)
    code = handoff.main([UID, "--provider", "lmstudio", "--model", "auto-detect-local-server",
                         "--thinking", "medium", "--economy", "--dry-run", "--json"])
    assert code == 0 and json.loads(capsys.readouterr().out)["status"] == "PREVIEW"


def test_missing_provider_without_tty_fails_not_hangs(env):
    assert handoff.main([UID, "--json"]) == 1


def test_not_found_exit_1(env, capsys):
    assert handoff.main(["zzzz-nothing-here-qqqq", "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "NOT_FOUND"
