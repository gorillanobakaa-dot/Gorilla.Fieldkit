"""probe on Linux (2026-10-10): the browser is started and stopped by PID with its children, as on Windows."""
import os
import shutil
import subprocess
import sys
import time

import pytest

from fieldkit.buildh import probe

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the POSIX side; Windows stops with taskkill (tested there)")


def _gone(pid, within=10):
    import psutil
    end = time.time() + within
    while time.time() < end:
        try:
            if psutil.Process(pid).status() == psutil.STATUS_ZOMBIE:
                return True
        except psutil.NoSuchProcess:
            return True
        time.sleep(0.1)
    return False


def test_stop_tree_stops_the_process_and_its_children_and_nothing_else():
    bystander = subprocess.Popen(["sleep", "300"])
    p = subprocess.Popen(["sh", "-c", "sleep 300 & sleep 300 & wait"], start_new_session=True)
    try:
        import psutil
        time.sleep(0.3)
        kids = [c.pid for c in psutil.Process(p.pid).children(recursive=True)]
        assert len(kids) == 2
        probe.stop_tree(p.pid)
        assert all(_gone(k) for k in kids + [p.pid])
        assert bystander.poll() is None                                # not ours: still running
        probe.stop_tree(p.pid)                                         # already gone: not an error
    finally:
        bystander.kill()
        p.kill()


def test_processes_in_finds_only_what_runs_from_the_copy(tmp_path):
    copy = tmp_path / "gprobe_app_x"
    copy.mkdir()
    shutil.copy(shutil.which("sleep"), copy / "sleep")
    mine = subprocess.Popen([str(copy / "sleep"), "300"])
    other = subprocess.Popen(["sleep", "300"])
    try:
        time.sleep(0.3)
        assert probe.processes_in(copy) == [mine.pid]
        assert probe.stop_in(copy) == [mine.pid] and _gone(mine.pid) and other.poll() is None
    finally:
        mine.kill()
        other.kill()


def test_launch_reads_the_probe_lines_and_stops_the_browser(tmp_path):
    app = tmp_path / "gprobe_app_y"
    app.mkdir()
    fx = app / "firefox"
    fx.write_text("#!/bin/sh\necho 'GPROBE hello'\necho 'GPROBE-DONE'\nexec sleep 300\n", encoding="utf-8", newline="\n")
    os.chmod(fx, 0o755)
    r = probe.launch(app, timeout=60)
    assert r["done"] and r["lines"] == ["hello"] and r["seconds"] < 30
    assert probe.processes_in(app) == []                              # the browser this started is stopped
