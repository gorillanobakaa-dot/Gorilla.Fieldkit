"""fieldkit peers link, the Windows items of 2026-10-22: approve in one command, hello carries toolset and time,
ask refuses when the start precheck fails, the brief names the interpreter by its full path."""
import sys

import yaml

from fieldkit.peers import link

OK_ROWS = lambda agent: [("python -P import", True, "link root"), ("claude exe", True, "found")]


def test_approve_writes_shows_and_pins_in_one_command(tmp_path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_LINK_HOME", str(tmp_path / "h"))
    said = []
    work = tmp_path / "work"
    work.mkdir()
    assert link.approve(confirm=True, say=said.append, allow=["claude"], cwd=str(work), wall_minutes=45) == 0
    d = yaml.safe_load((tmp_path / "h" / "link.yaml").read_text(encoding="utf-8"))
    assert d["allow_start"] == ["claude"] and d["cwd"] == str(work.resolve()) and d["wall_minutes"] == 45
    assert d["start_commands"] == {} and d["accept_to_desktop"] is False          # untouched keys kept
    assert any("allow_start" in m for m in said) and said[-1] == "APPROVED"      # shown before the yes
    reg, why = link.approvals()
    assert why == "approved" and reg["allow_start"] == ["claude"]
    # a later run keeps what it is not told to change
    assert link.approve(confirm=True, say=said.append, wall_minutes=10) == 0
    d = yaml.safe_load((tmp_path / "h" / "link.yaml").read_text(encoding="utf-8"))
    assert d["allow_start"] == ["claude"] and d["wall_minutes"] == 10
    assert link.approve(confirm=True, say=said.append, allow=["claude,codex"]) == 0
    assert link.approvals()[0]["allow_start"] == ["claude", "codex"]


def test_approve_with_flags_refuses_bad_input_and_no_terminal(tmp_path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_LINK_HOME", str(tmp_path))
    said = []
    assert link.approve(confirm=True, say=said.append, allow=["evil"], cwd=str(tmp_path)) == 2
    assert "unknown agent evil" in said[-1] and not (tmp_path / "link.yaml").exists()
    assert link.approve(confirm=True, say=said.append, allow=["claude"], cwd=str(tmp_path / "nope")) == 2
    assert "not a folder" in said[-1]
    assert link.approve(isatty=False, say=said.append, allow=["claude"], cwd=str(tmp_path)) == 3
    assert not (tmp_path / "link.yaml").exists()                                 # nothing written without a person
    assert link.approve(confirm=False, say=said.append, allow=["claude"], cwd=str(tmp_path)) == 1
    assert link.approvals()[1].startswith("no approved")                         # a no pins nothing
    monkeypatch.setenv("FIELDKIT_AUTOPILOT", "1")
    assert link.approve(confirm=True, say=said.append, allow=["claude"], cwd=str(tmp_path)) == 3


def test_hello_carries_toolset_and_time(monkeypatch):
    v = type("V", (), {"check": lambda self, r: None})()
    r = link.handle({"verb": "hello"}, v, agents_fn=lambda: [], toolset_fn=lambda: {"python": "3.12.10"})
    assert r["ok"] and r["toolset"] == {"python": "3.12.10"} and abs(r["time"] - link.time.time()) < 5
    import fieldkit.peers as pkg
    monkeypatch.setitem(sys.modules, "fieldkit.peers.link_ops", None)            # not there: None, never a crash
    monkeypatch.delattr(pkg, "link_ops", raising=False)
    assert link.toolset() is None


def test_ask_refuses_when_the_precheck_fails_and_says_why(tmp_path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_LINK_HOME", str(tmp_path))
    link.approve(confirm=True, say=lambda m: None, allow=["claude"], cwd=str(tmp_path))
    started = []
    pop = lambda argv, **kw: started.append(argv) or type("P", (), {"pid": 7})()
    bad = lambda agent: [("python -P import", False, "loads C:/main, not the link"), ("claude exe", True, "found")]
    ok, why = link.start_session("claude", "x", "vaio", popen=pop, check_fn=lambda a: link.startcheck(a, bad),
                                 flags_fn=set, sleep=lambda s: None)
    assert not ok and not started and why == "not started: precheck failed: python -P import: loads C:/main, not the link"
    ok, why = link.start_session("claude", "x", "vaio", popen=pop, check_fn=lambda a: link.startcheck(a, OK_ROWS),
                                 flags_fn=set, sleep=lambda s: None)
    assert ok and started
    # the precheck is asked about the agent the peer asked for, not always claude
    (tmp_path / "link.yaml").write_text("allow_start: [codex]\ncwd: " + tmp_path.as_posix() +
                                        "\nstart_commands: {codex: [codex, run]}\n", encoding="utf-8")
    link.approve(confirm=True, say=lambda m: None)
    asked = []
    link.start_session("codex", "x", "vaio", popen=pop, check_fn=lambda a: asked.append(a) or (False, "no"))
    assert asked == ["codex"]


def test_startcheck_fails_closed():
    assert link.startcheck("claude", OK_ROWS) == (True, "2 checks ok")
    assert link.startcheck("claude", lambda a: []) == (False, "startcheck returned no checks")
    ok, why = link.startcheck("claude", lambda a: 1 / 0)
    assert not ok and "ZeroDivisionError" in why
    seen = []
    link.startcheck("codex", lambda a: seen.append(a) or OK_ROWS(a))
    assert seen == ["codex"]


def test_the_brief_names_the_interpreter_by_full_path(tmp_path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_LINK_HOME", str(tmp_path))
    link.approve(confirm=True, say=lambda m: None, allow=["claude"], cwd=str(tmp_path))
    started = []
    pop = lambda argv, **kw: started.append(argv) or type("P", (), {"pid": 7})()
    link.start_session("claude", "x", "vaio", popen=pop, check_fn=lambda a: (True, ""), flags_fn=set,
                       sleep=lambda s: None)
    brief = started[0][6]
    assert f"{sys.executable} -P -m fieldkit peers link send" in brief
    assert f"{sys.executable} -P -m fieldkit peers link inbox" in brief
    assert started[0][0] == sys.executable
