"""A running emerge holds the checkouts it reads; nothing fast-forwards a held checkout (P29, owner order 2026-11-16).

Firefox 160's install aborted with "the checkout moved during the run" after build and package were green: the
peers-link listener's self_update fast-forwarded Fieldkit main under the build. Fixture repositories only."""
import contextlib
import os
import subprocess
from types import SimpleNamespace

import pytest

from fieldkit.core import checkout_hold, sync
from fieldkit.method import train
from fieldkit.peers import link

NL = chr(10)


@pytest.fixture(autouse=True)
def holds_dir(tmp_path, monkeypatch):
    d = tmp_path / "holds"
    monkeypatch.setattr(checkout_hold, "_dir", lambda root=None: d)
    return d


def git(repo, *a):
    return subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True, text=True).stdout.strip()


def setup(tmp_path):
    origin, here, other = tmp_path / "origin.git", tmp_path / "here", tmp_path / "other"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    subprocess.run(["git", "clone", "-q", str(origin), str(here)], check=True, capture_output=True)
    for k, v in (("user.email", "t@example.org"), ("user.name", "t")):
        git(here, "config", k, v)
    (here / "a.txt").write_text("1" + NL, encoding="utf-8")
    git(here, "add", "a.txt")
    git(here, "commit", "-q", "-m", "one")
    git(here, "push", "-q", "origin", "main")
    subprocess.run(["git", "clone", "-q", str(origin), str(other)], check=True, capture_output=True)
    for k, v in (("user.email", "t@example.org"), ("user.name", "t")):
        git(other, "config", k, v)
    (other / "a.txt").write_text("2" + NL, encoding="utf-8")
    git(other, "commit", "-q", "-am", "next")
    git(other, "push", "-q", "origin", "main")
    git(here, "fetch", "-q", "origin")
    return here, git(other, "rev-parse", "HEAD")


def dead_pid():
    p = subprocess.Popen(["git", "--version"], stdout=subprocess.DEVNULL)
    p.wait()
    return p.pid


def test_a_live_hold_names_the_run_a_dead_one_is_stale_and_release_clears_it(tmp_path):
    co = tmp_path / "co"
    co.mkdir()
    assert checkout_hold.busy(co) is None
    stale = checkout_hold.hold("emerge old", [co], pid=dead_pid())
    assert stale and checkout_hold.busy(co) is None                       # a crashed run never blocks for ever
    p = checkout_hold.hold("emerge firefox-windows", [str(co)])
    why = checkout_hold.busy(co)
    assert "emerge firefox-windows" in why and str(os.getpid()) in why
    assert checkout_hold.busy(tmp_path) is None                            # only the checkouts it named
    checkout_hold.release(p)
    assert checkout_hold.busy(co) is None


def test_the_listener_self_update_leaves_a_held_checkout_alone(tmp_path):
    here, new = setup(tmp_path)
    before = git(here, "rev-parse", "HEAD")
    p = checkout_hold.hold("emerge firefox-windows", [here])
    r = link.self_update(here)
    assert r.startswith("left alone: held by emerge firefox-windows"), r
    assert git(here, "rev-parse", "HEAD") == before
    checkout_hold.release(p)
    assert link.self_update(here) == "moved" and git(here, "rev-parse", "HEAD") == new


def test_sync_apply_refuses_a_held_checkout(tmp_path):
    here, new = setup(tmp_path)
    before = git(here, "rev-parse", "HEAD")

    def row():
        return {"verdict": "BEHIND", "dirty": False, "repo": str(here), "upstream": "origin/main", "behind": 1,
                "why": "1 upstream commit(s) to take", "ok": False}
    green = lambda slug, sha: (True, "green on both systems")              # noqa: E731
    p = checkout_hold.hold("emerge firefox-windows", [here])
    r = sync.apply(row(), ci=green)
    assert "refused: held by emerge firefox-windows" in r["why"] and r["verdict"] == "BEHIND"
    assert git(here, "rev-parse", "HEAD") == before
    checkout_hold.release(p)
    r = sync.apply(row(), ci=green)
    assert r["verdict"] == "TAKEN" and git(here, "rev-parse", "HEAD") == new


def test_ship_unfreeze_refuses_a_held_checkout(tmp_path):
    here, _ = setup(tmp_path)
    before = git(here, "rev-parse", "HEAD")
    train.freeze(here, "test", checkout=here)
    p = checkout_hold.hold("emerge firefox-windows", [here])
    r = train.unfreeze(here, checkout=here)
    assert r["state"] == "held-by-build" and not r["ok"] and "emerge firefox-windows" in r["why"]
    assert git(here, "rev-parse", "HEAD") == before
    assert any(x["checkout"] == str(here) for x in train.frozen(here))      # still frozen, nothing lost
    checkout_hold.release(p)


def test_an_emerge_run_holds_its_checkouts_only_while_it_runs(tmp_path, monkeypatch):
    from fieldkit.core import liveness
    from fieldkit.emerge import runner
    co = tmp_path / "co"
    co.mkdir()
    monkeypatch.setattr(runner, "watched_repos", lambda recipe: [str(co)])
    monkeypatch.setattr(liveness, "beacon", lambda *a, **k: contextlib.nullcontext())
    e = runner.Emerge.__new__(runner.Emerge)
    e.recipe = SimpleNamespace(name="firefox-windows")
    e._capture_begin = lambda: None
    e._capture_end = lambda tok: None
    seen = {}

    def run_all(only):
        seen["during"] = checkout_hold.busy(co)
        raise RuntimeError("a stage blew up")
    e._run_all = run_all
    with pytest.raises(RuntimeError):
        e.run()
    assert seen["during"] and "emerge firefox-windows" in seen["during"]
    assert checkout_hold.busy(co) is None                                  # released even when the run fails
