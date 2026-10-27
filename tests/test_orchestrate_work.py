"""Jobs on a received tree (stage O3, 2026-10-26): the boss sends a folder and a job, the worker runs a Fieldkit
command line or a Gorilla OpenCode brief on a COPY of it, the artefacts come back with their sha256 signed."""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from fieldkit import orchestrate_work as ow
from fieldkit.mandate import jobs

DIGEST = "ab" * 32
MSHA = "cd" * 32


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_LINK_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


def received(home, name="proj", digest=DIGEST, files=None):
    """A tree that arrived by put: the folder in received/ and its receipt."""
    tree = home / "received" / name
    for rel, text in (files or {"main.go": "package main\n", "go.mod": "module x\n"}).items():
        (tree / rel).parent.mkdir(parents=True, exist_ok=True)
        (tree / rel).write_text(text)
    (home / "receipts").mkdir(parents=True, exist_ok=True)
    rows = [[p.relative_to(tree).as_posix(), hashlib.sha256(p.read_bytes()).hexdigest()]
            for p in sorted(tree.rglob("*")) if p.is_file()]
    (home / "receipts" / f"20261026-000000-{digest[:16]}.json").write_text(json.dumps(
        {"digest": digest, "saved": str(tree), "kind": "dir", "sha256": rows}))
    return tree


def run_req(**kw):
    req = {"id": "run-Gorilla.Fieldkit-abababababab-11111111", "kind": "run", "repo": "Gorilla.Fieldkit",
           "mandate": MSHA, "tree_digest": DIGEST, "argv": ["pipeline", "run", "go-app", "--var", "src=."],
           "artefacts": ["dist/*"], "wall_minutes": 5, "from_host": "boss"}
    req.update(kw)
    return req


# ---- what a worker takes -------------------------------------------------------------------------------------------
@pytest.mark.parametrize("change,why", [
    ({"argv": ["mandate", "approve"]}, "not run as a job"),
    ({"argv": ["ship", "push"]}, "not run as a job"),
    ({"argv": ["publish"]}, "not run as a job"),
    ({"argv": ["admit", "--", "sh", "-c", "rm -rf ~"]}, "only `admit suite`"),
    ({"argv": ["admit", "status"]}, "only `admit suite`"),
    ({"argv": ["autopilot", "claude", "x"]}, "not run as a job"),
    ({"argv": ["pipeline; rm -rf ~"]}, "not a Fieldkit command"),
    ({"argv": []}, "argv is a list"),
    ({"argv": "pipeline run"}, "argv is a list"),
    ({"artefacts": ["../../etc/passwd"]}, "artefacts"),
    ({"artefacts": ["/etc/*"]}, "artefacts"),
    ({"wall_minutes": 0}, "wall_minutes"),
    ({"wall_minutes": 99999}, "wall_minutes"),
    ({"tree_digest": "abc"}, "64-hex digest"),
    ({"kind": "agent", "brief": ""}, "brief"),
    ({"kind": "agent", "brief": "x", "brain": "file:///etc"}, "brain"),
    ({"kind": "agent", "brief": "x", "model": "a b; c"}, "model"),
])
def test_a_job_the_worker_must_not_or_cannot_run_is_refused_with_the_reason(change, why):
    assert why in ow.check_work(run_req(**change))


def test_a_good_run_and_a_good_agent_job_are_taken():
    assert ow.check_work(run_req()) is None
    assert ow.check_work(run_req(argv=["admit", "suite", "tests/test_x.py"])) is None   # what placement sends
    assert ow.check_work(run_req(kind="agent", argv=None, brief="build it", model="")) is None   # default model


def test_the_worker_needs_the_tree_first_and_runs_one_job_at_a_time(home):
    started = []
    popen = lambda argv, **kw: started.append(argv)          # noqa: E731
    lock = home / "job.lock"
    r = ow.accept(run_req(), "/m.yaml", lock, popen=popen)
    assert not r["ok"] and "send it first" in r["error"] and not started
    received(home)
    r = ow.accept(run_req(), "/m.yaml", lock, popen=popen)
    assert r["ok"] and started[0][-3:] == ["orchestrate", "work", run_req()["id"]] and lock.is_file()
    job = json.loads((home / "work" / run_req()["id"] / "job.json").read_text())
    assert job["tree"].endswith("received/proj") or job["tree"].endswith("received\\proj")
    r = ow.accept(run_req(id="run-Gorilla.Fieldkit-abababababab-22222222"), "/m.yaml", lock, popen=popen)
    assert not r["ok"] and "one at a time" in r["error"] and len(started) == 1


def test_the_job_protocol_hands_run_jobs_over_after_its_own_checks(home, monkeypatch):
    got = []
    monkeypatch.setattr(ow, "accept", lambda req, mpath, lock, cap, popen: got.append((req, mpath)) or {"ok": True})
    ok = jobs.handle_job(run_req(), find_mandate=lambda repo, s: "/m.yaml", lock=home / "l")
    assert ok["ok"] and got[0][1] == "/m.yaml"
    none = jobs.handle_job(run_req(), find_mandate=lambda repo, s: None, lock=home / "l")
    assert not none["ok"] and "no mandate" in none["error"] and len(got) == 1


# ---- the job itself ------------------------------------------------------------------------------------------------
def start(home, req):
    received(home)
    ow.accept(req, "/m.yaml", home / "job.lock", popen=lambda *a, **k: None)
    return req["id"]


class Ran:
    """A fake subprocess.run: records the call and makes what a build would make in the tree copy."""

    def __init__(self, rc=0, make=("dist/app.exe",), timeout=False):
        self.calls, self.rc, self.make, self.timeout = [], rc, make, timeout

    def __call__(self, argv, cwd=None, env=None, timeout=None, **kw):
        self.calls.append({"argv": argv, "cwd": cwd, "env": env})
        if self.timeout:
            raise subprocess.TimeoutExpired(argv, timeout)
        for rel in self.make if cwd else ():         # lms unload runs with no working folder
            (Path(cwd) / rel).parent.mkdir(parents=True, exist_ok=True)
            (Path(cwd) / rel).write_bytes(b"MZ built " + rel.encode())

        class R:
            returncode = self.rc
            stdout = stderr = ""
        return R()


def go(home, req, ran, sent=None):
    sent = [] if sent is None else sent
    delivered = []
    put = lambda p, device=None: sent.append((Path(p).name, device)) or {"ok": True, "sha256": "x"}  # noqa: E731
    r = ow.work(start(home, req), run=ran, put=put, deliver=delivered.append, sign=lambda r: r, health_fn=list)
    assert delivered == [r]
    return r


def test_a_run_job_builds_on_a_copy_and_sends_back_every_artefact_with_its_sha256(home):
    ran = Ran()
    sent = []
    r = go(home, run_req(), ran, sent)
    assert r["status"] == "pass" and r["kind"] == "run" and r["commit"] == DIGEST, r
    c = ran.calls[0]
    assert c["argv"] == ow.run_argv(run_req()["argv"])                # on Windows inside fieldkit guard
    assert Path(c["cwd"]) == home / "work" / run_req()["id"] / "tree"
    assert not (home / "received" / "proj" / "dist").exists()          # received/ never changed
    built = Path(c["cwd"]) / "dist" / "app.exe"
    assert r["artefacts"] == [{"name": "dist/app.exe", "size": built.stat().st_size,
                               "sha256": hashlib.sha256(built.read_bytes()).hexdigest()}]
    assert sent == [("app.exe", None)]                                # "boss" is not a household device here
    assert not (home / "job.lock").exists()


def test_a_failed_build_a_missing_artefact_and_the_wall_cap_are_failures_with_concerns(home):
    r = go(home, run_req(), Ran(rc=2))
    assert r["status"] == "fail" and r["evidence"].startswith("exit 2")
    r = go(home, run_req(id="run-Gorilla.Fieldkit-abababababab-33333333"), Ran(make=()))
    assert r["status"] == "fail" and "no file matched" in r["concerns"][0]["text"]
    r = go(home, run_req(id="run-Gorilla.Fieldkit-abababababab-44444444"), Ran(timeout=True))
    assert r["status"] == "fail" and "wall cap" in r["concerns"][0]["text"]


def test_an_artefact_that_did_not_reach_the_boss_fails_the_job(home):
    delivered = []
    r = ow.work(start(home, run_req()), run=Ran(), put=lambda p, device=None: {"ok": False, "error": "down"},
                deliver=delivered.append, sign=lambda r: r, health_fn=list)
    assert r["status"] == "fail" and "did not reach the boss" in r["concerns"][0]["text"]


def test_a_job_that_cannot_start_still_reports(home, monkeypatch):
    jid = start(home, run_req())
    job = json.loads((home / "work" / jid / "job.json").read_text())
    job["tree"] = str(home / "gone")
    (home / "work" / jid / "job.json").write_text(json.dumps(job))
    delivered = []
    r = ow.work(jid, run=Ran(), put=None, deliver=delivered.append, sign=lambda r: r, health_fn=list)
    assert r["status"] == "not-run" and "could not run" in r["evidence"] and delivered


def test_an_agent_job_uses_the_workers_own_provider_with_a_pinned_cheap_model(home, monkeypatch):
    monkeypatch.setattr(ow, "opencode_exe", lambda: "/usr/bin/gorilla-opencode")
    ran = Ran(make=("out.txt",))
    req = run_req(kind="agent", argv=None, brief="write out.txt", model="", artefacts=["out.txt"])
    r = go(home, req, ran)
    c = ran.calls[0]
    assert c["argv"] == ["/usr/bin/gorilla-opencode", "-p", "write out.txt", "-c", c["cwd"], "-q"]   # no spinner
    assert "XDG_CONFIG_HOME" not in c["env"] or c["env"]["XDG_CONFIG_HOME"] == __import__("os").environ.get(
        "XDG_CONFIG_HOME")                                             # the worker's own config, keys and all
    pinned = json.loads((Path(c["cwd"]) / ".gorilla-opencode.json").read_text())
    assert {a["model"] for a in pinned["agents"].values()} == {ow.DEFAULT_MODEL}
    assert len(ran.calls) == 1 and r["status"] == "pass"              # no brain URL: nothing to unload


def test_an_agent_job_with_a_local_brain_unloads_what_it_loaded(home, monkeypatch):
    monkeypatch.setattr(ow, "opencode_exe", lambda: "/usr/bin/gorilla-opencode")
    monkeypatch.setattr(ow.shutil, "which", lambda n: "/usr/bin/lms" if n == "lms" else None)
    ran = Ran(make=("out.txt",))
    req = run_req(kind="agent", argv=None, brief="b", model="qwen", brain="http://127.0.0.1:1234/v1",
                  artefacts=["out.txt"])
    r = go(home, req, ran)
    cfg = json.loads((Path(ran.calls[0]["env"]["XDG_CONFIG_HOME"]) / "gorilla-opencode" / "config.json").read_text())
    assert cfg["localEndpoints"] == [{"name": "brain", "baseURL": "http://127.0.0.1:1234/v1"}]
    assert ran.calls[1]["argv"] == ["/usr/bin/lms", "unload", "--all"] and r["status"] == "pass", r["evidence"]
    assert "another computer" in ow.unload_local_models("http://10.77.0.1:1234/v1", run=ran)


# ---- the boss ------------------------------------------------------------------------------------------------------
def test_the_boss_checks_the_job_before_sending_the_tree_and_keeps_it_in_the_ledger(home, tmp_path, monkeypatch):
    from fieldkit.mandate import mandate as M
    monkeypatch.setattr(M, "_sha", lambda p: MSHA)
    puts, calls = [], []
    put = lambda tree, device=None: puts.append(tree) or {"ok": True, "digest": DIGEST, "files": 2, "bytes": 9}  # noqa
    call = lambda req, device=None: calls.append((req, device)) or {"ok": True, "started": req["id"]}   # noqa
    jid, said, _ = ow.send("run", "w1", tmp_path, "Gorilla.Fieldkit", argv=["ship", "push"], put=put, call=call,
                           find_mandate=lambda p: "/m.yaml")
    assert jid is None and "not run as a job" in said and not puts      # nothing crossed for a refused job
    jid, said, _ = ow.send("run", "w1", tmp_path, "Gorilla.Fieldkit", argv=["pipeline", "run", "go-app"],
                           artefacts=["dist/*"], put=put, call=call, find_mandate=lambda p: "/m.yaml")
    assert said == "sent" and calls[0][1] == "w1" and calls[0][0]["tree_digest"] == DIGEST
    assert jobs._ledger()[jid]["status"] == "sent"
    again = ow.send("run", "w1", tmp_path, "Gorilla.Fieldkit", argv=["pipeline", "run", "go-app"],
                    artefacts=["dist/*"], put=put, call=call, find_mandate=lambda p: "/m.yaml")
    assert "already sent" in again[1] and len(calls) == 1
    assert "no mandate" in ow.send("run", "w1", tmp_path, "X", argv=["x"], put=put, call=call,
                                   find_mandate=lambda p: None)[1]


def test_the_boss_takes_an_artefact_only_with_the_sha256_the_worker_signed(home):
    f = home / "received" / "app.exe"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"MZ")
    sha = hashlib.sha256(b"MZ").hexdigest()
    (home / "receipts").mkdir()
    (home / "receipts" / "r.json").write_text(json.dumps({"saved": str(f), "sha256": [["app.exe", sha]]}))
    res = {"artefacts": [{"name": "dist/app.exe", "sha256": sha}, {"name": "dist/b", "sha256": "0" * 64}]}
    assert [ok for _, ok, _ in ow.artefacts_check(res)] == [True, False]
    f.write_bytes(b"MZ changed")
    assert ow.artefacts_check(res)[0][1] is False


def test_a_signed_result_covers_its_artefacts_and_older_results_sign_as_before():
    old = {k: "v" for k in jobs.RESULT_FIELDS}
    assert jobs._canon(old) == json.dumps(old, sort_keys=True, separators=(",", ":")).encode()
    new = dict(old, artefacts=[{"name": "a", "sha256": "1" * 64, "size": 1}])
    assert jobs._canon(new) != jobs._canon(dict(new, artefacts=[{"name": "a", "sha256": "2" * 64, "size": 1}]))


def test_on_windows_a_run_job_always_goes_through_the_guard():
    """Owner rule: every compile on the Windows laptop runs inside fieldkit guard; the worker adds it itself."""
    me = [sys.executable, "-P", "-m", "fieldkit"]
    assert ow.run_argv(["pipeline", "run", "go-app"], platform="win32") == [*me, "guard", "--", *me, "pipeline",
                                                                            "run", "go-app"]
    assert ow.run_argv(["pipeline", "run", "go-app"], platform="linux") == [*me, "pipeline", "run", "go-app"]


# ---- the evidence rule (O3 false PASS, 2026-10-26) -----------------------------------------------------------------
class Said(Ran):
    """A fake run that prints `text` into the job log and exits rc, making no file."""

    def __init__(self, text, rc=0):
        super().__init__(rc=rc, make=())
        self.text = text

    def __call__(self, argv, cwd=None, env=None, timeout=None, stdout=None, **kw):
        if stdout is not None and self.text:
            stdout.write(self.text)
            stdout.flush()
        return super().__call__(argv, cwd=cwd, env=env, timeout=timeout, **kw)


FALSE_PASS_ARGV = ["regress", "record", "vaio-train", "--", "python3", "-m", "pytest", "-q", "-rA", "-p",
                   "no:cacheprovider", "tests/test_train.py"]
FALSE_PASS_LOG = ("vaio-train: 0 passed, 0 failed (0 test names read; exit 1)\n"
                  "  | /usr/bin/python3: No module named pytest\n")


def test_the_o3_false_pass_is_a_fail_the_job_that_tested_nothing(home):
    """Reproduced from the two real job folders run-Gorilla.Fieldkit-6a90e8d43aab-*: exit 0 in 0 s, that log, no
    artefact declared; the worker said PASS."""
    ran = Said(FALSE_PASS_LOG)
    r = go(home, run_req(argv=FALSE_PASS_ARGV, artefacts=[]), ran)
    assert r["status"] == "fail" and "tests never ran" in r["concerns"][0]["text"], r
    # and the `python3` in such a line is now the worker's own Python (the one with pytest), first on PATH
    assert ran.calls[0]["env"]["PATH"].split(os.pathsep)[0] == os.path.dirname(sys.executable)


def test_exit_0_with_no_output_and_no_declared_artefact_is_a_fail(home):
    r = go(home, run_req(argv=["host"], artefacts=[]), Said(""))
    assert r["status"] == "fail" and "no output and no declared artefact" in r["concerns"][0]["text"], r
    r = go(home, run_req(id="run-Gorilla.Fieldkit-abababababab-55555555", argv=["host"], artefacts=[]),
           Said("host: linux\n"))
    assert r["status"] == "pass", r                    # it said something: not this rule's business


@pytest.mark.parametrize("argv,out,ok,why", [
    (["admit", "suite"], "....\n4 passed, 1 skipped in 3.20s\n", True, ""),
    (["admit", "suite"], "\nno tests ran in 0.01s\n", False, "tests never ran"),
    (["admit", "suite"], "1 failed, 3 passed in 2.0s\n", False, "1 test(s) failed"),
    (["admit", "suite"], "3 passed, 2 errors in 2.0s\n", False, "2 error(s)"),
    (["admit", "suite"], "collected nothing at all\n", False, "no test count"),
    (FALSE_PASS_ARGV, "vaio-train: 5 passed, 0 failed (5 test names read; exit 0)\n", True, ""),
    (FALSE_PASS_ARGV, "vaio-train: 4 passed, 1 failed (5 test names read; exit 1)\n", False, "1 test(s) failed"),
    (["pipeline", "run", "go-app"], "built\n", True, ""),
])
def test_a_test_job_passes_only_on_a_test_count_with_a_pass_and_no_failure(argv, out, ok, why):
    passed, concerns = ow.judge("run", argv, 0, out, [])
    assert passed is ok and (not why or why in concerns[0]["text"]), concerns


def test_a_non_zero_exit_and_an_agent_job_are_judged_as_before():
    assert ow.judge("run", ["admit", "suite"], 1, "4 passed in 1s", [])[0] is False
    assert ow.judge("agent", None, 0, "", [])[0] is True


def test_a_real_job_runs_contained_and_its_output_reaches_the_log(home):
    """No fake: the job runs through fieldkit/core/contain.py (process group / Job Object, wall cap, stall stop)."""
    r = ow.work(start(home, run_req(argv=["host"], artefacts=[])), put=lambda p, device=None: {"ok": True},
                deliver=lambda r: None, sign=lambda r: r, health_fn=list)
    log = (home / "work" / run_req()["id"] / "job.log").read_text()
    assert r["status"] == "pass" and len(log.splitlines()) > 1, (r, log)
