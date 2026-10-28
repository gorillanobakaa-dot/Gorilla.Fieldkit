# Version: 1.0.0 · updated 26-10-28-18-21
"""Plan C7 (2026-10-28): a note to an idle computer says it was not heard, and sits as an `unread` friction row."""
import time

import pytest

from fieldkit.peers import link, link_receipt, link_relay
from fieldkit.peers import peers as local_peers

NOW = 1_800_000_000.0
OK = type("V", (), {"check": lambda self, r: None})()


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_LINK_HOME", str(tmp_path))
    monkeypatch.delenv("FIELDKIT_LINK_UNREAD_MINUTES", raising=False)
    return tmp_path


def at(minutes):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(NOW - minutes * 60))


def test_a_note_nothing_listens_for_is_answered_not_heard(home, monkeypatch):
    monkeypatch.setattr(link_relay, "other_listener", lambda *a: None)
    r = link.handle({"verb": "message", "from_host": "win", "text": "phase 3 is yours"}, OK)
    assert r["ok"] and r["heard"] is False and "task start" in r["heard_why"]
    monkeypatch.setattr(link_relay, "other_listener", lambda *a: 4242)
    r = link.handle({"verb": "message", "from_host": "win", "text": "again"}, OK)
    assert r["heard"] is True and "4242" in r["heard_why"]


def test_a_note_handed_to_its_session_is_heard_and_never_unread(home, monkeypatch):
    monkeypatch.setattr(link_relay, "other_listener", lambda *a: None)
    monkeypatch.setattr(local_peers, "send", lambda to, text, as_name: {"ok": True})
    r = link.handle({"verb": "message", "from_host": "win", "text": "x", "to": "main"}, OK)
    assert r["heard"] is True
    items = link.inbox()
    assert items[0]["handed"] == "main"
    items[0]["at"] = at(60)
    assert link_receipt.unread(items, now=NOW, upto=0) == []


def test_unread_after_the_wait_until_a_reader_reaches_it(home, monkeypatch):
    items = [{"n": 1, "kind": "message", "from_host": "win", "at": at(30), "text": "old"},
             {"n": 2, "kind": "message", "from_host": "win", "at": at(3), "text": "fresh"},
             {"n": 3, "kind": "ask", "from_host": "win", "at": at(30), "text": "ask"},
             {"n": 4, "kind": "message", "from_host": "win", "at": at(60 * 72), "text": "ancient"}]
    late = link_receipt.unread(items, now=NOW, upto=0)
    assert [i["n"] for i in late] == [1] and late[0]["age_minutes"] == 30
    monkeypatch.setenv("FIELDKIT_LINK_UNREAD_MINUTES", "2")
    assert [i["n"] for i in link_receipt.unread(items, now=NOW, upto=0)] == [1, 2]
    link_receipt.mark_seen(1)
    assert [i["n"] for i in link_receipt.unread(items, now=NOW)] == [2]
    link_relay.mark_read(2)
    assert link_receipt.unread(items, now=NOW) == []


def test_link_inbox_marks_what_it_printed_as_read(home, capsys):
    link.store("message", {"from_host": "win", "text": "one"})
    link.store("message", {"from_host": "win", "text": "two"})
    assert link_receipt.read_upto() == 0
    assert link.main(["inbox"]) == 0
    assert "two" in capsys.readouterr().out and link_receipt.read_upto() == 2


def test_needs_you_to_a_session_is_refused_and_says_not_heard(home, monkeypatch, capsys):
    sent = []
    monkeypatch.setattr(link, "call", lambda req, **k: sent.append(req) or {"ok": True, "heard": False,
                                                                           "heard_why": "nobody"})
    assert link.main(["send", "do", "it", "--needs-you", "--to", "main"]) == 2 and not sent
    assert link.main(["send", "do", "it", "--needs-you", "--to", "owner"]) == 0
    assert link.main(["send", "phase", "3"]) == 0
    assert "NOT HEARD on the other computer: nobody" in capsys.readouterr().err
