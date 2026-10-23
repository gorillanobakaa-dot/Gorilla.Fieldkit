# Version: 1.0.0 · updated 26-10-23-15-10
"""One orchestrator, many workers (fieldkit/mandate/jobs.py, verdict.py; plan section 16, 2026-10-23): a worker runs
the job and reports, concerns with a fix offered, never approves or objects; the orchestrator's mandate decides. A
result is signed for provenance only. And the mandate's release door."""
import json
import subprocess

import pytest

from fieldkit.mandate import mandate as M
from fieldkit.mandate import verdict as V


def _g(repo, *a):
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=tester", *a], check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    origin = tmp_path / "Gorilla.Opencode.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    w = tmp_path / "w"
    subprocess.run(["git", "clone", "-q", str(origin), str(w)], check=True, capture_output=True)
    _g(w, "checkout", "-q", "-b", "main")
    (w / "go.mod").write_text("module x\n")
    _g(w, "add", "-A")
    _g(w, "commit", "-qm", "one")
    _g(w, "push", "-q", "origin", "main")
    _g(w, "checkout", "-q", "-b", "feat/x")
    (w / "a.go").write_text("package x\n")
    _g(w, "add", "-A")
    _g(w, "commit", "-qm", "two")
    _g(w, "push", "-q", "origin", "feat/x")
    return w, _g(w, "rev-parse", "HEAD")


def _go(here, base):
    """go test's output on the commit (the verdict worktree) and on origin/main (the baseline worktree)."""
    return lambda cmd, cwd, **k: subprocess.CompletedProcess(cmd, 0, base if "mandate-base-" in str(cwd) else here, "")


FEAT = ["feat/*"]


def test_a_pass_on_this_os_and_fewer_tests_is_a_fail(repo):
    w, sha = repo
    two = "ok  \tx/a\t0.1s\nok  \tx/b\t0.2s\n"
    r = V.verdict(w, sha, name="Gorilla.Opencode", branches=FEAT, run=_go(two, two), local={})
    assert r["status"] == "pass" and r["concerns"] == [], r
    r = V.verdict(w, sha, name="Gorilla.Opencode", branches=FEAT, run=_go("ok  \tx/a\t0.1s\n", two), local={})
    assert r["status"] == "fail" and "fewer tests" in r["evidence"]


def test_only_pushed_work_on_a_mandated_branch_is_run(repo):
    w, sha = repo
    assert V.verdict(w, sha, name="Gorilla.Opencode", branches=["release/*"], run=_go("ok x\n", "ok x\n"),
                     local={})["status"] == "not-run"
    (w / "b.go").write_text("package x\n")
    _g(w, "add", "-A")
    _g(w, "commit", "-qm", "local only")
    r = V.verdict(w, _g(w, "rev-parse", "HEAD"), name="Gorilla.Opencode", branches=FEAT, run=_go("ok x\n", "ok x\n"),
                  local={})
    assert r["status"] == "not-run" and "on no branch of origin" in r["evidence"]
    assert V.verdict(w, sha[:10], name="Gorilla.Opencode", branches=FEAT, local={})["status"] == "not-run"


def test_the_suite_is_this_computers_never_the_orchestrators(repo):
    w, _ = repo
    assert V.test_cfg(w, "Gorilla.Opencode", local={})["cmd"] == "go test ./..."
    mine = {"cmd": "go test -short ./..."}
    assert V.test_cfg(w, "Gorilla.Opencode", local={"mandate": {"tests": {"Gorilla.Opencode": mine}}}) == mine
    from fieldkit.mandate import jobs
    assert "cmd" not in jobs.handle_job.__code__.co_varnames                  # a job carries no command


@pytest.mark.parametrize("path,text", [
    ("conftest.py", "def pytest_collection_modifyitems(items): items.clear()\n"),
    ("pytest.ini", "[pytest]\naddopts = -k nothing\n"),
    ("main_test.go", "package x\nimport \"testing\"\nfunc TestMain(m *testing.M) {}\n"),
    ("pyproject.toml", "[tool.pytest.ini_options]\naddopts = '-k nothing'\n"),
])
def test_a_commit_that_changes_how_its_tests_run_is_a_concern_not_a_refusal(repo, path, text):
    # owner, 2026-10-23: workers "report their concerns ... offer to fix ... continue working ... do not object"
    w, _ = repo
    (w / path).write_text(text)
    _g(w, "add", "-A")
    _g(w, "commit", "-qm", "the tests, differently")
    _g(w, "push", "-q", "origin", "feat/x")
    r = V.verdict(w, _g(w, "rev-parse", "HEAD"), name="Gorilla.Opencode", branches=FEAT, run=_go("ok x\n", "ok x\n"),
                  local={})
    assert r["status"] == "pass" and path in r["concerns"][0]["text"] and r["concerns"][0]["fix_offered"]


def test_a_version_bump_in_pyproject_is_not_a_test_change(repo):
    w, _ = repo
    (w / "pyproject.toml").write_text("[project]\nversion = '0.2.0'\n")
    _g(w, "add", "-A")
    _g(w, "commit", "-qm", "0.2.0")
    _g(w, "push", "-q", "origin", "feat/x")
    assert V.test_control_changes(w, _g(w, "rev-parse", "HEAD")) == []


# ---- the job protocol --------------------------------------------------------------------------------------------------
MSHA = "ab" * 32


@pytest.fixture
def linkhome(tmp_path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_LINK_HOME", str(tmp_path / "link"))
    monkeypatch.setattr(M, "home", lambda: tmp_path / "mandate")
    return tmp_path / "link"


def test_the_worker_starts_a_job_it_can_do_and_says_why_when_it_cannot(repo, linkhome):
    from fieldkit.mandate import jobs
    w, sha = repo
    started = []
    popen = lambda argv, **k: started.append(argv)
    job = {"verb": "job", "id": "test-x", "kind": "test", "repo": "Gorilla.Opencode", "commit": sha, "mandate": MSHA}
    h = lambda req, fm=lambda repo, s: "/m.yaml": jobs.handle_job(req, popen=popen, repos=[w], find_mandate=fm,
                                                                 lock=linkhome / "job.lock")
    assert "fieldkit mandate share" in h(job, fm=lambda repo, s: None)["error"]          # the fix, not an objection
    assert "kind" in h(dict(job, kind="merge"))["error"]                                # workers never merge
    assert "sync.repos" in h(dict(job, repo="Other"))["error"]
    assert not h(dict(job, commit="abc"))["ok"]
    assert started == []
    assert h(job)["ok"] and started[0][-8:] == ["job", "run", "test-x", str(w), sha, "/m.yaml", "--lock",
                                               str(linkhome / "job.lock")]
    assert "one at a time" in h(job)["error"] and len(started) == 1


def test_a_result_is_signed_for_provenance_and_checked_against_the_pinned_worker_key(repo, linkhome, tmp_path):
    from fieldkit.mandate import jobs
    w, sha = repo
    sent = []
    r = jobs.run_job("test-x", w, sha, tmp_path / "m.yaml", call=lambda q: sent.append(q) or {"ok": True},
                     verdict_fn=lambda *a, **k: {"status": "pass", "evidence": "2 passed",
                                                 "concerns": [{"text": "c", "fix_offered": "f"}]}) \
        if (tmp_path / "m.yaml").write_text("project: p\ngoal: g\nexpires: 2026-10-30\nrepos: {}\nnever: []\n") \
        else None
    assert sent[0]["verb"] == "result" and sent[0]["result"]["sig"] and "APPROVE" not in json.dumps(sent[0])
    assert jobs.handle_result(sent[0])["ok"]
    raw = bytes.fromhex(M._review_paths()[1].read_text().strip())
    fp = M.fingerprint(raw)
    assert jobs.verified_results("Gorilla.Opencode", sha, workers={fp: raw}, own_key="orchestrator")[0]["id"] == "test-x"
    assert jobs.verified_results("Gorilla.Opencode", sha, workers={fp: raw}, own_key=fp) == []      # its own key
    forged = dict(r, status="pass", evidence="I say so", id="forged")
    jobs.handle_result({"result": forged})
    tampered = dict(r, evidence="changed after signing", id="test-y")
    jobs.handle_result({"result": tampered})
    assert [x["id"] for x in jobs.verified_results("Gorilla.Opencode", sha, workers={fp: raw}, own_key="o")] == ["test-x"]
    assert "CONCERN: c" in "\n".join(jobs.lines())


def test_the_orchestrator_sends_a_job_once_and_waits_in_one_call(linkhome, tmp_path):
    from fieldkit.mandate import jobs
    mp = tmp_path / "m.yaml"
    mp.write_text("x")
    calls = []
    call = lambda q: calls.append(q) or {"ok": True}
    jid, what = jobs.send("test", "Gorilla.Opencode", "a" * 40, mp, call=call)
    assert what == "sent" and calls[0]["verb"] == "job" and calls[0]["mandate"] == M._sha(mp)
    assert "already sent" in jobs.send("test", "Gorilla.Opencode", "a" * 40, mp, call=call)[1] and len(calls) == 1
    clock = iter([0, 1, 2, 9999])
    assert jobs.wait(jid, 10, sleep=lambda s: None, now=lambda: next(clock))["status"] == "sent"   # timed out
    jobs.handle_result({"result": {"id": jid, "status": "fail", "evidence": "3 failed"}})
    assert jobs.wait(jid, 10, sleep=lambda s: None)["status"] == "fail"


def test_a_concern_stops_the_merge_only_when_the_owners_mandate_says_so(monkeypatch):
    from datetime import date
    m = {"project": "p", "goal": "g", "expires": "2026-10-30", "never": [], "ask_when": [],
         "repos": {"Gorilla.Opencode": {"merge_to_main": {"when": ["tests_on:windows"]}}}}
    monkeypatch.setattr(M, "verify", lambda p, today=None: (True, "in force"))
    monkeypatch.setattr(M, "load", lambda p: m)

    class Pv:
        PROVERS = {"tests_on": lambda rp, c, cfg, os_name=None: (os_name == "windows", "ran it; CONCERNS: conftest.py")}

        @staticmethod
        def where(rp):
            return "Gorilla.Opencode", "feat/x"
    a = lambda: M.allows("x.yaml", "merge_main", today=date(2026, 10, 23), repo_path=".", commit="a" * 40, provers=Pv)
    assert a()[0] == "allow"
    m["ask_when"] = ["worker_concern"]
    v, why = a()
    assert v == "ask" and "worker_concern" in why


def test_the_link_hands_job_and_result_to_the_tools(monkeypatch, linkhome):
    from fieldkit.mandate import jobs
    from fieldkit.peers import link
    k = b"k" * 32
    got = []
    monkeypatch.setattr(jobs, "handle_job", lambda req, popen=None: got.append(req) or {"ok": True, "started": req["id"]})
    monkeypatch.setattr(link, "_log", lambda text: None)
    req = link.sign({"verb": "job", "id": "test-x", "kind": "test", "repo": "R", "commit": "a" * 40, "mandate": MSHA}, k)
    assert link.handle(req, link.Verifier(k))["started"] == "test-x"
    assert not link.handle(dict(req, commit="b" * 40), link.Verifier(k))["ok"]              # changed after signing
    res = link.sign({"verb": "result", "result": {"id": "test-x", "status": "pass"}, "text": "RESULT"}, k)
    assert link.handle(res, link.Verifier(k))["stored"] == "test-x"


def test_ship_merge_gives_the_worker_the_job_once_then_waits(repo, monkeypatch):
    from fieldkit.method import ship
    w, sha = repo
    why = "m: not proven: tests_on:windows: no passing RESULT from a worker on windows for " + sha[:10]
    sent = []
    monkeypatch.setattr(ship, "request_test_job", lambda repo, commit, call=None: sent.append(commit) or ("test-j", "sent"))
    r = ship.merge(w, mandate=lambda *a, **k: ("ask", why, None))
    assert r["state"] == "waiting-result" and "job wait test-j" in r["next"] and sent == [sha]
    bad = lambda *a, **k: ("ask", why + "; full_tests_pass_here: the suite failed", None)
    assert ship.merge(w, mandate=bad)["state"] == "not-mandated"                      # its own suite first


# ---- the release door ----------------------------------------------------------------------------------------------
def test_the_release_door_names_projects(tmp_path, monkeypatch):
    from datetime import date
    m = {"project": "p", "goal": "g", "expires": "2026-10-30", "repos": {}, "never": [],
         "release": {"cycle": {"gorilla-opencode": ["v0.1.151"]}}}
    monkeypatch.setattr(M, "verify", lambda p, today=None: (True, "in force"))
    monkeypatch.setattr(M, "load", lambda p: m)
    a = lambda **k: M.allows("x.yaml", "release", today=date(2026, 10, 23), **k)[0]
    # the Windows review, point 4: only the exact version the owner wrote
    assert a(project="gorilla-opencode", version="v0.1.151") == "allow"
    assert a(project="gorilla-opencode", version="v0.1.152") == "ask" and a(project="gorilla-opencode") == "ask"
    assert a(project="firefox", version="v0.1.151") == "ask" and a() == "ask"
    m["release"] = {"cycle": ["gorilla-opencode"]}                          # a list names no version: not opened
    assert a(project="gorilla-opencode", version="v0.1.151") == "ask"
    m["release"] = {"cycle": {"gorilla-opencode": ["v0.1.151"]}}
    assert "gorilla-opencode v0.1.151" in "\n".join(M.doors(m))
