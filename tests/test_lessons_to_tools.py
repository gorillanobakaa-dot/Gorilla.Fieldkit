# Version: 1.0.0 · updated 26-10-28-19-02
"""2026-10-28's lessons made checks (HARNESS-PLAN I8, docs/LESSONS.yaml): each job in its own worktree, no second
start of the same brief, a sync exit code a waiter can trust, and the plan line counting lessons with no tool."""
import json
import subprocess
from pathlib import Path

import pytest

from fieldkit import plan
from fieldkit.core import sync
from fieldkit.lifecycle import tasks
from fieldkit.lifecycle.worktrees import Refused, make as real_make, reap as real_reap   # before conftest stubs them

NOW = 1_790_000_000
NOGIT = lambda *a, **k: type("R", (), {"returncode": 1, "stdout": ""})()                 # noqa: E731


@pytest.fixture(autouse=True)
def iso(tmp_path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_LIFECYCLE_DIR", str(tmp_path / "lc"))       # the ledger: never the checkout's state/
    monkeypatch.setenv("FIELDKIT_ROOM_DIR", str(tmp_path / "room"))
    monkeypatch.setenv("FIELDKIT_COORDINATION_DIR", str(tmp_path / "coord"))
    (tmp_path / "room").mkdir()
    from fieldkit.autopilot import room
    monkeypatch.setattr(room, "ROOM_DIR", tmp_path / "room")


def _g(d, *a):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-C", str(d), *a],
                          check=True, capture_output=True, text=True).stdout.strip()


def _repo(tmp_path):
    hub = tmp_path / "hub.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(hub)], check=True)
    co = tmp_path / "checkout"
    subprocess.run(["git", "clone", "-q", str(hub), str(co)], check=True, capture_output=True)
    (co / "a.txt").write_text("1\n")
    _g(co, "add", "-A")
    _g(co, "commit", "-qm", "one")
    _g(co, "push", "-q", "origin", "HEAD:main")
    _g(co, "fetch", "-q", "origin")
    return co


def brief(tmp_path, text="Do the job.\nMore. Plan O3.", name="brief.md"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


# ---- L-20261028-6: every job in its own worktree ---------------------------------------------------------------

def test_a_job_gets_its_own_worktree_at_origin_main_and_it_goes_once_its_branch_is_on_origin(tmp_path):
    co, root = _repo(tmp_path), tmp_path / "scratch"
    _g(co, "switch", "-qc", "feat/elsewhere")                    # the main checkout is on another branch
    a, why = real_make(co, "T-1-a", root=root)
    b, _ = real_make(co, "T-2-b", root=root)
    assert a and b and a != b and a.parent == root and "own worktree" in why
    assert _g(a, "rev-parse", "HEAD") == _g(co, "rev-parse", "origin/main")
    assert _g(co, "rev-parse", "--abbrev-ref", "HEAD") == "feat/elsewhere"          # untouched by the jobs
    with pytest.raises(Refused, match="never shares a tree"):
        real_make(co, "T-1-a", root=root)
    assert real_make(tmp_path / "scratch", "T-3", root=tmp_path / "s2")[0] is None   # outside git: in place
    Path(str(a) + ".json").write_text(json.dumps({"pid": 99}))
    Path(str(b) + ".json").write_text(json.dumps({"pid": 98}))
    _g(a, "switch", "-qc", "feat/job-a")
    (a / "b.txt").write_text("2\n")
    _g(a, "add", "-A")
    _g(a, "commit", "-qm", "job a")
    r = real_reap(co, root=root, alive=lambda pid: pid == 98)
    assert r["removed"] == [] and any("not on origin" in w for p, w in r["kept"] if Path(p).name == a.name)
    assert any("running" in w for p, w in r["kept"] if Path(p).name == b.name)
    _g(a, "push", "-q", "origin", "feat/job-a")                  # shipped
    (a / "c.txt").write_text("dirty\n")
    _g(a, "add", "c.txt")
    r = real_reap(co, root=root, alive=lambda pid: False)
    assert [Path(p).name for p in r["removed"]] == [b.name] and not b.exists()      # clean at origin/main, ended
    assert any("uncommitted" in w for p, w in r["kept"] if Path(p).name == a.name)
    _g(a, "reset", "-q", "--hard")
    r = real_reap(co, root=root, alive=lambda pid: False)
    assert [Path(p).name for p in r["removed"]] == [a.name] and not a.exists() and not b.exists()
    assert _g(co, "rev-parse", "feat/job-a")                     # the branch stays; only the tree went


def test_reap_never_touches_a_worktree_it_did_not_make(tmp_path):
    co, root = _repo(tmp_path), tmp_path / "scratch"
    other = root / "wt-lessons"
    root.mkdir()
    _g(co, "worktree", "add", "-q", "--detach", str(other), "origin/main")
    Path(str(other) + ".json").write_text(json.dumps({"pid": 5}))
    assert real_reap(co, root=root, alive=lambda pid: False)["removed"] == [] and other.exists()


def test_task_start_here_runs_the_job_in_the_worktree_it_made(tmp_path):
    made = []
    rec = tasks.start(brief(tmp_path), cwd=tmp_path, launch=lambda a: {"pid": 7, "log": "L"}, now=NOW,
                      worktree=lambda repo, tid: made.append((repo, tid)) or (tmp_path / "wt-job-x", "own worktree"))
    assert made == [(tmp_path.resolve(), rec["id"])] and rec["worktree"] == str(tmp_path / "wt-job-x")
    assert f"cwd: {tmp_path / 'wt-job-x'}" in Path(rec["runbook"]).read_text(encoding="utf-8")
    assert json.loads((tmp_path / "wt-job-x.json").read_text())["pid"] == 7


def test_no_worktree_in_a_git_repository_means_the_job_is_not_started(tmp_path, monkeypatch):
    def no(repo, name):
        raise Refused("git worktree add x failed: disk full")
    launched = []
    rec = tasks.start(brief(tmp_path), cwd=tmp_path, launch=launched.append, now=NOW, worktree=no)
    assert rec["status"] == "not-started" and "disk full" in rec["answer"] and launched == []
    assert tasks.ledger()[rec["id"]]["status"] == "not-started"
    from fieldkit.peers import link
    monkeypatch.setenv("FIELDKIT_LINK_HOME", str(tmp_path))
    (tmp_path / "link.yaml").write_text("allow_start: [claude]\ncwd: " + str(tmp_path).replace("\\", "/") + "\n")
    link.approve(confirm=True, say=lambda m: None)
    ok, why = link.start_session("claude", "fieldkit-task T-9-z", "win", popen=lambda *a, **k: launched.append(a),
                                 inbox_add=lambda *a, **k: None, flags_fn=lambda: set(), sleep=lambda s: None,
                                 check_fn=lambda a: (True, ""), follow=lambda *a: None, worktree=no)
    assert not ok and "no worktree of its own" in why and launched == []


def test_the_cable_listener_starts_its_job_in_its_own_worktree(tmp_path, monkeypatch):
    from fieldkit.peers import link
    monkeypatch.setenv("FIELDKIT_LINK_HOME", str(tmp_path))
    (tmp_path / "link.yaml").write_text("allow_start: [claude]\ncwd: " + str(tmp_path).replace("\\", "/") + "\n")
    link.approve(confirm=True, say=lambda m: None)
    seen, wt = {}, tmp_path / "wt-job-T-9-z"
    proc = type("P", (), {"pid": 7, "poll": lambda self: None})()
    ok, why = link.start_session("claude", "fieldkit-task T-9-z", "win",
                                 popen=lambda argv, **k: seen.update(argv=argv, cwd=k["cwd"]) or proc,
                                 inbox_add=lambda *a, **k: None, flags_fn=lambda: set(), sleep=lambda s: None,
                                 check_fn=lambda a: (True, ""), follow=lambda *a: seen.update(follow=a),
                                 worktree=lambda repo, name: (wt, f"own worktree {wt} ({name})"))
    assert ok and seen["cwd"] == str(wt) and str(wt) in seen["argv"] and "T-9-z" in why
    assert seen["follow"][1] == str(tmp_path).replace("\\", "/")      # branches are refs of the checkout: found there
    assert json.loads((tmp_path / "wt-job-T-9-z.json").read_text())["pid"] == 7


# ---- L-20261028-7: no second start of the same brief --------------------------------------------------------------

def test_a_second_start_of_the_same_brief_is_refused_while_the_first_runs(tmp_path, monkeypatch, capsys):
    from fieldkit import lifecycle as lc
    from fieldkit.peers import peers
    monkeypatch.setattr(peers, "pid_alive", lambda pid: True)
    launched = []
    go = lambda a: launched.append(a) or {"pid": 7}                                     # noqa: E731
    first = tasks.start(brief(tmp_path), cwd=tmp_path, launch=go, now=NOW)
    with pytest.raises(tasks.AlreadyStarted) as e:
        tasks.start(brief(tmp_path, name="copy.md"), cwd=tmp_path, launch=go, now=NOW + 60)    # same text
    assert first["id"] in str(e.value) and "--again" in str(e.value) and len(launched) == 1
    with pytest.raises(tasks.AlreadyStarted):                                           # same title, other text
        tasks.start(brief(tmp_path, "Do the job.\nOther words O3.", "b2.md"), cwd=tmp_path, launch=go, now=NOW + 60)
    with pytest.raises(tasks.AlreadyStarted):                                           # on another machine too
        tasks.start(brief(tmp_path), on="vaio", now=NOW + 60,
                    call=lambda req, timeout, device: {"ok": True, "started": True})
    again = tasks.start(brief(tmp_path), cwd=tmp_path, launch=go, now=NOW + 61, again=True)
    assert again["status"] == "started" and len(launched) == 2
    monkeypatch.setattr(tasks.time, "time", lambda: NOW + 120)
    assert lc.cmd_task(["start", str(brief(tmp_path)), "--cwd", str(tmp_path)]) == 3
    assert "NOT STARTED  already started" in capsys.readouterr().out


def test_a_finished_died_or_overdue_twin_does_not_block(tmp_path, monkeypatch):
    from fieldkit.peers import peers
    alive = {"v": True}
    monkeypatch.setattr(peers, "pid_alive", lambda pid: alive["v"])
    go = lambda a: {"pid": 7}                                                           # noqa: E731
    a = tasks.start(brief(tmp_path), cwd=tmp_path, launch=go, now=NOW, wall=30)
    tasks.report(a["id"], "done")
    b = tasks.start(brief(tmp_path), cwd=tmp_path, launch=go, now=NOW + 1, wall=30)      # done: free again
    alive["v"] = False
    c = tasks.start(brief(tmp_path), cwd=tmp_path, launch=go, now=NOW + 2, wall=30)      # b's process is gone
    alive["v"] = True
    with pytest.raises(tasks.AlreadyStarted):
        tasks.start(brief(tmp_path), cwd=tmp_path, launch=go, now=NOW + 2 + 59 * 60)      # c still in budget
    d = tasks.start(brief(tmp_path), cwd=tmp_path, launch=go, now=NOW + 2 + 61 * 60)      # past wall + slack
    assert b["status"] == c["status"] == d["status"] == "started"


# ---- L-20261028-8: sync's exit code and words a waiter can trust -------------------------------------------------

ROWS_BAD = [{"repo": "a", "ok": True, "verdict": "IN-SYNC", "why": "main = origin/main"},
            {"repo": "b", "ok": False, "verdict": "IN-SYNC", "why": "feat = origin/feat; origin/main has 2 commit(s)"}]


def test_sync_not_in_sync_is_exit_3_and_never_says_in_sync(capsys, monkeypatch):
    out = "\n".join(sync.lines(ROWS_BAD))
    assert "IN SYNC" not in out and "IN-SYNC" not in out.splitlines()[1] and out.splitlines()[-1].startswith(
        "SYNC: DIFFERS")
    assert sync.exit_code(ROWS_BAD) == 3 and sync.exit_code(ROWS_BAD[:1]) == 0 and sync.exit_code([]) == 3
    j = sync.as_json(ROWS_BAD)
    assert j["in_sync"] is False and j["exit"] == 3 and [r["verdict"] for r in j["repos"]] == ["IN-SYNC", "IN-SYNC"]
    assert sync.lines(ROWS_BAD[:1])[-1] == "SYNC: OK"
    from fieldkit import cli
    monkeypatch.setattr(sync, "run", lambda **k: ROWS_BAD)
    assert cli.main(["sync", "--json", "--no-fetch"]) == 3
    assert json.loads(capsys.readouterr().out)["in_sync"] is False
    monkeypatch.setattr(sync, "run", lambda **k: ROWS_BAD[:1])
    assert cli.main(["sync", "--no-fetch"]) == 0 and capsys.readouterr().out.strip().endswith("SYNC: OK")


# ---- docs/LESSONS.yaml on the plan line --------------------------------------------------------------------------

def test_the_plan_line_counts_lessons_with_no_tool_or_no_test(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("")
    (tmp_path / plan.PLAN).write_text("## Order\n| phase | items |\n|---|---|\n| 1 | R1 |\n\n| R1 | x | PLANNED |\n")
    assert "LESSONS.yaml missing" in plan.next_(tmp_path, run=NOGIT)["line"]
    (tmp_path / plan.LESSONS).write_text(
        "lessons:\n"
        "  - {id: L-1, what: a, tool: t, test: tests/test_x.py}\n"
        "  - {id: L-2, what: b, tool: '', test: tests/test_x.py}\n"
        "  - {id: L-3, what: c, tool: t, test: tests/test_missing.py}\n"
        "  - {id: L-4, what: d, tool: t}\n")
    r = plan.next_(tmp_path, run=NOGIT)
    assert r["lessons"] == {"total": 4, "untooled": ["L-2", "L-3", "L-4"]}
    assert r["line"].endswith("; lessons with no tool yet: 3 of 4")
    (tmp_path / plan.LESSONS).write_text("lessons:\n  - {id: L-1, what: a, tool: t, test: tests/test_x.py}\n")
    assert "lessons" not in plan.next_(tmp_path, run=NOGIT)["line"]


def test_every_lesson_in_this_checkout_names_a_tool_and_a_test_that_exists():
    ls = plan.lessons(plan.ROOT)
    assert ls and ls["total"] >= 10 and ls["untooled"] == []
