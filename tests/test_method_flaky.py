"""fieldkit flaky: reachable from the CLI, verdicts from run counts, the log read back, OS-wide names found.
No real pytest run is started: `repeat` takes the runner as a parameter."""
from fieldkit import cli
from fieldkit.method import flaky


def test_verdicts_from_counts():
    assert flaky.verdict(0, 10) == "STABLE"
    assert flaky.verdict(10, 10) == "REAL"
    assert flaky.verdict(3, 10) == "FLAKY 3/10"


def test_repeat_counts_failures_and_keeps_the_first_error():
    seq = iter([True, False, True, False])
    run = lambda t: ({"ok": True, "errors": [], "thread": []} if next(seq) else
                     {"ok": False, "errors": ["E   boom"], "thread": ["Traceback: pipe busy"]})
    [row] = flaky.repeat(["tests/test_nothing_here.py::t"], n=4, run=run)
    assert row["failed"] == 2 and row["verdict"] == "FLAKY 2/4"
    assert row["errors"] == ["E   boom"] and row["thread"] == ["Traceback: pipe busy"]


def test_the_log_gives_each_test_once_newest_last(tmp_path):
    log = tmp_path / "flaky-tests.log"
    log.write_text("2026-10-16 10:58:00 passed when run again: tests/test_a.py::x\n"
                   "2026-10-16 11:00:00 passed when run again: tests/test_b.py::y\n"
                   "2026-10-16 12:01:00 passed when run again: tests/test_a.py::x\n", encoding="utf-8")
    assert flaky.logged(log) == ["tests/test_b.py::y", "tests/test_a.py::x"]
    assert flaky.logged(tmp_path / "missing.log") == []


def test_scan_finds_names_that_live_in_the_whole_system_and_skips_comments(tmp_path):
    f = tmp_path / "x.py"
    f.write_text('PIPE = r"\\\\.\\pipe\\gorilla-peers"\n'
                 "srv.bind(('127.0.0.1', 8765))\n"
                 "# port = 9999 in a comment is not code\n"
                 "lock_name = 'lk' + str(os.getpid())\n", encoding="utf-8")
    kinds = [h["kind"] for h in flaky.scan([f])]
    assert kinds == ["named pipe", "fixed TCP port", "pid-derived name"]


def test_reachable_as_fieldkit_flaky(tmp_path, capsys):
    clean = tmp_path / "clean.py"
    clean.write_text("x = 1\n", encoding="utf-8")
    assert cli.main(["flaky", "scan", str(clean)]) == 0
    assert "SCAN: 0 OS-wide name(s)" in capsys.readouterr().out
