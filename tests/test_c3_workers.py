# Version: 1.0.0 · updated 26-10-28-19-50
"""Workers with and without AI (HARNESS-PLAN C3, 2026-10-28): a no-AI `run` job does plain heavy work (a build or
test program, not only a Fieldkit command) on a device with no local model and returns a checked result; `fieldkit
task start --on DEVICE --run` sends it and `fieldkit task status` turns the signed result into done or failed."""
import json
import os
import sys
from pathlib import Path

import pytest

from fieldkit import orchestrate_work as ow
from fieldkit.core import liveness
from fieldkit.mandate import jobs

from test_orchestrate_work import DIGEST, Ran, Said, received, run_req

NOW = 1_800_000_000.0


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_LINK_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("FIELDKIT_LIVENESS_DIR", str(tmp_path / "liveness"))
    monkeypatch.setenv("FIELDKIT_LIFECYCLE_DIR", str(tmp_path / "lc"))
    monkeypatch.setenv("FIELDKIT_ROOM_DIR", str(tmp_path / "room"))
    monkeypatch.setenv("FIELDKIT_COORDINATION_DIR", str(tmp_path / "coord"))
    (tmp_path / "lc").mkdir()
    (tmp_path / "room").mkdir()
    from fieldkit.autopilot import room
    monkeypatch.setattr(room, "ROOM_DIR", tmp_path / "room")
    monkeypatch.setattr(liveness, "room_line", lambda *a: None)
    return tmp_path / "home"


def plain_req(argv, **kw):
    return run_req(argv=argv, plain=True, id="run-Gorilla.Fieldkit-abababababab-c3c3c3c3", **kw)


# ---- what a no-AI worker takes ------------------------------------------------------------------------------------
@pytest.mark.parametrize("argv", [
    ["make", "-j2"], ["go", "test", "./..."], ["cargo", "build", "--release"], ["ninja", "-C", "out"],
    ["python", "-m", "pytest", "-q", "tests"], ["pytest", "-q"], ["ctest"], ["make.exe"], ["npm", "test"],
])
def test_a_plain_build_or_test_line_is_a_job(argv):
    assert ow.check_work(plain_req(argv)) is None


@pytest.mark.parametrize("argv,why", [
    (["sh", "-c", "make"], "not a build or test program"),
    (["git", "push"], "not a build or test program"),
    (["curl", "http://x"], "not a build or test program"),
    (["/usr/bin/make"], "not a build or test program"),
    (["..\\make"], "not a build or test program"),
    (["cargo", "publish"], "not run as a job"),
    (["npm", "publish"], "not run as a job"),
    (["npm", "exec", "x"], "not run as a job"),
    (["python", "-c", "import os"], "python runs only `-m`"),
    (["python", "setup.py"], "python runs only `-m`"),
    (["python", "-m", "pip", "install", "x"], "python runs only `-m`"),
    (["python", "-m"], "python runs only `-m`"),
])
def test_a_plain_line_that_is_no_build_or_test_or_that_publishes_is_refused(argv, why):
    assert why in ow.check_work(plain_req(argv))


def test_a_plain_flag_never_lets_a_fieldkit_line_skip_its_own_refusals():
    """Without plain the line is a Fieldkit command, as before: `make` would be `fieldkit make`, ship is refused."""
    assert "not run as a job" in ow.check_work(run_req(argv=["ship", "push"]))
    assert "not a build or test program" in ow.check_work(plain_req(["ship", "push"]))


# ---- the plain job itself -----------------------------------------------------------------------------------------
def go(home, req, ran, monkeypatch=None):
    received(home)
    ow.accept(req, "/m.yaml", home / "job.lock", popen=lambda *a, **k: None)
    delivered = []
    r = ow.work(req["id"], run=ran, put=lambda p, device=None: {"ok": True}, deliver=delivered.append,
                sign=lambda r: r, health_fn=list)
    assert delivered == [r]
    return r


def test_a_plain_job_runs_the_program_itself_not_a_fieldkit_command(home, monkeypatch):
    """The failure without C3: the worker ran `python -m fieldkit make -j2`, which is no command (exit 2)."""
    monkeypatch.setattr(ow.shutil, "which", lambda name, path=None: f"/opt/bin/{name}")
    ran = Ran(make=("app",))
    r = go(home, plain_req(["make", "-j2"], artefacts=["app"]), ran)
    want = ["/opt/bin/make", "-j2"]
    if sys.platform == "win32":
        want = [sys.executable, "-P", "-m", "fieldkit", "guard", "--", *want]
    assert ran.calls[0]["argv"] == want and r["status"] == "pass", r
    assert r["artefacts"][0]["name"] == "app" and len(r["artefacts"][0]["sha256"]) == 64
    assert "exit 0" in r["evidence"] and "building..." in r["evidence"]           # exit code and log tail


def test_a_plain_python_is_the_workers_own_python_and_windows_keeps_the_guard(monkeypatch):
    me = [sys.executable, "-P", "-m", "fieldkit"]
    assert ow.run_argv(["python", "-m", "pytest"], platform="linux", plain=True) == [sys.executable, "-m", "pytest"]
    monkeypatch.setattr(ow.shutil, "which", lambda name, path=None: "C:/Go/bin/go.exe")
    assert ow.run_argv(["go", "test"], platform="win32", plain=True) == [*me, "guard", "--", "C:/Go/bin/go.exe",
                                                                         "test"]


def test_a_program_the_worker_does_not_have_is_not_run_with_the_reason(home, monkeypatch):
    monkeypatch.setattr(ow.shutil, "which", lambda name, path=None: None)
    r = go(home, plain_req(["cargo", "build"]), Ran())
    assert r["status"] == "not-run" and "cargo is not installed on this worker" in r["evidence"], r


@pytest.mark.parametrize("argv,out,ok,why", [
    (["go", "test", "./..."], "ok  \tx/a\t0.1s\nok  \tx/b\t(cached)\n", True, ""),
    (["go", "test", "./..."], "--- FAIL: TestX (0.00s)\nFAIL\tx/a\t0.1s\nok  \tx/b\t0.1s\n", False, "failed"),
    (["go", "test", "./..."], "ok  \tx/a\t0.1s [no tests to run]\n", False, "tests never ran"),
    (["go", "test", "./..."], "?   \tx/a\t[no test files]\n", False, "no test count"),
    (["cargo", "test"], "test result: ok. 3 passed; 0 failed; 0 ignored\ntest result: ok. 2 passed; 0 failed;\n",
     True, ""),
    (["cargo", "test"], "test result: FAILED. 3 passed; 1 failed; 0 ignored\n", False, "1 test(s) failed"),
    (["ctest"], "100% tests passed, 0 tests failed out of 4\n", True, ""),
    (["ctest"], "75% tests passed, 1 tests failed out of 4\n", False, "1 test(s) failed"),
    (["make", "-j2"], "cc -o app main.c\n", True, ""),                                 # a build, not a test job
])
def test_a_plain_test_job_passes_only_on_its_own_test_count(argv, out, ok, why):
    passed, concerns = ow.judge("run", argv, 0, out, [])
    assert passed is ok and (not why or why in concerns[0]["text"]), concerns


def test_a_real_plain_test_job_runs_contained_and_is_judged_by_its_count(home, monkeypatch):
    """No fake runner: `python -m pytest` on a received tree with one passing and then one failing test."""
    real = ow.run_argv
    monkeypatch.setattr(ow, "run_argv", lambda args, platform=None, **kw: real(args, platform="linux", **kw))
    req = plain_req(["python", "-m", "pytest", "-q", "-p", "no:cacheprovider"], artefacts=[])
    tree = received(home, files={"test_one.py": "def test_one():\n    assert 1\n"})
    ow.accept(req, "/m.yaml", home / "job.lock", popen=lambda *a, **k: None)
    r = ow.work(req["id"], put=lambda p, device=None: {"ok": True}, deliver=lambda r: None, sign=lambda r: r,
                health_fn=list)
    assert r["status"] == "pass" and "1 passed" in r["evidence"], r["evidence"]
    (tree / "test_one.py").write_text("def test_one():\n    assert 0\n")
    r = ow.work(req["id"], put=lambda p, device=None: {"ok": True}, deliver=lambda r: None, sign=lambda r: r,
                health_fn=list)
    assert r["status"] == "fail" and "1 failed" in r["evidence"], r["evidence"]


def test_the_boss_sends_plain_and_a_plain_job_has_its_own_id(home, tmp_path, monkeypatch):
    from fieldkit.mandate import mandate as M
    monkeypatch.setattr(M, "_sha", lambda p: "cd" * 32)
    calls = []
    put = lambda tree, device=None: {"ok": True, "digest": DIGEST, "files": 1, "bytes": 1}   # noqa: E731
    call = lambda req, device=None: calls.append(req) or {"ok": True}                         # noqa: E731
    jid, said, _ = ow.send("run", "vaio", tmp_path, "Gorilla.Fieldkit", argv=["make"], put=put, call=call,
                           find_mandate=lambda p: "/m.yaml", plain=True)
    assert said == "sent" and calls[0]["plain"] is True and calls[0]["argv"] == ["make"]
    assert ow.job_key("run", ["make"]) != ow.job_key("run", ["make"], plain=True)
    jid2, said2, _ = ow.send("run", "vaio", tmp_path, "Gorilla.Fieldkit", argv=["git", "push"], put=put, call=call,
                             find_mandate=lambda p: "/m.yaml", plain=True)
    assert jid2 is None and "not a build or test program" in said2 and len(calls) == 1


# ---- fieldkit task start --on DEVICE --run ------------------------------------------------------------------------
def fake_send(sent, said="sent"):
    def send(kind, device, tree, project, argv=None, artefacts=(), wall=60, plain=False):
        sent.append({"kind": kind, "device": device, "tree": tree, "argv": argv, "plain": plain,
                     "artefacts": artefacts, "wall": wall})
        return "run-Gorilla.Fieldkit-abababababab-11112222", said, {"ok": True, "digest": DIGEST}
    return send


def test_a_run_task_goes_to_the_device_as_a_job_with_no_ai(home, tmp_path):
    from fieldkit.lifecycle import tasks
    sent = []
    r = tasks.start_run(["make", "-j2"], "vaio", tmp_path, artefacts=["app"], wall=30, send=fake_send(sent), now=NOW)
    assert sent == [{"kind": "run", "device": "vaio", "tree": str(tmp_path), "argv": ["make", "-j2"], "plain": True,
                     "artefacts": ["app"], "wall": 30}]
    assert r["status"] == "started" and r["job"].startswith("run-") and r["digest"] == DIGEST
    assert "no AI" in r["how"] and tasks.ledger()[r["id"]]["job"] == r["job"]
    f = tasks.start_run(["fieldkit", "admit", "suite"], "vaio", tmp_path, send=fake_send(sent), now=NOW + 1)
    assert sent[-1]["argv"] == ["admit", "suite"] and sent[-1]["plain"] is False and f["status"] == "started"
    with pytest.raises(tasks.AlreadyStarted):
        tasks.start_run(["make", "-j2"], "vaio", tmp_path, artefacts=["app"], send=fake_send(sent), now=NOW + 2)
    with pytest.raises(ValueError):
        tasks.start_run(["make"], None, tmp_path, send=fake_send(sent))
    with pytest.raises(ValueError):
        tasks.start_run(["make"], "vaio", tmp_path / "missing", send=fake_send(sent))
    n = tasks.start_run(["go", "test"], "vaio", tmp_path, send=fake_send(sent, "not taken: busy"), now=NOW + 3)
    assert n["status"] == "not-started" and "busy" in n["answer"]


def result(status="pass", arts=()):
    return {"id": "run-Gorilla.Fieldkit-abababababab-11112222", "status": status, "host": "vaio",
            "evidence": "ok exit 0 in 3 s; 1 of 1 artefact(s) sent\nbuilt", "concerns": [], "artefacts": list(arts)}


@pytest.mark.parametrize("worker,signed,checks,want,note", [
    ("pass", result(), [("app", True, "/r/app")], "done", "pass on vaio"),
    ("pass", result(), [("app", False, "no file with that sha256 arrived here")], "failed", "no file with that"),
    ("pass", None, [], "failed", "no result signed by a pinned worker key"),
    ("fail", result("fail"), [], "failed", "fail on vaio"),
])
def test_task_status_turns_the_signed_result_into_done_or_failed_once(home, tmp_path, worker, signed, checks,
                                                                      want, note):
    from fieldkit.lifecycle import tasks
    r = tasks.start_run(["make"], "vaio", tmp_path, send=fake_send([]), now=NOW)
    led = jobs._ledger()
    led[r["job"]] = {"status": "sent"}
    jobs._save(led)
    assert tasks.collect_jobs(tasks.ledger(), result_of=lambda *a: signed, check=lambda x: checks) == []
    led[r["job"]] = {"status": worker}
    jobs._save(led)
    got = []
    out = tasks.collect_jobs(tasks.ledger(), result_of=lambda *a: got.append(a) or signed, check=lambda x: checks)
    assert [o["status"] for o in out] == [want] and note in out[0]["note"], out
    assert got == [(r["job"], "Gorilla.Fieldkit", DIGEST)]
    assert tasks.ledger()[r["id"]]["status"] == want
    assert tasks.collect_jobs(tasks.ledger(), result_of=lambda *a: signed, check=lambda x: checks) == []


def test_the_task_command_line_sends_a_run_task(home, tmp_path, monkeypatch, capsys):
    import fieldkit.lifecycle as lc
    sent = []
    monkeypatch.setattr(ow, "send", fake_send(sent))
    rc = lc.cmd_task(["start", "--on", "vaio", "--run", "--tree", str(tmp_path), "--artefact", "out/*", "--",
                      "cargo", "test", "--release"])
    assert rc == 0 and sent[0]["argv"] == ["cargo", "test", "--release"] and sent[0]["artefacts"] == ["out/*"]
    assert "no AI" in capsys.readouterr().out
    assert lc.cmd_task(["start", "--run", "--tree", str(tmp_path), "--", "make"]) == 2        # no device
