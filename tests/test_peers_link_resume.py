"""A listener that restarts keeps its promise (2026-10-28): the I9 job ended with its branch local only, because the
listener running then predated the branch return and a restart would have lost the follow anyway. Fixture repositories
and job folders only."""
import json
import os
import subprocess
import time

from fieldkit.peers import link_keepalive, link_return as lr

T0 = 1_800_000_000


def git(repo, *a, when=None):
    env = dict(os.environ, GIT_COMMITTER_DATE=f"@{when} +0000", GIT_AUTHOR_DATE=f"@{when} +0000") if when else None
    return subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True, text=True, env=env).stdout


def job(tmp_path, at, checkpoint=None, follow=None, returned=False):
    d = tmp_path / "jobs" / (time.strftime("%Y%m%d-%H%M%S", time.localtime(at)) + "-claude")
    d.mkdir(parents=True)
    if checkpoint is not None:
        (d / "CHECKPOINT.json").write_text(json.dumps(checkpoint), encoding="utf-8")
    if follow is not None:
        (d / "follow.json").write_text(json.dumps(follow), encoding="utf-8")
    if returned:
        (d / "branch-return.json").write_text("{}", encoding="utf-8")
    return d


def test_follow_leaves_what_a_new_listener_needs(tmp_path):
    class P:
        pid = 4242

        def wait(self):
            return 0
    d = tmp_path / "j"
    d.mkdir()
    lr.follow(P(), tmp_path, {"main": "abc"}, d, "fieldkit-task T-1 do O2").join(5)
    rec = json.loads((d / "follow.json").read_text(encoding="utf-8"))
    assert rec["pid"] == 4242 and rec["before"] == {"main": "abc"} and "T-1" in rec["brief"]


def test_a_starting_listener_owes_recent_unreturned_jobs_only(tmp_path):
    now = T0
    a = job(tmp_path, now - 600, follow={"pid": 1, "cwd": "x", "before": {}})
    b = job(tmp_path, now - 1200, checkpoint={"cwd": "x"})                     # an older listener's run
    job(tmp_path, now - 300, follow={"pid": 1, "cwd": "x"}, returned=True)       # already home
    job(tmp_path, now - 3 * 86400, checkpoint={"cwd": "x"})                      # history
    job(tmp_path, now - 100)                                                     # nothing to finish it with
    assert lr.pending(tmp_path / "jobs", now=now) == [b, a]
    assert lr.resume(tmp_path / "jobs", now=now, start=False) == [b, a]


def test_ended_by_checkpoint_or_by_a_gone_process(tmp_path):
    d = job(tmp_path, T0, checkpoint={"cwd": "x"})
    assert not lr.ended(d, 7, alive=lambda p: True)
    assert lr.ended(d, 7, alive=lambda p: False)
    assert not lr.ended(d, None, alive=lambda p: False)                        # no pid, no end: keep waiting
    (d / "CHECKPOINT.json").write_text(json.dumps({"ended_at": T0 + 60}), encoding="utf-8")
    assert lr.ended(d, 7, alive=lambda p: True)


def test_finish_waits_then_brings_home_with_the_recorded_snapshot(tmp_path):
    d = job(tmp_path, T0, follow={"pid": 9, "cwd": str(tmp_path), "before": {"main": "a"}, "brief": "b"})
    states, naps, seen = iter([True, True, False]), [], {}

    def bring(proc, cwd, before, jobd, brief, run=None, branches=None):
        seen.update(cwd=cwd, before=before, brief=brief, branches=branches)
        return {"ok": True}
    assert lr.finish(d, alive=lambda p: next(states), sleep=naps.append, bring=bring) == {"ok": True}
    assert len(naps) == 2 and seen == {"cwd": str(tmp_path), "before": {"main": "a"}, "brief": "b", "branches": None}


def test_an_older_listeners_job_pushes_the_branches_made_in_its_window(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    start = T0
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "t@example.org")
    git(repo, "config", "user.name", "t")
    git(repo, "commit", "-q", "--allow-empty", "-m", "base", when=start - 3600)
    for b, when in [("feat/old", start - 1800), ("feat/made", start + 300), ("fix/pushed", start + 400),
                    ("wip/made", start + 500), ("fix/after", start + 9000)]:
        git(repo, "switch", "-q", "-c", b, "main")
        git(repo, "commit", "-q", "--allow-empty", "-m", b, when=when)
    git(repo, "update-ref", "refs/remotes/origin/fix/pushed", "fix/pushed")
    d = job(tmp_path, start, checkpoint={"cwd": str(repo), "ended_at": start + 600, "result": "done"})
    assert lr.window_branches(repo, d) == ["feat/made"]
    seen = {}

    def bring(proc, cwd, before, jobd, brief, run=None, branches=None):
        seen.update(branches=branches, brief=brief)
    lr.finish(d, bring=bring)
    assert seen == {"branches": ["feat/made"], "brief": "done"}


def test_bring_home_pushes_the_given_branches_when_there_is_no_snapshot(tmp_path):
    pushed, sent = [], []
    rep = lr.bring_home(None, tmp_path, {}, tmp_path, "fieldkit-task T-9 x", branches=["feat/made"],
                        push=lambda path: pushed.append(path) or {"ok": True, "pushed": True},
                        send=lambda text, needs_you=False: sent.append(text),
                        run=lambda *a, **k: subprocess.CompletedProcess(a, 0, "", ""))
    assert [r["branch"] for r in rep["pushed"]] == ["feat/made"] and rep["task"] == "T-9"
    assert json.loads((tmp_path / "branch-return.json").read_text(encoding="utf-8"))["task"] == "T-9"
    assert sent and "feat/made" in sent[0]


def test_a_restart_stops_the_listener_only():
    for text in link_keepalive.units(root="/r", py="/p").values():
        assert "\nKillMode=process\n" in text
