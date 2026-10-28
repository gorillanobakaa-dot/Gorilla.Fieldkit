"""A job started over the cable brings its branch home (link_return), and `peers link restart` fails loudly when the
new listener is not the one on the port (link_ops.restart). Found 2026-10-28 on the VAIO."""
import json
import subprocess

import pytest

from fieldkit.peers import link, link_ops, link_return as R


def _git(cwd, *a):
    return subprocess.run(["git", "-C", str(cwd), *a], capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _git(r, "config", "user.email", "t@t")
    _git(r, "config", "user.name", "t")
    (r / "a").write_text("1")
    _git(r, "add", "a")
    _git(r, "commit", "-qm", "one")
    return r


def _commit_on(repo, branch, wt=None):
    where = wt or repo
    if wt:
        _git(repo, "worktree", "add", "-q", "-b", branch, str(wt))
    else:
        _git(repo, "branch", branch)
        _git(repo, "checkout", "-q", branch)
    (where / "b").write_text(branch)
    _git(where, "add", "b")
    _git(where, "commit", "-qm", f"on {branch}")
    if not wt:
        _git(repo, "checkout", "-q", "main")


def test_the_branch_a_run_made_is_pushed_and_the_command_post_told(repo, tmp_path):
    before = R.heads(repo)
    assert set(before) == {"main"}
    _commit_on(repo, "fix/thing")                       # not checked out anywhere: a scratch worktree pushes it
    _commit_on(repo, "feat/other", wt=tmp_path / "wt")  # checked out in the run's own worktree
    _commit_on(repo, "wip/job")                         # not covered by the mandate: left, and named
    pushed_from, sent = [], []

    def push(path):
        pushed_from.append((_git(path, "branch", "--show-current"), path))
        return {"ok": True, "pushed": True}
    rep = R.bring_home(object(), repo, before, tmp_path, brief="fieldkit-task T-1-x done", push=push,
                       send=lambda text, needs_you=False: sent.append((text, needs_you)))
    assert [b for b, _ in pushed_from] == ["feat/other", "fix/thing"]
    assert dict(pushed_from)["feat/other"] == str(tmp_path / "wt")
    assert [r["branch"] for r in rep["pushed"] if r["pushed"]] == ["feat/other", "fix/thing"]
    assert rep["left_local"] == ["wip/job"] and rep["task"] == "T-1-x"
    assert "fk-return-" not in _git(repo, "worktree", "list")          # the scratch worktree is gone
    assert json.loads((tmp_path / "branch-return.json").read_text())["task"] == "T-1-x"
    (text, needs_you), = sent
    assert "fix/thing pushed" in text and "wip/job" in text and needs_you    # something stayed: the owner hears


def test_a_refused_push_is_reported_not_swallowed(repo, tmp_path):
    before = R.heads(repo)
    _commit_on(repo, "fix/thing")
    sent = []
    rep = R.bring_home(object(), repo, before, tmp_path, push=lambda p: {"ok": False, "error": "rejected"},
                       send=lambda text, needs_you=False: sent.append((text, needs_you)))
    assert rep["pushed"] == [{"branch": "fix/thing", "pushed": False, "detail": "rejected"}]
    assert "NOT pushed: rejected" in sent[0][0] and sent[0][1]


def test_a_run_that_moved_nothing_pushes_nothing_and_says_nothing(repo, tmp_path):
    sent = []
    rep = R.bring_home(object(), repo, R.heads(repo), tmp_path, push=lambda p: 1 / 0,
                       send=lambda *a, **k: sent.append(a))
    assert rep["pushed"] == [] and not sent
    assert R.heads(tmp_path / "not-a-repo") == {}


def test_start_session_hands_the_live_run_to_the_branch_return(tmp_path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_LINK_HOME", str(tmp_path))
    (tmp_path / "link.yaml").write_text("allow_start: [claude]\ncwd: " + str(tmp_path).replace("\\", "/") + "\n")
    link.approve(confirm=True, say=lambda m: None)
    followed = []
    proc = type("P", (), {"pid": 7, "poll": lambda self: None})()
    ok, why = link.start_session("claude", "fieldkit-task T-9-z", "win", popen=lambda *a, **k: proc,
                                 inbox_add=lambda *a, **k: None, flags_fn=lambda: set(), sleep=lambda s: None,
                                 check_fn=lambda a: (True, ""), follow=lambda *a: followed.append(a))
    assert ok and followed and followed[0][0] is proc and followed[0][1] == str(tmp_path).replace("\\", "/")
    assert "T-9-z" in followed[0][4]


def _restart(monkeypatch, tmp_path, holder, alive, listen=lambda me: True):
    monkeypatch.setenv("FIELDKIT_LINK_HOME", str(tmp_path))
    monkeypatch.setattr(link, "config", lambda: {"me": "10.0.0.1"})
    return link_ops.restart(wait=1, start=lambda: 500, listen=listen, holder=lambda port: holder,
                            alive=lambda pid: alive, sleep=lambda s: None)


def test_restart_fails_loudly_when_an_old_listener_keeps_the_port(monkeypatch, tmp_path):
    code, lines = _restart(monkeypatch, tmp_path, holder=123, alive=False)
    assert code == 3 and "FAILED" in lines[-1] and "exited" in lines[-1] and "PID 123" in lines[-1]
    code, lines = _restart(monkeypatch, tmp_path, holder=123, alive=True)
    assert code == 3 and "is not the one" in lines[-1]


def test_restart_fails_at_once_when_the_new_listener_dies_and_nothing_listens(monkeypatch, tmp_path):
    code, lines = _restart(monkeypatch, tmp_path, holder=None, alive=False, listen=lambda me: False)
    assert code == 3 and "exited and nothing listens" in lines[-1]


def test_restart_passes_when_the_new_listener_holds_the_port(monkeypatch, tmp_path):
    code, lines = _restart(monkeypatch, tmp_path, holder=500, alive=True)
    assert code == 0 and "PID 500" in lines[-1]
    code, _ = _restart(monkeypatch, tmp_path, holder=None, alive=True)    # the holder cannot be told: alive is enough
    assert code == 0
