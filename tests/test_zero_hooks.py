import json

import pytest

from fieldkit.zero import channels, hooks, net


@pytest.mark.parametrize("agent", hooks.EVENTS)
def test_configure_preserves_other_hooks_and_is_idempotent(tmp_path, agent):
    path = hooks.path_for(agent, tmp_path, {})
    path.parent.mkdir(parents=True)
    existing = {"type": "command", "command": "another-hook"}
    event = hooks.EVENTS[agent][0]
    if agent == "opencode":
        original = {"hooks": [{"event": "after_tool", **existing}]}
    elif agent == "antigravity":
        original = {"other": {event: [existing]}}
    else:
        original = {"hooks": {event: [{"hooks": [existing]}]}}
    path.write_text(json.dumps(original), encoding="utf-8")
    assert not hooks.configure(agent, home=tmp_path, env={})["configured"]
    assert json.loads(path.read_text(encoding="utf-8")) == original
    assert hooks.configure(agent, apply=True, home=tmp_path, env={})["changed"]
    document = json.loads(path.read_text(encoding="utf-8"))
    if agent == "opencode":
        assert document["hooks"][0] == original["hooks"][0]
    elif agent == "antigravity":
        assert document["other"] == original["other"]
    else:
        assert document["hooks"][event][0]["hooks"] == [existing]
    assert not hooks.configure(agent, apply=True, home=tmp_path, env={})["changed"]


def test_invalid_configuration_is_left_intact(tmp_path):
    path = hooks.path_for("codex", tmp_path, {})
    path.parent.mkdir()
    path.write_text('{"hooks":{"SessionStart":false}}', encoding="utf-8")
    with pytest.raises(ValueError, match="malformed"):
        hooks.configure("codex", apply=True, home=tmp_path, env={})
    assert json.loads(path.read_text(encoding="utf-8")) == {"hooks": {"SessionStart": False}}


def test_hook_rejects_invalid_event_and_only_uses_session_metadata():
    observed = []

    def receive(call, event):
        observed.append((call, event))
        return "new message"

    raw = {"session_id": "12345678-session", "cwd": "/work", "prompt": "user input",
           "tool_input": {"command": "irrelevant"}, "hook_event_name": "PreToolUse"}
    assert hooks.receive("codex", "SessionStart", raw, receive) == {}
    out = hooks.receive("codex", "PreToolUse", raw, receive)["hookSpecificOutput"]
    assert out["hookEventName"] == "PreToolUse" and "new message" in out["additionalContext"]
    assert observed == [({"session_id": "12345678-session", "cwd": "/work", "_agent_kind": "codex",
                         "host_session": "12345678-session"}, "PreToolUse")]
    assert net.station_id(env={"CODEX_THREAD_ID": raw["session_id"]}, host="host") == "codex:12345678@host"


def test_native_hooks_deliver_a_batch_without_acknowledging_hidden_messages(tmp_path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_COORDINATION_DIR", str(tmp_path / "coordination"))
    monkeypatch.setenv("FIELDKIT_LINK_HOME", str(tmp_path / "link"))
    monkeypatch.setattr(net, "machine", lambda: "host")
    monkeypatch.setattr(net, "_ear_of", lambda station: None)
    monkeypatch.setattr(net, "ingest_cable", lambda **kw: 0)
    monkeypatch.setattr(channels, "default", lambda: [channels.AgentHooks(), channels.Pull()])
    zero = "claude:abcdefgh@host"
    codex = "codex:01234567@host"
    gemini = "gemini:abcdef12@host"
    net.open_net(zero, quiet="00:00-00:00", now=1_800_000_000)
    receive = lambda call, event: net._net_line(call, event, now=1_800_000_010)
    cr = {"session_id": "01234567-codex"}
    gr = {"session_id": "abcdef12-gemini"}
    hooks.receive("codex", "SessionStart", cr, receive)
    hooks.receive("gemini", "SessionStart", gr, receive)
    sent = [net.send(gemini, f"message {i}", to=codex, flash=True, now=1_800_000_009)
            for i in range(net.HOOK_MAX_LINES + 1)]
    assert all([d["channel"] for d in r["pushed"]] == ["agent-hook"] for r in sent)
    later = sent[-1]["entry"]["id"]
    first = hooks.receive("codex", "PreToolUse", cr, receive)["hookSpecificOutput"]["additionalContext"]
    assert later not in first and "+1 more" in first
    assert not net._acked({later}, codex)
    second = hooks.receive("codex", "PreToolUse", cr, receive)["hookSpecificOutput"]["additionalContext"]
    assert later in second and net._acked({later}, codex) == {later}
    assert not hooks.receive("codex", "PreToolUse", cr, receive)
    reply = net.send(codex, "Codex reply", to=gemini, flash=True, now=1_800_000_009)["entry"]["id"]
    assert reply in hooks.receive("gemini", "AfterTool", gr, receive)["hookSpecificOutput"]["additionalContext"]


@pytest.mark.parametrize(("agent", "event", "payload", "wanted"), [
    ("codex", "PreToolUse", {"tool_name": "Bash", "tool_input": {"command": "rg needle ."}}, "updatedInput"),
    ("gemini", "BeforeTool", {"tool_name": "run_shell_command", "tool_input": {"command": "rg needle ."}}, "tool_input"),
    ("antigravity", "PreToolUse", {"toolCall": {"name": "run_command", "args": {"CommandLine": "rg needle ."}}},
     "overwrite"),
])
def test_search_dispatch_rewrites_at_the_native_hook(agent, event, payload, wanted):
    response = hooks.receive(agent, event, payload)
    assert wanted in json.dumps(response)
    assert "fieldkit.desk.pfind --content" in json.dumps(response)
