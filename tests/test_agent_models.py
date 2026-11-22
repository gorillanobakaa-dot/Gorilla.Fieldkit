"""Agent-model guard (fieldkit/tokens/agent_models.py): Explore kept on Haiku; sabotage cases repaired, never deleted."""
import json
import os
import time

import pytest

from fieldkit.tokens import agent_models as am


@pytest.fixture(autouse=True)
def iso(tmp_path, monkeypatch):
    home = tmp_path / "claude"
    (home / "agents").mkdir(parents=True)
    monkeypatch.setenv("FIELDKIT_CLAUDE_HOME", str(home))
    monkeypatch.setenv("FIELDKIT_AGENT_MODELS_TRASH", str(tmp_path / "trash"))
    monkeypatch.setenv("FIELDKIT_AGENT_MODELS_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("CLAUDE_CODE_SUBAGENT_MODEL", raising=False)
    monkeypatch.chdir(tmp_path)
    return home


def target(home):
    return home / "agents" / am.FILENAME


def test_missing_is_repaired(iso):
    assert am.check()["verdict"] == "MISSING"
    code, lines = am.enforce()
    assert code == 0 and "REPAIRED" in lines[0]
    assert target(iso).read_bytes() == am.TEMPLATE.encode("utf-8")
    assert am.check()["verdict"] == "OK"


def test_model_switched_to_opus_is_repaired_and_old_file_kept(iso, tmp_path):
    target(iso).write_text(am.TEMPLATE.replace("model: haiku", "model: opus"), encoding="utf-8")
    assert am.check()["verdict"] == "WRONG_MODEL"
    code, _ = am.enforce()
    assert code == 0 and am.check()["verdict"] == "OK"
    kept = list((tmp_path / "trash").rglob(am.FILENAME))
    assert kept and "model: opus" in kept[0].read_text(encoding="utf-8")


def test_model_line_removed_means_inherit(iso):
    target(iso).write_text(am.TEMPLATE.replace("model: haiku\n", ""), encoding="utf-8")
    r = am.check()
    assert r["verdict"] == "WRONG_MODEL" and r["model"] == "(inherit)"


def test_body_altered_is_repaired(iso):
    target(iso).write_text(am.TEMPLATE + "Also review and edit files.\n", encoding="utf-8")
    assert am.check()["verdict"] == "ALTERED"
    assert am.enforce()[0] == 0 and am.check()["verdict"] == "OK"


def test_crlf_and_bom_are_not_a_change(iso):
    target(iso).write_bytes(am.BOM + am.TEMPLATE.replace("\n", "\r\n").encode("utf-8"))
    assert am.check()["verdict"] == "OK"


def test_second_explore_definition_moved_aside_not_deleted(iso, tmp_path):
    target(iso).write_text(am.TEMPLATE, encoding="utf-8")
    other = iso / "agents" / "explore-v2.md"
    other.write_text("---\nname: Explore\nmodel: opus\n---\nx\n", encoding="utf-8")
    assert am.check()["verdict"] == "CONFLICT"
    assert am.enforce()[0] == 0
    assert not other.exists() and list((tmp_path / "trash").rglob("explore-v2.md"))
    assert am.check()["verdict"] == "OK"


def test_project_override_and_env_are_reported_not_edited(iso, tmp_path, monkeypatch):
    target(iso).write_text(am.TEMPLATE, encoding="utf-8")
    proj = tmp_path / "proj" / ".claude" / "agents"
    proj.mkdir(parents=True)
    pf = proj / "explore.md"
    pf.write_text("---\nname: Explore\nmodel: opus\n---\n", encoding="utf-8")
    monkeypatch.setenv("CLAUDE_CODE_SUBAGENT_MODEL", "opus")
    r = am.check(cwd=tmp_path / "proj")
    assert r["verdict"] == "OK" and len(r["reports"]) == 2
    am.enforce(cwd=tmp_path / "proj")
    assert "model: opus" in pf.read_text(encoding="utf-8")


def _run(home, agent, model, sid="s1", aid="a1"):
    d = home / "projects" / "p" / sid / "subagents"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"agent-{aid}.meta.json").write_text(json.dumps({"agentType": agent}), encoding="utf-8")
    (d / f"agent-{aid}.jsonl").write_text(
        json.dumps({"type": "user", "message": {"content": "x"}}) + "\n"
        + json.dumps({"type": "assistant", "message": {"model": model}}) + "\n", encoding="utf-8")


def test_audit_flags_explore_off_haiku_after_template(iso):
    _run(iso, "Explore", "claude-haiku-5-5", aid="a1")
    _run(iso, "Explore", "claude-opus-5-5", aid="a2")
    _run(iso, "general-purpose", "claude-sonnet-5-5", aid="a3")
    r = am.audit(days=1, since=time.time() - 3600)
    assert {(x["agent"], x["model"]) for x in r["rows"]} == {
        ("Explore", "claude-haiku-5-5"), ("Explore", "claude-opus-5-5"), ("general-purpose", "claude-sonnet-5-5")}
    assert [x["model"] for x in r["ignored"]] == ["claude-opus-5-5"]
    assert am.audit(days=1, since=time.time() + 3600)["ignored"] == []


def test_hook_silent_when_ok_and_repairs_when_deleted(iso):
    target(iso).write_text(am.TEMPLATE, encoding="utf-8")
    call = {"session_id": "s9", "cwd": str(iso)}
    assert am.hook_line(call, "UserPromptSubmit") == ""
    assert am.hook_line(call, "UserPromptSubmit") == ""
    target(iso).unlink()
    line = am.hook_line(call, "UserPromptSubmit")
    assert "REPAIRED" in line and "MISSING" in line
    assert target(iso).is_file()
    assert am.hook_line(call, "PreToolUse") == ""


def test_hook_never_raises(iso, monkeypatch):
    monkeypatch.setattr(am, "check", lambda *a, **k: 1 / 0)
    assert am.hook_line({"session_id": "x"}, "UserPromptSubmit") == ""


def test_cli_exit_codes(iso, capsys):
    assert am.main(["check"]) == 1
    assert am.main(["enforce"]) == 0
    assert am.main(["check"]) == 0
    assert am.main(["template"]) == 0 and "model: haiku" in capsys.readouterr().out
