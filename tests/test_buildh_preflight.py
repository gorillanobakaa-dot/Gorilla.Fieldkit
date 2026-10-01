"""preflight: the machine and working copy must be fit BEFORE a job starts."""
import subprocess

import pytest

from fieldkit.buildh import preflight, task


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(task, "STATE", tmp_path / "state")
    w = tmp_path / "work"
    w.mkdir()
    subprocess.run(["git", "init", "-q", str(w)], check=True)
    (w / "a.txt").write_text("a\n")
    subprocess.run(["git", "-C", str(w), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(w), "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "x"], check=True)
    task.start("p1", "demo", w, [], budget_tokens=1000, meta={"upstream": {"version": "0"}})
    monkeypatch.setattr(preflight.vault, "verify", lambda *a, **k: {"intact": True, "problems": []})
    return w


def rows(**kw):
    return {r["check"]: r for r in preflight.run("p1", **kw)}


def test_a_healthy_machine_is_ready(world):
    r = rows()
    assert all(x["ok"] for k, x in r.items()), [k for k, x in r.items() if not x["ok"]]


def test_a_stale_lock_is_reported_and_only_removed_when_no_git_runs(world, monkeypatch):
    lock = world / ".git" / "index.lock"
    lock.write_text("")
    monkeypatch.setattr(preflight, "_running", lambda image: [])
    assert not rows()["git locks"]["ok"] and lock.exists()                   # reported, not touched
    assert rows(fix_locks=True)["git locks"]["ok"] and not lock.exists()      # proven stale, removed


def test_a_lock_is_never_removed_while_git_is_running(world, monkeypatch):
    lock = world / ".git" / "index.lock"
    lock.write_text("")
    monkeypatch.setattr(preflight, "_running", lambda image: ["git.exe"])
    r = rows(fix_locks=True)["git locks"]
    assert not r["ok"] and lock.exists() and "running" in r["evidence"]


def test_uncommitted_changes_stop_a_job(world):
    (world / "a.txt").write_text("changed by hand\n")
    assert not rows()["working copy clean"]["ok"]


def test_too_little_disk_stops_a_build(world, monkeypatch):
    monkeypatch.setattr(preflight.shutil, "disk_usage", lambda p: type("U", (), {"free": 50 * 2 ** 30})())
    assert rows()["disk space"]["ok"] and not rows(build=True)["disk space"]["ok"]


def test_an_unreachable_model_server_is_a_failure(world):
    r = rows(model=True, lm_url="http://127.0.0.1:9/v1/models")
    assert not r["model server answers"]["ok"]


def test_the_report_says_ready_or_not(world):
    assert preflight.lines(preflight.run("p1"))[-1] == "PREFLIGHT: READY"
    (world / "a.txt").write_text("x\n")
    assert "NOT READY" in preflight.lines(preflight.run("p1"))[-1]
