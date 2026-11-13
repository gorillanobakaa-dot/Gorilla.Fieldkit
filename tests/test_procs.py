"""`fieldkit procs check|reap` (fieldkit/core/procs.py): one verdict per process tree Fieldkit started, Linux + Windows.

CI: 0 zombies after a 2000-child run (ubuntu-latest), 0 orphans after a contained run that leaves a grandchild
(both systems; on windows-latest the job object kills it)."""
import ast
import json
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import psutil
import pytest

from fieldkit.core import contain, liveness, procs

PY = sys.executable
ROOT = Path(__file__).resolve().parents[1]
NOW = time.time()


def _tab(*rows):
    """rows: (pid, ppid, state, ctime) -> the table procs.table() returns."""
    return {pid: (pp, st, ct) for pid, pp, st, ct in rows}


def _beacon(pid=10, ct=100.0, members=None, kind="runbook", rid="r1"):
    return {"kind": kind, "id": rid, "pid": pid, "ctime": ct, "label": "", "members": members or {}}


def _load(share=0.0, age=60):
    return lambda pid: (share, age)


def test_a_clean_tree_is_ok():
    res = procs.check(records=[_beacon()], entries=[], tab=_tab((10, 1, "S", 100.0), (11, 10, "S", 101.0)),
                      load=_load())
    assert res["status"] == "OK" and res["trees"][0]["verdict"] == "OK" and res["zombies"] == 0


def test_zombie_children_of_a_supervisor_are_counted_and_named():
    rows = [(10, 1, "S", 100.0)] + [(100 + i, 10, "Z", 101.0) for i in range(3)]
    res = procs.check(records=[_beacon()], entries=[], tab=_tab(*rows), load=_load())
    t = res["trees"][0]
    assert t["verdict"] == "ZOMBIES" and t["zombies"] == 3 and res["status"] == "FINDINGS"


def test_more_than_fifty_zombies_is_a_hog():
    rows = [(10, 1, "S", 100.0)] + [(100 + i, 10, "Z", 101.0) for i in range(51)]
    res = procs.check(records=[_beacon()], entries=[], tab=_tab(*rows), load=_load())
    assert res["trees"][0]["verdict"] == "HOG" and "51 zombie" in res["trees"][0]["why"]


def test_a_core_pinned_for_ten_minutes_is_a_hog_but_a_young_one_is_not():
    tab = _tab((10, 1, "S", 100.0))
    old = procs.check(records=[_beacon()], entries=[], tab=tab, load=_load(0.8, 3600))
    young = procs.check(records=[_beacon()], entries=[], tab=tab, load=_load(0.8, 60))
    assert old["trees"][0]["verdict"] == "HOG" and young["trees"][0]["verdict"] == "OK"


def test_members_whose_owner_is_gone_are_orphans_and_the_kill_list_is_pid_plus_ctime():
    tab = _tab((11, 1, "S", 101.0), (12, 1, "S", 102.0))                       # owner 10 is not in the table
    rec = _beacon(members={"11": {"ctime": 101.0}, "12": {"ctime": 102.0}, "13": {"ctime": 103.0}})
    res = procs.check(records=[rec], entries=[], tab=tab, load=_load())
    t = res["trees"][0]
    assert t["verdict"] == "ORPHAN" and t["kill"] == [(11, 101.0), (12, 102.0)]      # 13 is gone: not listed


def test_a_reused_pid_is_never_a_member():
    tab = _tab((11, 1, "S", 555.0))                                             # same number, another creation time
    rec = _beacon(members={"11": {"ctime": 101.0}})
    assert procs.check(records=[rec], entries=[], tab=tab, load=_load())["status"] == "OK"


def test_a_registered_service_is_never_an_orphan_but_a_plain_one_whose_session_died_is():
    tab = _tab((20, 1, "S", 200.0), (21, 1, "S", 210.0))
    svc = {"pid": 20, "ctime": 200.0, "cls": "link-relay", "owner_pid": 99, "owner_ctime": 5.0, "name": "python"}
    plain = {"pid": 21, "ctime": 210.0, "cls": "pytest", "owner_pid": 99, "owner_ctime": 5.0, "name": "python"}
    res = procs.check(records=[], entries=[svc, plain], tab=tab, load=_load())
    verdicts = {t["id"]: t["verdict"] for t in res["trees"]}
    assert verdicts == {"20": "OK", "21": "ORPHAN"}


def test_reap_stops_only_orphans_by_pid_and_ctime_and_names_what_it_cannot_fix(tmp_path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_LIVENESS_DIR", str(tmp_path))
    rows = [(10, 1, "S", 100.0)] + [(100 + i, 10, "Z", 101.0) for i in range(3)] + [(30, 1, "S", 300.0)]
    res = procs.check(records=[_beacon(), _beacon(pid=40, ct=400.0, members={"30": {"ctime": 300.0}}, rid="r2")],
                      entries=[], tab=_tab(*rows), load=_load())
    stopped = []
    done = procs.reap(res, stopper=lambda p, c: stopped.append((p, c)) or (True, "stopped"), own_reaper=lambda: 7)
    assert stopped == [(30, 300.0)] and done["reaped"] == 7
    assert len(done["left"]) == 1 and "PID 10" in done["left"][0]          # a foreign supervisor's zombies: said, not faked


def test_reap_never_touches_its_own_process():
    res = {"findings": [{"verdict": "ORPHAN", "kill": [(os.getpid(), 1.0)], "id": "x", "kind": "k", "why": "w",
                         "zombies": 0, "mine": False, "pid": 1}]}
    stopped = []
    done = procs.reap(res, stopper=lambda p, c: stopped.append(p) or (True, "x"), own_reaper=lambda: 0)
    assert stopped == [] and done["stopped"] == []


def test_the_real_table_sees_this_process_and_a_live_orphan_is_stopped(tmp_path, monkeypatch):
    """Both systems: a real member whose beacon owner is dead is found as ORPHAN and `reap` stops it."""
    monkeypatch.setenv("FIELDKIT_LIVENESS_DIR", str(tmp_path))
    member = subprocess.Popen([PY, "-c", "import time; time.sleep(60)"])
    try:
        mct = contain.ctime(member.pid)
        dead = subprocess.Popen([PY, "-c", "pass"])
        dct = contain.ctime(dead.pid) or 1.0
        dead.wait()
        rec = _beacon(pid=dead.pid, ct=dct, members={str(member.pid): {"ctime": mct}}, rid="real")
        res = procs.check(records=[rec], entries=[])
        t = res["trees"][0]
        assert t["verdict"] == "ORPHAN" and [k[0] for k in t["kill"]] == [member.pid]
        procs.reap(res, own_reaper=lambda: 0)
        member.wait(timeout=15)
        assert member.returncode is not None
    finally:
        if member.poll() is None:
            member.kill()


def test_a_contained_run_that_leaves_a_grandchild_ends_with_zero_orphans(tmp_path):
    """The windows-latest gate (job object, KILL_ON_JOB_CLOSE) and the ubuntu one (group kill): 0 orphans after it."""
    pidf = tmp_path / "gc.pid"
    spin = tmp_path / "spin.py"
    spin.write_text(f"import os, pathlib, time\npathlib.Path(r'{pidf}').write_text(str(os.getpid()))\n"
                    "end = time.time() + 60\nwhile time.time() < end:\n    time.sleep(0.05)\n")
    parent = tmp_path / "parent.py"
    parent.write_text(f"import subprocess, sys, time, pathlib\nsubprocess.Popen([sys.executable, r'{spin}'])\n"
                      f"while not pathlib.Path(r'{pidf}').exists():\n    time.sleep(0.05)\n")
    with liveness.beacon("procs-test", "t1") as b:
        o = contain.run([PY, str(parent)], label="step", deadline_s=60)
        gpid = int(pidf.read_text())
        deadline = time.time() + 10
        while time.time() < deadline and procs._alive(procs.table(), gpid, contain.ctime(gpid)):
            time.sleep(0.1)
        res = procs.check(records=[json.loads(b.path.read_text())], entries=[])
    assert o.leftovers or o.status == "ok"
    assert [t for t in res["findings"] if t["verdict"] == "ORPHAN"] == []
    assert contain.ctime(gpid) is None


SUBREAPER_DRIVER = textwrap.dedent('''
    import json, os, sys
    from fieldkit.core import contain, liveness, procs
    N = int(sys.argv[1])
    worker = ("import os, time\\n"
              f"for i in range({N}):\\n"
              "    if os.fork() == 0:\\n"
              "        if os.fork() == 0:\\n"
              "            time.sleep(0.02); os._exit(0)\\n"
              "        os._exit(0)\\n"
              "    os.wait()\\n"
              "time.sleep(2)\\n")
    contain.POLL_S = 0.2
    with liveness.beacon("procs-test", "big") as b:
        r = contain.run([sys.executable, "-c", worker], label="big")
        res = procs.check(records=[json.loads(b.path.read_text())], entries=[])
    print(json.dumps({"status": r.status, "zombies": res["zombies"], "own": contain.zombie_children(),
                      "verdicts": [t["verdict"] for t in res["trees"]]}))
''')


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="zombies exist on Linux only")
def test_zero_zombies_after_a_2000_child_run(tmp_path):
    """The ubuntu-latest gate: a worker that leaves 2000 re-parented orphans ends with 0 zombies under the supervisor,
    and `procs check` says so. Its own process: the subreaper is permanent for a process."""
    drv = tmp_path / "drv.py"
    drv.write_text(SUBREAPER_DRIVER)
    env = {**os.environ, "FIELDKIT_LIVENESS_DIR": str(tmp_path / "lv"), "PYTHONPATH": str(ROOT)}
    r = subprocess.run([PY, str(drv), "2000"], capture_output=True, text=True, timeout=240, env=env)
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out["status"] == "ok" and out["own"] == 0 and out["zombies"] == 0, (out, r.stderr[-500:])
    assert "ZOMBIES" not in out["verdicts"] and "HOG" not in out["verdicts"]


def test_the_cli_prints_the_verdict_and_exits_3_on_a_finding(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(procs, "check", lambda: {"status": "FINDINGS", "zombies": 2, "trees": [
        {"kind": "runbook", "id": "r", "pid": 1, "age_s": 60, "cpu_share": 0.1, "members": 0, "zombies": 2,
         "verdict": "ZOMBIES", "why": "2 uncollected", "kill": [], "mine": False}],
        "findings": [{"kind": "runbook", "id": "r", "pid": 1, "age_s": 60, "cpu_share": 0.1, "members": 0,
                      "zombies": 2, "verdict": "ZOMBIES", "why": "2 uncollected", "kill": [], "mine": False}]})
    assert procs.main(["check"]) == 3
    out = capsys.readouterr().out
    assert "procs: ZOMBIES runbook r" in out and "NEXT:" in out
    monkeypatch.setattr(procs, "check", lambda: {"status": "OK", "zombies": 0, "trees": [], "findings": []})
    assert procs.main(["check", "--json"]) == 0


# ---- every spawn site of a Fieldkit supervisor goes through the container or is recorded ----------------------------
SUPERVISOR_DIRS = ("autopilot", "emerge", "lifecycle", "thermal", "peers")
#: a direct Popen that is allowed, and why (everything else must use contain / core.proc, or call procs.record)
DIRECT_OK = {
    "autopilot/quiet.py": "the quiet holder ends when the PID it waits for ends; it holds nothing",
    "autopilot/run.py": "the owner's own windows (Claude login terminal, Antigravity): the owner closes them",
    "thermal/sensors.py": "the die-rise burn test: its busy threads are killed by the same function that starts them",
    "peers/link.py": "start_session: an agent session the owner sees and archives; the argument is an injected seam",
}


def _spawns(path):
    """-> line numbers of `subprocess.Popen(...)` / `Popen(...)` / `popen(...)` calls (not default arguments)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            f = n.func
            name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
            if name in ("Popen", "popen"):
                out.append(n.lineno)
    return out


def test_every_supervisor_spawn_is_contained_or_recorded():
    bad = []
    for d in SUPERVISOR_DIRS:
        for p in sorted((ROOT / "fieldkit" / d).glob("*.py")):
            rel = f"{d}/{p.name}"
            if _spawns(p) and rel not in DIRECT_OK and "procs.record(" not in p.read_text(encoding="utf-8"):
                bad.append(f"{rel}: lines {_spawns(p)}")
    assert not bad, "a Fieldkit supervisor starts a process outside the container and does not record it: " + str(bad)


def test_the_detached_services_record_themselves_so_procs_check_sees_them():
    for rel in ("thermal/keeper.py", "peers/link_relay.py"):
        assert "procs.record(" in (ROOT / "fieldkit" / rel).read_text(encoding="utf-8"), rel


def test_the_hog_and_the_keeper_run_procs_check():
    assert "procs.pass_line" in (ROOT / "fieldkit" / "hog" / "__init__.py").read_text(encoding="utf-8")
    assert "procs.pass_line" in (ROOT / "fieldkit" / "lifecycle" / "watch.py").read_text(encoding="utf-8")


def test_record_writes_the_registry_by_pid_and_ctime(tmp_path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_HOG_DIR", str(tmp_path))
    from fieldkit.hog import registry
    p = subprocess.Popen([PY, "-c", "import time; time.sleep(20)"])
    try:
        assert procs.record(p.pid, "link-relay", "test") is True
        e = registry.load()[0]
        assert e["pid"] == p.pid and e["cls"] == "link-relay" and psutil.Process(p.pid).create_time() - e["ctime"] < 0.1
    finally:
        p.kill()
        p.wait()
