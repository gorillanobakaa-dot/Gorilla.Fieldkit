"""R13: the verifier judges a fresh worktree of origin/main; a worker never runs in the main checkout."""
import subprocess
import sys

import pytest

from fieldkit.autopilot import goal, goal_fresh, hardlines, inbox, runbook
from fieldkit.lifecycle import worktrees

REAL_MAKE = worktrees.make          # conftest stubs worktrees.make per test: keep the real one
PY = sys.executable.replace("\\", "/")


def git(cwd, *a):
    r = subprocess.run(["git", "-C", str(cwd), *a], capture_output=True, text=True,
                       env={**__import__("os").environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


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
    monkeypatch.setenv("FIELDKIT_SCRATCH_ROOT", str(tmp_path / "scr"))
    monkeypatch.setenv("FIELDKIT_SCRATCH_STATE", str(tmp_path / "scrstate"))
    for k, v in (("GIT_AUTHOR_NAME", "t"), ("GIT_AUTHOR_EMAIL", "t@t"), ("GIT_COMMITTER_NAME", "t"),
                 ("GIT_COMMITTER_EMAIL", "t@t")):
        monkeypatch.setenv(k, v)
    from fieldkit.core import eta
    monkeypatch.setattr(eta, "HISTORY", tmp_path / "history.jsonl")


@pytest.fixture
def main(tmp_path):
    """A main checkout whose origin/main passes the check, left on a branch where it fails (the 11-02 hazard)."""
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    m = tmp_path / "main"
    subprocess.run(["git", "clone", "-q", str(bare), str(m)], check=True, capture_output=True)
    git(m, "checkout", "-q", "-b", "main")
    (m / "check.py").write_text("import sys; sys.exit(0)\n")
    (m / ".gitignore").write_text("local/\n")
    git(m, "add", "-A")
    git(m, "commit", "-q", "-m", "good")
    git(m, "push", "-q", "origin", "main")
    git(m, "checkout", "-q", "-b", "stale")
    (m / "check.py").write_text("import sys; sys.exit(1)\n")
    git(m, "commit", "-q", "-am", "stale work")
    (m / "local" / "goal-checks").mkdir(parents=True)
    (m / "local" / "goal-checks" / "c.py").write_text("import sys; sys.exit(0)\n")
    return m


def mk(main, tmp_path, run="python check.py", jobs=()):
    goal.new({"id": "g1", "objective": "o", "cwd": str(main), "seed_jobs": list(jobs),
              "criteria": [{"id": "c1", "run": f'"{PY}" check.py' if sys.platform != "win32" else f'& "{PY}" check.py'}]})
    return goal.load("g1")


def test_the_check_runs_on_origin_main_while_the_main_checkout_sits_on_a_stale_branch(main, tmp_path):
    g, st = mk(main, tmp_path)
    res = goal.verify("g1", g, st, ids=["c1"])
    assert [r["verdict"] for r in res] == ["PASS"]
    assert res[0]["tree"].startswith("origin/main ")
    assert git(main, "branch", "--show-current") == "stale"                 # the main checkout was never touched
    assert (main / "check.py").read_text().endswith("exit(1)\n")
    assert git(main, "worktree", "list").count("\n") == 0                  # the fresh tree is removed
    assert not list((tmp_path / "scr").glob("verify-*"))                    # and its scratch job folder


def test_local_goal_checks_are_copied_into_the_tree(main, tmp_path):
    g, st = mk(main, tmp_path)
    c = {"id": "x", "run": f'"{PY}" local/goal-checks/c.py' if sys.platform != "win32"
         else f'& "{PY}" local/goal-checks/c.py'}
    assert goal.verify("g1", g, st, ids=[], extra=[c])[0]["verdict"] == "PASS"


def test_a_folder_not_in_git_is_judged_where_it_is(tmp_path):
    d = tmp_path / "plain"
    d.mkdir()
    (d / "check.py").write_text("import sys; sys.exit(0)\n")
    goal.new({"id": "g1", "objective": "o", "cwd": str(d),
              "criteria": [{"id": "c1", "run": f'"{PY}" check.py' if sys.platform != "win32" else f'& "{PY}" check.py'}]})
    g, st = goal.load("g1")
    r = goal.verify("g1", g, st)[0]
    assert r["verdict"] == "PASS" and "tree" not in r


def test_no_fresh_tree_is_a_fail_never_the_main_checkout(main, tmp_path, monkeypatch):
    g, st = mk(main, tmp_path)

    def boom(self):
        raise goal_fresh.Refused("disk full")
    monkeypatch.setattr(goal_fresh.Tree, "__enter__", boom)
    r = goal.verify("g1", g, st)[0]
    assert r["verdict"] == "FAIL" and "no fresh tree: disk full" in r["why"]


def test_a_worker_gets_its_own_worktree_and_the_main_checkout_keeps_its_branch(main, tmp_path):
    cwd, why = goal_fresh.worker_cwd("g1", "j1", str(main), make=REAL_MAKE)
    assert cwd != str(main) and "own worktree" in why
    assert git(main, "branch", "--show-current") == "stale"
    assert not goal_fresh.is_main_checkout(cwd) and goal_fresh.is_main_checkout(main)
    again, why2 = goal_fresh.worker_cwd("g1", "j1", str(main), prior=cwd, attempt=2)
    assert again == cwd and "kept" in why2


def test_a_job_whose_cwd_is_the_main_checkout_and_gets_no_worktree_is_refused(main, tmp_path, monkeypatch):
    def no(*a, **k):
        raise worktrees.Refused("scratch is full")
    monkeypatch.setattr(worktrees, "make", no)
    with pytest.raises(goal_fresh.Refused, match="main checkout"):
        goal_fresh.worker_cwd("g1", "j1", str(main))
    g, st = mk(main, tmp_path, jobs=[{"id": "j1", "brief": "b", "cwd": str(main), "touches": ["a"]}])
    st.update(needs_plan=False, rounds=1)
    goal.save_state("g1", st)
    ran = []
    goal.step("g1", run_job=lambda *a, **k: ran.append(a) or {"status": "done"}, say=lambda m: None)
    _, st = goal.load("g1")
    assert ran == [] and st["jobs"]["j1"]["status"] == "failed" and "refused" in st["jobs"]["j1"]["why"]


def test_a_cwd_outside_git_is_left_alone(tmp_path):
    d = tmp_path / "plain"
    d.mkdir()
    assert goal_fresh.worker_cwd("g1", "j1", str(d)) == (str(d), "not the main checkout")


def test_reverify_runs_one_jobs_checks_on_the_fresh_tree_and_marks_it_verified(main, tmp_path):
    chk = f'"{PY}" check.py' if sys.platform != "win32" else f'& "{PY}" check.py'
    g, st = mk(main, tmp_path, jobs=[{"id": "j1", "brief": "b", "cwd": str(main), "touches": ["a"],
                                      "verify": [{"run": chk}, {"run": chk}]}])
    st["jobs"]["j1"]["status"] = "failed-verify"
    goal.save_state("g1", st)
    said = []
    ok, text = goal_fresh.reverify("g1", "j1", say=said.append)
    _, st = goal.load("g1")
    assert ok and text.startswith("PASS 2/2 for job j1 of goal g1 on origin/main ")
    assert st["jobs"]["j1"]["status"] == "verified" and "re-verified" in st["jobs"]["j1"]["why"]
    assert len(said) == 2 and all("origin/main" in s for s in said)
    assert git(main, "branch", "--show-current") == "stale"


def test_reverify_with_a_supervisor_running_leaves_the_marker_for_its_next_round(main, tmp_path, monkeypatch):
    chk = f'"{PY}" check.py' if sys.platform != "win32" else f'& "{PY}" check.py'
    g, st = mk(main, tmp_path, jobs=[{"id": "j1", "brief": "b", "cwd": str(main), "touches": ["a"],
                                      "verify": [{"run": chk}]}])
    st["jobs"]["j1"]["status"] = "failed-verify"
    goal.save_state("g1", st)
    goal._wj(goal.GOALS / ".supervisor.lock", {"pid": 1, "heartbeat": 9e12})
    monkeypatch.setattr(goal, "_holder_alive", lambda lk: True)
    ok, _ = goal_fresh.reverify("g1", "j1", say=lambda m: None)
    _, st = goal.load("g1")
    assert ok and st["jobs"]["j1"]["status"] == "failed-verify"             # not written behind the supervisor's back
    assert goal_fresh.apply_markers("g1", st) == ["j1"] and st["jobs"]["j1"]["status"] == "verified"


def test_reverify_names_a_missing_goal_or_job(main, tmp_path):
    assert goal_fresh.reverify("nope", "j1")[0] is False
    mk(main, tmp_path)
    ok, text = goal_fresh.reverify("g1", "zz")
    assert not ok and "no job zz" in text


def test_a_failing_recheck_leaves_the_job_as_it_was(main, tmp_path):
    bad = f'"{PY}" -c "import sys; sys.exit(3)"' if sys.platform != "win32" else f'& "{PY}" -c "import sys; sys.exit(3)"'
    g, st = mk(main, tmp_path, jobs=[{"id": "j1", "brief": "b", "cwd": str(main), "touches": ["a"],
                                      "verify": [{"run": bad}]}])
    st["jobs"]["j1"]["status"] = "failed-verify"
    goal.save_state("g1", st)
    ok, text = goal_fresh.reverify("g1", "j1", say=lambda m: None)
    _, st = goal.load("g1")
    assert not ok and text.startswith("FAIL 0/1") and st["jobs"]["j1"]["status"] == "failed-verify"
