"""Session jobs (2026-10-27): the boss gives any device brief + repo + branch; the device runs a headless agent on a
worktree of the branch and pushes it, with no click anywhere. Authority: cable key, the boss's pinned Ed25519 key,
the owner-wide mandate in force on the device, its scope. Every authority refusal is an inbox row on the device."""
import json
import subprocess
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from fieldkit import orchestrate_session as osj
from fieldkit.autopilot import inbox
from fieldkit.mandate import jobs
from fieldkit.mandate import mandate as M

MSHA = "cd" * 32
MANDATE = """project: owner-wide
scope: owner-wide
goal: test
expires: 2099-01-01
defaults:
  push: ["feat/*", "fix/*"]
repos:
  Gorilla.Fieldkit: {}
never: [force_push]
"""


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_LINK_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


@pytest.fixture
def mandate(tmp_path):
    p = tmp_path / "owner-wide.yaml"
    p.write_text(MANDATE, encoding="utf-8")
    return p


def keypair():
    k = Ed25519PrivateKey.generate()
    raw = k.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return k, M.fingerprint(raw), raw


BOSS = keypair()


def req(**kw):
    r = {"verb": "job", "id": osj.job_id("Gorilla.Fieldkit", "feat/x", "do it"), "kind": "session",
         "repo": "Gorilla.Fieldkit", "branch": "feat/x", "base": "main", "agent": "claude", "brief": "do it",
         "wall_minutes": 30, "mandate": MSHA, "from_host": "boss"}
    r.update(kw)
    return r


def signed(r, key=BOSS):
    return osj.boss_sign(r, key_fn=lambda: (key[0], key[1]))


PINNED = {BOSS[1]: BOSS[2]}


# ---- the boss's signature ------------------------------------------------------------------------------------------
def test_a_job_signed_by_a_pinned_boss_passes_and_any_change_breaks_it():
    s = signed(req())
    assert osj.boss_check(s, PINNED) is None
    for k, v in (("branch", "feat/y"), ("brief", "rm -rf ~"), ("repo", "pfind"), ("mandate", "ef" * 32),
                 ("from_host", "evil"), ("agent", "codex"), ("wall_minutes", 1440)):
        assert "does not match" in osj.boss_check(dict(s, **{k: v}), PINNED), k


def test_an_unsigned_job_or_one_from_a_key_not_pinned_is_refused():
    assert "not signed" in osj.boss_check(req(), PINNED)
    other = keypair()
    assert "not pinned" in osj.boss_check(signed(req(), other), PINNED)
    assert "enrolment" in osj.boss_check(signed(req(), other), PINNED)


# ---- scope ---------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("change,why", [
    ({"branch": "main"}, "base branch"),
    ({"branch": "wip/x"}, "out of scope"),
    ({"branch": "feat/../x"}, "plain branch"),
    ({"repo": "not-mandated"}, "does not name"),
    ({"agent": "codex"}, "cannot be started"),
    ({"brief": ""}, "brief"),
    ({"brief": "x" * (osj.MAX_BRIEF + 1)}, "brief"),
    ({"wall_minutes": 0}, "wall_minutes"),
    ({"wall_minutes": "x"}, "wall_minutes"),
    ({"base": "a b"}, "base"),
])
def test_a_job_outside_the_mandate_is_refused_with_the_reason(mandate, change, why):
    assert why in osj.check_scope(req(**change), mandate)


def test_a_job_inside_the_mandate_is_in_scope(mandate):
    assert osj.check_scope(req(), mandate) is None


# ---- accept on the worker ------------------------------------------------------------------------------------------
def test_the_worker_starts_the_job_detached_and_one_at_a_time(home, mandate, tmp_path):
    started = []
    popen = lambda argv, **kw: started.append(argv)          # noqa: E731
    lock = home / "job.lock"
    r = osj.accept(signed(req()), mandate, lock, popen=popen, pinned=PINNED, find_repo=lambda n: tmp_path)
    assert r["ok"] and started[0][-3:] == ["orchestrate", "session-work", req()["id"]] and lock.is_file()
    job = json.loads((home / "work" / req()["id"] / "job.json").read_text())
    assert job["branch"] == "feat/x" and job["repo_path"] == str(tmp_path) and job["boss_key"] == BOSS[1]
    r2 = osj.accept(signed(req(id="session-other")), mandate, lock, popen=popen, pinned=PINNED,
                    find_repo=lambda n: tmp_path)
    assert not r2["ok"] and "one at a time" in r2["error"] and len(started) == 1


def test_on_linux_the_job_leaves_the_listeners_cgroup_so_a_listener_restart_does_not_kill_it(home):
    argv = osj.launch_argv("session-R-feat_x-1", platform="linux", which=lambda n: "/usr/bin/systemd-run")
    assert argv[:3] == ["/usr/bin/systemd-run", "--user", "--collect"] and "--unit=fieldkit-job-session-R-feat_x-1" in argv
    assert argv[-2:] == ["session-work", "session-R-feat_x-1"] and any(a.startswith("--setenv=PYTHONPATH=") for a in argv)
    assert osj.launch_argv("j", platform="linux", which=lambda n: None)[-2:] == ["session-work", "j"]
    assert osj.launch_argv("j", platform="win32", which=lambda n: "x")[0] != "x"


@pytest.mark.parametrize("make,why", [
    (lambda: req(), "not signed"),
    (lambda: signed(req(), keypair()), "not pinned"),
    (lambda: dict(signed(req()), branch="feat/other"), "does not match"),
    (lambda: signed(req(branch="main")), "base branch"),
])
def test_every_authority_refusal_runs_nothing_and_is_an_inbox_row_on_the_worker(home, mandate, tmp_path, make, why):
    started, rows = [], []
    r = osj.accept(make(), mandate, home / "job.lock", popen=lambda *a, **k: started.append(a), pinned=PINNED,
                   find_repo=lambda n: tmp_path, inbox_add=lambda *a, **k: rows.append((a, k)))
    assert not r["ok"] and why in r["error"] and not started
    assert len(rows) == 1 and rows[0][1]["kind"] == "mesh-job" and why in rows[0][0][1]
    assert json.loads(osj.refusals_path().read_text().splitlines()[-1])["why"] == r["error"]


def test_the_same_refusal_from_the_same_device_is_one_inbox_row_an_hour(home):
    rows = []
    add = lambda *a, **k: rows.append(a)                      # noqa: E731
    t = [1000.0]
    for _ in range(5):
        osj.refused(req(), "bad signature", inbox_add=add, now=lambda: t[0])
    assert len(rows) == 1 and len(osj.refusals_path().read_text().splitlines()) == 5
    osj.refused(req(), "another reason", inbox_add=add, now=lambda: t[0])
    t[0] += osj.REFUSAL_REPEAT_S + 1
    osj.refused(req(), "bad signature", inbox_add=add, now=lambda: t[0])
    assert len(rows) == 3


def test_the_job_protocol_refuses_an_expired_or_missing_mandate_into_the_inbox(home):
    rows = []
    r = jobs.handle_job(signed(req()), find_mandate=lambda repo, s: None, lock=home / "l",
                        inbox_add=lambda *a, **k: rows.append(a))
    assert not r["ok"] and "expired" in r["error"] and len(rows) == 1


def test_the_job_protocol_hands_session_jobs_over_after_its_own_checks(home, mandate, monkeypatch):
    got = []
    monkeypatch.setattr(osj, "accept", lambda req, mpath, **kw: got.append(mpath) or {"ok": True})
    assert jobs.handle_job(signed(req()), find_mandate=lambda repo, s: mandate, lock=home / "l")["ok"]
    assert got == [mandate]


def test_the_real_inbox_gets_the_row_when_none_is_injected(home):
    osj.refused(req(), "stale (clock differs by more than 120 s)")
    assert [i["kind"] for i in inbox.items()] == ["mesh-job"]


def test_a_job_failing_the_cable_check_is_an_inbox_row_too(home):
    from fieldkit.peers import link
    rows = []
    v = link.Verifier(b"k" * 32)
    r = link.handle(dict(req(), mac="00"), v, inbox_add=lambda *a, **k: rows.append(a))
    assert not r["ok"] and r["error"] == "bad signature" and len(rows) == 1 and "cable check" in rows[0][1]
    r = link.handle({"verb": "hello", "mac": "00"}, v, inbox_add=lambda *a, **k: rows.append(a))
    assert not r["ok"] and len(rows) == 1                     # only jobs: a bad hello is logged, not inboxed


# ---- the job itself, on a real git origin --------------------------------------------------------------------------
def sh(*a, cwd=None):
    return subprocess.run(a, cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def origin(tmp_path):
    bare = tmp_path / "origin.git"
    sh("git", "init", "-q", "--bare", "-b", "main", str(bare))
    seed = tmp_path / "seed"
    sh("git", "clone", "-q", str(bare), str(seed))
    for k, v in (("user.email", "t@example.com"), ("user.name", "t")):
        sh("git", "config", k, v, cwd=seed)
    (seed / "README.md").write_text("x\n")
    sh("git", "add", ".", cwd=seed)
    sh("git", "commit", "-q", "-m", "seed", cwd=seed)
    sh("git", "push", "-q", "origin", "main", cwd=seed)
    clone = tmp_path / "device-checkout"
    sh("git", "clone", "-q", str(bare), str(clone))
    for k, v in (("user.email", "t@example.com"), ("user.name", "t")):
        sh("git", "config", k, v, cwd=clone)
    return bare, clone


class Agent:
    """subprocess.run for the job: git runs for real; the agent (fieldkit autopilot) commits `files` in its cwd."""

    def __init__(self, files=("NOTE.md",), rc=0, commit=True):
        self.files, self.rc, self.commit, self.argv = files, rc, commit, None

    def __call__(self, argv, **kw):
        if argv[0] == "git":
            return subprocess.run(argv, **kw)
        self.argv = argv
        for f in self.files:
            (Path(kw["cwd"]) / f).write_text("made by the agent\n")
        if self.commit and self.files:
            sh("git", "add", ".", cwd=kw["cwd"])
            sh("git", "commit", "-q", "-m", "agent work", cwd=kw["cwd"])

        class R:
            returncode = self.rc
        return R()


def real_push(repo, base):
    b = sh("git", "branch", "--show-current", cwd=repo)
    r = subprocess.run(["git", "-C", repo, "push", "-q", "origin", b], capture_output=True, text=True)
    return {"ok": r.returncode == 0, "error": r.stderr}


def run_job(home, mandate, clone, agent, push=real_push, **kw):
    osj.accept(signed(req(**kw)), mandate, home / "job.lock", popen=lambda *a, **k: None, pinned=PINNED,
               find_repo=lambda n: clone)
    delivered = []
    r = osj.work(req(**kw)["id"], run=agent, ship_push=push, deliver=delivered.append, sign=lambda r: r,
                 flags_fn=lambda: {"--job", "--wall-minutes"})
    assert delivered == [r] and not (home / "job.lock").exists()
    return r


def test_a_job_runs_the_agent_in_its_own_worktree_and_the_tool_pushes_the_branch(home, mandate, origin):
    bare, clone = origin
    agent = Agent()
    r = run_job(home, mandate, clone, agent)
    assert r["status"] == "pass" and r["pushed"] and r["branch"] == "feat/x", r["evidence"]
    assert sh("git", "rev-parse", "refs/heads/feat/x", cwd=bare) == r["commit"]
    assert agent.argv[3:6] == ["fieldkit", "autopilot", "claude"] and "Do not push" in agent.argv[6]
    assert "do it" in agent.argv[6] and agent.argv[agent.argv.index("--wall-minutes") + 1] == "30"
    assert sh("git", "branch", "--show-current", cwd=clone) == "main"          # the device's own checkout untouched
    assert not (home / "work" / r["id"] / "tree").exists()                     # its scratch worktree removed


def test_a_job_on_an_existing_remote_branch_continues_it(home, mandate, origin):
    bare, clone = origin
    first = run_job(home, mandate, clone, Agent(files=("A.md",)))
    second = run_job(home, mandate, clone, Agent(files=("B.md",)), brief="more")
    assert second["status"] == "pass"
    assert sh("git", "rev-list", "--count", f"{first['commit']}..{second['commit']}", cwd=bare) == "1"


def test_no_commit_a_failed_agent_or_a_failed_push_is_a_failure_with_a_concern(home, mandate, origin):
    _, clone = origin
    r = run_job(home, mandate, clone, Agent(files=()))
    assert r["status"] == "fail" and "no commit" in r["concerns"][0]["text"] and not r["pushed"]
    r = run_job(home, mandate, clone, Agent(rc=1), brief="b2")
    assert r["status"] == "fail" and r["pushed"]                               # its commits still reach origin
    r = run_job(home, mandate, clone, Agent(files=("C.md",)), push=lambda repo, base: {"ok": False, "error": "denied"},
                brief="b3")
    assert r["status"] == "fail" and not r["pushed"] and "denied" in r["concerns"][0]["text"]
    r = run_job(home, mandate, clone, Agent(files=("D.md",)), push=lambda repo, base: {"ok": True}, brief="b4")
    assert r["status"] == "fail" and not r["pushed"]          # the tool said ok but origin does not have it: not pushed
    r = run_job(home, mandate, clone, Agent(files=("G.md",)), brief="b5")
    assert r["status"] == "pass" and r["pushed"]               # the next job carries the unpushed C and D along
    assert {"C.md", "D.md", "G.md"} <= set(sh("git", "ls-tree", "--name-only", r["commit"], cwd=clone).split())


def test_uncommitted_work_is_reported_and_kept(home, mandate, origin):
    _, clone = origin
    r = run_job(home, mandate, clone, Agent(files=("E.md",), commit=False))
    assert r["status"] == "fail" and any("uncommitted" in c["text"] for c in r["concerns"])
    assert (home / "work" / r["id"] / "tree" / "E.md").is_file()
    again = run_job(home, mandate, clone, Agent(files=("F.md",)), brief="next")
    assert again["status"] == "not-run" and "uncommitted work" in again["evidence"]
    assert (home / "work" / r["id"] / "tree" / "E.md").is_file()


def test_a_branch_the_devices_own_checkout_has_out_is_not_taken_over(home, mandate, origin):
    _, clone = origin
    sh("git", "checkout", "-q", "-b", "feat/x", cwd=clone)
    r = run_job(home, mandate, clone, Agent())
    assert r["status"] == "not-run" and "not taken over" in r["evidence"]
    assert sh("git", "branch", "--show-current", cwd=clone) == "feat/x"


def test_a_local_branch_that_diverged_from_origin_is_never_overwritten(home, mandate, origin):
    bare, clone = origin
    other = bare.parent / "other"
    sh("git", "clone", "-q", str(bare), str(other))
    sh("git", "checkout", "-q", "-b", "feat/x", cwd=other)
    (other / "THEIRS.md").write_text("theirs\n")
    sh("git", "add", ".", cwd=other)
    sh("git", "-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-q", "-m", "theirs", cwd=other)
    sh("git", "push", "-q", "origin", "feat/x", cwd=other)
    sh("git", "checkout", "-q", "-b", "feat/x", cwd=clone)
    (clone / "LOCAL.md").write_text("mine\n")
    sh("git", "add", ".", cwd=clone)
    sh("git", "commit", "-q", "-m", "local only", cwd=clone)
    mine = sh("git", "rev-parse", "HEAD", cwd=clone)
    sh("git", "checkout", "-q", "main", cwd=clone)
    r = run_job(home, mandate, clone, Agent())
    assert r["status"] == "not-run" and "diverged" in r["evidence"]
    assert sh("git", "rev-parse", "refs/heads/feat/x", cwd=clone) == mine


def test_a_signed_session_result_covers_branch_and_pushed():
    r = {"id": "x", "kind": "session", "repo": "R", "commit": "a" * 40, "status": "pass", "evidence": "", "concerns": [],
         "os": "LINUX", "host": "h", "at": "t", "key": "k", "branch": "feat/x", "pushed": True}
    assert jobs._canon(r) != jobs._canon(dict(r, pushed=False)) != jobs._canon(dict(r, branch="feat/y"))
    old = {k: v for k, v in r.items() if k not in ("branch", "pushed")}
    assert b"branch" not in jobs._canon(old)                                    # older results sign as before


# ---- the boss ------------------------------------------------------------------------------------------------------
def test_the_boss_refuses_out_of_scope_before_sending_and_files_a_refusal(home, mandate, monkeypatch):
    calls, rows = [], []
    monkeypatch.setattr(M, "_sha", lambda p: MSHA)
    jid, said = osj.send("vaio", "Gorilla.Fieldkit", "main", "x", call=lambda r, device: calls.append(r),
                         find_mandate=lambda repo: mandate, key_fn=lambda: BOSS[:2])
    assert said.startswith("not sent") and "base branch" in said and not calls
    jid, said = osj.send("vaio", "Gorilla.Fieldkit", "feat/x", "do it",
                         call=lambda r, device: calls.append(r) or {"ok": False, "error": "no mandate"},
                         find_mandate=lambda repo: mandate, key_fn=lambda: BOSS[:2],
                         inbox_add=lambda *a, **k: rows.append(a))
    assert said == "not taken: no mandate" and len(rows) == 1 and osj.boss_check(calls[0], PINNED) is None
    jid, said = osj.send("vaio", "Gorilla.Fieldkit", "feat/x", "do it", call=lambda r, device: {"ok": True},
                         find_mandate=lambda repo: mandate, key_fn=lambda: BOSS[:2])
    assert said == "sent" and jobs._ledger()[jid]["status"] == "sent"
    again = osj.send("vaio", "Gorilla.Fieldkit", "feat/x", "do it", call=lambda r, device: 1 / 0,
                     find_mandate=lambda repo: mandate, key_fn=lambda: BOSS[:2])
    assert again[1].startswith("already sent")


def test_the_boss_without_a_mandate_in_force_sends_nothing(home):
    assert osj.send("vaio", "Gorilla.Fieldkit", "feat/x", "x", find_mandate=lambda repo: None,
                    call=lambda r, device: 1 / 0)[1].startswith("no mandate")
