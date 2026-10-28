# Version: 1.1.0 · updated 26-10-28-20-50
"""2026-10-16, owner: the Windows and Linux machines in sync, every tool working on both. fieldkit sync takes only
fast-forwards CI proved on both systems; platformcheck holds every card's `platforms` to its tests."""
import subprocess

from fieldkit.core import sync
from fieldkit.desk import platformcheck as pc


def _g(d, *a):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-C", str(d), *a],
                          check=True, capture_output=True, text=True).stdout.strip()


def _machines(tmp_path):
    hub = tmp_path / "hub.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(hub)], check=True)
    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", "-q", str(hub), str(seed)], check=True, capture_output=True)
    (seed / "a.txt").write_text("1\n")
    _g(seed, "add", "-A")
    _g(seed, "commit", "-qm", "one")
    _g(seed, "push", "-q", "origin", "HEAD:main")
    win, lin = tmp_path / "win", tmp_path / "lin"
    for m in (win, lin):
        subprocess.run(["git", "clone", "-q", str(hub), str(m)], check=True, capture_output=True)
    (lin / "tool.py").write_text("print('new linux tool')\n")
    _g(lin, "add", "-A")
    _g(lin, "commit", "-qm", "linux adds a tool")
    _g(lin, "push", "-q", "origin", "HEAD:main")
    return win, lin


def test_a_commit_from_the_other_machine_is_taken_only_when_ci_proved_it(tmp_path, monkeypatch):
    win, _ = _machines(tmp_path)
    row = sync.state(win)
    assert row["verdict"] == "BEHIND" and row["behind"] == 1 and not row["ok"]
    no = sync.apply(dict(row), ci=lambda slug, sha: (False, "CI not green"))
    assert no["verdict"] == "BEHIND" and "refused: CI not green" in no["why"] and not (win / "tool.py").exists()
    monkeypatch.setattr(sync, "_slug", lambda repo: "o/r")      # a local hub has no GitHub slug
    yes = sync.apply(sync.state(win), ci=lambda slug, sha: (True, "CI green on windows, ubuntu"))
    assert yes["verdict"] == "TAKEN" and (win / "tool.py").exists()


def test_a_pending_tip_takes_the_newest_green_commit_below_it(tmp_path, monkeypatch):
    """2026-10-28, plan item 30: main's tip is always mid-CI while PRs merge; the tip-only rule never took anything."""
    win, lin = _machines(tmp_path)
    green = _g(lin, "rev-parse", "HEAD")
    (lin / "late.py").write_text("print('merged a minute ago')\n")
    _g(lin, "add", "-A")
    _g(lin, "commit", "-qm", "still in CI")
    _g(lin, "push", "-q", "origin", "HEAD:main")
    tip = _g(lin, "rev-parse", "HEAD")
    monkeypatch.setattr(sync, "_slug", lambda repo: "o/r")
    seen = []

    def ci(slug, sha):
        seen.append(sha)
        return (True, f"CI green for {sha[:10]}") if sha == green else (False, f"CI not green for {sha[:10]}: test (pending)")
    r = sync.apply(sync.state(win), ci=ci)
    assert seen == [tip, green]
    assert r["verdict"] == "TAKEN-PART" and not r["ok"] and r["behind"] == 1 and r["taken_sha"] == green
    assert _g(win, "rev-parse", "HEAD") == green and (win / "tool.py").exists() and not (win / "late.py").exists()
    assert f"to {green[:10]}" in r["why"] and "stayed 1 commit(s) behind the tip" in r["why"] and "pending" in r["why"]
    # nothing green at all: refused, the tree stays where it was
    r = sync.apply(sync.state(win), ci=lambda s, h: (False, "CI not green: red"))
    assert r["verdict"] == "BEHIND" and "refused: CI not green: red" in r["why"] and not (win / "late.py").exists()


def test_dirty_or_diverged_is_never_taken(tmp_path):
    win, _ = _machines(tmp_path)
    (win / "a.txt").write_text("half-done\n")
    r = sync.apply(sync.state(win), ci=lambda s, h: (True, "green"))
    assert r["verdict"] == "BEHIND" and "uncommitted" in r["why"]
    _g(win, "commit", "-qam", "local work")
    r = sync.state(win)
    assert r["verdict"] == "DIVERGED" and not r["ok"]


def test_ci_must_cover_both_systems():
    class R:
        returncode, stderr = 0, ""
        stdout = '[{"name": "test (ubuntu-latest)", "status": "completed", "conclusion": "success"}]'
    ok, ev = sync.ci_green("o/r", "abc", run=lambda *a, **k: R())
    assert not ok and "no windows job" in ev


def test_cards_hold_their_platform_promise(tmp_path):
    t = tmp_path / "test_x.py"
    t.write_text("import pytest\npytestmark = pytest.mark.windows_only\n")
    cards = [{"id": "both", "platforms": ["windows", "linux"], "test": ["python", "-m", "pytest", str(t)]},
             {"id": "old", "platforms": ["windows"]},
             {"id": "new", "platforms": ["linux"]},
             {"id": "why", "platforms": ["linux"], "platforms_why": "reads /proc"},
             {"id": "odd", "platforms": ["amiga"]}]
    r = pc.check(cards, frozen={"old"})
    assert r["backlog"] == ["old"]
    joined = "\n".join(r["problems"])
    assert "both: promises ['linux']" in joined and "new: linux only" in joined and "odd: platforms" in joined
    assert "why" not in [p.split(":")[0] for p in r["problems"]]


def test_the_real_registry_has_no_platform_problem():
    """The backlog may only shrink: a new single-system card needs a port or a platforms_why."""
    assert pc.check()["problems"] == []
