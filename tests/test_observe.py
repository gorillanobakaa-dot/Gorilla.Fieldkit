"""fieldkit observe: every question has a reader on all three systems (a table test), each reader refuses (returns
None) on a system that is not its own, and on THIS system the readers answer on a fixture: a file the test holds open,
a socket the test listens on, the test's own PID. Run: python -m pytest -q tests/test_observe.py"""
import os
import socket
import sys
import threading
import time

import pytest

from fieldkit import observe

ARGS = {"open-file": ("/x",), "port": (1,), "proc": (1,), "watch": (".", 0), "trace": (1, 0), "usage": (1, 0),
        "events": (["x"],)}
HERE = {"linux": sys.platform.startswith("linux"), "windows": sys.platform == "win32", "macos": sys.platform == "darwin"}
live = pytest.mark.skipif(not HERE["linux"], reason="live fixtures run on the reader of this system; Windows by CI")


@pytest.mark.parametrize("system", observe.SYSTEMS)
@pytest.mark.parametrize("question", observe.QUESTIONS)
def test_every_question_has_a_reader_on_every_system(system, question):
    assert callable(getattr(observe.reader(system), observe.QUESTIONS[question][0]))


@pytest.mark.parametrize("system", [s for s in observe.SYSTEMS if not HERE[s]])
@pytest.mark.parametrize("question", observe.QUESTIONS)
def test_a_reader_returns_nothing_on_another_system(system, question):
    assert observe.ask(question, *ARGS[question], system=system) is None


def test_questions_match_the_plan_row():
    assert set(observe.QUESTIONS) == {"open-file", "port", "proc", "watch", "trace", "usage", "events"}


@live
def test_open_file_finds_this_process(tmp_path):
    f = tmp_path / "held.txt"
    with open(f, "w"):
        rows = observe.ask("open-file", str(f))
    assert any(r["pid"] == os.getpid() for r in rows)
    assert not any(r["pid"] == os.getpid() for r in observe.ask("open-file", str(f)))      # closed: nobody


@live
def test_port_finds_the_listening_socket():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen()
    try:
        rows = observe.ask("port", s.getsockname()[1])
    finally:
        s.close()
    assert any(r["pid"] == os.getpid() and r["state"] == "LISTEN" for r in rows)


@live
def test_proc_describes_this_process(tmp_path):
    r = observe.ask("proc", os.getpid())
    assert os.path.realpath(r["exe"]) == os.path.realpath(sys.executable) and r["cwd"] == os.getcwd()
    assert r["cmdline"] and r["user"] and r["map_regions"] > 0
    assert "error" in observe.ask("proc", 2 ** 22 + 12345)


@live
def test_proc_lists_children():
    import subprocess
    c = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"])
    try:
        assert c.pid in [k["pid"] for k in observe.ask("proc", os.getpid())["children"]]
    finally:
        c.kill()
        c.wait()


@live
def test_watch_sees_a_new_file(tmp_path):
    threading.Timer(0.5, lambda: (tmp_path / "new.txt").write_text("x")).start()
    r = observe.ask("watch", str(tmp_path), 1.5)
    assert any(e["event"] == "created" and e["path"].endswith("new.txt") for e in r["events"])


@live
def test_usage_samples_this_process():
    r = observe.ask("usage", os.getpid(), 2)
    assert len(r["samples"]) == 2 and r["samples"][0]["rss_kb"] > 0
    assert "error" in observe.ask("usage", 2 ** 22 + 12345, 1)


@live
def test_trace_answers_or_says_it_needs_admin():
    import shutil
    import subprocess
    if not shutil.which("strace"):
        pytest.skip("strace not installed")
    c = subprocess.Popen([sys.executable, "-c", "import time\nwhile True:\n open('/etc/hostname').close();time.sleep(.1)"])
    try:
        time.sleep(0.5)
        r = observe.ask("trace", c.pid, 1)
    finally:
        c.kill()
        c.wait()
    assert r.get("needs_admin") or any("/etc/hostname" in e for e in r["events"])


@live
def test_events_filters_by_every_word(monkeypatch):
    import shutil
    if not shutil.which("journalctl"):
        pytest.skip("journalctl not installed")
    r = observe.ask("events", ["zzz-no-such-word-zzz"])
    assert r["lines"] == []


def test_cli_exit_codes(capsys):
    if not HERE["linux"]:
        pytest.skip("linux")
    assert observe.main(["proc", str(os.getpid())]) == 0
    assert "VERDICT" in capsys.readouterr().out
    assert observe.main(["proc", str(2 ** 22 + 12345)]) == 1
