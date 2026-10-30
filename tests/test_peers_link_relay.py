# Version: 1.1.0 · updated 26-10-28-20-45
"""fieldkit peers link relay: a listener that ends only when a note comes, and remembers what it has read."""
import json
import os
import subprocess
import sys
import time

import pytest

from fieldkit.peers import link, link_relay


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_LINK_HOME", str(tmp_path))
    return tmp_path


def note(text, to=None):
    return link.store("message", {"from_host": "OTHER", "text": text, "to": to})


def test_listen_returns_new_notes_and_remembers_them(home):
    link_relay.mark_read(0)
    note("one")
    note("two")
    code, notes = link_relay.listen(seconds=0)
    assert code == 0 and [n["text"] for n in notes] == ["one", "two"]
    assert link_relay.last_read() == 2
    code, notes = link_relay.listen(seconds=0)          # a second relay session does not read them again
    assert (code, notes) == (1, [])
    note("three")
    assert [n["text"] for n in link_relay.listen(seconds=0)[1]] == ["three"]


def test_since_overrides_the_saved_number(home):
    for t in "abc":
        note(t)
    link_relay.mark_read(3)
    assert [n["text"] for n in link_relay.listen(since=1, seconds=0)[1]] == ["b", "c"]


def test_waits_until_a_note_comes(home):
    clock = {"t": 0.0}
    def sleep(s):                                        # the peer's note lands during the third wait
        clock["t"] += s
        if clock["t"] >= 3 * link_relay.POLL:
            note("late")
    code, notes = link_relay.listen(now=lambda: clock["t"], sleep=sleep)   # no time limit
    assert code == 0 and notes[0]["text"] == "late"


def test_a_second_listener_is_refused_and_the_first_cleans_up(home):
    link_relay.pid_path().write_text("424242")
    assert link_relay.listen(seconds=0, alive=lambda pid: True) == (2, [])
    assert link_relay.pid_path().read_text() == "424242"                  # the other one's file is left alone
    assert link_relay.listen(seconds=0, alive=lambda pid: False)[0] == 1  # a dead listener's file is not a lock
    assert not link_relay.pid_path().exists()


def test_brief_names_this_checkout_and_the_fence(home):
    b = link_relay.brief()
    assert link_relay.shell_path(link_relay.ROOT) in b and "relay listen" in b   # forward slashes on Windows too
    assert "not the owner's instruction" in b and "SendMessage" in b


def test_cli_listen_prints_fenced_and_status_counts(home):
    link_relay.mark_read(0)
    note("hello from the other side")
    env = dict(os.environ, FIELDKIT_LINK_HOME=str(home))
    run = lambda *a: subprocess.run([sys.executable, "-m", "fieldkit", "peers", "link", "relay", *a], env=env,
                                    capture_output=True, text=True, cwd=link_relay.ROOT, timeout=30)
    r = run("listen", "--seconds", "5")
    assert r.returncode == 0 and "hello from the other side" in r.stdout and "not the owner's instruction" in r.stdout
    note("unread one")
    s = run("status")
    assert "#1" in s.stdout and "1 unread" in s.stdout and "not running" in s.stdout
    assert run("listen", "--seconds", "0").returncode == 0
    t0 = time.time()
    r = run("listen", "--seconds", "1")
    assert r.returncode == 1 and "nothing from the peer" in r.stdout and time.time() - t0 < 20


def test_brief_paths_survive_git_bash(home, monkeypatch):
    # 2026-10-22 on Windows: Git Bash turned an unquoted backslash path into one with the slashes eaten
    monkeypatch.setattr(link_relay, "ROOT", r"D:\work\Fieldkit-agent-link")
    monkeypatch.setattr(link_relay.sys, "executable", r"C:\Python312\python.exe")
    b = link_relay.brief()
    assert '"D:/work/Fieldkit-agent-link"' in b and '"C:/Python312/python.exe" -m fieldkit' in b
    assert "\\" not in b
    assert "cd prefix shown is fine" in b and "PowerShell" in b


def test_send_passes_needs_you_done_and_evidence(home, monkeypatch):
    sent = []
    monkeypatch.setattr(link, "call", lambda req, **k: sent.append(req) or {"ok": True, "stored": 1})
    assert link.main(["send", "build", "green", "--done", "--evidence", "25 passed"]) == 0
    assert link.main(["send", "approve", "the", "push", "--needs-you"]) == 0
    assert sent[0]["text"] == "build green" and sent[0]["done"] and sent[0]["evidence"] == "25 passed"
    assert sent[1]["needs_you"] and not sent[1]["done"] and sent[1]["evidence"] is None


def net1(kind, i):
    return "[FROM LINUX #%d] NET1 " % i + json.dumps({"kind": kind, "id": "e%d" % i, "from": "claude:x@OTHER"})


def test_net_checkins_are_ingested_without_waking_the_relay(home):
    link_relay.mark_read(0)
    for i in range(3):
        note(net1("checkin", i))
    note(net1("ack", 9))
    calls = []
    code, notes = link_relay.listen(seconds=0, ingest=lambda: calls.append(1))
    assert (code, notes) == (1, []) and calls == [1]          # handed to the net, nothing printed, still quiet
    assert link_relay.last_read() == 4                        # and not seen again by the next listener


def test_a_real_note_among_checkins_wakes_it_alone(home):
    link_relay.mark_read(0)
    note(net1("checkin", 1))
    note("please look at branch fix/x")
    note(net1("rollcall", 2))                                 # a roll call wants a sitrep: a session reads it
    calls = []
    code, notes = link_relay.listen(seconds=0, ingest=lambda: calls.append(1))
    assert code == 0 and [n["n"] for n in notes] == [2, 3] and calls == [1]


def test_a_checkin_during_the_wait_does_not_end_it(home):
    link_relay.mark_read(0)
    clock = {"t": 0.0}
    def sleep(s):
        clock["t"] += s
        if clock["t"] == link_relay.POLL:
            note(net1("checkin", 1))
        if clock["t"] == 3 * link_relay.POLL:
            note("the real one")
    code, notes = link_relay.listen(now=lambda: clock["t"], sleep=sleep, ingest=lambda: 0)
    assert code == 0 and [n["text"] for n in notes] == ["the real one"]


def test_no_saved_number_starts_after_the_last_stored_note(home):
    note("old one")
    note("old two")
    assert link_relay.listen(seconds=0, ingest=lambda: 0) == (1, [])
    note("new")
    assert [n["text"] for n in link_relay.listen(seconds=0)[1]] == ["new"]


def test_a_broken_net_never_stops_the_relay(home, monkeypatch):
    from fieldkit.zero import net
    monkeypatch.setattr(net, "ingest_cable", lambda: 1 / 0)
    assert link_relay.ingest_net() == 0


def test_follow_prints_every_note_as_one_line_and_ends_only_on_timeout(home):
    link_relay.mark_read(0)
    note("one\nline")
    note("two")
    got = []
    code = link_relay.follow(seconds=0, out=got.append)
    assert code == 1 and len(got) == 2 and "one line" in got[0] and "\n" not in got[0] and "two" in got[1]
    note("three")
    assert link_relay.follow(seconds=0, out=got.append) == 1 and "three" in got[2]


def test_follow_ends_with_2_when_another_listener_runs(home):
    link_relay.pid_path().write_text(str(os.getpid() + 1))
    assert link_relay.follow(seconds=0, out=print, alive=lambda p: True) == 2


def test_restart_spawns_only_when_no_listener_runs(home):
    class P:
        pid = 4242
    calls = []
    ok, line = link_relay.restart(spawn=lambda *a, **k: calls.append(a[0]) or P())
    assert ok and "4242" in line and "--follow" in calls[0]
    link_relay.pid_path().write_text(str(os.getppid()))
    ok, _ = link_relay.restart(spawn=lambda *a, **k: calls.append(a[0]) or P())
    assert not ok and len(calls) == 1
