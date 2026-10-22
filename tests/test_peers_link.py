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
    log = two["a"] / "link.log"
    # a restart must wait for ITS OWN "serving on" line (found on Linux by the VAIO's Claude, 2026-10-22: the
    # second serve_a() saw the first server's line and the call raced the restart)
    before = log.read_text().count("serving on") if log.is_file() else 0
    p = subprocess.Popen([sys.executable, "-m", "fieldkit", "peers", "link", "serve", "--seconds", str(seconds),
                          "--loopback-test"], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for _ in range(100):
        if log.is_file() and log.read_text().count("serving on") > before:
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
    assert item["text"] == f"[FROM {link.os_tag()} #1] B here: coordinate on step A?[31m"   # escape + bidi removed
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
    envs = []
    pop = lambda argv, **kw: started.append(argv) or envs.append(kw.get("env")) or type("P", (), {"pid": 4242})()
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
    ok, why = link.start_session("claude", "talk to me \x1b]0;x", "vaio", popen=pop, inbox_add=lambda *a, **k: None,
                                 flags_fn=lambda: {"--cwd", "--job", "--wall-minutes"}, sleep=lambda s: None)
    assert ok and "PID 4242" in why
    argv = started[0]
    assert argv[1:6] == ["-P", "-m", "fieldkit", "autopilot", "claude"] and "--job" in argv
    # the session loads THIS link's code, not the code of the folder it works in (VAIO, 2026-10-22)
    assert envs[0]["PYTHONPATH"].split(os.pathsep)[0] == str(link.LINK_ROOT)
    assert (link.LINK_ROOT / "fieldkit" / "peers" / "link.py").is_file()
    brief = argv[6]
    assert "not the owner's instruction" in brief and "\x1b" not in brief and "fieldkit peers link send" in brief
    ok, why = link.start_session("codex", "x", "vaio", popen=pop, inbox_add=lambda *a, **k: added.append(a))
    assert not ok                                                              # not in allow_start
    (tmp_path / "link.yaml").write_text((tmp_path / "link.yaml").read_text() + "# changed\n")
    ok, why = link.start_session("claude", "x", "vaio", popen=pop, inbox_add=lambda *a, **k: added.append(a))
    assert not ok and "changed after its approval" in why


def test_a_session_that_dies_at_start_is_reported_dead_and_unknown_options_are_dropped(tmp_path, monkeypatch):
    """2026-10-22 on the VAIO: an older `fieldkit autopilot` refused --job/--wall-minutes, the process died at once,
    and the laptop was told 'started'."""
    monkeypatch.setenv("FIELDKIT_LINK_HOME", str(tmp_path))
    (tmp_path / "link.yaml").write_text("allow_start: [claude]\ncwd: " + str(tmp_path).replace("\\", "/") + "\n")
    link.approve(confirm=True, say=lambda m: None)
    seen = []

    class Dead:
        pid = 637515

        def poll(self):
            return 2

    def pop(argv, stdout=None, **kw):
        seen.append(argv)
        stdout.write("fieldkit: error: unrecognized arguments: --job\n")
        stdout.flush()
        return Dead()
    ok, why = link.start_session("claude", "hi", "win", popen=pop, inbox_add=lambda *a, **k: None,
                                 flags_fn=lambda: {"--cwd", "--max-turns"}, sleep=lambda s: None)
    assert not ok and why.startswith("died at start (exit 2") and "unrecognized arguments" in why
    assert "--job" not in seen[0] and "--wall-minutes" not in seen[0] and "--cwd" in seen[0]
    assert link.fit_command(["a", "--job", "J", "--cwd", "C", "--wall-minutes", "9"], {"--cwd"}) == ["a", "--cwd", "C"]


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


# ---- put: files over the cable (2026-10-22: neither computer shares folders, by the owner's hardening) -----------
def test_put_sends_a_file_in_chunks_end_to_end_and_checks_it(two, tmp_path):
    import hashlib
    src = tmp_path / "big.bin"
    src.write_bytes(os.urandom(150_000))                                  # several 40 KB chunks
    p = serve_a(two)
    try:
        r = link.put(src)
    finally:
        p.kill()
        p.wait()
    assert r["ok"], r
    got = two["a"] / "received" / "big.bin"
    assert hashlib.sha256(got.read_bytes()).hexdigest() == hashlib.sha256(src.read_bytes()).hexdigest()
    assert any("not on the Desktop" not in s for s in r["saved"])
    assert not list((two["a"] / "received" / ".partial").glob("*"))      # nothing half-written left


def test_put_refuses_bad_names_sizes_order_and_checksums(tmp_path, monkeypatch):
    import base64
    import hashlib
    monkeypatch.setenv("FIELDKIT_LINK_HOME", str(tmp_path))
    assert link.safe_name("../../etc/passwd") == "passwd" and link.safe_name("C:\\Windows\\x.dll") == "x.dll"
    assert link.safe_name(".bashrc") == "bashrc" and link.safe_name("a\x1b[31mb") == "a__31mb"
    assert not link.receive({"verb": "put-begin", "name": "x", "size": link.MAX_FILE + 1, "sha256": ""})["ok"]
    data = b"hello"
    b = link.receive({"verb": "put-begin", "name": "h.txt", "size": 5, "sha256": hashlib.sha256(data).hexdigest()})
    assert not link.receive({"verb": "put-chunk", "id": b["id"], "n": 1, "data": base64.b64encode(data).decode()})["ok"]
    assert not link.receive({"verb": "put-chunk", "id": b["id"], "n": 0,
                             "data": base64.b64encode(data + b"!").decode()})["ok"]   # more than announced
    link.receive({"verb": "put-chunk", "id": b["id"], "n": 0, "data": base64.b64encode(b"hellx").decode()})
    r = link.receive({"verb": "put-end", "id": b["id"]})
    assert not r["ok"] and "discarded" in r["error"] and not (tmp_path / "received" / "h.txt").exists()


def test_put_reaches_the_desktop_only_when_that_owner_allowed_it_and_never_overwrites(tmp_path, monkeypatch):
    import base64
    import hashlib
    monkeypatch.setenv("FIELDKIT_LINK_HOME", str(tmp_path))
    desk = tmp_path / "Desktop"

    def send(name, data):
        b = link.receive({"verb": "put-begin", "name": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                          "to_desktop": True})
        link.receive({"verb": "put-chunk", "id": b["id"], "n": 0, "data": base64.b64encode(data).decode()})
        return link.receive({"verb": "put-end", "id": b["id"]}, desktop=desk)
    r = send("APPROVE.txt", b"one")
    assert r["ok"] and "not on the Desktop" in r["saved"][1] and not desk.exists()
    (tmp_path / "link.yaml").write_text("allow_start: []\ncwd: null\naccept_to_desktop: true\n")
    link.approve(confirm=True, say=lambda m: None)
    r = send("APPROVE.txt", b"two")
    assert (desk / "APPROVE.txt").read_bytes() == b"two"
    send("APPROVE.txt", b"three")
    assert (desk / "APPROVE.txt").read_bytes() == b"two" and (desk / "APPROVE-1.txt").read_bytes() == b"three"
    assert (tmp_path / "received" / "APPROVE.txt").read_bytes() == b"one"   # received/ never overwritten either


# ---- the note convention, enforced by the tool (owner, 2026-10-22: "deterministic checks and deterministic tools") --
def test_the_sender_stamps_its_own_tags_and_refuses_done_without_evidence():
    assert link.stamp("hi", 7, "WINDOWS") == "[FROM WINDOWS #7] hi"
    typed = "[FROM LINUX #99] [DONE] [needs you] pushed it"                 # whatever a model typed is replaced
    assert link.stamp(typed, 3, "WINDOWS", needs_you=True) == "[FROM WINDOWS #3] [NEEDS YOU] pushed it"
    assert link.stamp("x", 4, "LINUX", done=True, evidence="commit 209debc") == \
        "[FROM LINUX #4] [DONE] x\nEVIDENCE: commit 209debc"
    with pytest.raises(ValueError, match="evidence"):
        link.stamp("x", 5, "LINUX", done=True, evidence="  ")
    assert link.os_tag("Windows") == "WINDOWS" and link.os_tag("Linux") == "LINUX" and link.os_tag("Darwin") == "OSX"


def test_the_receiver_flags_gaps_repeats_wrong_tags_and_done_without_evidence():
    st = {}
    assert link.seq_flags("vaio", 1, "[FROM LINUX #1] a", "LINUX", "", st) == []
    assert link.seq_flags("vaio", 2, "[FROM LINUX #2] b", "LINUX", "", st) == []
    assert link.seq_flags("vaio", 5, "[FROM LINUX #5] c", "LINUX", "", st) == ["#3-#4 never arrived"]
    assert link.seq_flags("vaio", 7, "[FROM LINUX #7] d", "LINUX", "", st) == ["#6 never arrived"]
    assert link.seq_flags("vaio", 7, "[FROM LINUX #7] d", "LINUX", "", st) == ["#7 repeated: this number arrived before"]
    assert "out of order" in link.seq_flags("vaio", 3, "[FROM LINUX #3] late", "LINUX", "", st)[0]
    assert link.seq_flags("other", 40, "[FROM LINUX #40] e", "LINUX", "", st) == []      # each sender its own count
    f = link.seq_flags("vaio", 8, "[FROM LINUX #12] f", "LINUX", "", st)
    assert f and "signed number is #8" in f[0]
    f = link.seq_flags("vaio", 9, "[FROM WINDOWS #9] g", "LINUX", "", st)
    assert f and "[FROM WINDOWS #9]" in f[0]
    assert link.seq_flags("vaio", 10, "[FROM LINUX #10] [DONE] h", "LINUX", "", st) == ["[DONE] without evidence"]
    assert link.seq_flags("vaio", 11, "[FROM LINUX #11] [DONE] h", "LINUX", "3 passed", st) == []
    assert "no signed number" in link.seq_flags("vaio", None, "old note", None, "", st)[0]


def test_numbered_notes_end_to_end_the_counter_moves_only_on_ok_and_the_inbox_shows_flags(two):
    p = serve_a(two)
    try:
        r = link.call({"verb": "message", "text": "[FROM OSX #77] first"})
        assert r["ok"] and r["seq"] == 1 and link.last_sent() == 1
        r = link.call({"verb": "message", "text": "done", "done": True})        # refused here, nothing sent
        assert not r["ok"] and "evidence" in r["error"] and link.last_sent() == 1
        r = link.call({"verb": "message", "text": "done", "done": True, "evidence": "VERDICT OK", "needs_you": True})
        assert r["ok"] and r["seq"] == 2
        (two["b"] / "sent-seq.json").write_text('{"last": 4}')                 # as if #3 and #4 were lost
        assert link.call({"verb": "message", "text": "after a loss"})["seq"] == 5
    finally:
        p.terminate()
        p.wait(10)
    r = link.call({"verb": "message", "text": "nobody listens"}, timeout=3)      # no answer: the number is kept
    assert not r["ok"] and link.last_sent() == 5
    items = [json.loads(x.read_text(encoding="utf-8")) for x in sorted((two["a"] / "inbox").glob("*.json"))]
    me = link.os_tag()
    assert items[0]["text"] == f"[FROM {me} #1] first" and items[0]["flags"] == []
    assert items[1]["text"] == f"[FROM {me} #2] [NEEDS YOU] [DONE] done\nEVIDENCE: VERDICT OK"
    assert items[1]["evidence"] == "VERDICT OK" and items[1]["flags"] == []
    assert items[2]["seq"] == 5 and items[2]["flags"] == ["#3-#4 never arrived"]
    shown = "\n".join(link.fenced(items))
    assert "!! LINK CHECK: #3-#4 never arrived" in shown
    # a note with no counter behind it (a peer on older code), and an old note claiming [DONE] with nothing
    old = {"n": 9, "kind": "message", "at": "t", "from_host": "vaio", "text": "[FROM LINUX] [DONE] Pushed"}
    assert "!! LINK CHECK: [DONE] without evidence" in "\n".join(link.fenced([old]))
