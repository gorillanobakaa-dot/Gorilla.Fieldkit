"""fieldkit thermal ensure: the governor is started when down, proven by its lease; the brake pauses guarded jobs;
heavy work refuses while the governor is down (2026-11-02: the laptop went down with the governor not running)."""
import json
import time

import pytest

from fieldkit.thermal import brake, keeper

pytestmark = pytest.mark.real_keeper


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def test_up_when_the_lease_is_fresh_and_the_process_lives(tmp_path):
    brake.lease_write(tmp_path, pid=4242, now=1000.0)
    started = []
    r = keeper.ensure(tmp_path, start=lambda: started.append(1), now=lambda: 1001.0, alive=lambda pid: True)
    assert r["ok"] and r["state"] == "up" and not started


def test_a_dead_governor_is_started_and_proven_by_its_new_lease(tmp_path):
    brake.lease_write(tmp_path, pid=4242, now=1000.0)              # fresh lease, dead process
    c = Clock()

    def start():
        brake.lease_write(tmp_path, pid=777, now=c.t)              # the new governor takes the lease
        return 777
    r = keeper.ensure(tmp_path, start=start, sleep=c.sleep, now=c, alive=lambda pid: pid == 777, say=lambda *_: None)
    assert r["ok"] and r["state"] == "started" and r["pid"] == 777


def test_a_stale_lease_counts_as_down(tmp_path):
    brake.lease_write(tmp_path, pid=4242, now=1000.0)
    assert keeper.state(tmp_path, now=1000.0 + brake.LEASE_S + 1, alive=lambda pid: True)["up"] is False


def test_a_governor_that_never_takes_its_lease_is_down_not_up(tmp_path):
    c = Clock()
    r = keeper.ensure(tmp_path, start=lambda: 1, sleep=c.sleep, now=c, alive=lambda pid: True, wait_s=5)
    assert not r["ok"] and r["state"] == "down" and "lease" in r["error"]


def test_heavy_work_refuses_on_windows_while_the_governor_is_down():
    down = lambda **k: {"ok": False, "state": "down", "error": "x"}
    up = lambda **k: {"ok": True, "state": "up"}
    assert "governor is down" in keeper.refusal("win32", down)
    assert keeper.refusal("win32", up) == ""


def test_heavy_work_refuses_on_linux_too_and_never_on_macos():
    down = lambda **k: {"ok": False, "state": "down", "error": "x"}
    up = lambda **k: {"ok": True, "state": "up"}
    assert "governor is down" in keeper.refusal("linux", down)
    assert keeper.refusal("linux", up) == ""
    assert keeper.refusal("darwin", down) == ""


def test_a_zombie_governor_is_not_alive():
    import subprocess, sys, time
    from fieldkit.peers.peers import pid_alive
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    time.sleep(1)                                   # exited, not yet waited on: a zombie
    try:
        assert sys.platform != "linux" or not pid_alive(p.pid)
    finally:
        p.wait()


def test_the_governor_says_the_cpu_temperature(tmp_path):
    from fieldkit.thermal import watch
    said = []
    watch.forever(sensor=(lambda: 61.5, False), ticks=2, sleep=lambda s: __import__("time").sleep(0.4), folder=tmp_path,
                  say=said.append)
    assert any("61.5 C" in m for m in said), said


def test_the_lease_is_replaced_atomically_never_seen_empty(tmp_path, monkeypatch):
    calls = []
    real = brake.os.replace
    monkeypatch.setattr(brake.os, "replace", lambda a, b: calls.append((a, b)) or real(a, b))
    brake.lease_write(tmp_path, pid=1, now=5.0, temp=60.0)
    assert len(calls) == 1 and json.loads((tmp_path / "machine-governor.json").read_text())["temp_c"] == 60.0
    assert [f.name for f in tmp_path.iterdir()] == ["machine-governor.json"]


def test_the_lease_is_written_before_the_sensor_proof(tmp_path, monkeypatch):
    from fieldkit.buildh import buildrun
    from fieldkit.thermal import watch
    seen = []
    def proof(say, **k):
        seen.append(brake.machine_alive(tmp_path))
        return None, None, False
    monkeypatch.setattr(buildrun, "thermal_sensor", proof)
    assert watch.forever(folder=tmp_path, say=lambda *_: None) == 3
    assert seen == [True]


def test_the_guard_refuses_when_the_governor_is_down(monkeypatch):
    from fieldkit.thermal import buildguard
    monkeypatch.setattr(keeper, "refusal", lambda *a, **k: "the machine thermal governor is down")
    with pytest.raises(buildguard.BuildRefused, match="governor is down"):
        with buildguard._fan_and_governor(lambda *_: None, "t", None, False, None, None):
            pass


def test_the_goal_supervisor_starts_no_job_while_the_governor_is_down(monkeypatch, tmp_path):
    from fieldkit.autopilot import goal
    monkeypatch.setattr(goal, "_thermal_refusal", lambda: "the machine thermal governor is down")
    monkeypatch.setattr(goal, "lock", lambda now: (True, ""))
    monkeypatch.setattr(goal, "unlock", lambda: None)
    monkeypatch.setattr(goal, "GOALS", tmp_path)
    monkeypatch.setattr(goal, "unfinished", lambda: ["g"])
    stepped, said = [], []
    monkeypatch.setattr(goal, "step", lambda *a, **k: stepped.append(1) or {"status": "active"})
    assert goal.supervise(["g"], once=True, say=said.append, hold=False) == 0
    assert not stepped and any("no job starts" in s for s in said)


class P:
    def __init__(self, pid):
        self.pid, self.state = pid, "run"

    def suspend(self):
        self.state = "paused"

    def resume(self):
        self.state = "run"


def test_the_brake_pauses_guarded_jobs_when_hot_and_resumes_them_when_cool():
    kids = {10: [P(11), P(12)]}
    b = keeper.Brake(holds=lambda: [{"pid": 10}], children=lambda pid: kids[pid], say=lambda *_: None)
    b(90)
    assert all(p.state == "run" for p in kids[10])                 # a small step down pauses nothing
    b(30)                                                          # the floor (>= HOT)
    assert all(p.state == "paused" for p in kids[10])
    b(30)
    assert len(b.paused) == 2                                      # idempotent
    b(85)
    assert all(p.state == "run" for p in kids[10]) and not b.paused


def test_the_governor_lever_is_the_brake_and_it_resumes_on_exit(tmp_path):
    from fieldkit.thermal import watch
    calls = []
    orig = keeper.Brake.resume
    keeper.Brake.resume = lambda self, pct=100: calls.append(pct) or 0
    try:
        watch.forever(target=67, say=lambda *_: None, sensor=(lambda: 50.0, False), ticks=1, sleep=lambda s: None,
                      folder=tmp_path)
    finally:
        keeper.Brake.resume = orig
    assert calls, "the governor left without resuming paused jobs"


def test_the_keeper_runs_at_logon_every_5_minutes_and_on_resume():
    x = keeper.task_xml(r"C:\py\pythonw.exe", r"C:\fk", user=r"PC\someone")
    assert r"<UserId>PC\someone</UserId></LogonTrigger>" in x and "<LogonType>InteractiveToken</LogonType>" in x
    assert "<RunLevel>LeastPrivilege</RunLevel>" in x
    assert "<LogonTrigger>" in x and "PT5M" in x and "EventID=107" in x and "EventID=507" in x
    assert "-m fieldkit thermal ensure" in x and "IgnoreNew" in x
    u = keeper.systemd_units("/usr/bin/python3", "/opt/fk")
    assert "KillMode=process" in u[f"{keeper.UNIT}.service"]      # else systemd kills the governor with the oneshot
    assert "OnUnitActiveSec=5min" in u[f"{keeper.UNIT}.timer"] and "thermal ensure" in u[f"{keeper.UNIT}.service"]


def test_install_uses_schtasks_on_windows_and_a_user_timer_on_linux(tmp_path):
    ran = []
    run = lambda cmd, **k: ran.append(cmd) or type("R", (), {"returncode": 0, "stdout": "ok", "stderr": ""})()
    assert keeper.install(run=run, platform="win32", scratch=tmp_path)["ok"]
    assert ran[-1][:4] == ["schtasks", "/Create", "/F", "/TN"] and (tmp_path / "keeper-task.xml").is_file()
    assert keeper.install(run=run, platform="linux", home=tmp_path)["ok"]
    assert ran[-1][-1] == f"{keeper.UNIT}.timer" and (tmp_path / ".config/systemd/user" / f"{keeper.UNIT}.timer").is_file()
    assert not keeper.install(run=run, platform="darwin")["ok"]
