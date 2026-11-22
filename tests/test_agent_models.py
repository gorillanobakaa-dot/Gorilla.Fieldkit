"""Agent-model guard (fieldkit/tokens/agent_models.py): Explore kept on Haiku, general-purpose on Sonnet;
sabotage cases repaired, never deleted."""
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
    # the general-purpose pin starts correct; tests that sabotage it say so
    (home / "agents" / "general-purpose.md").write_bytes(am.PINS[1]["template"].encode("utf-8"))
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


def test_hook_silent_when_ok_and_reports_without_rewriting_when_deleted(iso):
    """F2.1: SessionStart reports a mismatch and names the repair; it never rewrites ~/.claude."""
    target(iso).write_text(am.TEMPLATE, encoding="utf-8")
    call = {"session_id": "s9", "cwd": str(iso)}
    assert am.hook_line(call, "SessionStart") == ""
    target(iso).unlink()
    line = am.hook_line(call, "SessionStart")
    assert "MISSING" in line and "fieldkit agent-models enforce" in line
    assert not target(iso).exists()                       # reported, NOT repaired by the hook
    assert am.hook_line(call, "PreToolUse") == ""


def test_user_prompt_submit_does_no_file_io(iso, monkeypatch):
    """F2.1: the old hook ran check() (file reads and hashes) at every prompt."""
    touched = []
    for name in ("check", "enforce", "audit", "state_dir", "_read", "_settings"):
        monkeypatch.setattr(am, name, lambda *a, _n=name, **k: touched.append(_n) or {})
    assert am.hook_line({"session_id": "x", "cwd": str(iso)}, "UserPromptSubmit") == ""
    assert touched == []


def test_hook_error_is_one_line_not_silence(iso, monkeypatch):
    monkeypatch.setattr(am, "check", lambda *a, **k: 1 / 0)
    line = am.hook_line({"session_id": "x"}, "SessionStart")
    assert line.startswith("AGENT MODELS GUARD ERROR") and "ZeroDivisionError" in line and "\n" not in line


def settings_like_quota_fix(home):
    """What claude-quota-fix Install.ps1 leaves in ~/.claude/settings.json."""
    (home / "settings.json").write_text(json.dumps({
        "model": "sonnet", "hooks": {"Stop": [{"hooks": []}]},
        "env": {"CLAUDE_CODE_SUBAGENT_MODEL": "haiku", "MAX_THINKING_TOKENS": "8000",
                "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE": "60"}}), encoding="utf-8")


def test_check_passes_on_a_machine_set_up_by_quota_fix_install(iso):
    """F2.2: one source of truth; the kit's own env value is not reported as a hazard."""
    target(iso).write_text(am.TEMPLATE, encoding="utf-8")
    settings_like_quota_fix(iso)
    r = am.check()
    assert r["verdict"] == "OK" and r["reports"] == []
    assert am.SUBAGENT_MODEL == am.WANT_MODEL == "haiku"


def test_another_subagent_model_is_still_reported(iso, monkeypatch):
    target(iso).write_text(am.TEMPLATE, encoding="utf-8")
    monkeypatch.setenv("CLAUDE_CODE_SUBAGENT_MODEL", "opus")
    assert any("CLAUDE_CODE_SUBAGENT_MODEL=opus" in x for x in am.check()["reports"])


def test_doctor_reports_then_install_writes_templates_and_merges_settings(iso, tmp_path):
    (iso / "settings.json").write_text(json.dumps({"hooks": {"Stop": [1]}, "env": {"KEEP": "1",
                                                   "CLAUDE_CODE_SUBAGENT_MODEL": "opus"}}), encoding="utf-8")
    code, lines = am.doctor()
    assert code == 1 and any("MISSING" in x for x in lines) and not target(iso).exists()
    code, lines = am.doctor(install=True)
    assert code == 0, lines
    data = json.loads((iso / "settings.json").read_text(encoding="utf-8"))
    assert data["hooks"] == {"Stop": [1]} and data["env"]["KEEP"] == "1"           # nothing else clobbered
    assert data["env"]["CLAUDE_CODE_SUBAGENT_MODEL"] == "haiku" and data["model"] == "sonnet"
    assert data["env"]["MAX_THINKING_TOKENS"] == "8000"
    assert target(iso).read_bytes() == am.TEMPLATE.encode("utf-8")
    assert "opus" in next((tmp_path / "trash").rglob("settings.json")).read_text(encoding="utf-8")
    assert am.doctor()[0] == 0 and am.check()["reports"] == []


def test_doctor_install_leaves_an_invalid_settings_file_alone(iso):
    (iso / "settings.json").write_text("{not json", encoding="utf-8")
    code, lines = am.doctor(install=True)
    assert code == 3 and any("NOT MERGED" in x for x in lines)
    assert (iso / "settings.json").read_text(encoding="utf-8") == "{not json"


def test_audit_prints_counts_and_a_fail_line_for_explore_not_on_haiku(iso, capsys):
    """F2.3: model counts per agent; any Explore not on Haiku is a FAIL line, whenever it started."""
    _run(iso, "Explore", "claude-haiku-5-5", aid="a1")
    _run(iso, "Explore", "claude-opus-5-5", aid="a2")
    assert am.main(["audit", "--days", "1"]) == 1
    out = capsys.readouterr().out
    assert "claude-haiku-5-5" in out and "FAIL  1 Explore run(s) on claude-opus-5-5, not haiku" in out
    assert "AUDIT OK" not in out


def test_audit_clean_when_every_explore_is_on_haiku(iso, capsys):
    _run(iso, "Explore", "claude-haiku-5-5", aid="a1")
    assert am.main(["audit", "--days", "1"]) == 0
    out = capsys.readouterr().out
    assert "AUDIT OK" in out and "FAIL" not in out


def test_cli_exit_codes(iso, capsys):
    assert am.main(["check"]) == 1
    assert am.main(["enforce"]) == 0
    assert am.main(["check"]) == 0
    assert am.main(["template"]) == 0
    out = capsys.readouterr().out
    assert "model: haiku" in out and "model: sonnet" in out


def gp(home):
    return home / "agents" / "general-purpose.md"


def test_general_purpose_missing_or_on_opus_is_repaired_to_sonnet(iso, tmp_path):
    target(iso).write_text(am.TEMPLATE, encoding="utf-8")
    gp(iso).unlink()
    r = am.check()
    assert r["verdict"] == "MISSING" and r["agent"] == "general-purpose"
    assert am.enforce()[0] == 0 and am.check()["verdict"] == "OK"
    gp(iso).write_text(am.GP_TEMPLATE.replace("model: sonnet", "model: opus"), encoding="utf-8")
    r = am.check()
    assert r["verdict"] == "WRONG_MODEL" and r["model"] == "opus"
    code, lines = am.enforce()
    assert code == 0 and "general-purpose.md was WRONG_MODEL" in lines[0]
    assert "model: sonnet" in gp(iso).read_text(encoding="utf-8")
    assert any("model: opus" in k.read_text(encoding="utf-8") for k in (tmp_path / "trash").rglob("general-purpose.md"))


def test_audit_flags_general_purpose_on_opus(iso):
    _run(iso, "general-purpose", "claude-opus-5-5", aid="g1")
    _run(iso, "general-purpose", "claude-sonnet-5-5", aid="g2")
    r = am.audit(days=1, since=time.time() - 3600)
    assert [(x["agent"], x["model"]) for x in r["ignored"]] == [("general-purpose", "claude-opus-5-5")]
