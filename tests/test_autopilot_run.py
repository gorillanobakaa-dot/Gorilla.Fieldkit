# Version: 1.0.0 · updated 26-10-28-15-24
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


def test_the_prompt_goes_on_stdin_so_a_dash_prompt_is_not_an_option(state, monkeypatch):
    # 2026-10-22: a link brief opens with "--- text from the other computer ---"; passed in argv, claude read it as an
    # unknown option and the started session died in 2 s
    # (2026-10-23: the long-horizon runner, merged in, uses popen and sent short prompts in argv; this still holds)
    seen = {}

    class P:
        returncode, pid = 0, 1

        def __init__(self, args, **kw):
            seen["args"] = args

        def communicate(self, input=None, timeout=None):
            seen["input"] = input
            return "{}", ""
    monkeypatch.setattr(run, "claude_cmd", lambda: ["claude"])
    brief = "--- text from the other computer ---\nsay hello\n--- end ---"
    assert run.claude(brief, popen=P)["ok"]
    assert seen["input"] == brief and brief not in seen["args"]
    assert seen["args"][:2] == ["claude", "-p"]


def test_the_same_open_question_is_one_inbox_item_with_a_count():
    a = inbox.add("vaio does not answer on the cable", "first", "fieldkit peers link heal vaio")
    b = inbox.add("vaio does not answer on the cable", "second")
    assert b["n"] == a["n"] and b["repeats"] == 2 and b["why"] == "second" and b["command"].endswith("heal vaio")
    assert len(inbox.items()) == 1 and "(x2, last " in inbox.lines(inbox.items())[1]
    inbox.done(a["n"])
    assert inbox.add("vaio does not answer on the cable", "again")["n"] != a["n"]


def test_claude_found_where_the_installers_put_it_when_not_on_path(monkeypatch, tmp_path):
    monkeypatch.setattr(run.shutil, "which", lambda name: None)
    monkeypatch.setattr(run.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "none"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "none"))
    assert run.claude_exe() is None or not run.claude_exe().startswith(str(tmp_path))
    exe = tmp_path / ".local" / "bin" / ("claude.exe" if run.sys.platform == "win32" else "claude")
    exe.parent.mkdir(parents=True)
    exe.write_text("", encoding="utf-8")
    exe.chmod(0o755)
    assert run.claude_exe() == str(exe)


def test_the_repeat_count_keeps_counting_and_a_broken_item_does_not_hide_the_match():
    # fieldkit mutate (2026-10-28): both kill mutants the test above let through
    a = inbox.add("vaio does not answer on the cable", "first")
    inbox.add("vaio does not answer on the cable", "second")
    (inbox.INBOX / "0000-broken.json").write_text("{not json", encoding="utf-8")
    c = inbox.add("vaio does not answer on the cable", "third")
    assert c["n"] == a["n"] and c["repeats"] == 3 and len(inbox.items()) == 1


@pytest.fixture
def no_claude(monkeypatch, tmp_path):
    """Claude Code not on PATH and nothing outside tmp_path executable, so the machine's own install does not count."""
    monkeypatch.setattr(run.shutil, "which", lambda name: None)
    monkeypatch.setattr(run.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "none"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "none"))
    real = run.os.access
    monkeypatch.setattr(run.os, "access", lambda p, m: str(p).startswith(str(tmp_path)) and real(p, m))
    return "claude.exe" if run.sys.platform == "win32" else "claude"


def put(path, mode=0o755):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")
    path.chmod(mode)
    return path


def test_claude_off_path_skips_a_folder_and_a_file_it_cannot_run(no_claude, tmp_path):
    (tmp_path / ".local" / "bin" / no_claude).mkdir(parents=True)
    good = put(tmp_path / ".claude" / "local" / no_claude)
    assert run.claude_exe() == str(good)                            # a folder named claude is not the program
    if run.sys.platform != "win32":                                 # Windows has no execute bit
        (tmp_path / ".local" / "bin" / no_claude).rmdir()
        put(tmp_path / ".local" / "bin" / no_claude, 0o644)
        assert run.claude_exe() == str(good)


@pytest.mark.skipif(run.sys.platform == "win32", reason="nvm installs are looked up on Linux and macOS only")
def test_claude_under_nvm_is_the_newest_node_version(no_claude, tmp_path):
    put(tmp_path / ".nvm" / "versions" / "node" / "v18.0.0" / "bin" / "claude")
    new = put(tmp_path / ".nvm" / "versions" / "node" / "v20.1.0" / "bin" / "claude")
    assert run.claude_exe() == str(new)


def test_claude_on_path_comes_before_the_install_folders(no_claude, tmp_path, monkeypatch):
    put(tmp_path / ".local" / "bin" / no_claude)
    monkeypatch.setattr(run.shutil, "which", lambda name: "/on/path/claude")
    assert run.claude_exe() == "/on/path/claude"
