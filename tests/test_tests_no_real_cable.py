# Version: 1.0.0 · updated 26-10-28-20-38
"""Tests never reach the real cable (plan item 22, 2026-10-28). Since C2 a Claude Code hook checks a station in to a
far Zero over the cable; the C6 hook test started a child hook process whose station was on this computer and whose
Zero was claude:aaaaaaaa@HOSTA, so the child sent fixture check-ins over the REAL cable to the other computer (VAIO
notes #232, L15 #129 #130). tests/conftest.py now refuses every real peer address at socket.connect (here and in every
child) and points the Zero cable channel at a fake file."""
import json
import os
import socket
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from fieldkit.core import offline
from fieldkit.zero import channels as ch
from fieldkit.zero import net

ROOT = Path(__file__).resolve().parents[1]


def _fake_cable_lines():
    p = Path(os.environ.get("FIELDKIT_FAKE_CABLE") or os.devnull)
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.is_file() else []


def test_the_suite_guards_the_cable_peers_in_this_process_and_in_children(tmp_path, monkeypatch):
    assert getattr(socket.socket.connect, offline.MARK, False), "no cable guard in the test process"
    assert "10.77.0.0/30" in os.environ["FIELDKIT_BLOCKED_PEERS"].split(",")
    log = tmp_path / "refused"
    monkeypatch.setenv("FIELDKIT_BLOCKED_PEERS_LOG", str(log))
    with pytest.raises(ConnectionRefusedError, match="cable peer 10.77.0.2:47077"):
        socket.create_connection(("10.77.0.2", 47077), timeout=0.5)
    assert log.read_text(encoding="utf-8").split() == ["10.77.0.2:47077"]
    code = ("import socket,fieldkit\ntry:\n socket.create_connection(('10.77.0.1',47077),timeout=0.5)\n"
            "except ConnectionRefusedError as e:\n print('REFUSED' if 'cable peer' in str(e) else 'other')\n")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60, cwd=str(ROOT))
    assert r.stdout.strip() == "REFUSED", r.stderr
    assert log.read_text(encoding="utf-8").split() == ["10.77.0.2:47077", "10.77.0.1:47077"]


def test_the_zero_cable_channel_takes_the_fake_cable_in_tests():
    before = len(_fake_cable_lines())
    c = ch.Cable(household=lambda: {"me": "A", "devices": [{"name": "A"}, {"name": "PEER"}]})
    r = c.push({"type": "checkin", "from": "claude:zzzzzzzz@A"}, {})
    assert [x["ok"] for x in r] == [False] and "fake cable" in r[0]["detail"]
    got = _fake_cable_lines()[before:]
    assert [x["device"] for x in got] == ["PEER"] and got[0]["req"]["verb"] == "message"


def test_a_zero_hook_child_process_never_sends_a_check_in_over_the_cable(tmp_path, monkeypatch):
    """The C6 hook test's shape, on a paired computer: the peer is a loopback listener standing in for the other
    computer. Before the fix the child connected to it (the real cable, on the L15 and the VAIO)."""
    coord, link_home = tmp_path / "coord", tmp_path / "link"
    link_home.mkdir()
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(4)
    srv.settimeout(0.2)
    port, reached, stop = srv.getsockname()[1], [], threading.Event()

    def accept():
        while not stop.is_set():
            try:
                c, _a = srv.accept()
            except OSError:
                continue
            reached.append(c.recv(65536))
            c.close()

    t = threading.Thread(target=accept, daemon=True)
    t.start()
    me = socket.gethostname()
    (link_home / "household.json").write_text(json.dumps({"me": me, "port": port, "devices": [
        {"name": me, "address": ["127.0.0.2"], "link": "cable"},
        {"name": "PEER", "address": ["127.0.0.1"], "port": port, "link": "cable"}]}), encoding="utf-8")
    (link_home / "link.key").write_text("00" * 32, encoding="ascii")          # a fixture key, not the household's
    monkeypatch.setenv("FIELDKIT_COORDINATION_DIR", str(coord))
    monkeypatch.setenv("FIELDKIT_LINK_HOME", str(link_home))
    monkeypatch.setattr(net, "machine", lambda: "HOSTA")
    monkeypatch.setattr(net, "ingest_cable", lambda *a, **k: 0)
    monkeypatch.setattr(ch, "default", lambda: [])
    net.open_net("claude:aaaaaaaa@HOSTA", now=None, env={"CLAUDE_CODE_SESSION_ID": "aaaaaaaa-1"})
    before = len(_fake_cable_lines())
    hook_call = {"session_id": "zzzzzzzz-1", "tool_name": "Edit", "tool_input": {"file_path": str(tmp_path / "f.py")},
                 "cwd": str(tmp_path)}
    code = "import sys,json;from fieldkit.autopilot import toolrouter;toolrouter._room(json.loads(sys.argv[1]))"
    try:
        r = subprocess.run([sys.executable, "-c", code, json.dumps(hook_call)], capture_output=True, text=True,
                           timeout=60, cwd=str(ROOT), env=dict(os.environ))
    finally:
        stop.set()
        t.join(2)
        srv.close()
    assert r.returncode == 0, r.stderr
    assert reached == [], f"the child hook sent {len(reached)} request(s) over the cable: {reached[:1]}"
    sent = [x for x in _fake_cable_lines()[before:] if f"claude:zzzzzzzz@{me}" in x["req"].get("text", "")]
    assert sent and sent[0]["device"] == "PEER", "the far check-in did not take the fake cable"
