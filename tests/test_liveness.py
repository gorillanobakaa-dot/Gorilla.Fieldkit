"""The process liveness protocol (fieldkit/core/contain.py, fieldkit/core/liveness.py): no harness-started process is
orphaned, stalls silently or blocks its supervisor. Each named failure mode (liveness.MODES) has its test here.

The reproduction of 2026-10-26: a verifier criterion's shell starts a CPU-spinning grandchild and dies. Before: the
orphan survives, holds the output pipe, and the supervisor blocks. After: the container stops it, the criterion is
TIMEOUT (or the leftover is stopped), and the supervisor goes on."""
import json
import os
import subprocess
import sys
import textwrap
import threading
import time

import psutil
import pytest

from fieldkit.core import contain, liveness

PY = sys.executable


def _alive(pid):
    try:
        p = psutil.Process(pid)
        return p.status() != psutil.STATUS_ZOMBIE
    except psutil.Error:
        return False


def _wait_file(p, s=20):
    t = time.time() + s
    while time.time() < t:
        if p.exists() and p.read_text().strip():
            return int(p.read_text().strip())
        time.sleep(0.1)
    raise AssertionError(f"{p} never written")


def _gone(pid, s=10):
    t = time.time() + s
    while time.time() < t:
        if not _alive(pid):
            return True
        time.sleep(0.1)
    return False


def _hard_kill(pid):
    try:
        psutil.Process(pid).kill()
    except psutil.Error:
        pass


def orphan_maker(tmp_path, then="exit"):
    """A script: start a CPU-spinning grandchild (writes its PID), then exit (its parent dies) or hang."""
    pidf = tmp_path / "grandchild.pid"
    spin = tmp_path / "spin.py"
    spin.write_text(textwrap.dedent(f"""
        import os, pathlib, time
        pathlib.Path(r"{pidf}").write_text(str(os.getpid()))
        print("spinning", flush=True)
        end = time.time() + 120      # bounded: a mutant that breaks containment never leaks a spinner for long
        while time.time() < end:
            pass
    """))
    parent = tmp_path / "parent.py"
    parent.write_text(textwrap.dedent(f"""
        import subprocess, sys, time, pathlib
        subprocess.Popen([sys.executable, r"{spin}"])
        while not pathlib.Path(r"{pidf}").exists():
            time.sleep(0.05)
        print("parent leaves", flush=True)
        {"time.sleep(90)" if then == "hang" else "sys.exit(0)"}
    """))
    return parent, pidf


# ---- FM-1 / FM-3: the reproduction -------------------------------------------------------------------------------
def test_before_the_orphan_holds_the_pipe_and_a_bare_communicate_blocks(tmp_path):
    """The old shape (subprocess.run, capture_output): the parent is gone, the grandchild holds stdout, the reader
    does not return. Proven with a thread that is still blocked after 4 s; the orphan is then stopped by hand."""
    parent, pidf = orphan_maker(tmp_path)
    done = threading.Event()

    def old():
        subprocess.run([PY, str(parent)], capture_output=True)
        done.set()
    threading.Thread(target=old, daemon=True).start()
    gpid = _wait_file(pidf)
    try:
        assert not done.wait(4), "the old runner returned: the reproduction no longer reproduces"
        assert _alive(gpid), "the orphan should still be running in the old shape"
    finally:
        _hard_kill(gpid)
    assert done.wait(20)


def test_reproduction_orphan_grandchild(tmp_path):
    """After: the same script in a container returns at once, its orphan is stopped and named as a leftover."""
    parent, pidf = orphan_maker(tmp_path)
    t0 = time.time()
    c = contain.Container("repro", deadline_s=60)
    try:
        o = c.run([PY, str(parent)])
        gpid = _wait_file(pidf)
        assert o.status == "ok" and o.rc == 0
        assert time.time() - t0 < 30
        assert "parent leaves" in o.out
        assert gpid in o.leftovers, (o.as_dict(), gpid)
        assert _gone(gpid, 3), "run() returned with the orphan still running (before close)"
    finally:
        c.close()
        _hard_kill(int(pidf.read_text()) if pidf.exists() else 0)


def test_close_stops_what_is_still_inside_and_says_so(tmp_path):
    c = contain.Container("close")
    p = c.popen([PY, "-c", "import time; time.sleep(60)"])
    try:
        left = c.close()                    # a venv launcher may sit in front of the real interpreter: both are named
        assert p.pid in left and all(_gone(x) for x in left)
    finally:
        _hard_kill(p.pid)


def test_a_hung_criterion_with_an_orphan_is_timeout_and_the_supervisor_goes_on(tmp_path, monkeypatch):
    """The verifier criterion of 2026-10-26, end to end through goal.verify: the shell hangs while its grandchild
    spins; the wall deadline stops the whole container, the verdict is TIMEOUT (not FAIL), verify returns."""
    from fieldkit.autopilot import goal
    monkeypatch.setattr(goal, "GOALS", tmp_path / "goals")
    from fieldkit.thermal import buildguard           # the container's deadline is under test, not the thermal guard
    monkeypatch.setattr(buildguard, "STATUS", tmp_path / "no-fan-control")
    parent, pidf = orphan_maker(tmp_path, then="hang")
    g = {"id": "g", "objective": "o", "cwd": str(tmp_path),
         "criteria": [{"id": "go-test", "run": f'& "{PY}" "{parent}"' if os.name == "nt" else f'"{PY}" "{parent}"',
                       "timeout": 8}]}
    goal.new(g)
    gg, st = goal.load("g")
    t0 = time.time()
    with liveness.beacon("goal-supervisor", "test") as b:
        res = goal.verify("g", gg, st)
        assert not b.rec["steps"], "the step stayed open after the verdict"
    gpid = _wait_file(pidf)
    try:
        assert res[0]["verdict"] == "TIMEOUT", res[0]
        assert "wall deadline" in res[0]["why"] and res[0]["deadline"] == 8
        assert time.time() - t0 < 60
        assert _gone(gpid), "the orphan outlived the deadline"
        assert st["criteria"]["go-test"]["verdict"] == "TIMEOUT"
        line = json.loads((goal.gdir("g") / "verdicts.jsonl").read_text().splitlines()[-1])
        assert line["verdict"] == "TIMEOUT" and line["seconds"] >= 7
        assert "TIMEOUT" in (goal.gdir("g") / "events.log").read_text()
    finally:
        _hard_kill(gpid)


def test_a_grandchild_holding_output_never_blocks(tmp_path):
    """FM-3: output goes to files; a leftover child holding the handle cannot block the reader."""
    parent, pidf = orphan_maker(tmp_path)
    c = contain.Container("pipes", keep_leftovers=True)
    t0 = time.time()
    o = c.run([PY, str(parent)], deadline_s=60)
    gpid = _wait_file(pidf)
    try:
        assert o.status == "ok" and time.time() - t0 < 30
        assert gpid in o.leftovers and _alive(gpid)      # kept on request, and still the run returned
    finally:
        c.kill()
        c.close()
        _hard_kill(gpid)
    assert _gone(gpid)


def test_the_runner_used_by_pipelines_and_emerge_stops_leftovers(tmp_path):
    from fieldkit.core.proc import Runner
    parent, pidf = orphan_maker(tmp_path)
    r = Runner(tmp_path / "st").run([PY, str(parent)], timeout=60)
    gpid = _wait_file(pidf)
    try:
        assert r.ok and "left running" in r.stderr and _gone(gpid)
        assert Runner(tmp_path / "st").started() == {}
    finally:
        _hard_kill(gpid)


def test_the_runner_timeout_stops_the_whole_tree(tmp_path):
    from fieldkit.core.proc import Runner
    parent, pidf = orphan_maker(tmp_path, then="hang")
    r = Runner(tmp_path / "st").run([PY, str(parent)], timeout=5)
    gpid = _wait_file(pidf)
    try:
        assert r.returncode == 124 and "timed out" in r.stderr and _gone(gpid)
    finally:
        _hard_kill(gpid)


def test_a_runbook_step_is_contained(tmp_path):
    from fieldkit.autopilot import runbook
    parent, pidf = orphan_maker(tmp_path, then="hang")
    cmd = f'& "{PY}" "{parent}"' if os.name == "nt" else f'"{PY}" "{parent}"'
    rc, secs = runbook._run_step({"id": "s", "run": cmd, "cwd": str(tmp_path), "timeout": 6}, tmp_path / "s.log",
                                 lambda m: None)
    gpid = _wait_file(pidf)
    try:
        assert rc == 124 and secs < 60 and _gone(gpid)
        assert "TIMEOUT after 6 s" in (tmp_path / "s.log").read_text()
    finally:
        _hard_kill(gpid)


# ---- FM-9: the silent stall --------------------------------------------------------------------------------------
SILENT = "import sys, time; print('partial: started the commit', flush=True); time.sleep(90)"


def test_a_silent_child_is_a_stall_with_its_tail_kept_and_reported(tmp_path, monkeypatch):
    """No output and no CPU for idle_s: stopped as STALL long before the wall deadline, tail on disk, ROOM told."""
    said = []
    monkeypatch.setattr(liveness, "room_line", lambda verb, text: said.append((verb, text)))
    t0 = time.time()
    with liveness.beacon("autopilot", "silent") as b:
        o = contain.run([PY, "-c", SILENT], label="silent relief", deadline_s=600, idle_s=3)
        assert b.rec["steps"] == {}
    assert o.status == "stalled" and o.stalled and o.timed_out and o.rc == 124
    assert time.time() - t0 < 30, "a 3 s stall must be stopped within seconds, not at the 600 s deadline"
    assert "STALL: no progress for 3 s" in o.why and o.killed
    assert "partial: started the commit" in o.out
    tail = open(o.tail_file, encoding="utf-8").read()
    assert "partial: started the commit" in tail and "STALL" in tail
    assert said and said[0][0] == "stop" and "STALL silent relief" in said[0][1]
    acts = (liveness.state_dir() / "actions.jsonl").read_text()
    assert '"stall"' in acts


def test_a_busy_but_quiet_child_is_not_a_stall(tmp_path):
    """A test suite printing nothing for minutes still uses CPU: that is progress, not a stall."""
    o = contain.run([PY, "-c", "import time\nend = time.time() + 6\nwhile time.time() < end: pass"],
                    label="busy", deadline_s=60, idle_s=3)
    assert o.status == "ok", o.as_dict()


def test_autopilot_claude_reports_a_silent_stall(tmp_path, monkeypatch):
    """The relief run of 2026-10-26 (90 min, rc 124, empty output), with a fake silent claude and a 3 s idle limit."""
    from fieldkit.autopilot import hardlines, run
    fake = tmp_path / "silent_claude.py"
    fake.write_text("import sys, json, time\nsys.stdin.read()\n"
                    "print(json.dumps({'type': 'system', 'subtype': 'init'}), flush=True)\ntime.sleep(90)\n")
    monkeypatch.setattr(hardlines, "STATE", tmp_path / "ap")
    monkeypatch.setenv("FIELDKIT_CLAUDE_EXE", str(fake))
    monkeypatch.setenv("FIELDKIT_IDLE_MINUTES", "0.05")
    monkeypatch.setattr(liveness, "room_line", lambda *a: None)
    t0 = time.time()
    out = run.claude("commit staged set 2", cwd=str(tmp_path), timeout=600)
    assert out["stalled"] and out["timed_out"] and out["rc"] == 124 and not out["ok"]
    assert time.time() - t0 < 40
    assert '"init"' in out["stdout"] and '"init"' in open(out["tail_file"], encoding="utf-8").read()
    assert liveness.current() is None, "the run's own beacon was not closed"


def test_stream_json_result_is_parsed():
    from fieldkit.autopilot import run
    text = '{"type":"system"}\n{"type":"assistant"}\n{"type":"result","result":"done","total_cost_usd":0.1}\n'
    assert run.parse_result(text)["result"] == "done"
    assert run.parse_result('{"result": "x"}')["result"] == "x"
    assert run.parse_result("no json") is None


def test_a_step_without_progress_is_stalled_in_the_check():
    r = rec(steps={"2": {"label": "claude relief", "started": 0, "deadline_at": None, "progress_at": 400,
                         "idle_s": 600}},
            members={"20": {"ctime": 2.0, "step": "2", "name": "claude.exe"}})
    live = {(10, 1.0), (20, 2.0)}
    assert liveness.assess(r, now=400 + 600 + liveness.GRACE_S - 1, same=same_for(live)) == []
    f = liveness.assess(r, now=400 + 600 + liveness.GRACE_S + 1, same=same_for(live))
    assert f[0]["state"] == "STALLED" and "no progress" in f[0]["line"] and f[0]["kill"] == [[20, 2.0]]


# ---- FM-5: the guard (pre-commit) --------------------------------------------------------------------------------
def test_the_guard_stops_a_hung_command_at_its_deadline(tmp_path):
    parent, pidf = orphan_maker(tmp_path, then="hang")
    t0 = time.time()
    rc = contain.wait_inherit([PY, str(parent)], "hung suite", 5)
    gpid = _wait_file(pidf)
    try:
        assert rc == 124 and time.time() - t0 < 40 and _gone(gpid)
    finally:
        _hard_kill(gpid)


def test_the_guard_deadline_comes_from_the_measured_suite(monkeypatch):
    from fieldkit import testcost
    from fieldkit.core import preflight
    monkeypatch.setattr(testcost, "budget", lambda check: 400.0)
    assert preflight.command_deadline("test") == 1200.0
    monkeypatch.setattr(testcost, "budget", lambda check: None)
    assert preflight.command_deadline("test") == preflight.GUARD_CAP_S
    assert preflight.command_deadline("build", 90) == 90.0


# ---- deadlines ---------------------------------------------------------------------------------------------------
def test_deadline_for_is_never_none_and_is_capped():
    assert liveness.deadline_for() == liveness.HARD_CAP_S
    assert liveness.deadline_for([10, 20]) == liveness.FLOOR_S            # 3 x 20 = 60, raised to the floor
    assert liveness.deadline_for([200, 400]) == 1200
    assert liveness.deadline_for([5000]) == liveness.HARD_CAP_S
    assert liveness.deadline_for([10], explicit=99999) == liveness.HARD_CAP_S
    assert liveness.deadline_for([10], explicit=42) == 42
    assert liveness.deadline_for([None, 0]) == liveness.HARD_CAP_S


def test_a_criterion_deadline_is_derived_from_its_history(tmp_path, monkeypatch):
    from fieldkit.autopilot import goal
    monkeypatch.setattr(goal, "GOALS", tmp_path / "goals")
    (tmp_path / "goals" / "g").mkdir(parents=True)
    (tmp_path / "goals" / "g" / "verdicts.jsonl").write_text(
        "\n".join(json.dumps(x) for x in [{"id": "c", "verdict": "PASS", "seconds": 150},
                                         {"id": "c", "verdict": "TIMEOUT", "seconds": 3600},
                                         {"id": "other", "verdict": "PASS", "seconds": 999}]))
    assert goal.history("g", "c") == [150.0]
    seen = {}

    def fake(cmd, **kw):
        seen["timeout"] = kw["timeout"]
        return subprocess.CompletedProcess(cmd, 0, "ok", "")
    r = goal.run_check({"id": "c", "run": "x"}, str(tmp_path), run=fake, hist=goal.history("g", "c"))
    assert seen["timeout"] == 450 and r["verdict"] == "PASS" and r["deadline"] == 450


def test_a_fake_timeout_is_timeout_not_fail(tmp_path):
    from fieldkit.autopilot import goal

    def fake(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, kw["timeout"])
    r = goal.run_check({"id": "c", "run": "x", "timeout": 5}, str(tmp_path), run=fake)
    assert r["verdict"] == "TIMEOUT" and r["rc"] == 124


# ---- the beacon and the check ------------------------------------------------------------------------------------
def rec(**kw):
    base = {"kind": "goal-supervisor", "id": "x", "pid": 10, "ctime": 1.0, "heartbeat": 1000.0, "stale_s": 180,
            "steps": {}, "members": {}}
    base.update(kw)
    return base


def same_for(live):
    return lambda pid, ct: (pid, ct) in live


def test_a_fresh_run_is_ok():
    assert liveness.assess(rec(), now=1010, same=same_for({(10, 1.0)})) == []


def test_a_stale_heartbeat_with_a_live_owner_is_stalled():
    f = liveness.assess(rec(), now=1000 + 181, same=same_for({(10, 1.0)}))
    assert [x["state"] for x in f] == ["STALLED"] and "heartbeat 181 s" in f[0]["line"] and f[0]["kill"] == []


def test_a_step_past_its_deadline_is_stalled_and_stopped():
    """FM-2 / FM-4: a fresh heartbeat (the timer thread lives) does not hide a stuck step."""
    r = rec(steps={"3": {"label": "verify go-test", "started": 900, "deadline_at": 950}},
            members={"20": {"ctime": 2.0, "step": "3", "name": "go.exe"}, "21": {"ctime": 3.0, "step": "4"}})
    live = {(10, 1.0), (20, 2.0)}
    f = liveness.assess(r, now=1000 + 11, same=same_for(live))
    assert [x["state"] for x in f] == ["STALLED"] and f[0]["kill"] == [[20, 2.0]]
    assert "verify go-test" in f[0]["line"]
    assert liveness.assess(r, now=950 + liveness.GRACE_S - 1, same=same_for(live)) == []


def test_members_of_a_dead_owner_are_orphans_and_stopped():
    """FM-6: the owner (supervisor) is gone; its members still run."""
    r = rec(members={"20": {"ctime": 2.0, "step": "1", "name": "go.exe"}})
    f = liveness.assess(r, now=1001, same=same_for({(20, 2.0)}))
    assert f[0]["state"] == "ORPHAN" and f[0]["kill"] == [[20, 2.0]] and "go.exe 20" in f[0]["line"]
    stopped = []
    res = liveness.check(act=True, same=same_for({(20, 2.0)}), records=[r], now=1001,
                         stopper=lambda pid, ct: (stopped.append((pid, ct)), (True, "stopped"))[1],
                         inbox_add=lambda *a, **k: stopped.append(("inbox", a[0])))
    assert res["status"] == "FINDINGS" and stopped[0] == (20, 2.0)
    assert stopped[-1][0] == "inbox" and stopped[-1][1].startswith("liveness: stopped 1 of 1")


def test_a_member_that_outlived_its_step_is_an_orphan():
    r = rec(steps={}, members={"20": {"ctime": 2.0, "step": "1", "name": "go.exe"}})
    f = liveness.assess(r, now=1001, same=same_for({(10, 1.0), (20, 2.0)}))
    assert [x["state"] for x in f] == ["ORPHAN"] and "outlived" in f[0]["line"]


def test_a_dead_run_with_nothing_left_is_forgotten(tmp_path):
    p = tmp_path / "r.json"
    p.write_text("{}")
    res = liveness.check(act=True, records=[rec(_path=str(p))], same=same_for(set()), now=1001, inbox_add=False)
    assert res["status"] == "OK" and not p.exists()


def test_check_without_act_stops_nothing():
    r = rec(members={"20": {"ctime": 2.0, "step": "1"}})
    stopped = []
    res = liveness.check(act=False, records=[r], same=same_for({(20, 2.0)}), now=1001,
                         stopper=lambda *a: stopped.append(a) or (True, ""))
    assert res["status"] == "FINDINGS" and stopped == [] and res["acted"] == []


# ---- FM-7: PID reuse; never the owner's processes ----------------------------------------------------------------
def test_a_reused_pid_is_never_stopped(tmp_path):
    """A recorded member whose PID now belongs to another process (creation time differs) is left alone: here the
    'other process' is a real live one, and stop_pid must refuse it."""
    p = subprocess.Popen([PY, "-c", "import time; time.sleep(60)"])
    try:
        real = contain.ctime(p.pid)
        ok, why = liveness.stop_pid(p.pid, real - 100.0)
        assert ok and "reused" in why and _alive(p.pid)
        r = rec(pid=1, ctime=1.0, members={str(p.pid): {"ctime": real - 100.0, "step": "1"}})
        res = liveness.check(act=True, records=[r], now=1001, inbox_add=False)
        assert res["acted"] == [] and _alive(p.pid)
    finally:
        p.kill()
        p.wait()


def test_only_recorded_members_are_ever_candidates():
    """The owner's processes are never in a beacon: a check over beacons cannot name them. The owner process itself
    is never a kill target either, even when stalled."""
    r = rec(steps={"1": {"label": "s", "started": 0, "deadline_at": 1}}, members={})
    f = liveness.assess(r, now=99999, same=lambda pid, ct: True)
    assert all(10 not in [k[0] for k in x.get("kill") or []] for x in f)


def test_stop_pid_stops_a_matching_member():
    p = subprocess.Popen([PY, "-c", "import time; time.sleep(60)"])
    try:
        ok, why = liveness.stop_pid(p.pid, contain.ctime(p.pid))
        assert ok and "stopped" in why
        p.wait(10)
    finally:
        if p.poll() is None:
            p.kill()


# ---- FM-4: the lock ----------------------------------------------------------------------------------------------
def test_a_lock_held_by_a_reused_pid_is_taken_over(tmp_path, monkeypatch):
    from fieldkit.autopilot import goal
    monkeypatch.setattr(goal, "GOALS", tmp_path)
    other = subprocess.Popen([PY, "-c", "import time; time.sleep(60)"])
    try:
        goal._wj(tmp_path / ".supervisor.lock", {"pid": other.pid, "ctime": contain.ctime(other.pid) - 50,
                                                 "heartbeat": time.time()})
        ok, _ = goal.lock()
        assert ok, "a live PID with another creation time is not the holder"
        goal._wj(tmp_path / ".supervisor.lock", {"pid": other.pid, "ctime": contain.ctime(other.pid),
                                                 "heartbeat": time.time()})
        ok, why = goal.lock()
        assert not ok and "another supervisor" in why
        assert goal._rj(tmp_path / ".supervisor.lock")["pid"] == other.pid
    finally:
        other.kill()
        other.wait()
    ok, _ = goal.lock()
    assert ok and goal._rj(tmp_path / ".supervisor.lock")["ctime"] == contain.ctime(os.getpid())


# ---- FM-8: the inside watchdog -----------------------------------------------------------------------------------
def test_the_beacon_thread_stops_an_overdue_step_from_inside(tmp_path):
    """An elevated owner never needs another session: its beacon stops its own overdue step."""
    clock = [1000.0]
    b = liveness.Beacon("t", "w", now=lambda: clock[0], grace=5)
    b.start()
    killed = []
    try:
        sid = b.set_step("stuck", 10, killer=lambda why: killed.append(why) or [123])
        clock[0] += 14
        b.beat()
        assert killed == []
        clock[0] += 2
        b.beat()
        assert killed == ["watchdog"] and sid not in b.rec["steps"]
        acts = (liveness.state_dir() / "actions.jsonl").read_text()
        assert '"watchdog"' in acts and "123" in acts
    finally:
        b.close()
    assert not b.path.exists() and liveness.current() is None


def test_a_beacon_records_its_containers_members(tmp_path):
    parent, pidf = orphan_maker(tmp_path)
    with liveness.beacon("runbook", "rb") as b:
        o = contain.run([PY, str(parent)], label="step", deadline_s=60)
        data = json.loads(b.path.read_text())
    gpid = _wait_file(pidf)
    try:
        assert str(gpid) in data["members"] and data["members"][str(gpid)]["step"] is not None
        assert data["steps"] == {} and o.leftovers
    finally:
        _hard_kill(gpid)


def test_nested_beacons_restore_the_outer_one():
    with liveness.beacon("goal-supervisor", "a") as a:
        with liveness.beacon("runbook", "b"):
            assert liveness.current() is not a
        assert liveness.current() is a
    assert liveness.current() is None


# ---- the command -------------------------------------------------------------------------------------------------
def test_cli_exit_codes(capsys):
    assert liveness.main(["check"]) == liveness.EXIT_OK
    assert "liveness: OK" in capsys.readouterr().out
    assert liveness.main(["check", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "OK"
    assert liveness.main(["nope"]) == liveness.EXIT_USAGE
    assert liveness.main([]) == liveness.EXIT_USAGE
    assert liveness.main(["modes"]) == 0
    out = capsys.readouterr().out
    assert all(m["id"] in out for m in liveness.MODES)


def test_cli_finds_an_orphan_and_exits_3(capsys):
    r = rec(pid=1, ctime=1.0, members={})
    p = liveness.state_dir() / "runs" / "dead.json"
    child = subprocess.Popen([PY, "-c", "import time; time.sleep(60)"])
    try:
        r["members"] = {str(child.pid): {"ctime": contain.ctime(child.pid), "step": "1", "name": "python"}}
        p.write_text(json.dumps(r))
        assert liveness.main(["check"]) == liveness.EXIT_FINDINGS
        assert "ORPHAN" in capsys.readouterr().out and _alive(child.pid)
    finally:
        child.kill()
        child.wait()


def test_every_mode_names_an_existing_test():
    names = set(globals())
    for m in liveness.MODES:
        f, _, t = m["test"].partition("::")
        assert f == "tests/test_liveness.py" and t in names, m


def test_preflight_lists_the_liveness_step():
    from fieldkit.core import preflight
    assert "fieldkit.core.liveness:preflight_check" in preflight.STEPS


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="PR_SET_CHILD_SUBREAPER is Linux only")
def test_linux_subreaper_is_set():
    assert contain.enable_subreaper() is True


def test_doctor_rows_are_ok():
    rows = liveness.doctor()
    assert all(ok for _n, ok, _d in rows), rows


# ---- FM-10: busy spin with no children ---------------------------------------------------------------------------
SPIN = "exec('while True: pass')"


def test_a_lone_silent_spinner_is_stopped_as_busy_no_children(tmp_path, monkeypatch):
    """One process, a core, no output for busy_s: stopped long before the wall deadline, the reason named."""
    monkeypatch.setattr(liveness, "room_line", lambda verb, text: None)
    t0 = time.time()
    o = contain.run([PY, "-c", SPIN], label="verify go-test spin", deadline_s=600, busy_s=3)
    assert o.status == "stalled" and o.rc == 124 and "BUSY-NO-CHILDREN" in o.why
    assert time.time() - t0 < 30


def test_a_spinner_that_prints_is_not_busy_no_children(tmp_path):
    prog = ("import time\nend = time.time() + 6\nwhile time.time() < end:\n"
            "    print('x', flush=True)\n    for _ in range(3000000): pass")
    o = contain.run([PY, "-c", prog], label="noisy", deadline_s=60, busy_s=3)
    assert o.status == "ok", o.as_dict()


def test_busy_for_reads_the_step_label_from_the_local_settings(monkeypatch):
    from fieldkit.core import settings
    monkeypatch.setattr(settings, "local_settings", lambda: {"liveness": {"busy_steps": {
        "verify go-test*": {"busy_minutes": 5, "on_busy": "warn"}}}})
    assert contain.busy_for("verify go-test ./x") == (300.0, "warn")
    assert contain.busy_for("verify pytest") == (None, None)
