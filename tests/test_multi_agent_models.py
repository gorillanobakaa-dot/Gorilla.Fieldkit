from __future__ import annotations

from pathlib import Path
import pytest

from fieldkit.autopilot import run as ap_run
from fieldkit.lifecycle import models, routing, tasks
from fieldkit.peers import agentfind, link


def test_every_agent_comes_from_choice_yaml_with_providers_and_reasoning():
    """P39: the catalogue is choice.yaml, four levels; no invented or stale name."""
    cat = models.agents_catalog()
    for required in ("claude", "codex", "agy", "antigravity", "gemini", "gorilla-opencode", "kiro"):
        assert required in cat, f"missing agent {required}"
        assert cat[required]["providers"] and cat[required]["thinking_levels"]
    assert "lmstudio" in cat["gorilla-opencode"]["providers"] and "ollama" in cat["codex"]["providers"]
    from fieldkit.lifecycle import choice
    stale = set(choice.load()["stale"])
    assert not stale & set(m for v in cat.values() for m in v["models"])


def test_generic_and_stale_names_refused_and_specific_choice_validated(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_LIFECYCLE_DIR", str(tmp_path))
    r_mod = models.validate_agent_choice("agy", "gemini", "high")
    assert r_mod["ok"] is False and r_mod["verdict"] == "generic-refused"
    assert models.validate_agent_choice("antigravity", "claude-sonnet-4-5", "high")["verdict"] == "stale-model"
    assert models.validate_agent_choice("codex", "gpt-5-codex", "high")["verdict"] == "stale-model"
    assert models.validate_agent_choice("claude", "sonnet", "lowest")["verdict"] == "invalid-thinking"
    assert models.validate_agent_choice("claude", "gpt-5.5", "high")["verdict"] == "invalid-model-for-agent"
    assert models.validate_agent_choice("claude", "sonnet", "xhigh")["ok"] is True
    g = models.validate_agent_choice("codex", "gpt-5.5", "high")       # the spend guard applies to every agent
    assert g["verdict"] in ("model-refused", "allowed")


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
        models.CHOICE_PROMPT
        in res["prompt"]
    )

    # Persist choice via routing.set_choice
    saved = routing.set_choice("solo", "claude-sonnet-5-5-medium", agent="agy", thinking="high")
    assert saved["agent"] == "agy"
    assert saved["ceiling"] == "claude-sonnet-5-5-medium"
    assert saved["thinking"] == "high"

    ch = routing.choice()
    assert ch["agent"] == "agy"
    assert ch["ceiling"] == "claude-sonnet-5-5-medium"
    assert ch["thinking"] == "high"


def test_autopilot_and_link_default_start_have_all_five_agents(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("FIELDKIT_LIFECYCLE_DIR", str(tmp_path))
    for agent_name in (
        "antigravity",
        "agy",
        "gemini",
        "codex",
        "gorilla-opencode",
        "claude",
    ):
        assert agent_name in models.CODING_AGENTS
        assert agent_name in link.DEFAULT_START
        assert agent_name in link.AGENTS
        assert agent_name in agentfind.AGENTS

    # Verify agent_run refuses when choice is omitted
    r_missing = ap_run.agent_run(None, "do work", cwd=tmp_path)
    assert r_missing["ok"] is False
    assert r_missing["needs_choice"] is True
    assert (
        models.CHOICE_PROMPT
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

    # 1. No agent/model/thinking and no person: the owner's recorded choice, else an inbox item (no hidden fallback)
    from fieldkit.lifecycle import choice
    from fieldkit.autopilot import inbox as ib
    asked = []
    monkeypatch.setattr(choice, "menu", lambda **kw: [])
    monkeypatch.setattr(ib, "add", lambda *a, **k: asked.append(a))
    monkeypatch.setattr(choice, "_tty", lambda: False)
    with pytest.raises(ValueError) as exc_missing:
        tasks.start(
            brief,
            title="no-choice",
            plan="O3",
            require_choice=True,
            worktree=lambda r, i: (None, "test"),
            launch=lambda cmd: {"pid": 1234, "log": "test.log"},
        )
    assert "no recorded choice" in str(exc_missing.value) and asked

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
        model="claude-sonnet-5-5-medium",
        thinking="high",
        require_choice=True,
        worktree=lambda r, i: (None, "test"),
        launch=lambda cmd: {"pid": 1234, "log": "test.log"},
    )
    assert r3["agent"] == "agy"
    assert r3["model"] == "claude-sonnet-5-5-medium"
    assert r3["thinking"] == "high"
    runbook = Path(r3["runbook"]).read_text(encoding="utf-8")
    assert "coding_agent: agy" in runbook
    assert "model: claude-sonnet-5-5-medium" in runbook
    assert "thinking: high" in runbook


def test_agent_run_builds_valid_cli_args_for_agy_antigravity_and_gemini(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_LIFECYCLE_DIR", str(tmp_path))
    monkeypatch.setenv("FIELDKIT_AUTOPILOT_STATE", str(tmp_path / "ap"))
    seen = []

    class FakeOut:
        rc = 0
        timed_out = False
        out = "{}"
        err = ""

    from fieldkit.core import contain
    monkeypatch.setattr(contain, "run", lambda args, **kw: seen.append((args, kw)) or FakeOut())
    monkeypatch.setattr(ap_run, "agent_exe", lambda ag: f"/bin/{ag}")

    for ag in ("agy", "antigravity"):
        res = ap_run.agent_run(
            ag, "hello", cwd=tmp_path, model="claude-sonnet-5-5-medium", thinking="high", max_turns=50
        )
        assert res["ok"] is True
        argv, kw = seen[-1]
        assert argv[0] == "/bin/agy"
        assert "--print" in argv and "--dangerously-skip-permissions" in argv
        assert "--effort" in argv and "high" in argv
        assert "chat" not in argv and "--thinking" not in argv
        assert kw["stdin_text"] == "hello"

    res_g = ap_run.agent_run(
        "gemini", "hello", cwd=tmp_path, model="gemini-4-hydrogen-high", thinking="medium", max_turns=50
    )
    # Without a mandate allowing gemini-4-*, spend guard refuses BEFORE launching
    assert res_g["ok"] is False and res_g["refused"] is True
    monkeypatch.setattr(models, "check_model", lambda m, **kw: {"ok": True, "model": m, "tier": "premium", "why": "test"})
    res_g2 = ap_run.agent_run(
        "gemini", "hello", cwd=tmp_path, model="auto", thinking="medium", max_turns=50
    )
    assert res_g2["ok"] is True
    argv_g, kw_g = seen[-1]
    assert argv_g[0] == "/bin/gemini"
    assert "--approval-mode" in argv_g and "yolo" in argv_g
    assert kw_g["stdin_text"] == "hello"
