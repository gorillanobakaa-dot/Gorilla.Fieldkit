"""R12: goals use both computers; a silent supervisor is an alarm; a false "stalled" is impossible."""
import threading
import time

import pytest

from fieldkit.autopilot import goal, goal_peer, hardlines, inbox, runbook
from fieldkit.lifecycle import tasks


@pytest.fixture(autouse=True)
def iso(tmp_path, monkeypatch):
    st = tmp_path / "ap"
    st.mkdir()
    monkeypatch.setattr(hardlines, "STATE", st)
    monkeypatch.setattr(hardlines, "STOP_FILE", st / "STOP")
    monkeypatch.setattr(inbox, "INBOX", st / "inbox")
    monkeypatch.setattr(goal, "GOALS", tmp_path / "goals")
    monkeypatch.setattr(runbook, "JOBS", tmp_path / "jobs")
    monkeypatch.setenv("FIELDKIT_AUTOPILOT_STATE", str(st))
    monkeypatch.setattr(goal_peer, "gate_busy", lambda: False)
    monkeypatch.setattr(goal_peer, "_HOLDERS", {})
    monkeypatch.setattr(goal_peer, "_SEEN", {})
    monkeypatch.setattr(goal_peer, "_LAST", {"t": None, "note": ""})
    from fieldkit.core import eta
    monkeypatch.setattr(eta, "HISTORY", tmp_path / "history.jsonl")
    monkeypatch.setattr(goal, "_WORKERS", {})


def pjob(jid, tmp_path, **kw):
    return dict({"id": jid, "brief": f"do {jid}", "cwd": str(tmp_path), "touches": [f"{jid}.txt"]}, **kw)


def mk(tmp_path, gid="g1", jobs=()):
    goal.new({"id": gid, "objective": "o", "cwd": str(tmp_path), "seed_jobs": list(jobs),
              "criteria": [{"id": "c1", "run": "exit 1"}]})
    _, st = goal.load(gid)
    st.update(needs_plan=False, rounds=1)
    goal.save_state(gid, st)


class Workers:
    """run_job stand-in: a job named in hold blocks until its event is set."""

    def __init__(self, hold=None):
        self.hold, self.ran = hold or {}, []

    def __call__(self, job_dir, brief, cwd, **kw):
        jid = kw["job_id"].split("-", 1)[1]
        self.ran.append(jid)
        if jid in self.hold:
            assert self.hold[jid].wait(timeout=10)
        return {"status": "done"}


class Cable:
    """A fake peer: records `task start --on`, answers a ledger the test edits."""

    def __init__(self, monkeypatch):
        self.sent, self.led = [], {}
        monkeypatch.setattr(tasks, "ledger", lambda: self.led)
        monkeypatch.setattr(tasks, "ingest", lambda items=None: [])

    def start(self, brief_path, on=None, **kw):
        self.sent.append({"on": on, "brief": open(brief_path, encoding="utf-8").read(), **kw})
        tid = f"T-fake-{len(self.sent)}"
        self.led[tid] = {"id": tid, "status": "started", "on": on}
        return {"id": tid, "status": "started"}


def test_a_heavy_job_goes_to_the_peer_while_the_slot_is_held_and_its_result_comes_back(tmp_path, monkeypatch):
    mk(tmp_path, jobs=[pjob("j1", tmp_path, heavy=True), pjob("j2", tmp_path, heavy=True)])
    cable, ev = Cable(monkeypatch), threading.Event()
    w = Workers(hold={"j1": ev})
    monkeypatch.setattr(goal, "ROUND_WAIT", 0.3)
    peer = lambda: {"name": "vaio"}
    out = goal.step("g1", run_job=w, say=lambda m: None, peer=peer, start_task=cable.start)
    _, st = goal.load("g1")
    assert goal_peer.holder() == "g1/j1"                       # j1 holds the laptop's slot
    assert [c["on"] for c in cable.sent] == ["vaio"]            # j2 started on the peer, not beside j1
    assert "goal.json" in cable.sent[0]["brief"] and "do j2" in cable.sent[0]["brief"]
    assert st["jobs"]["j2"]["status"] == "running" and st["jobs"]["j2"]["remote"]["on"] == "vaio"
    assert out["status"] == "active"
    cable.led["T-fake-1"]["status"] = "done"                    # the peer reported
    ev.set()
    for _ in range(3):
        goal.step("g1", run_job=w, say=lambda m: None, peer=peer, start_task=cable.start,
                  claude=lambda *a, **k: {"is_error": True, "result": "no planner in this test"})
    _, st = goal.load("g1")
    assert st["jobs"]["j1"]["status"] == "verified" and st["jobs"]["j2"]["status"] == "verified"
    assert w.ran == ["j1"] and goal_peer.holder() == ""         # j2 never ran here


def test_a_windows_only_job_waits_for_the_laptop_and_says_why(tmp_path, monkeypatch):
    mk(tmp_path, jobs=[pjob("j1", tmp_path, heavy=True), pjob("j2", tmp_path, heavy=True, windows_only=True)])
    cable, ev, said = Cable(monkeypatch), threading.Event(), []
    w = Workers(hold={"j1": ev})
    monkeypatch.setattr(goal, "ROUND_WAIT", 0.2)
    goal.step("g1", run_job=w, say=said.append, peer=lambda: {"name": "vaio"}, start_task=cable.start)
    _, st = goal.load("g1")
    ev.set()
    assert cable.sent == [] and st["jobs"]["j2"]["status"] == "queued"
    assert any("windows-only" in m and "g1/j1" in m for m in said)


def test_no_idle_peer_means_the_job_waits_with_the_reason(tmp_path, monkeypatch):
    mk(tmp_path, jobs=[pjob("j1", tmp_path, heavy=True), pjob("j2", tmp_path, heavy=True)])
    ev, said = threading.Event(), []
    w = Workers(hold={"j1": ev})
    monkeypatch.setattr(goal, "ROUND_WAIT", 0.2)
    goal.step("g1", run_job=w, say=said.append, peer=lambda: None)
    ev.set()
    assert any("peer is not idle" in m for m in said)


def test_a_job_not_marked_heavy_runs_beside_the_holder(tmp_path, monkeypatch):
    assert goal_peer.place({}, lambda: None)[0] == "here"
    goal_peer.claim("g1", "j1")
    assert goal_peer.place({"heavy": False}, lambda: None)[0] == "here"
    assert goal_peer.place({"heavy": True}, lambda: {"name": "vaio"})[0] == "peer"
    assert goal_peer.place({"heavy": True, "windows_only": True}, lambda: {"name": "vaio"})[0] == "wait"


def test_a_failed_send_leaves_the_job_queued(tmp_path, monkeypatch):
    mk(tmp_path, jobs=[pjob("j1", tmp_path, heavy=True), pjob("j2", tmp_path, heavy=True)])
    ev = threading.Event()
    w = Workers(hold={"j1": ev})
    monkeypatch.setattr(goal, "ROUND_WAIT", 0.2)

    def refuse(*a, **k):
        raise ValueError("no cable")
    goal.step("g1", run_job=w, say=lambda m: None, peer=lambda: {"name": "vaio"}, start_task=refuse)
    ev.set()
    _, st = goal.load("g1")
    assert st["jobs"]["j2"]["status"] == "queued" and "no cable" in st["jobs"]["j2"]["waits_why"]


def test_a_step_gives_the_next_goal_its_turn_instead_of_blocking_on_a_worker(tmp_path, monkeypatch):
    """The 22:37 silence: step blocked in futures.wait for as long as a job ran, so no other goal was stepped."""
    mk(tmp_path, jobs=[pjob("j1", tmp_path)])
    ev = threading.Event()
    monkeypatch.setattr(goal, "ROUND_WAIT", 0.3)
    t0 = time.time()
    out = goal.step("g1", run_job=Workers(hold={"j1": ev}), say=lambda m: None)
    ev.set()
    assert out["status"] == "active" and time.time() - t0 < 5
    assert goal.running("g1") == ["j1"]


def test_a_silent_main_loop_raises_the_alarm_in_the_problems_file_and_the_room(tmp_path):
    f, room = tmp_path / "ZERO-OPEN-PROBLEMS.md", []
    f.write_text("| # | Problem |\n|---|---|\n| 41 | x | OPEN | y |\n", encoding="utf-8")
    now = 100000.0
    goal_peer.beat("goal g1", lambda: now)
    assert goal_peer.check_alarms([], lambda: now + 600, f, lambda *a: room.append(a)) == []
    late = now + goal_peer.STALL_ROUNDS * goal_peer.ROUND_NOMINAL + 60
    out = goal_peer.check_alarms([], lambda: late, f, lambda *a: room.append(a))
    body = f.read_text(encoding="utf-8")
    assert len(out) == 1 and "| 42 |" in body and "silent" in body and "OPEN (R12)" in body
    assert room and "ALARM" in room[0][1]
    assert goal_peer.check_alarms([], lambda: late + 5, f, lambda *a: room.append(a)) == []     # once per hour


def test_a_goal_idle_over_half_of_the_last_hour_raises_the_alarm(tmp_path):
    f, room = tmp_path / "P.md", []
    st = {"jobs": {}, "answers": {}, "questions": {}, "status": "active"}
    t = 200000.0
    for i in range(30):                                          # 30 min of rounds with nothing running
        goal_peer.sample("g1", st, {}, lambda i=i: t + i * 60)
    now = t + 29 * 60
    out = goal_peer.check_alarms(["g1"], lambda: now, f, lambda *a: room.append(a))
    assert len(out) == 1 and "goal g1 was idle 100%" in out[0]
    # a goal that works, or waits for the owner, is not idle
    for gid, workers, extra in (("g2", {"j": {"future": None}}, {}), ("g3", {}, {"paused_until": now + 900})):
        for i in range(30):
            goal_peer.sample(gid, dict(st, **extra), workers, lambda i=i: t + i * 60)
        assert goal_peer.idle_share(gid, lambda: now)[0] == 0.0


def test_the_heartbeat_line_names_here_peer_waiting_and_the_slot_holder():
    st = {"jobs": {"a": {"status": "queued"}, "b": {"status": "running"}, "c": {"status": "running"}}}
    goal_peer.claim("g1", "b")
    line = goal_peer.heartbeat_line("g1", st, {"b": {}, "c": {"remote": {"on": "vaio", "task": "T"}}})
    assert line == "HEARTBEAT g1: here ['b']; peer ['c@vaio']; waiting ['a']; slot holder g1/b"


def test_stalled_is_false_while_a_job_ran_or_waits_and_true_only_with_nothing_going_on():
    g = {"stall_rounds": 3}
    mkst = lambda jobs, act, rounds: {"criteria": {"c": {"state": "pending"}}, "rounds": rounds, "stall": 2,
                                      "stall_at": rounds - 1, "best_met": 0, "stall_activity": 0, "activity": act,
                                      "jobs": jobs}
    assert goal.stall_check(g, mkst({}, 1, 5)) == ""                                   # a job ran since the last look
    assert goal.stall_check(g, mkst({"j": {"status": "queued"}}, 0, 5)) == ""          # one waits
    assert goal.stall_check(g, mkst({"j": {"status": "running"}}, 0, 5)) == ""         # one runs
    assert "stalled" in goal.stall_check(g, mkst({"j": {"status": "verified"}}, 0, 5))
