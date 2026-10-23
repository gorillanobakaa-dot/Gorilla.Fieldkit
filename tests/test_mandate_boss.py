# Version: 1.0.0 · updated 26-10-23-15-30
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
