"""P28 E: fieldkit once - a command that already succeeded is not run or asked again; a watcher stops on no news."""
import json
import subprocess
import sys
from types import SimpleNamespace

from fieldkit import once as O


def runner(rc, calls):
    def run(argv, **kw):
        calls.append(argv)
        return SimpleNamespace(returncode=rc)
    return run


def test_success_is_skipped_on_the_same_signature(tmp_path):
    j, calls = tmp_path / "j.jsonl", []
    res, rc = O.run(j, "k", "s1", ["x"], runner=runner(0, calls))
    assert (res["done"], res["skipped"], rc) == (True, False, 0)
    res, rc = O.run(j, "k", "s1", ["x"], runner=runner(0, calls))
    assert (res["done"], res["skipped"], rc) == (True, True, 0)
    assert calls == [["x"]]


def test_changed_inputs_run_again(tmp_path):
    j, calls = tmp_path / "j.jsonl", []
    src = tmp_path / "in.txt"
    src.write_text("a", encoding="utf-8")
    s1 = O.state_signature([src])
    O.run(j, "k", s1, ["x"], runner=runner(0, calls))
    src.write_text("b", encoding="utf-8")
    s2 = O.state_signature([src])
    assert s1 != s2
    assert O.check(j, "k", s2) == {"done": False, "key": "k", "sig": s2, "why": "inputs changed since the success"}
    O.run(j, "k", s2, ["x"], runner=runner(0, calls))
    assert len(calls) == 2


def test_folder_signature_sees_a_new_file_and_a_missing_path(tmp_path):
    d = tmp_path / "d"
    d.mkdir()
    (d / "a").write_text("1", encoding="utf-8")
    s1 = O.state_signature([d])
    (d / "b").write_text("2", encoding="utf-8")
    assert O.state_signature([d]) != s1
    assert O.state_signature([tmp_path / "nope"]) != O.state_signature([])


def test_a_failure_runs_again_and_returns_its_exit_code(tmp_path):
    j, calls = tmp_path / "j.jsonl", []
    res, rc = O.run(j, "k", "s", ["x"], runner=runner(4, calls))
    assert (res["done"], rc) == (False, 4)
    assert O.check(j, "k", "s")["why"] == "last run exit 4"
    res, rc = O.run(j, "k", "s", ["x"], runner=runner(0, calls))
    assert (res["done"], rc, len(calls)) == (True, 0, 2)


def test_a_later_failure_cancels_an_earlier_success(tmp_path):
    j = tmp_path / "j.jsonl"
    O.run(j, "k", "s", ["x"], runner=runner(0, []))
    O.append(j, {"kind": "run", "key": "k", "sig": "s", "exit": 1})
    assert O.check(j, "k", "s")["done"] is False


def test_forget_appends_and_never_erases(tmp_path):
    j, calls = tmp_path / "j.jsonl", []
    O.run(j, "k", "s", ["x"], runner=runner(0, calls))
    before = j.read_text(encoding="utf-8")
    O.forget(j, "k")
    assert j.read_text(encoding="utf-8").startswith(before)
    assert O.check(j, "k", "s")["why"] == "forgotten"
    O.run(j, "k", "s", ["x"], runner=runner(0, calls))
    assert len(calls) == 2


def test_watch_stops_on_the_same_news_at_another_time(tmp_path):
    j = tmp_path / "j.jsonl"
    a = O.watch(j, "w", "2026-11-16T10:00:01Z nothing changed\n")
    assert (a["done"], a["changed"]) == (False, True)
    b = O.watch(j, "w", "2026-11-16T10:05:09Z nothing changed\n")
    assert (b["done"], b["changed"], b["repeats"]) == (True, False, 2)
    c = O.watch(j, "w", "10:09 nothing changed")
    assert c["repeats"] == 3
    d = O.watch(j, "w", "10:10 PR 571 merged")
    assert (d["done"], d["changed"], d["repeats"]) == (False, True, 1)


def test_command_key_depends_on_argv_and_folder(tmp_path):
    assert O.command_key(["a"], tmp_path) == O.command_key(["a"], tmp_path)
    assert O.command_key(["a"], tmp_path) != O.command_key(["b"], tmp_path)
    assert O.command_key(["a"], tmp_path) != O.command_key(["a"], tmp_path / "x")


def test_torn_journal_line_is_skipped(tmp_path):
    j = tmp_path / "j.jsonl"
    O.append(j, {"kind": "run", "key": "k", "sig": "s", "exit": 0})
    with open(j, "a", encoding="utf-8") as f:
        f.write('{"kind": "ru')
    assert O.check(j, "k", "s")["done"] is True


def test_cli_prints_one_json_line_last_and_the_exit_codes(tmp_path, capsys):
    j = str(tmp_path / "j.jsonl")
    cmd = [sys.executable, "-c", "pass"]
    assert O.main(["check", "--journal", j, "--"] + cmd) == 3
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1])["done"] is False
    assert O.main(["run", "--journal", j, "--"] + cmd) == 0
    capsys.readouterr()
    assert O.main(["run", "--journal", j, "--json", "--"] + cmd) == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 1 and json.loads(out[0])["skipped"] is True
    assert O.main(["check", "--journal", j, "--"] + cmd) == 0
    capsys.readouterr()
    f = tmp_path / "w.txt"
    f.write_text("same", encoding="utf-8")
    assert O.main(["watch", "--journal", j, "--key", "w", "--file", str(f)]) == 3
    assert O.main(["watch", "--journal", j, "--key", "w", "--file", str(f)]) == 0
    assert "stop watching" in capsys.readouterr().out


def test_a_hung_command_is_killed_and_journalled_as_exit_124(tmp_path, capsys):
    """F4.1: no timeout meant a hang left the journal at 'running' forever."""
    j = tmp_path / "j.jsonl"
    seen = {}

    def hang(argv, timeout=None):
        seen["timeout"] = timeout
        raise subprocess.TimeoutExpired(argv, timeout)
    res, rc = O.run(j, "k", "s", ["x"], runner=hang, timeout=5)
    assert rc == 124 and res["exit"] == 124 and not res["done"] and seen["timeout"] == 5
    assert "killed" in capsys.readouterr().err
    assert O.latest(j, "k")["exit"] == 124


def test_real_sleep_command_times_out_through_the_cli(tmp_path):
    j = str(tmp_path / "j.jsonl")
    rc = O.main(["run", "--journal", j, "--json", "--timeout", "1", "--", sys.executable, "-c", "import time; time.sleep(60)"])
    assert rc == 124
