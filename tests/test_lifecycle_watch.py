# Version: 1.0.0 · updated 26-10-30
"""fieldkit task watch (plan Z1): UNHANDLED, STALLED and DIED are found, acted on once, and shown to Zero."""
import argparse
import json
import time

from fieldkit.lifecycle import tasks, watch
from fieldkit.zero import schedule

NOW = time.mktime(time.strptime("2026-10-30 12:00:00", "%Y-%m-%d %H:%M:%S"))


def at(minutes_ago):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(NOW - minutes_ago * 60))


def task(tid, status="started", on="here", ago=60, **kw):
    return {"id": tid, "status": status, "on": on, "at": at(ago), "title": f"job {tid}", "hash": "h" + tid,
            "brief": "/b.md", **kw}


def find(led, **kw):
    return {f["id"]: f["state"] for f in watch.findings(led, now=NOW, alive=kw.pop("alive", lambda p: True), **kw)}


def test_blocked_without_follow_up_is_unhandled():
    led = {"T-1": task("T-1", "blocked", on="vaio", reported=at(40), note="stuck")}
    assert find(led) == {"T-1": "UNHANDLED"}


def test_a_later_task_of_the_same_brief_handles_it():
    led = {"T-1": task("T-1", "blocked", ago=90, reported=at(80)),
           "T-2": {**task("T-2", ago=30), "hash": "hT-1"}}
    assert "T-1" not in find(led, stalled_min=999)


def test_old_ended_task_is_history():
    assert find({"T-1": task("T-1", "failed", ago=60 * 30, reported=at(60 * 29))}) == {}


def test_done_task_is_not_a_finding():
    assert find({"T-1": task("T-1", "done", reported=at(5))}) == {}


def test_silent_running_task_is_stalled_and_a_report_or_log_resets_it(tmp_path):
    log = tmp_path / "log"
    log.write_text("x")
    old = NOW - 3600
    import os
    os.utime(log, (old, old))
    led = {"T-1": task("T-1", ago=60, log=str(log))}
    assert find(led, stalled_min=10) == {"T-1": "STALLED"}
    os.utime(log, (NOW - 60, NOW - 60))                   # the log grew a minute ago
    assert find(led, stalled_min=10) == {}
    led["T-1"]["reported"] = at(1)
    assert find({"T-1": {**led["T-1"], "log": None}}, stalled_min=10) == {}


def test_a_note_from_the_peer_counts_as_a_sign_of_life():
    led = {"T-1": task("T-1", on="vaio", ago=60)}
    notes = [{"from_host": "vaio", "text": "working", "at": at(2)}]
    assert find(led, notes=notes) == {}
    assert find(led) == {"T-1": "STALLED"}


def test_dead_process_without_report_is_died_not_stalled():
    led = {"T-1": task("T-1", pid=99999, ago=20)}
    assert find(led, alive=lambda p: False) == {"T-1": "DIED"}


def test_died_task_with_a_follow_up_is_quiet():
    led = {"T-1": task("T-1", pid=1, ago=30), "T-2": {**task("T-2", ago=5), "hash": "hT-1"}}
    assert "T-1" not in find(led, alive=lambda p: p == 2 or False, stalled_min=999)


def test_act_writes_inbox_pings_once_and_hook_shows_it(tmp_path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_LIFECYCLE_DIR", str(tmp_path))
    led = {"T-1": task("T-1", "blocked", on="vaio", reported=at(40)), "T-2": task("T-2", on="vaio", ago=60)}
    found = watch.findings(led, now=NOW, alive=lambda p: True)
    inbox, pings, rooms = [], [], []
    kw = dict(ping=lambda d, t: pings.append((d, t)) or {"ok": True}, inbox_add=lambda *a, **k: inbox.append((a, k)),
              led=led, now=NOW, room=lambda *a: rooms.append(a))
    done = watch.act(found, **kw)
    assert pings == [("vaio", "T-2")] and len(inbox) == 1 and "fieldkit task start /b.md --again" in inbox[0][1]["command"]
    assert rooms and len(done) == 2
    watch.act(found, **{**kw, "now": NOW + 60})            # the next run: no second ping, no second room line
    assert pings == [("vaio", "T-2")] and len(rooms) == 1
    lines = watch.hook_lines(now=NOW + 120)
    assert len(lines) == 2 and lines[0].startswith("TASK WATCH") and "T-1" in lines[0]
    assert watch.hook_lines(now=NOW + 7200) == []          # an old run says nothing


def run(led, **kw):
    a = argparse.Namespace(once=True, act=False, stalled=10, hours=24, every=1, scheduled=False, schedule=False,
                           apply=False, remove=False, json=False)
    a.__dict__.update(kw)
    return watch.main(a, ledger=led, notes=[])


def test_exit_code_3_on_a_finding_and_0_when_quiet(monkeypatch, capsys):
    monkeypatch.setattr(tasks, "ingest", lambda: [])
    monkeypatch.setattr(tasks, "collect_jobs", lambda t: [])
    monkeypatch.setattr(watch.time, "time", lambda: NOW)
    assert run({"T-1": task("T-1", "failed", reported=at(5))}) == 3
    assert "UNHANDLED T-1" in capsys.readouterr().out
    assert run({}) == 0


def test_schedule_plan_uses_its_own_unit_every_5_minutes():
    lx = schedule.plan("linux", python="/py", root="/r", home="/h", job=schedule.WATCH)
    assert "fieldkit-task-watch.timer" in lx[-1][1][-1] and "task watch --act --scheduled" in lx[0][2]
    assert "OnCalendar=*:0/5" in lx[1][2]
    w = schedule.plan("win32", python="py", root="/r", job=schedule.WATCH)
    assert "PT5M" in w[0][2] and "Fieldkit task watch" in w[1][1]
    assert "PT15M" in schedule.plan("win32", python="py", root="/r")[0][2]       # the comms check is unchanged
