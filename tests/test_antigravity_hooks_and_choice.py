"""Tests for portable Antigravity global hooks (`guard.py` + `hooks.json`), the 150k context `/compact` / new-session
warning, and P39 multi-agent session-launch confirmation (Claude + Antigravity + agy + Gemini + Codex + OpenCode)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from fieldkit import rules
from fieldkit.antigravity import hooks as agy_hooks
from fieldkit.autopilot import toolrouter as tr
from fieldkit.lifecycle import choice, models, routing, session_choice

ROOT = Path(rules.__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_MODE_DIR", str(tmp_path / "mode"))
    monkeypatch.setenv("FIELDKIT_LIFECYCLE_DIR", str(tmp_path / "lc"))
    monkeypatch.setenv("FIELDKIT_ROTATE_DIR", str(tmp_path / "rot"))
    monkeypatch.delenv("FIELDKIT_AUTOPILOT", raising=False)
    monkeypatch.delenv("FIELDKIT_JOB_DIR", raising=False)
    monkeypatch.delenv("FIELDKIT_AUDIT_ONLY", raising=False)


def test_stranger_pc_rules_install_creates_antigravity_hooks_and_passes_selftest(tmp_path, monkeypatch):
    monkeypatch.setattr(rules, "_which", lambda name: None)
    home = tmp_path / "stranger_home"
    (home / ".gemini").mkdir(parents=True)
    # Pre-existing custom hook in hooks.json is preserved and backed up
    cfg_dir = agy_hooks.config_dir(home)
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "hooks.json").write_text(
        json.dumps({"my-linter": {"PostToolUse": [{"matcher": "run_command", "hooks": [{"command": "echo ok"}]}]}}),
        encoding="utf-8",
    )

    before = rules.check(ROOT, home=home)
    hook_rows = [r for r in before if r["kind"] == "hooks"]
    assert hook_rows and not hook_rows[0]["ok"]

    bk = tmp_path / "backups"
    done = rules.install_all(home=home, root=ROOT, backup_dir=bk)
    hj = agy_hooks.hooks_json_path(home)
    gp = agy_hooks.guard_py_path(home)
    assert hj.is_file() and gp.is_file()
    assert any(x["path"] == str(hj) and "hooks:" in x["done"] for x in done)
    assert any((bk / "antigravity-hooks").glob("hooks.json.*"))

    parsed = json.loads(hj.read_text(encoding="utf-8"))
    assert "my-linter" in parsed  # user hook preserved
    assert "PreToolUse" in parsed["owner-guard"] and "PreInvocation" in parsed["owner-guard"]

    after = rules.check(ROOT, home=home)
    assert all(r["ok"] for r in after), after

    # Run the installed guard.py --selftest in a subprocess
    env = dict(os.environ, FIELDKIT_ROOT=str(ROOT))
    proc = subprocess.run([sys.executable, str(gp), "--selftest"], capture_output=True, text=True, env=env, timeout=30)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "all guard cases pass" in proc.stdout


def test_agy_binary_on_path_counts_as_installed_for_new_machine(tmp_path, monkeypatch):
    monkeypatch.setattr(rules, "_which", lambda name: "/usr/local/bin/agy" if name == "agy" else None)
    home = tmp_path / "fresh_pc"
    home.mkdir()
    assert rules.installed("gemini", home)
    rules.install_all(home=home, root=ROOT, backup_dir=tmp_path / "bk")
    assert agy_hooks.guard_py_path(home).is_file()
    assert all(r["ok"] for r in rules.check(ROOT, home=home))


def test_150k_context_warning_surfaces_compact_and_new_session_and_resets_after_compact(tmp_path):
    home = tmp_path / "h"
    conv = "11112222-3333-4444-5555-666677778888"
    tpath = home.joinpath(".gemini", "antigravity", "brain", conv, ".system_generated", "logs", "transcript.jsonl")
    tpath.parent.mkdir(parents=True)

    # Below 150k -> no warning
    tpath.write_text(
        json.dumps({"type": "PLANNER_RESPONSE", "input_tokens": 20_000, "cache_read_tokens": 120_000}) + "\n",
        encoding="utf-8",
    )
    payload = {"conversationId": conv, "transcriptPath": str(tpath), "toolCall": {"name": "view_file", "args": {}}}
    assert agy_hooks.context_warning(payload, home=home) is None
    assert agy_hooks.pre_invocation(payload, home=home) == {}

    # Crosses 150k -> PreInvocation banner + PreToolUse force_ask both mention /compact and new session
    tpath.write_text(
        json.dumps({"type": "PLANNER_RESPONSE", "input_tokens": 15_000, "cache_read_tokens": 145_000}) + "\n",
        encoding="utf-8",
    )
    pre = agy_hooks.pre_invocation(payload, home=home)
    step = pre["injectSteps"][0]["systemMessage"]
    assert "/compact" in step["metadata"]["user_markdown"] and "new session" in step["metadata"]["user_markdown"]
    assert "/compact" in step["systemMessage"] and "rotate handover" in step["systemMessage"]

    sfile = tmp_path / "ctx-state.json"
    gate1 = agy_hooks.midturn_context_gate(payload, state_file=sfile, home=home)
    assert gate1 and gate1[0] == "force_ask" and "/compact" in gate1[1] and "new session" in gate1[1]
    # Immediate next tool call in the same turn is not spammed until 10 more calls
    assert agy_hooks.midturn_context_gate(payload, state_file=sfile, home=home) is None

    # Handover command itself is never blocked even on first crossing
    handover_payload = {
        "conversationId": "handover-conv",
        "transcriptPath": str(tpath),
        "toolCall": {"name": "run_command", "args": {"CommandLine": "fieldkit rotate handover --session handover-conv"}},
    }
    assert agy_hooks.midturn_context_gate(handover_payload, state_file=sfile, home=home) is None

    # User runs /compact -> context drops back below 150k -> state resets cleanly
    tpath.write_text(
        json.dumps({"type": "PLANNER_RESPONSE", "input_tokens": 18_000, "cache_read_tokens": 10_000}) + "\n",
        encoding="utf-8",
    )
    assert agy_hooks.midturn_context_gate(payload, state_file=sfile, home=home) is None
    # Crossing 150k a second time warns again immediately
    tpath.write_text(
        json.dumps({"type": "PLANNER_RESPONSE", "input_tokens": 155_000, "cache_read_tokens": 0}) + "\n",
        encoding="utf-8",
    )
    gate_again = agy_hooks.midturn_context_gate(payload, state_file=sfile, home=home)
    assert gate_again and gate_again[0] == "force_ask"


def test_multi_agent_menu_offers_claude_antigravity_agy_gemini_and_codex_while_gating_flagships(monkeypatch):
    monkeypatch.setattr(
        choice,
        "codex_cache_models",
        lambda home=None: {"gpt-5.4-mini": ["medium"], "gpt-5.6-luna": ["high"], "gpt-5.6-terra": ["high"]},
    )

    def fake_finder(wanted):
        return [{"agent": n, "installed": True, "path": f"/bin/{n}", "version": "1.0"} for n in wanted]

    rows = choice.menu(finder=fake_finder, http=lambda url: None, run=lambda *a, **k: None, refresh=True)
    opts = choice.options(rows)
    by_agent = {}
    for o in opts:
        by_agent.setdefault(o["agent"], set()).add(o["model"])

    # Anthropic Claude models stay present and selectable alongside Gemini, agy, Antigravity, Codex, OpenCode
    assert {"haiku", "sonnet"} <= by_agent["claude"]
    assert {"gemini-4-carbon-medium", "gemini-4-argon-medium", "gemini-3.8-flash-medium", "claude-sonnet-5-5-medium"} <= by_agent["agy"]
    assert {"gemini-4-carbon-medium", "gemini-3.8-flash-medium"} <= by_agent["antigravity"]
    assert {"auto", "flash", "pro"} <= by_agent["gemini"]
    assert {"gpt-5.4-mini", "gpt-5.6-luna"} <= by_agent["codex"]
    assert "sonnet" in by_agent["gorilla-opencode"] and "gemini-4-carbon-medium" in by_agent["gorilla-opencode"]

    # Reviewer and flagship models remain gated by the spend guard
    all_usable_models = {o["model"] for o in opts}
    for gated in ("opus", "fable", "claude-opus-5-5-high", "gemini-4-hydrogen-high", "gpt-5.6-terra"):
        assert gated not in all_usable_models


def test_session_launch_hook_forces_asking_owner_and_enforces_chosen_agent():
    sid = "sess-multi-1"
    claude_ask = "fieldkit peers link ask --agent claude --model sonnet --thinking medium --brief b.md"
    agy_ask = "fieldkit peers link ask --agent agy --model gemini-4-carbon-medium --thinking medium --brief b.md"

    # 1. Unconfirmed launch is denied in toolrouter (no silent default to Claude!)
    out = tr.verdict({"session_id": sid, "tool_name": "Bash", "tool_input": {"command": claude_ask}})
    assert out and out["hookSpecificOutput"]["permissionDecision"] == "deny"
    reason = out["hookSpecificOutput"]["permissionDecisionReason"]
    for name in ("claude", "antigravity", "agy", "gemini", "codex", "gorilla-opencode"):
        assert name in reason

    # 2. Deterministic no-AI `task start --run` is NOT blocked
    assert session_choice.command_refusal("fieldkit task start --on peer --run -- pytest -q", sid) is None

    # 3. Owner chooses `agy` + `gemini-4-carbon-medium` -> `agy` allowed, sneaking `claude` denied
    assert routing.main([
        "choose", "--session-agent", "agy", "--session-provider", "google",
        "--session-model", "gemini-4-carbon-medium", "--session-thinking", "medium", "--session", sid,
    ]) == 0
    assert tr.verdict({"session_id": sid, "tool_name": "Bash", "tool_input": {"command": agy_ask}}) is None
    sneak = tr.verdict({"session_id": sid, "tool_name": "Bash", "tool_input": {"command": claude_ask}})
    assert sneak and "MISMATCH" in sneak["hookSpecificOutput"]["permissionDecisionReason"]

    # 4. Owner can equally choose `claude` + `sonnet` when desired
    assert routing.main([
        "choose", "--session-agent", "claude", "--session-provider", "anthropic",
        "--session-model", "sonnet", "--session-thinking", "medium", "--session", sid,
    ]) == 0
    assert tr.verdict({"session_id": sid, "tool_name": "Bash", "tool_input": {"command": claude_ask}}) is None
