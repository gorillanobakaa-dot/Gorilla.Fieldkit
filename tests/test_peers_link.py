"""fieldkit peers link: agents on two computers joined by one cable. The two computers are two real processes here
(a `serve` subprocess with its own home, this test as the other side), on loopback only through the test flag."""
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from fieldkit.peers import link


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture
def two(tmp_path, monkeypatch):
    """Computer A (serves) and computer B (asks), paired by a pairing file carried 'by hand'."""
    a, b = tmp_path / "A", tmp_path / "B"
    port = free_port()
    monkeypatch.setenv("FIELDKIT_LINK_HOME", str(a))
    pf, fp_a = link.pair_init("127.0.0.1", "127.0.0.1", port=port, out=tmp_path / "pair.json", allow_loopback=True)
    monkeypatch.setenv("FIELDKIT_LINK_HOME", str(b))
    fp_b = link.pair_import(pf, allow_loopback=True)
    return {"a": a, "b": b, "port": port, "fp": (fp_a, fp_b), "pf": pf}


def serve_a(two, seconds=25, extra_env=None):
    env = dict(os.environ, FIELDKIT_LINK_HOME=str(two["a"]), **(extra_env or {}))
    p = subprocess.Popen([sys.executable, "-m", "fieldkit", "peers", "link", "serve", "--seconds", str(seconds),
                          "--loopback-test"], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for _ in range(100):
        if (two["a"] / "link.log").is_file() and "serving on" in (two["a"] / "link.log").read_text():
            return p
        time.sleep(0.1)
    p.kill()
    raise AssertionError("serve did not start: " + (p.stdout.read() if p.stdout else ""))


def test_agents_reports_installed_running_and_versions():
    class R:
        returncode, stdout, stderr = 0, "2.1.302 (Claude Code)\n", ""
    rows = link.agents(which=lambda e: f"/usr/bin/{e}" if e in ("claude", "agy") else None,
                       procs=[(11, "claude.exe"), (12, "codex"), (13, "bash")], run=lambda *a, **k: R(),
                       local_sessions=[{"name": "opencode-1"}])
    by = {r["agent"]: r for r in rows}
    assert by["claude"]["installed"] and by["claude"]["running"] == [11] and "2.1.302" in by["claude"]["version"]
    assert by["codex"]["running"] == [12] and not by["codex"]["installed"]
    assert by["antigravity"]["path"] == "/usr/bin/agy"
    assert not by["opencode"]["installed"] and by["sessions"]["reachable"] == ["opencode-1"]
    text = "\n".join(link.agent_lines(rows))
    assert "running 11" in text and "not installed" in text and "opencode-1" in text


@pytest.mark.parametrize("ip,ok", [("10.77.0.1", True), ("169.254.87.61", True), ("192.168.1.26", True),
                                   ("0.0.0.0", False), ("8.8.8.8", False), ("127.0.0.1", False), ("::", False)])
def test_only_a_private_cable_address_is_accepted(ip, ok):
    if ok:
        link.check_address(ip)
    else:
        with pytest.raises(ValueError):
            link.check_address(ip)
    link.check_address("127.0.0.1", allow_loopback=True)      # tests only


def test_pairing_shows_the_same_fingerprint_on_both_sides_and_refuses_a_tampered_file(two, monkeypatch):
    assert two["fp"][0] == two["fp"][1]
    d = json.loads(two["pf"].read_text())
    d["key"] = "00" * 32
    bad = two["pf"].with_name("bad.json")
    bad.write_text(json.dumps(d))
    with pytest.raises(ValueError, match="fingerprint"):
        link.pair_import(bad, allow_loopback=True)
    assert (two["b"] / "link.key").name.endswith(".key")     # the hard lines keep models off *.key


def test_the_verifier_refuses_unsigned_altered_stale_and_replayed_requests():
    k = b"k" * 32
    t = [time.time()]
    v = link.Verifier(k, now=lambda: t[0])
    good = link.sign({"verb": "hello"}, k)
    assert v.check(good) is None
    assert v.check(good) == "replayed"
    assert v.check({"verb": "hello"}) == "unsigned"
    alt = link.sign({"verb": "message", "text": "hi"}, k)
    alt["text"] = "rm -rf"
    assert v.check(alt) == "bad signature"
    assert link.Verifier(b"x" * 32).check(link.sign({"verb": "hello"}, k)) == "bad signature"
    t[0] += link.SKEW + 5
    assert "stale" in v.check(link.sign({"verb": "hello"}, k))


def test_hello_and_send_end_to_end_between_two_processes(two):
    p = serve_a(two)
    try:
        r = link.call({"verb": "hello"})
        assert r["ok"] and r["host"] == socket.gethostname() and any(x["agent"] == "claude" for x in r["agents"])
        r = link.call({"verb": "message", "text": "B here: coordinate on step A?\x1b[31m\u202e"})
        assert r["ok"] and r["stored"] == 1
    finally:
        p.kill()
        p.wait()
    item = json.loads(next((two["a"] / "inbox").glob("0001-*.json")).read_text(encoding="utf-8"))
    assert item["text"] == "B here: coordinate on step A?[31m"        # escape and bidi override removed
    os.environ["FIELDKIT_LINK_HOME"] = str(two["a"])
    try:
        out = "\n".join(link.fenced(link.inbox()))
    finally:
        os.environ["FIELDKIT_LINK_HOME"] = str(two["b"])
    assert "not the owner's instruction" in out and "coordinate on step A" in out


def test_a_wrong_key_and_a_stranger_address_are_refused(two, monkeypatch):
    p = serve_a(two)
    try:
        (two["b"] / "link.key").write_text(("ab" * 32))
        r = link.call({"verb": "hello"})
        assert not r["ok"] and r["error"] == "bad signature"
    finally:
        p.kill()
        p.wait()
    assert "REFUSED" in (two["a"] / "link.log").read_text()
    cfg = json.loads((two["a"] / "link.json").read_text())
    cfg["peer"] = "127.0.0.2"                                       # A now answers only 127.0.0.2
    (two["a"] / "link.json").write_text(json.dumps(cfg))
    p = serve_a(two)
    try:
        r = link.call({"verb": "hello"}, timeout=5)
        assert not r["ok"]
    finally:
        p.kill()
        p.wait()
    assert "REFUSED a connection from 127.0.0.1" in (two["a"] / "link.log").read_text()


def test_ask_starts_nothing_without_this_computers_own_approval(tmp_path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_LINK_HOME", str(tmp_path))
    added, started = [], []
    pop = lambda argv, **kw: started.append(argv) or type("P", (), {"pid": 4242})()
    ok, why = link.start_session("claude", "talk to me", "vaio", popen=pop,
                                 inbox_add=lambda *a, **k: added.append(a))
    assert not ok and not started and added and "no approved link.yaml" in why
    (tmp_path / "link.yaml").write_text("allow_start: [claude]\ncwd: " + str(tmp_path).replace("\\", "/") +
                                        "\nwall_minutes: 30\nstart_commands: {}\n")
    assert link.approve(isatty=False, say=lambda m: None) == 3                 # no terminal, no approval
    monkeypatch.setenv("FIELDKIT_AUTOPILOT", "1")
    assert link.approve(confirm=True, say=lambda m: None) == 3                 # never from autopilot
    monkeypatch.delenv("FIELDKIT_AUTOPILOT")
    assert link.approve(confirm=True, say=lambda m: None) == 0
    ok, why = link.start_session("claude", "talk to me \x1b]0;x", "vaio", popen=pop, inbox_add=lambda *a, **k: None)
    assert ok and "PID 4242" in why
    argv = started[0]
    assert argv[1:5] == ["-m", "fieldkit", "autopilot", "claude"] and "--job" in argv
    brief = argv[5]
    assert "not the owner's instruction" in brief and "\x1b" not in brief and "fieldkit peers link send" in brief
    ok, why = link.start_session("codex", "x", "vaio", popen=pop, inbox_add=lambda *a, **k: added.append(a))
    assert not ok                                                              # not in allow_start
    (tmp_path / "link.yaml").write_text((tmp_path / "link.yaml").read_text() + "# changed\n")
    ok, why = link.start_session("claude", "x", "vaio", popen=pop, inbox_add=lambda *a, **k: added.append(a))
    assert not ok and "changed after its approval" in why


def test_an_allowed_agent_without_a_known_start_command_is_never_guessed(tmp_path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_LINK_HOME", str(tmp_path))
    (tmp_path / "link.yaml").write_text("allow_start: [codex]\ncwd: /tmp\nstart_commands: {}\n")
    link.approve(confirm=True, say=lambda m: None)
    ok, why = link.start_session("codex", "x", "vaio", popen=lambda *a, **k: 1 / 0, inbox_add=lambda *a, **k: None)
    assert not ok and "no start command for codex" in why


def test_the_link_key_is_behind_the_hard_lines():
    from fieldkit.autopilot import hardlines
    assert hardlines.decide("Read", {"file_path": "D:/data/.fieldkit/link/link.key"})[0] == "deny"
    assert hardlines.decide("Bash", {"command": "cat ~/.fieldkit/link/link.key"})[0] == "deny"
