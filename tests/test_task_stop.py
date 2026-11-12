"""fieldkit task stop (I-07, 2026-11-11): the reparented worker dies with the task. Real processes, nothing mocked."""
import os
import subprocess
import sys
import time

import pytest

from fieldkit.lifecycle import taskstop

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="process groups and /proc")


def _alive(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:                                   # a zombie is dead for our purposes
        return open(f"/proc/{pid}/stat").read().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return False


def test_stop_kills_the_runner_and_the_worker_that_outlived_it(tmp_path):
    # the runner starts its own session (as window.launch does) and spawns a worker, then the runner is killed alone
    script = tmp_path / "runner.py"
    script.write_text("import subprocess, sys, time\n"
                      "w = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
                      "print(w.pid, flush=True)\ntime.sleep(60)\n")
    p = subprocess.Popen([sys.executable, str(script)], stdout=subprocess.PIPE, text=True, start_new_session=True)
    worker = int(p.stdout.readline())
    os.kill(p.pid, 9)                       # what happened on 2026-11-11: only the runner died
    p.wait()
    assert _alive(worker)
    r = taskstop.stop({"pid": p.pid}, grace=3)
    time.sleep(0.3)
    assert worker in r["signalled"] and not _alive(worker) and not r["left"]


def test_stop_finds_a_worker_that_left_the_group_by_its_worktree(tmp_path):
    wt = tmp_path / "wt"
    wt.mkdir()
    w = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], cwd=wt, start_new_session=True)
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], cwd=tmp_path, start_new_session=True)
    try:
        r = taskstop.stop({"pid": 0, "worktree": str(wt)}, grace=3)
        w.wait(timeout=5)
        assert w.pid in r["signalled"] and other.pid not in r["signalled"] and _alive(other.pid)
    finally:
        other.kill()
        other.wait()
        if w.poll() is None:
            w.kill()
