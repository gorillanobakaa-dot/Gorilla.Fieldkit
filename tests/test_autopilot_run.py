# Version: 1.0.0 · updated 26-10-18-18-30
"""Autopilot plumbing: the Claude hook speaks the documented deny format and fails closed, a run has no GitHub or
git credential, the inbox collects owner decisions, publish never runs inside autopilot, quiet spares its own tree."""
import io
import json

import pytest

from fieldkit.autopilot import claude_hook, hardlines, inbox, quiet, run


@pytest.fixture(autouse=True)
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(hardlines, "STATE", tmp_path)
    monkeypatch.setattr(hardlines, "STOP_FILE", tmp_path / "STOP")
    monkeypatch.setattr(run, "ON", tmp_path / "on.json")
    monkeypatch.setattr(inbox, "INBOX", tmp_path / "inbox")
    return tmp_path


def hook(payload):
    out = io.StringIO()
    claude_hook.main(io.StringIO(json.dumps(payload) if isinstance(payload, dict) else payload), out)
    return out.getvalue()


def test_the_claude_hook_denies_in_the_documented_shape_and_logs(state):
    out = json.loads(hook({"tool_name": "Bash", "tool_input": {"command": "gh release create v1"}}))
    h = out["hookSpecificOutput"]
    assert h["hookEventName"] == "PreToolUse" and h["permissionDecision"] == "deny" and "HARD LINE 1" in h["permissionDecisionReason"]
    assert hook({"tool_name": "Bash", "tool_input": {"command": "python -m pytest -q"}}) == ""
    assert "DENY" in (state / "hook.log").read_text(encoding="utf-8")


def test_a_broken_call_is_denied_not_waved_through():
    out = json.loads(hook("this is not json"))
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_an_autopilot_run_cannot_reach_a_credential(state):
    env = run.isolated_env({"GH_TOKEN": "ghp_secret", "PATH": "x"})
    assert env["GH_TOKEN"] == "" and env["GIT_CONFIG_KEY_0"] == "credential.helper" and env["GIT_CONFIG_VALUE_0"] == ""
    assert env["GIT_TERMINAL_PROMPT"] == "0" and env["FIELDKIT_AUTOPILOT"] == "1"
    assert not any((state / "no-credentials").iterdir())          # gh sees no hosts.yml


def test_the_settings_block_on_hook_failure_and_carry_deny_rules(state):
    s = json.loads(run.settings().read_text(encoding="utf-8"))
    h = s["hooks"]["PreToolUse"][0]["hooks"][0]
    assert s["permissions"]["defaultMode"] == "bypassPermissions" and h["onFailure"] == "block"
    assert "Bash(git push:*)" in s["permissions"]["deny"]
    ws = json.loads(run.workspace(state / "ws").read_text(encoding="utf-8"))
    assert ws["env"]["GH_TOKEN"] == "" and ws["env"]["FIELDKIT_AUTOPILOT"] == "1"


def test_switch_stop_and_resume(state):
    assert run.switch(True)["autopilot"] and not run.switch(False)["autopilot"]
    assert run.stop("no more tests")["stopped"] and "no more tests" in run.status()["stop_reason"]
    assert not run.resume()["stopped"]


def test_the_inbox_collects_and_closes_items():
    a = inbox.add("approve 17 dispositions", "the leak gate needs them", "fieldkit build-harness leakgate-approve x")
    b = inbox.add("publish 158 build 2", "release ready", kind="publish")
    assert [i["n"] for i in inbox.items()] == [a["n"], b["n"]]
    inbox.done(a["n"], "approved")
    assert [i["n"] for i in inbox.items()] == [b["n"]] and "INBOX: 1 item" in inbox.lines(inbox.items())[0]


def test_publish_never_runs_inside_autopilot(monkeypatch, tmp_path):
    monkeypatch.setenv("FIELDKIT_AUTOPILOT", "1")
    said = []
    assert inbox.publish(tmp_path, say=said.append, run=lambda *a, **k: pytest.fail("ran something")) == 3
    assert "REFUSED" in said[0]


def test_quiet_spares_its_own_tree_and_matches_names():
    class P:
        def __init__(s, pid, name, parent=None):
            s.pid, s.info = pid, {"pid": pid, "name": name, "exe": f"C:/{name}"}
            s._parent = parent
        def parents(s):
            out, p = [], s._parent
            while p:
                out.append(p); p = p._parent
            return out
        def children(s, recursive=True):
            return [x for x in PROCS if x._parent is s]
    app = P(1, "Claude.exe"); helper = P(2, "msedgewebview2.exe", app); me = P(3, "python.exe", app)
    tg = P(5, "Telegram.exe"); other = P(40, "msedgewebview2.exe", tg)   # WebView2 judged by its HOST (2026-10-18)
    PROCS = [app, helper, me, other, tg]

    class FakePsutil:
        Error = Exception
        NoSuchProcess = Exception
        @staticmethod
        def Process(pid=None):
            return next(p for p in PROCS if p.pid == pid)
        @staticmethod
        def net_connections(kind):
            return []
        @staticmethod
        def process_iter(attrs):
            return PROCS
    import os as _os
    real = _os.getpid
    try:
        _os.getpid = lambda: 3
        rows = quiet.noisy(FakePsutil, wanted=["telegram"])
    finally:
        _os.getpid = real
    assert sorted(r["pid"] for r in rows) == [5, 40]         # the agent's own WebView2 (2) is spared, Telegram's is not
