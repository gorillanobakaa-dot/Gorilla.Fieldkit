"""leakgate-status: where a (running) leak gate is, from the run's own files, and how hot the machine has been since it
started (2026-10-06: the hourly sitrep of a 4-hour release run had the run folder, the PID and the 141 expected runs
typed in). Also the gate's own plan file and the thermal watcher's log read back with its dates."""
import datetime as dt
import json
import os

import pytest

from fieldkit import cli
from fieldkit.buildh import task
from fieldkit.core import settings
from fieldkit.leakgate import gate, scenarios as sc, status
from fieldkit.thermal import watchlog


# ------------------------------------------------------------------------------------------- the gate's plan
def test_the_plan_names_every_run_the_gate_will_start():
    quick = gate.planned_runs(1, True)
    release = gate.planned_runs(3, False)
    assert quick[0] == "startup-idle-r0-direct" and len(set(quick)) == len(quick)
    assert gate.modes_for("startup-idle", True) == ["direct", "proxied", "dns-controlled"]
    assert gate.modes_for("startup-idle", False) == ["direct", "proxied", "dns-controlled", "poisoned"]
    assert gate.modes_for(sc.GRACEFUL[0], False) == ["direct", "proxied"]
    assert len(release) == 3 * sum(len(gate.modes_for(s[0], False)) for s in sc.SCENARIOS)
    assert gate.planned_runs(2, True, only={"newtab"}) == [f"newtab-r{r}-{m}" for r in (0, 1)
                                                         for m in ("direct", "proxied", "dns-controlled")]


# ------------------------------------------------------------------------------------------- the thermal log
def _log(tmp_path, lines, end):
    p = tmp_path / "thermal_watch.log"
    p.write_text("\n".join(f"{t}   {c:.1f} C   peak  {c:.1f}   load {l:3d}%   0 compiler(s)" for t, c, l in lines) + "\n",
                 encoding="utf-8")
    os.utime(p, (end.timestamp(), end.timestamp()))
    return p


def test_the_log_gets_its_dates_back_across_midnight_and_stops_at_a_gap(tmp_path):
    end = dt.datetime(2026, 10, 7, 0, 30, 30)
    p = _log(tmp_path, [("09:00:00", 50.0, 90),                 # an earlier day: a gap of hours, never dated
                        ("23:58:00", 60.0, 80), ("23:59:00", 72.0, 90), ("00:00:30", 65.0, 70), ("00:30:00", 40.0, 10)], end)
    rows = watchlog.read(p)
    assert [r[0] for r in rows] == [dt.datetime(2026, 10, 6, 23, 58), dt.datetime(2026, 10, 6, 23, 59),
                                    dt.datetime(2026, 10, 7, 0, 0, 30), dt.datetime(2026, 10, 7, 0, 30)]
    s = watchlog.summary(rows, since=dt.datetime(2026, 10, 6, 23, 59), hot=70)
    assert s["readings"] == 3 and s["max"] == 72.0 and s["max_at"].endswith("23:59:00") and s["above"] == 1
    assert s["now"]["celsius"] == 40.0 and s["recent"]["minutes"] == 30
    assert watchlog.summary(rows, until=dt.datetime(2026, 10, 6, 23, 58, 30))["readings"] == 1
    assert watchlog.summary(rows, since=dt.datetime(2026, 10, 8)) is None
    assert "no thermal watcher readings" in watchlog.lines(None)[0]
    assert watchlog.read(tmp_path / "missing.log") == []


# ------------------------------------------------------------------------------------------- the run's status
@pytest.fixture
def runs(tmp_path, monkeypatch):
    monkeypatch.setattr(task, "STATE", tmp_path / "bh")
    monkeypatch.setattr(settings, "ROOT", tmp_path / "fk")
    root = tmp_path / "bh" / "leakgate" / "t1"
    (root / "20300101-080000" / "runs").mkdir(parents=True)
    (root / "not-a-run").mkdir()
    return root


def _start(d, names):
    for n in names:
        (d / "runs" / f"profile-{n}").mkdir()


def test_progress_comes_from_the_plan_file_and_the_profiles(runs, tmp_path):
    d = runs / "20300101-080000"
    planned = gate.planned_runs(1, True, only={"newtab", "home"})
    (d / gate.PLAN_FILE).write_text(json.dumps({"pid": os.getpid(), "runs": planned, "repeat": 1, "quick": True}),
                                    encoding="utf-8")
    _start(d, planned[:4])
    s = status.status("t1", thermal_log=None)
    assert s["run"].endswith("20300101-080000") and s["expected"] == 6 and s["started_runs"] == 4
    assert s["plan_source"] == gate.PLAN_FILE and s["pid"] == os.getpid() and s["result"] is None
    assert s["not_yet"] == planned[4:]
    text = "\n".join(status.lines(s))
    assert "4 of 6 scenario runs started (66%)" in text
    (d / "test-results.json").write_text(json.dumps({"FINAL_RESULT": "PASS"}), encoding="utf-8")
    assert "FINISHED: FINAL_RESULT PASS" in "\n".join(status.lines(status.status("t1")))


def test_an_older_run_uses_artifacts_or_the_launcher_log_and_says_so(runs, tmp_path):
    d = runs / "20300101-080000"
    launcher = tmp_path / "launcher"
    launcher.mkdir()
    head = f"leakgate: build 1 (157.0), packets ON, repeat 2, quick durations -> C:\\x\\state\\{d.name}\r\n"
    (launcher / "t1-20300101-080000.log").write_bytes(b"\xff\xfe" + head.encode("utf-16-le"))
    pl = status.plan(d, launcher)
    assert pl["total"] == len(gate.planned_runs(2, True)) and "launcher log" in pl["source"]
    (d / "artifacts.json").write_text(json.dumps({"a-r0-direct": {}, "a-r0-proxied": {}}), encoding="utf-8")
    assert status.plan(d, launcher)["total"] == 2
    (d / "artifacts.json").unlink()
    assert status.plan(d, tmp_path / "nowhere")["total"] is None


def test_interrupted_run_retains_sensors_and_has_no_final_verdict(runs):
    d = runs / "20300101-080000"
    planned = gate.planned_runs(1, True, only={"newtab"})
    gate._publish_json(d / gate.PLAN_FILE, {"pid": os.getpid(), "runs": planned})
    _start(d, planned)
    folder = d / gate.CHECKPOINT_DIR
    folder.mkdir()
    sensors = {"page_results": [{"page": "newtab"}], "page_requests": [{"path": "/"}],
               "doh_queries": [{"name": "example.invalid"}]}
    offsets = dict.fromkeys(sensors, 0)
    offsets = gate._scenario_checkpoint(folder, planned[0], 1, 2.75, [], {"files": []}, sensors, offsets)
    sensors["page_results"].append({"page": "next"})
    gate._scenario_checkpoint(folder, planned[1], 4, 1, [], {}, sensors, offsets)
    (folder / f"{planned[2]}.tmp").write_text("{", encoding="utf-8")
    first = json.loads((folder / f"{planned[0]}.json").read_text(encoding="utf-8"))
    second = json.loads((folder / f"{planned[1]}.json").read_text(encoding="utf-8"))
    assert first["elapsed_s"] == 2.75
    assert first["observations_since_previous_checkpoint"]["doh_queries"] == [{"name": "example.invalid"}]
    assert second["observations_since_previous_checkpoint"] == {
        "page_results": [{"page": "next"}], "page_requests": [], "doh_queries": []}
    report = status.status("t1")
    assert report["captured_runs"] == planned[:2] and report["result"] is None
    assert "2 scenario runs captured" in "\n".join(status.lines(report))


def test_restart_rejects_changed_inputs_gaps_corruption_and_finished_runs(runs):
    from fieldkit.leakgate import restart

    work = runs / "20300101-080000"
    folder = work / gate.CHECKPOINT_DIR
    folder.mkdir()
    names = gate.planned_runs(1, True, only={"newtab"})
    sensor_file = work / "runs" / "packets.pcap"
    sensor_file.write_bytes(b"fixture capture")
    streams = {"page_results": [{"page": "newtab"}], "page_requests": [], "doh_queries": []}
    artifact = {"pcap": str(sensor_file)}
    gate._scenario_checkpoint(folder, names[0], 90, 3, [], artifact, streams, dict.fromkeys(streams, 0))
    path = folder / f"{names[0]}.json"
    row = json.loads(path.read_text(encoding="utf-8"))
    receipt = {"row": restart.digest(row), "files": restart.files(work, artifact, gate._sha)}
    marker = {"context": "matching runner and inputs", "state": "ready", "records": {names[0]: receipt}}
    gate._publish_json(work / restart.FILE, marker)

    def recover(key=marker["context"]):
        return restart.prefix(work, names, key, gate.CHECKPOINT_DIR, gate._sha)

    assert recover() == [(row, receipt)]
    assert recover("changed policy or executable") == []
    running = dict(marker, state="running", owner={"pid": 10, "created": 1})
    gate._publish_json(work / restart.FILE, running)
    assert recover() == []
    assert restart.prefix(work, names, marker["context"], gate.CHECKPOINT_DIR, gate._sha,
                          recovered_cleanup=lambda *_: False) == []
    assert restart.prefix(work, names, marker["context"], gate.CHECKPOINT_DIR, gate._sha,
                          recovered_cleanup=lambda folder, owner: folder == work and owner["pid"] == 10) == [(row, receipt)]
    gate._publish_json(work / restart.FILE, dict(marker, records={names[1]: receipt}))
    assert recover() == []
    gate._publish_json(work / restart.FILE, marker)
    sensor_file.write_bytes(b"changed capture")
    assert recover() == []
    sensor_file.write_bytes(b"fixture capture")
    gate._publish_json(work / "test-results.json", {"FINAL_RESULT": "FAIL"})
    assert recover() == []


def test_windows_network_snapshot_does_not_produce_proof_on_query_error(monkeypatch):
    from fieldkit.leakgate import restart

    snapshot = dict(addresses=[{"IPAddress": "192.0.2.2"}], routes=[], dns=["fixture"], dnsLog=True,
                    profiles=[], firewall=[], firewallRules={}, proxy=[], packets=["fixture status"])
    monkeypatch.setattr(restart.fkproc, "check_output", lambda *args, **kw: json.dumps(snapshot).encode())
    first = restart.windows_network()
    assert first == restart.digest(snapshot)
    snapshot["dns"] = ["other"]
    assert first != restart.windows_network()
    snapshot.pop("packets")
    assert restart.windows_network() is None
    monkeypatch.setattr(restart.fkproc, "check_output", lambda *args, **kw: b"unreadable")
    assert restart.windows_network() is None


def test_crash_recovery_refuses_the_original_process_and_its_remaining_worker(tmp_path, monkeypatch):
    import psutil
    from types import SimpleNamespace
    from fieldkit.leakgate import restart

    previous = {"pid": 10, "created": 100}
    current = SimpleNamespace(create_time=lambda: 100)
    monkeypatch.setattr(psutil, "Process", lambda pid: current)
    assert not restart.owner_gone(tmp_path, previous)
    current.create_time = lambda: 200
    worker = SimpleNamespace(info={"cmdline": ["worker", str(tmp_path / "runs" / "capture")]})
    monkeypatch.setattr(psutil, "process_iter", lambda *a, **kw: iter([worker]))
    assert not restart.owner_gone(tmp_path, previous)
    monkeypatch.setattr(psutil, "process_iter", lambda *a, **kw: iter([]))
    assert restart.owner_gone(tmp_path, previous)


def test_a_gate_that_is_gone_or_a_reused_pid_is_not_running():
    started = dt.datetime.now()
    assert status.alive(os.getpid(), started) is True
    assert status.alive(os.getpid(), started - dt.timedelta(days=30)) is False   # a PID now held by a newer process
    assert status.alive(None, started) is None


def test_the_command_reads_the_newest_run_and_the_owner_thermal_log(runs, tmp_path, monkeypatch, capsys):
    from fieldkit.buildh import buildrun
    d = runs / "20300101-080000"
    _start(d, ["startup-idle-r0-direct"])
    owner = tmp_path / "owner"
    (owner / "state").mkdir(parents=True)
    _log(owner / "state", [("08:10:00", 55.0, 50)], dt.datetime(2030, 1, 1, 8, 10, 30))
    monkeypatch.setattr(task, "load", lambda tid: {"id": tid})
    monkeypatch.setattr(buildrun, "_owner_root", lambda t: str(owner))
    assert cli.main(["build-harness", "leakgate-status", "t1"]) == 0
    out = capsys.readouterr().out
    assert "1 of ? scenario runs started" in out and "1 readings" in out and "max 55 C" in out
    assert cli.main(["build-harness", "leakgate-status", "t1", "run=19990101-000000"]) == 2
