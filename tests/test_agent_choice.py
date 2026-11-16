"""P39: coding agent -> provider -> model -> reasoning, measured live, a menu for the person, never a hidden fallback."""
from __future__ import annotations

import json
import time

import pytest

from fieldkit.lifecycle import choice, models


def _found(*names):
    return lambda wanted: [{"agent": n, "installed": n in names, "path": f"/x/{n}" if n in names else "",
                            "version": "1.0" if n in names else ""} for n in wanted]


@pytest.fixture(autouse=True)
def _state(tmp_path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_LIFECYCLE_DIR", str(tmp_path))
    monkeypatch.setattr(choice, "codex_cache_models", lambda home=None: {})


def test_shipped_data_has_no_finding():
    assert choice.check() == []


def test_check_finds_a_stale_name_and_a_remote_private_endpoint():
    d = choice.load()
    d["providers"]["openai"]["models"].append("o4-mini")
    d["providers"]["lmstudio"]["endpoint"] = "http://10.0.0.5:1234/v1/models"
    bad = choice.check(d)
    assert any("stale model o4-mini" in b for b in bad)
    assert any("lmstudio is marked private" in b for b in bad)


def test_menu_marks_not_installed_blocked_and_local_server_down():
    now = time.time()
    models.mark_agent_unavailable("claude", "weekly limit", until=now + 3600, now=now)
    rows = choice.menu(finder=_found("claude", "codex"), http=lambda url: None, now=now, run=lambda *a, **k: None)
    by = {r["agent"]: r for r in rows}
    assert by["kiro"]["installed"] is False and "not installed" in by["kiro"]["why"]
    assert by["claude"]["usable"] is False and by["claude"]["until"] == pytest.approx(now + 3600)
    assert "back at" in choice.render(rows)
    lm = [p for p in by["codex"]["providers"] if p["provider"] == "lmstudio"][0]
    assert lm["private"] and not lm["usable"] and "not running" in lm["why"]
    assert all(o["agent"] != "claude" for o in choice.options(rows))


def test_local_llm_is_measured_and_shown_private():
    served = {"http://127.0.0.1:11434/api/tags": {"models": [{"name": "qwen3-coder:30b"}]}}
    rows = choice.menu(finder=_found("codex"), http=served.get, run=lambda *a, **k: None)
    codex = [r for r in rows if r["agent"] == "codex"][0]
    ol = [p for p in codex["providers"] if p["provider"] == "ollama"][0]
    assert ol["usable"] and ol["models"] == ["qwen3-coder:30b"]
    assert "PRIVATE: stays on this computer" in choice.render(rows)
    opt = [o for o in choice.options(rows) if o["provider"] == "ollama"][0]
    assert (opt["agent"], opt["model"], opt["private"]) == ("codex", "qwen3-coder:30b", True)


def test_a_local_endpoint_off_this_computer_is_never_asked():
    asked = []
    assert choice.local_models("lmstudio", {"endpoint": "http://10.0.0.5:1234/v1/models"},
                               http=lambda u: asked.append(u)) == (False, [])
    assert asked == []


def test_codex_models_come_from_its_own_cache(tmp_path, monkeypatch):
    monkeypatch.undo()
    (tmp_path / ".codex").mkdir()
    (tmp_path / ".codex" / "models_cache.json").write_text(json.dumps({"models": [
        {"slug": "gpt-9", "visibility": "list", "supported_reasoning_levels": [{"effort": "low"}, {"effort": "max"}]},
        {"slug": "hidden", "visibility": "hide"}]}), encoding="utf-8")
    assert choice.codex_cache_models(tmp_path) == {"gpt-9": ["low", "max"]}


def test_agy_list_is_read_from_its_command():
    class R:
        stdout = "Fetching available models...\ngemini-4-argon-medium\tGemini 4 Argon\nclaude-opus-5-5-high\tOpus\n"
    assert choice.command_models("/x/agy", ["models"], run=lambda *a, **k: R()) == [
        "gemini-4-argon-medium", "claude-opus-5-5-high"]


def test_quota_error_blocks_the_agent_until_the_reset_and_not_otherwise():
    now = time.time()
    assert choice.note_failure("codex", "all tests passed", now=now) is None
    rec = choice.note_failure("codex", "You've hit your usage limit. Try again later.", now=now)
    assert rec and rec["until"] > now
    assert models.is_agent_available("codex", now=now + 60) is False


def test_a_died_task_with_a_quota_log_blocks_its_agent():
    from fieldkit.lifecycle import tasks
    t = {"agent": "agy", "log": "x.log"}
    assert tasks._agent_quota(t, read=lambda p: "...\nError: quota exceeded for this account\n")
    assert models.is_agent_available("agy") is False
    assert tasks._agent_quota({"agent": "codex", "log": "y.log"}, read=lambda p: "done, PR opened") is None


def _rows():
    return [{"agent": "codex", "installed": True, "usable": True, "providers": [
        {"provider": "openai", "usable": True, "private": False, "model_rows": [
            {"model": "gpt-5.5", "usable": True, "reasoning": ["low", "high"]}]}]}]


def test_headless_uses_the_recorded_choice_else_inbox_never_a_fallback():
    asked = []
    r = choice.pick("code", rows=_rows(), interactive=False, inbox=lambda *a: asked.append(a))
    assert r["ok"] is False and asked and "no hidden fallback" in r["why"]
    choice.remember("code", "codex", "openai", "gpt-5.5", "high")
    r = choice.pick("code", rows=_rows(), interactive=False, inbox=lambda *a: asked.append(a))
    assert r["ok"] and r["agent"] == "codex" and "recorded" in r["how"]
    with pytest.raises(ValueError):
        choice.remember("code", "claude", "openai", "sonnet", "high")       # claude does not drive openai
    with pytest.raises(ValueError):
        choice.remember("code", "agy", "google", "argon", "high")           # stale


def test_a_recorded_choice_that_does_not_work_now_is_not_used():
    choice.remember("code", "claude", "anthropic", "sonnet", "high")
    asked = []
    r = choice.pick("code", rows=_rows(), interactive=False, inbox=lambda *a: asked.append(a))
    assert r["ok"] is False and "does not work now" in r["why"] and asked


def test_a_person_picks_by_number_and_reasoning():
    rows = [{"agent": "agy", "installed": True, "usable": True, "providers": [
        {"provider": "google", "usable": True, "private": False, "model_rows": [
            {"model": "gemini-4-hydrogen-high", "usable": True, "reasoning": ["low", "high"]}]}]}]
    r = choice.pick("code", rows=rows, interactive=True, ask=lambda _: "1 high", out=lambda *_: None)
    assert r == {"ok": True, "agent": "agy", "provider": "google", "model": "gemini-4-hydrogen-high",
                 "reasoning": "high", "how": "picked"}
