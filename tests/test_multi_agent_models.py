from __future__ import annotations

from pathlib import Path
import pytest

from fieldkit.autopilot import run as ap_run
from fieldkit.lifecycle import models, routing, tasks
from fieldkit.peers import agentfind, link


def test_all_five_agents_and_models_in_catalogue():
    cat = models.agents_catalog()
    for required_agent in ("antigravity", "agy", "codex", "gorilla-opencode", "claude"):
        assert required_agent in cat, f"missing agent {required_agent}"
        assert len(cat[required_agent]["models"]) >= 3
        assert cat[required_agent]["thinking_levels"] == [
            "lowest",
            "low",
            "medium",
            "high",
            "max",
        ]

    # Antigravity & agy must include Frontier/Ultra models: hydrogen, argon, carbon, etc.
    for ag in ("antigravity", "agy"):
        mset = set(cat[ag]["models"])
        for frontier in (
            "hydrogen",
            "argon",
            "carbon",
            "gemini-2.5-pro",
            "gemini-2.5-flash",
            "claude-sonnet-4-5",
            "claude-opus-4-6",
        ):
            assert frontier in mset


def test_generic_gemini_rejected_and_specific_choice_validated(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("FIELDKIT_LIFECYCLE_DIR", str(tmp_path))
    # Generic "gemini" as agent or model is rejected with a clear message
    r_ag = models.validate_agent_choice("gemini", "argon", "high")
    assert r_ag["ok"] is False
    assert "generic" in r_ag["why"].lower()

    r_mod = models.validate_agent_choice("antigravity", "gemini", "high")
    assert r_mod["ok"] is False
    assert "generic" in r_mod["why"].lower()

    # Valid specific combinations across agents & thinking levels
    for a, m, t in [
        ("antigravity", "hydrogen", "lowest"),
        ("antigravity", "argon", "medium"),
        ("antigravity", "carbon", "max"),
        ("agy", "argon", "high"),
        ("codex", "gpt-5-codex", "high"),
        ("codex", "o3", "max"),
        ("gorilla-opencode", "qwen3-coder", "medium"),
    ]:
        res = models.validate_agent_choice(a, m, t)
        assert res["ok"] is True, f"expected valid for {a}/{m}/{t}: {res}"


def test_claude_is_available_unless_its_quota_is_out_and_a_quota_block_expires(tmp_path: Path, monkeypatch):
    """Owner 2026-11-16: "not to block the fucking Claude ... A choice for the user." No agent is pinned off by default;
    a quota block lasts until its reset (else the cooldown), then the agent is offered again."""
    import time
    monkeypatch.setenv("FIELDKIT_LIFECYCLE_DIR", str(tmp_path))
    assert models.is_agent_available("claude") is True
    assert models.validate_agent_choice("claude", "sonnet", "medium")["ok"] is True

    now = time.time()
    models.mark_agent_unavailable("claude", reason="weekly quota 100%", now=now)          # no --until: cooldown
    assert models.is_agent_available("claude", now=now + 60) is False
    v = models.validate_agent_choice("claude", "sonnet", "medium")
    assert v["ok"] is False and "claude" in v["why"].lower()
    assert models.is_agent_available("claude", now=now + 3 * 3600) is True                 # it ended by itself

    assert models.is_agent_available("codex") is True
    models.mark_agent_unavailable("codex", reason="owner said so", until="always")         # only an explicit owner pin
    assert models.is_agent_available("codex") is False
    models.clear_agent_unavailable("codex")
    assert models.is_agent_available("codex") is True


def test_the_shipped_models_yaml_pins_no_agent_off():
    import yaml
    d = yaml.safe_load((Path(models.__file__).parent / "models.yaml").read_text(encoding="utf-8"))
    assert not ((d.get("agent_availability") or {}).get("unavailable") or {})


def test_mandatory_choice_prompt_when_unspecified_and_persisted_choice(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("FIELDKIT_LIFECYCLE_DIR", str(tmp_path))
    # When no agent/model/thinking is passed and no choice is persisted -> fails with exact prompt
    res = models.validate_agent_choice(None, None, None, require_explicit=True)
    assert res["ok"] is False
    assert (
        "WHICH CODING AGENT, WHICH MODEL WITH WHICH REASONING LEVEL WOULD YOU LIKE TO START?"
        in res["prompt"]
    )

    # Persist choice via routing.set_choice
    saved = routing.set_choice("solo", "argon", agent="agy", thinking="high")
    assert saved["agent"] == "agy"
    assert saved["ceiling"] == "argon"
    assert saved["thinking"] == "high"

    ch = routing.choice()
    assert ch["agent"] == "agy"
    assert ch["ceiling"] == "argon"
    assert ch["thinking"] == "high"


def test_autopilot_and_link_default_start_have_all_five_agents(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("FIELDKIT_LIFECYCLE_DIR", str(tmp_path))
    for agent_name in (
        "antigravity",
        "agy",
        "codex",
        "gorilla-opencode",
        "claude",
    ):
        assert agent_name in models.CODING_AGENTS
        assert agent_name in link.DEFAULT_START
        assert agent_name in agentfind.AGENTS

    # Verify agent_run refuses when choice is omitted
    r_missing = ap_run.agent_run(None, "do work", cwd=tmp_path)
    assert r_missing["ok"] is False
    assert r_missing["needs_choice"] is True
    assert (
        "WHICH CODING AGENT, WHICH MODEL WITH WHICH REASONING LEVEL WOULD YOU LIKE TO START?"
        in r_missing["prompt"]
    )

    # Verify agent_run refuses an agent whose quota is out
    models.mark_agent_unavailable("claude", reason="weekly quota 100%")
    r_claude = ap_run.agent_run(
        "claude", "do work", cwd=tmp_path, model="sonnet", thinking="medium"
    )
    assert r_claude["ok"] is False
    assert r_claude["refused"] is True


def test_tasks_start_blocks_unspecified_or_claude_and_starts_chosen_agent(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("FIELDKIT_LIFECYCLE_DIR", str(tmp_path))
    monkeypatch.setenv("FIELDKIT_ROOT", str(tmp_path))
    brief = tmp_path / "brief.md"
    brief.write_text("# Brief\nTest task.\n", encoding="utf-8")

    # 1. Calling tasks.start with no agent/model/thinking raises ValueError with the mandatory question
    with pytest.raises(ValueError) as exc_missing:
        tasks.start(
            brief,
            title="no-choice",
            plan="O3",
            require_choice=True,
            worktree=lambda r, i: (None, "test"),
            launch=lambda cmd: {"pid": 1234, "log": "test.log"},
        )
    assert (
        "WHICH CODING AGENT, WHICH MODEL WITH WHICH REASONING LEVEL WOULD YOU LIKE TO START?"
        in str(exc_missing.value)
    )

    # 2. Calling tasks.start with an agent whose quota is out raises ValueError
    models.mark_agent_unavailable("claude", reason="weekly quota 100%")
    with pytest.raises(ValueError) as exc_claude:
        tasks.start(
            brief,
            title="try-claude",
            plan="O3",
            agent="claude",
            model="sonnet",
            thinking="medium",
            require_choice=True,
            worktree=lambda r, i: (None, "test"),
            launch=lambda cmd: {"pid": 1234, "log": "test.log"},
        )
    assert "claude" in str(exc_claude.value).lower()

    # 3. Calling tasks.start with valid agent/model/thinking succeeds and writes agent-specific runbook
    r3 = tasks.start(
        brief,
        title="valid-agy",
        plan="O3",
        agent="agy",
        model="argon",
        thinking="high",
        require_choice=True,
        worktree=lambda r, i: (None, "test"),
        launch=lambda cmd: {"pid": 1234, "log": "test.log"},
    )
    assert r3["agent"] == "agy"
    assert r3["model"] == "argon"
    assert r3["thinking"] == "high"
    runbook = Path(r3["runbook"]).read_text(encoding="utf-8")
    assert "coding_agent: agy" in runbook
    assert "model: argon" in runbook
    assert "thinking: high" in runbook
