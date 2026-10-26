"""The load report and placement (stage O4, 2026-10-26): each device's numbers over the link verb `load`, the fixed
idle/capable rule, and a suite placed here, on an idle device, or here after the queue when the cable fails."""
import json
import subprocess

import pytest

from fieldkit import admission as A
from fieldkit import orchestrate_load as L


def row(name, os_="LINUX", free=1, cpu=5.0, temp=50.0, hot=70.0, pytest_=True, git=True, me=False, ok=True):
    r = {"name": name, "me": me, "ok": ok, "os": os_, "cpu_pct": cpu, "temp_c": temp, "hot": hot,
         "ram_free": 8 * 2 ** 30, "ram_total": 16 * 2 ** 30, "heavy": [],
         "gate": {"slots": 2, "offered": 2, "held": 2 - free, "free": free, "waiting": 0},
         "can": {"pytest": pytest_, "git": git}}
    r["idle"] = L.idle(r)
    return r


def test_heavy_jobs_are_found_by_their_command_line():
    procs = [(10, ["/usr/bin/python3", "-m", "pytest", "-q"]), (11, ["go", "test", "./..."]),
             (12, ["python", "-m", "fieldkit", "mutate", "f.py"]), (13, ["bash"]), (14, ["go", "build"]),
             (15, ["C:\\Go\\bin\\go.exe", "test", "./..."]), (16, ["pytest-3", "tests"])]
    found = {h["pid"]: h["what"] for h in L.heavy_jobs(procs)}
    assert found == {10: "full suite", 11: "go test", 12: "mutate", 15: "go test", 16: "full suite"}


@pytest.mark.parametrize("kw,expect", [
    ({}, True), ({"free": 0}, False), ({"cpu": 50.0}, False), ({"cpu": 49.9}, True),
    ({"temp": 70.0}, False), ({"temp": None}, True)])
def test_idle_rule(kw, expect):
    assert row("w", **kw)["idle"] is expect


def test_choose_same_os_capable_most_free_then_lowest_cpu():
    rows = [row("here", me=True, free=5), row("win", "WINDOWS", free=2), row("busy", free=0),
            row("nopytest", pytest_=False), row("a", free=1, cpu=20), row("b", free=1, cpu=10),
            row("down", ok=False)]
    assert L.choose(rows, "LINUX")["name"] == "b"
    assert L.choose(rows, "LINUX", any_os=True)["name"] == "win"   # most free slots, any OS
    assert L.choose([row("here", me=True)], "LINUX") is None        # never itself


def test_load_all_asks_every_other_device(monkeypatch):
    from fieldkit.peers import link
    monkeypatch.setattr(link, "household", lambda: {"me": "vaio", "devices": [
        {"name": "vaio"}, {"name": "win", "os": "WINDOWS"}, {"name": "old", "os": "OSX"}]})
    asked = []

    def call(req, device=None):
        asked.append((req["verb"], device))
        return {"ok": True, "load": row("x", "WINDOWS")} if device == "win" else {"ok": False,
                                                                                    "error": "unknown verb 'load'"}
    rows = L.load_all(call, here=row("vaio"))
    assert asked == [("load", "win"), ("load", "old")]
    assert [(r["name"], r["ok"], r["me"]) for r in rows] == [("vaio", True, True), ("win", True, False),
                                                             ("old", False, False)]
    assert "NO REPORT: unknown verb 'load'" in L.line(rows[2])
    assert " IDLE " in L.line(rows[1]) and "slots 1/2" in L.line(rows[1])


def test_report_reads_the_gate_and_the_rule(monkeypatch):
    gate = {"slots": 2, "offered": 1, "held": 1, "free": 0, "waiting": [{}], "hot": 70.0}
    r = L.report(cpu_fn=lambda: 3.0, temp_fn=lambda: 72.0, procs=[(5, ["go", "test"])], gate=gate)
    assert r["gate"] == {"slots": 2, "offered": 1, "held": 1, "free": 0, "waiting": 1}
    assert r["heavy"] == [{"what": "go test", "pid": 5}]
    assert r["idle"] is False and r["temp_c"] == 72.0 and r["can"]["python"]
    json.dumps(r)                                             # it crosses the link as JSON


def test_load_verb_is_read_only_and_answers_on_the_lan(monkeypatch):
    from fieldkit.peers import link
    assert "load" not in link.JOB_VERBS
    monkeypatch.setattr(L, "report", lambda: {"host": "h", "idle": True})

    class V:
        def check(self, req):
            return None
    assert link.handle({"verb": "load"}, V(), via="lan") == {"ok": True, "load": {"host": "h", "idle": True}}


# ---- placement -----------------------------------------------------------------------------------------------------
@pytest.fixture
def full_here(monkeypatch, tmp_path):
    """This computer's gate is full (one slot, held)."""
    monkeypatch.setattr(A, "base_slots", lambda local=None: 1)
    monkeypatch.setattr(A, "temperature", lambda: None)
    s = A.try_take(1, "test", "the other suite")
    monkeypatch.setattr(L, "export_tree", lambda repo: ((tmp_path / "x" / "tree"), "ee" * 20))
    (tmp_path / "x" / "tree").mkdir(parents=True)
    yield
    s.release()


def fakes(status="pass", said="sent", os_="WINDOWS", result=True):
    log = {"sent": [], "recorded": [], "here": 0}

    def send(kind, device, tree, project, argv=None, wall=60):
        log["sent"].append((kind, device, argv))
        return "run-X", said, {"ok": True, "digest": "d" * 64}

    def here():
        log["here"] += 1
        return 0
    kw = dict(send=send, wait_fn=lambda jid, s: {"status": status}, run_here=here, say=lambda s: None,
              record=lambda *a: log["recorded"].append(a),
              result_fn=lambda jid, p, d: ({"status": status, "os": os_, "evidence": "exit 0 in 9 s\n3 passed"}
                                           if result else None))
    return log, kw


def test_free_slot_runs_here_and_sends_nothing(monkeypatch):
    monkeypatch.setattr(A, "temperature", lambda: None)
    log, kw = fakes()
    r = L.place(".", rows=[row("vaio", me=True), row("win", "WINDOWS", free=2)], any_os=True, **kw)
    assert (r["where"], r["how"]) == ("here", "free slot") and log["sent"] == [] and log["here"] == 1


def test_full_here_places_on_the_idle_device_and_records_its_os(full_here):
    log, kw = fakes()
    r = L.place(".", ["tests/test_a.py"], any_os=True, rows=[row("vaio", me=True), row("win", "WINDOWS")], **kw)
    assert (r["where"], r["how"], r["status"]) == ("win", "placed", "pass")
    assert log["sent"] == [("run", "win", ["admit", "suite", "tests/test_a.py"])]
    assert log["here"] == 0
    check, status, _s, key, note = log["recorded"][0]
    assert (check, status, key) == ("placed-suite@WINDOWS", "pass", "ee" * 20) and "placed on win" in note


def test_same_os_result_counts_as_this_os_full_suite(full_here):
    log, kw = fakes(os_="LINUX")
    L.place(".", rows=[row("vaio", me=True), row("w2")], **kw)
    assert log["recorded"][0][0] == "full-suite"


def test_os_specific_suite_stays_on_its_os(full_here):
    log, kw = fakes()
    r = L.place(".", rows=[row("vaio", me=True), row("win", "WINDOWS")], **kw)    # no --any-os
    assert log["sent"] == [] and r["where"] == "here" and "no other device is idle and capable" in r["note"]


def test_a_failed_remote_suite_is_the_answer_not_rerun(full_here):
    log, kw = fakes(status="fail")
    r = L.place(".", rows=[row("vaio", me=True), row("w2")], **kw)
    assert (r["where"], r["status"], r["rc"]) == ("w2", "fail", 1) and log["here"] == 0
    assert log["recorded"][0][1] == "fail"


@pytest.mark.parametrize("kw,why", [
    ({"said": "not taken: a job is running here already"}, "did not take it"),
    ({"result": False}, "no signed result"),
    ({"status": "not-run"}, "could not run it")])
def test_cable_trouble_fails_closed_to_running_here(full_here, kw, why):
    log, f = fakes(**kw)
    r = L.place(".", rows=[row("vaio", me=True), row("w2")], **f)
    assert r["where"] == "here" and r["how"] == "queued here" and why in r["note"]
    assert log["here"] == 1 and log["recorded"] == []


def test_an_exception_while_placing_runs_here(full_here):
    log, f = fakes()

    def boom(*a, **k):
        raise ConnectionError("cable pulled")
    f["send"] = boom
    r = L.place(".", rows=[row("vaio", me=True), row("w2")], **f)
    assert r["where"] == "here" and "cable pulled" in r["note"] and log["here"] == 1


def test_export_tree_is_exactly_the_testcost_tree(tmp_path):
    from fieldkit import testcost
    repo = tmp_path / "r"
    repo.mkdir()
    g = lambda *a: subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)
    g("init", "-q")
    (repo / ".gitignore").write_text("junk/\n")
    (repo / "a.py").write_text("x = 1\n")
    g("add", "-A")
    g("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "c")
    (repo / "new.py").write_text("y = 2\n")                   # untracked: the tests see it, so it travels
    (repo / "junk").mkdir()
    (repo / "junk" / "big.bin").write_text("no")              # ignored: it does not
    d, key = L.export_tree(repo, tmp_path / "out")
    assert key == testcost.tree_key(repo)
    assert sorted(p.relative_to(d).as_posix() for p in d.rglob("*") if p.is_file()) == [".gitignore", "a.py",
                                                                                         "new.py"]
    assert subprocess.run(["git", "-C", str(repo), "status", "--porcelain"], capture_output=True,
                          text=True).stdout.count("??") == 1          # nothing staged by the export
