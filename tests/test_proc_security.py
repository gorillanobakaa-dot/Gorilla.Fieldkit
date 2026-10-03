"""proc: stop only what we started (PID + creation time), and a timeout stops the whole tree we started.

Process identity and killing are faked wherever a mistake could touch a real program; the one
real tree test only ever stops processes this test itself started."""
import json
import os
import sys
import time

import pytest

from fieldkit.core import proc
from fieldkit.core.proc import Runner


def _no_kill(*a, **k):
    raise AssertionError("must not kill anything")


def _seed(runner, entries):
    runner.pid_file.write_text(json.dumps(entries), encoding="utf-8")


def test_stop_refuses_a_reused_pid(tmp_path, monkeypatch):
    """The PID is in our list, but the process now holding it was created at another time."""
    monkeypatch.setattr(proc, "_kill_tree", _no_kill)
    r = Runner(tmp_path)
    how = proc._method()
    real = proc.create_time(os.getpid(), how)
    assert real is not None
    _seed(r, {str(os.getpid()): {"cmd": ["x"], "create_time": real - 1000, "how": how}})
    with pytest.raises(PermissionError, match="different process"):
        r.stop(os.getpid())
    assert str(os.getpid()) not in r.started()                  # the stale entry is pruned


def test_stop_refuses_a_legacy_entry_without_creation_time(tmp_path, monkeypatch):
    monkeypatch.setattr(proc, "_kill_tree", _no_kill)
    r = Runner(tmp_path)
    _seed(r, {str(os.getpid()): {"cmd": ["x"], "started": "2026-01-01 00:00:00"}})
    with pytest.raises(PermissionError):
        r.stop(os.getpid())


def test_stop_refuses_a_pid_never_started(tmp_path, monkeypatch):
    monkeypatch.setattr(proc, "_kill_tree", _no_kill)
    with pytest.raises(PermissionError, match="not started by fieldkit"):
        Runner(tmp_path).stop(os.getpid())


def test_stop_kills_tree_only_when_pid_and_time_match(tmp_path, monkeypatch):
    killed = []
    monkeypatch.setattr(proc, "_kill_tree", killed.append)
    monkeypatch.setattr(proc, "create_time", lambda pid, how=None: 123.0 if int(pid) == 424242 else None)
    r = Runner(tmp_path)
    _seed(r, {"424242": {"cmd": ["x"], "create_time": 123.0, "how": "fake"}})
    assert r.stop(424242) is True and killed == [424242]
    assert r.started() == {}


def test_stop_of_an_exited_process_is_false_and_pruned(tmp_path, monkeypatch):
    monkeypatch.setattr(proc, "_kill_tree", _no_kill)
    monkeypatch.setattr(proc, "create_time", lambda pid, how=None: None)
    r = Runner(tmp_path)
    _seed(r, {"424242": {"cmd": ["x"], "create_time": 123.0, "how": "fake"}})
    assert r.stop(424242) is False and r.started() == {}


def test_prune_keeps_only_live_matching_entries(tmp_path, monkeypatch):
    times = {1: 10.0, 2: 20.0, 3: None}                          # 3 has exited
    monkeypatch.setattr(proc, "create_time", lambda pid, how=None: times.get(int(pid)))
    r = Runner(tmp_path)
    _seed(r, {"1": {"create_time": 10.0, "how": "fake"},        # same process: kept
              "2": {"create_time": 5.0, "how": "fake"},         # PID reused: dropped
              "3": {"create_time": 30.0, "how": "fake"},        # exited: dropped
              "4": {"cmd": ["legacy"]}})                         # no creation time: dropped
    assert list(r.prune()) == ["1"] and list(r.started()) == ["1"]


def test_run_records_creation_time_and_forgets_after_exit(tmp_path, monkeypatch):
    seen = {}
    real_record = Runner._record_pid

    def spy(self, pid, cmd):
        real_record(self, pid, cmd)
        seen.update(self.started())
    monkeypatch.setattr(Runner, "_record_pid", spy)
    r = Runner(tmp_path)
    res = r.run([sys.executable, "-c", "pass"])
    entry = seen[str(res.pid)]
    assert entry["how"] == proc._method() and entry["create_time"] is not None
    assert str(res.pid) not in r.started()                     # finished: no longer ours to stop


def test_native_creation_time_reads_this_process():
    how = "windows" if os.name == "nt" else "linux"
    a = proc.create_time(os.getpid(), how)
    assert a is not None and a == proc.create_time(os.getpid(), how)
    assert proc.create_time(2 ** 22 + 7, how) is None          # no such process


GRANDPARENT = "\n".join([
    "import subprocess, sys, time, pathlib",
    "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])",
    "pathlib.Path(sys.argv[1]).write_text(str(g.pid))",
    "time.sleep(60)",
])


def test_timeout_stops_the_grandchild_too(tmp_path):
    """Real processes, but only ones this test started: the child and the grandchild it spawns."""
    marker = tmp_path / "grandchild.pid"
    r = Runner(tmp_path).run([sys.executable, "-c", GRANDPARENT, str(marker)], timeout=4)
    assert r.returncode == 124 and "child processes were stopped" in r.stderr
    assert r.seconds < 40           # without the tree kill the runner waited out the grandchild's full 60 s
    gpid = int(marker.read_text())
    deadline = time.monotonic() + 10
    while proc.create_time(gpid) is not None and time.monotonic() < deadline:
        time.sleep(0.2)
    assert proc.create_time(gpid) is None
