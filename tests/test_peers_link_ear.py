# Version: 1.0.0 · updated 26-11-10-12-00
"""I-27: every session's own ear (`fieldkit peers link relay listen --as STATION`) ends on net traffic for that
station, holds the plain relay's duty only while none runs, refuses a second ear for the same station, and reports
its state for Zero's comms check."""
import json
import os

import pytest

from fieldkit.peers import link, link_relay

ME = "claude:aaaa1111@L15"


class FakeNet:
    """The net log as a list; an offset is the number of entries before it."""
    def __init__(self):
        self.log = []

    def log_size(self):
        return len(self.log)

    def entries(self, off=0):
        return list(enumerate(self.log))[off:]

    def _addressed(self, e, me):
        return e.get("kind") == "msg" and e.get("from") != me and e.get("to") in ("*", me)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_LINK_HOME", str(tmp_path))
    return tmp_path


def run(net, **kw):
    kw.setdefault("seconds", 0)
    kw.setdefault("ingest", lambda: 0)
    kw.setdefault("alive", lambda pid: pid == os.getpid())
    return link_relay.listen_station(ME, net=net, **kw)


def test_the_ear_ends_on_traffic_for_its_station_only(home):
    net = FakeNet()
    net.log.append({"kind": "msg", "from": "x", "to": "claude:other@L15", "text": "not mine"})
    code, got, plain = run(net, since=0)
    assert (code, got) == (1, [])                                  # traffic for another station never wakes it
    net.log.append({"kind": "msg", "from": "zero", "to": ME, "text": "for you"})
    net.log.append({"kind": "msg", "from": "zero", "to": "*", "text": "for all"})
    code, got, plain = run(net)
    assert code == 0 and [e["text"] for e in got] == ["for you", "for all"]
    assert link_relay.ear_read(ME)["off"] == 3 and "pid" not in link_relay.ear_read(ME)
    assert run(net)[0] == 1                                        # read once: the saved offset carries on


def test_a_second_ear_for_the_same_station_is_refused(home):
    link_relay._ear_write(ME, {"pid": 99999, "off": 0})
    assert run(FakeNet(), alive=lambda pid: True)[0] == 2
    assert link_relay.ear_state(ME, alive=lambda pid: True)["state"] == "running"
    assert link_relay.ear_state(ME, alive=lambda pid: False)["state"] == "stopped"
    assert link_relay.ear_state("claude:never@L15")["state"] == "never"


def test_the_ear_holds_the_relay_duty_only_while_no_relay_runs(home):
    link_relay.mark_read(0)
    link.store("message", {"from_host": "VAIO", "text": "plain note"})
    link.store("message", {"from_host": "VAIO", "text": 'NET1 {"kind":"checkin","id":"C1"}'})
    link_relay.pid_path().write_text("424242")                      # a plain relay runs: its notes are its own
    code, got, plain = run(FakeNet(), alive=lambda pid: pid in (os.getpid(), 424242))
    assert (code, plain) == (1, []) and link_relay.last_read() == 0
    code, got, plain = run(FakeNet(), alive=lambda pid: pid == os.getpid())   # it died: the ear takes the duty
    assert code == 0 and [n["text"] for n in plain] == ["plain note"]          # the NET note is the net log's
    assert link_relay.last_read() == 2 and not link_relay.pid_path().exists()  # released when the ear ends


def test_ear_brief_and_line_say_deaf_and_how_to_start(home, monkeypatch):
    link_relay._ear_write(ME, {"off": 5})
    monkeypatch.setattr(link_relay, "ear_unheard", lambda st, heard=None: 3)
    assert link_relay.ear_brief(ME, alive=lambda pid: False) == {"state": "deaf", "unheard": 3}
    assert "relay listen --as claude:aaaa1111@L15 --since 5" in link_relay.ear_line(ME)
    link_relay._ear_write(ME, {"off": 5, "pid": os.getpid()})
    assert link_relay.ear_brief(ME, alive=lambda pid: True) == {"state": "running", "unheard": 0}


def test_ear_write_retries_a_locked_target_and_never_raises(tmp_path, monkeypatch):
    """Windows WinError 5: os.replace fails while another ear holds the file; the write retries, then gives up quietly."""
    monkeypatch.setattr(link_relay, "ear_path", lambda station: tmp_path / "ears" / "me.json")
    real, fails = os.replace, {"n": 2}

    def flaky(a, b):
        if fails["n"] > 0:
            fails["n"] -= 1
            raise PermissionError(5, "Access is denied")
        return real(a, b)
    monkeypatch.setattr(link_relay.os, "replace", flaky)
    assert link_relay._ear_write(ME, {"pid": 1}, pause=0) is True
    assert json.loads((tmp_path / "ears" / "me.json").read_text(encoding="utf-8")) == {"pid": 1}
    fails["n"] = 99
    assert link_relay._ear_write(ME, {"pid": 2}, tries=3, pause=0) is False
    assert json.loads((tmp_path / "ears" / "me.json").read_text(encoding="utf-8")) == {"pid": 1}
    assert not list((tmp_path / "ears").glob("*.tmp"))

