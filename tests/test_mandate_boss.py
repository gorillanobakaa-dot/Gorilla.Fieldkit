# Version: 1.0.0 · updated 26-10-23-19-20
"""Plan section 17 (owner: "Yes for everything", 2026-10-23): the boss is proposed by a fixed rule and named in the
sealed mandate; workers take jobs only from it; the boss keeps an append-only ledger and resumes from it; a result the
boss could not receive waits in the worker's outbox."""

import pytest

from fieldkit.mandate import boss, jobs
from fieldkit.mandate import mandate as M


def test_the_rule_is_always_on_then_memory_then_the_cable():
    laptop = {"host": "laptop", "always_on": False, "mem_gb": 32, "cable": True}
    desk = {"host": "desk", "always_on": True, "mem_gb": 8, "cable": False}
    vaio = {"host": "vaio", "always_on": True, "mem_gb": 16, "cable": False}
    air = {"host": "air", "always_on": True, "mem_gb": 16, "cable": True}
    assert boss.propose([laptop, desk])["host"] == "desk"            # always on beats more memory
    assert boss.propose([desk, vaio])["host"] == "vaio"               # then memory
    assert boss.propose([vaio, air])["host"] == "air"                 # then the cable
    assert boss.propose([]) is None


def test_asking_about_the_device_never_makes_a_key(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "home", lambda: tmp_path / "m")
    d = boss.device_facts()
    assert d["key"] is None and not (tmp_path / "m").exists() and d["host"]
    M.review_key()
    assert boss.device_facts()["key"] == M.review_key()[1]


def test_the_power_reading_on_linux(tmp_path):
    for name, kind, online in (("BAT0", "Battery", None), ("AC", "Mains", "1")):
        (tmp_path / name).mkdir()
        (tmp_path / name / "type").write_text(kind)
        if online:
            (tmp_path / name / "online").write_text(online)
    assert boss._power(tmp_path) == (True, True)
    (tmp_path / "AC" / "online").write_text("0")
    assert boss._power(tmp_path) == (True, False)


@pytest.fixture
def linkhome(tmp_path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_LINK_HOME", str(tmp_path / "link"))
    monkeypatch.setattr(M, "home", lambda: tmp_path / "mandate")
    return tmp_path / "link"


def test_a_worker_takes_jobs_only_from_the_boss_the_mandate_names(tmp_path, linkhome):
    from fieldkit.mandate import verdict as V
    mp = tmp_path / "m.yaml"
    mp.write_text("project: p\ngoal: g\nexpires: 2026-10-30\nrepos: {R: {}}\nnever: []\n"
                  "orchestrator: {device: vaio, key: aaaa-bbbb-cccc-dddd}\nbackup: {device: desk}\n")
    started = []
    job = {"verb": "job", "id": "test-x", "kind": "test", "repo": "R", "commit": "a" * 40, "mandate": "ab" * 32}
    h = lambda **k: jobs.handle_job(dict(job, **k), popen=lambda argv, **kw: started.append(argv), repos=[],
                                    find_mandate=lambda r, s: mp, lock=tmp_path / "lock")
    orig = V.find_repo
    V.find_repo = lambda name, repos=None: tmp_path
    try:
        r = h(from_host="laptop")
        assert not r["ok"] and "not the mandate's orchestrator (vaio, desk)" in r["error"] and started == []
        assert h(from_host="vaio")["ok"]
        (tmp_path / "lock").unlink()
        assert h(from_host="desk")["ok"]                               # the backup boss
    finally:
        V.find_repo = orig
    assert "BOSS (orchestrator) is vaio" in "\n".join(M.doors(M.load(mp)))


def test_the_ledger_is_append_only_and_a_restarted_boss_asks_once_for_missed_results(tmp_path, linkhome):
    mp = tmp_path / "m.yaml"
    mp.write_text("x")
    jid, _ = jobs.send("test", "R", "a" * 40, mp, call=lambda q: {"ok": True})
    jobs.handle_result({"result": {"id": "other", "status": "pass", "host": "w"}})
    assert [e["what"] for e in jobs.events()] == ["job-sent", "result"] and jobs.events(1)[0]["n"] == 2
    asked = []
    res = jobs.resume(call=lambda q: asked.append(q) or {"ok": True, "resent": 1})
    assert res["open"] == [jid] and asked == [{"verb": "resend"}] and jobs.events()[-1]["what"] == "boss-resumed"
    first = jobs.events_path().read_text().splitlines()[0]
    jobs.event("x")
    assert jobs.events_path().read_text().splitlines()[0] == first      # earlier lines are never rewritten


def test_a_result_the_boss_could_not_take_waits_in_the_outbox(linkhome):
    r = {"id": "test-x", "repo": "R", "commit": "a" * 40, "status": "pass", "concerns": []}
    jobs.deliver(r, call=lambda q: {"ok": False, "error": "connection refused"})
    assert (jobs.outbox() / "test-x.json").is_file()
    got = []
    assert jobs.flush(call=lambda q: got.append(q) or {"ok": True}) == 1
    assert got[0]["result"]["id"] == "test-x" and not (jobs.outbox() / "test-x.json").exists()


def test_the_link_answers_hello_with_the_device_and_resend_flushes(monkeypatch, linkhome):
    from fieldkit.peers import link
    k = b"k" * 32
    monkeypatch.setattr(link, "_log", lambda text: None)
    h = link.handle(link.sign({"verb": "hello"}, k), link.Verifier(k), agents_fn=lambda: [], toolset_fn=lambda: {})
    assert h["device"]["host"] and "always_on" in h["device"]
    monkeypatch.setattr(jobs, "flush", lambda call=None: 2)
    assert link.handle(link.sign({"verb": "resend"}, k), link.Verifier(k))["resent"] == 2


# ---- device health (plan 17.2 item 10) ----------------------------------------------------------------------------
def test_health_problems_are_concerns_and_only_an_unreachable_origin_stops_the_job(tmp_path, linkhome):
    import subprocess
    from fieldkit.mandate import health
    ok_git = lambda cmd, **k: subprocess.CompletedProcess(cmd, 0, "abc\trefs/heads/main\n", "")
    clock_off = lambda cmd, **k: subprocess.CompletedProcess(cmd, 0, "no\n", "") if cmd[0] == "timedatectl" \
        else ok_git(cmd)
    can, concerns, said = health.check(tmp_path, min_gb=10**6, run=clock_off, sleep=lambda s: None)
    assert can and any("temp space" in c["text"] and c["fix_offered"] for c in concerns)
    import sys
    if sys.platform.startswith("linux"):
        assert any("clock" in c["text"] and "set-ntp" in c["fix_offered"] for c in concerns)
    tries = []
    down = lambda cmd, **k: (tries.append(1) if cmd[0] == "git" else None) or \
        subprocess.CompletedProcess(cmd, 128, "", "Could not resolve host")
    can, concerns, said = health.check(tmp_path, run=down, sleep=lambda s: None)
    assert not can and len(tries) == 3 and any("network" in c["text"] for c in concerns)   # retried, then a concern


def test_a_sick_device_still_reports(tmp_path, linkhome):
    mp = tmp_path / "m.yaml"
    mp.write_text("project: p\ngoal: g\nexpires: 2026-10-30\nrepos: {}\nnever: []\n")
    sent = []
    import subprocess
    repo = tmp_path / "r"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", "https://example.invalid/o/R.git"], check=True)
    r = jobs.run_job("test-x", repo, "a" * 40, mp, call=lambda q: sent.append(q) or {"ok": True},
                     health_fn=lambda rp: (False, [{"text": "network: down", "fix_offered": "the Wi-Fi"}], ["network"]),
                     verdict_fn=lambda *a, **k: pytest.fail("a job that cannot run is not run"))
    assert r["status"] == "not-run" and r["concerns"][0]["text"] == "network: down" and sent[0]["verb"] == "result"


def test_awake_holds_an_inhibitor_only_while_the_job_runs(monkeypatch):
    from fieldkit.mandate import health
    import sys
    if sys.platform == "win32":
        pytest.skip("Windows uses SetThreadExecutionState, not a process")
    monkeypatch.setattr(health.shutil, "which", lambda name: "/usr/bin/" + name if name == "systemd-inhibit" else None)

    class P:
        terminated = False

        def terminate(self):
            P.terminated = True
    with health.awake(popen=lambda argv, **k: P()) as held:
        assert isinstance(held, P) and not P.terminated
    assert P.terminated


# ---- the task list, the startup check, the morning report (plan 17.2 items 3, 5, 6) --------------------------------
def test_tasks_start_failing_are_never_removed_and_pass_only_with_evidence(tmp_path, linkhome):
    from fieldkit.mandate import project
    spec = tmp_path / "tasks.yaml"
    spec.write_text("tasks:\n  - {id: build-all, what: every system builds}\n"
                    "  - {id: layout-air, what: the banner is whole on the Air, needs: screenshot}\n")
    assert project.init(spec) == (["build-all", "layout-air"], 2)
    assert all("failing" in ln for ln in project.task_lines()[:2])
    assert not project.mark_pass("build-all", "")[0]                                   # the model's word is not enough
    assert project.mark_pass("build-all", "release-cycle build: all 6 assets made by this build")[0]
    ok, why = project.mark_pass("layout-air", "looks right to me")
    assert not ok and "screenshot" in why                                              # tested as a person would
    shot = tmp_path / "air.png"
    shot.write_bytes(project.PNG + b"\0" * 20_000)
    assert project.mark_pass("layout-air", "the Air at 167x39", screenshot=shot)[0]
    spec.write_text("tasks:\n  - {id: build-all, what: changed}\n")
    assert project.init(spec) == ([], 2) and "passing" in project.task_lines()[0]      # kept, never reset or removed


def test_the_startup_check_names_the_next_thing(tmp_path, linkhome, monkeypatch):
    from fieldkit.mandate import project
    lines, nxt = project.start(smoke={"ok": True})
    assert "write the task list" in nxt and lines[0].startswith("LEDGER:")
    spec = tmp_path / "tasks.yaml"
    spec.write_text("tasks:\n  - {id: build-all, what: every system builds}\n")
    project.init(spec)
    assert "build-all" in project.start(smoke={"ok": True})[1]
    mp = tmp_path / "m.yaml"
    mp.write_text("x")
    jid, _ = jobs.send("test", "R", "a" * 40, mp, call=lambda q: {"ok": True})
    assert f"job wait {jid}" in project.start(smoke={"ok": True})[1]


def test_the_morning_report_is_one_page_from_the_ledger(tmp_path, linkhome):
    from fieldkit.mandate import project
    mp = tmp_path / "m.yaml"
    mp.write_text("x")
    jid, _ = jobs.send("test", "R", "a" * 40, mp, call=lambda q: {"ok": True})
    jobs.handle_result({"result": {"id": jid, "status": "pass", "host": "windows-laptop",
                                   "concerns": [{"text": "clock: NTP synchronised: no", "fix_offered": "w32tm"}]}})
    rep = "\n".join(project.report())
    assert f"{jid} pass" in rep and "concern from windows-laptop: clock" in rep and "NEEDS YOU: nothing" in rep
    jobs.event("ask", why="a test failed twice: tests_on:windows")
    assert "NEEDS YOU: a test failed twice" in "\n".join(project.report())


# ---- recovery loops (2026-10-23, "incepe cu toate"): the same failure twice stops; progress is measured; all logged --
def test_health_moves_only_its_own_old_scratch_to_the_home_disk(tmp_path, monkeypatch):
    import os
    from fieldkit.mandate import health
    tmp, home = tmp_path / "tmp", tmp_path / "home-temp"
    for name in ("mandate-verdict-old", "mandate-verdict-young", "someone-elses"):
        (tmp / name).mkdir(parents=True)
        (tmp / name / "f").write_text("x")
    os.utime(tmp / "mandate-verdict-old", (1_700_000_000, 1_700_000_000))
    os.utime(tmp / "someone-elses", (1_700_000_000, 1_700_000_000))
    free = iter([False, False])
    monkeypatch.setattr(health, "free_temp", lambda min_gb=5, where=None: (next(free, True), "", ""))
    monkeypatch.setattr(health.os, "environ", dict(os.environ))
    done = health.repair_temp(where=str(tmp), home_temp=str(home))
    assert not (tmp / "mandate-verdict-old").exists() and (tmp / "mandate-verdict-young").exists()
    assert (tmp / "someone-elses").exists()                                       # never another program's
    moved = list((home / "moved-from-temp").rglob("mandate-verdict-old"))
    assert moved and (moved[0] / "f").read_text() == "x" and "moved 1" in done[0]
    assert health.os.environ["TMPDIR"] == str(home) and "home disk" in done[1]    # still short: the job's temp moves


def test_a_silent_job_is_asked_once_then_moved_once_then_reported(tmp_path, linkhome):
    import time
    mp = tmp_path / "m.yaml"
    mp.write_text("x")
    jid, _ = jobs.send("test", "R", "a" * 40, mp, call=lambda q: {"ok": True})
    t0 = time.time()
    asked = []
    call = lambda q: asked.append(q) or {"ok": True, "resent": 0}
    assert jobs.supervise(cap=100, now=t0 + 50, call=call) == []                  # within its time: nothing
    assert "asked the worker to resend" in jobs.supervise(cap=100, now=t0 + 120, call=call)[0]
    assert jobs.supervise(cap=100, now=t0 + 130, call=call) == [] and len(asked) == 1     # asked once, not again
    moved = []
    other = {"desk": lambda q: moved.append(q) or {"ok": True, "started": q["id"]}}
    assert "moved to desk" in jobs.supervise(cap=100, now=t0 + 200, call=call, workers=other)[0]
    assert moved[0]["id"] == jid
    later = time.time() + 1000
    assert "second worker too" in jobs.supervise(cap=100, now=later, call=call, workers=other)[0]
    assert len(moved) == 1                                                       # never a third try
    whats = [e["what"] for e in jobs.events()]
    assert whats[-3:] == ["resend-asked", "job-moved", "ask"]


def test_with_no_other_worker_a_silent_job_is_reported(tmp_path, linkhome):
    import time
    mp = tmp_path / "m.yaml"
    mp.write_text("x")
    jid, _ = jobs.send("test", "R", "a" * 40, mp, call=lambda q: {"ok": True})
    t0 = time.time()
    call = lambda q: {"ok": True}
    jobs.supervise(cap=100, now=t0 + 120, call=call)
    assert "no other worker" in jobs.supervise(cap=100, now=t0 + 200, call=call)[0]
    from fieldkit.mandate import project
    assert f"job {jid} silent" in "\n".join(project.report())


def test_the_watchdog_restarts_a_deaf_listener_and_stops_after_two_failures(linkhome):
    from fieldkit.peers import link_watchdog as W
    reported = []
    assert W.tick(listening=lambda: True, restart=None, report=reported.append) == "listening"
    assert "restarted" in W.tick(listening=lambda: False, restart=lambda: (0, ["listening on x"]),
                                 report=reported.append)
    bad = lambda: (3, ["NOT LISTENING after 10 s"])
    assert "restart 1 failed" in W.tick(listening=lambda: False, restart=bad, report=reported.append)
    assert "STOPPED" in W.tick(listening=lambda: False, restart=bad, report=reported.append) and len(reported) == 1
    calls = []
    W.tick(listening=lambda: False, restart=lambda: calls.append(1) or (0, []), report=reported.append)
    assert calls == [] and len(reported) == 1                                    # no third restart, no second report
    assert W.tick(listening=lambda: True, restart=None, report=reported.append) == "listening"
    log = (linkhome / "watchdog.log").read_text()
    assert "restarted" in log and "STOPPED" in log and "resumes" in log


def test_the_watchdog_timer_on_linux(tmp_path):
    import sys
    from fieldkit.peers import link_watchdog as W
    ran = []
    code, lines = W.install(run=lambda cmd, **k: ran.append(cmd) or __import__("subprocess").CompletedProcess(cmd, 0),
                            home=tmp_path)
    if not sys.platform.startswith("linux"):
        assert code == 3
        return
    unit = (tmp_path / ".config/systemd/user/fieldkit-link-watchdog.service").read_text()
    assert code == 0 and "peers link watchdog --once" in unit and ran[-1][-1] == "fieldkit-link-watchdog.timer"


def test_the_flags_reach_every_command(tmp_path, linkhome, monkeypatch, capsys):
    # 2026-10-23: the first real job found that `fieldkit mandate` refused every flag after the command; the worker's
    # own process (job run ... --lock) would have died at start. Through main(), the way a person and the worker call it.
    from fieldkit.mandate import project
    spec = tmp_path / "t.yaml"
    spec.write_text("tasks:\n  - {id: build-all, what: every system builds}\n")
    assert M.main(["tasks", "init", str(spec)]) == 0
    assert M.main(["tasks", "pass", "build-all", "--evidence", "6 assets made"]) == 0
    assert "6 assets made" in project._load()["build-all"]["evidence"]
    assert M.main(["report", "--since", "0"]) == 0
    monkeypatch.setattr(jobs, "mandate_for", lambda name, sha=None: tmp_path / "m.yaml")
    (tmp_path / "m.yaml").write_text("x")
    sent = []
    monkeypatch.setattr(jobs, "send", lambda kind, repo, commit, mp, call=None: sent.append((kind, repo, commit))
                        or ("test-1", "sent"))
    assert M.main(["job", "send", "Gorilla.Fieldkit", "a" * 40, "--kind", "test"]) == 0
    assert sent == [("test", "Gorilla.Fieldkit", "a" * 40)]
    jobs.handle_result({"result": {"id": "test-1", "status": "pass", "evidence": "9 passed"}})
    assert M.main(["job", "wait", "test-1", "--seconds", "1"]) == 0
    ran = []
    lock = tmp_path / "job.lock"
    lock.write_text("x")
    monkeypatch.setattr(jobs, "run_job", lambda jid, rp, c, mp: ran.append((jid, str(rp), c, str(mp)))
                        or {"status": "pass", "evidence": "ok"})
    assert M.main(["job", "run", "test-1", str(tmp_path), "a" * 40, str(tmp_path / "m.yaml"), "--lock", str(lock)]) == 0
    assert ran == [("test-1", str(tmp_path), "a" * 40, str(tmp_path / "m.yaml"))] and not lock.exists()
