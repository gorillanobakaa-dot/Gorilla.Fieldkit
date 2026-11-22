"""Installed-copy sync (fieldkit/core/installed.py): detached worktree follows origin/main, fast-forward only."""
import subprocess
import sys

import pytest

from fieldkit.core import installed as inst


def git(cwd, *a):
    return subprocess.run(["git", "-C", str(cwd), "-c", "user.email=t@t", "-c", "user.name=t", *a],
                          capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def repos(tmp_path):
    origin, work, tree = tmp_path / "origin.git", tmp_path / "work", tmp_path / "inst"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    subprocess.run(["git", "clone", "-q", str(origin), str(work)], check=True)
    (work / "a.txt").write_text("1")
    git(work, "add", "."); git(work, "commit", "-qm", "one"); git(work, "push", "-q", "origin", "HEAD:main")
    subprocess.run(["git", "clone", "-q", str(origin), str(tree)], check=True)
    git(tree, "checkout", "-q", "--detach")

    def advance(text="2", name="a.txt"):
        (work / name).write_text(text)
        git(work, "add", "."); git(work, "commit", "-qm", "next"); git(work, "push", "-q", "origin", "HEAD:main")
    return origin, work, tree, advance


class FakeRun:
    """Stands in for the subprocess runner of the smoke test and pip: records calls, always succeeds."""
    def __init__(self):
        self.calls = []

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "fieldkit 0.0.0\n", "")


def test_level_is_ok(repos):
    _, _, tree, _ = repos
    row = inst.inspect(tree)
    assert row["verdict"] == "OK" and inst.line(row).startswith("installed copy: PASS")


def test_behind_is_fail_and_names_last_fetch(repos):
    _, _, tree, advance = repos
    advance(); git(tree, "fetch", "-q", "origin", "main")
    row = inst.inspect(tree)
    assert row["verdict"] == "BEHIND" and row["behind"] == 1
    assert inst.line(row).startswith("installed copy: FAIL") and "last fetched ref" in row["why"]


def test_sync_fast_forwards_and_skips_pip_when_pyproject_same(repos):
    _, _, tree, advance = repos
    advance()
    fake = FakeRun()
    row = inst.sync(tree, run=fake)
    assert row["ok"] and git(tree, "rev-parse", "HEAD") == git(tree, "rev-parse", "origin/main")
    assert not any("pip" in c for c in fake.calls)
    assert inst.sync_line(row).startswith("installed copy sync: PASS")


def test_sync_reruns_pip_when_pyproject_changed(repos):
    _, _, tree, advance = repos
    advance("x", "pyproject.toml")
    fake = FakeRun()
    assert inst.sync(tree, run=fake)["ok"]
    assert any("pip" in c for c in fake.calls)


def test_dirty_tree_refused_and_untouched(repos):
    _, _, tree, advance = repos
    advance()
    (tree / "a.txt").write_text("mine")
    row = inst.sync(tree, run=FakeRun())
    assert not row["ok"] and "uncommitted" in row["why"] and (tree / "a.txt").read_text() == "mine"


def test_diverged_refused(repos):
    _, _, tree, advance = repos
    (tree / "b.txt").write_text("local"); git(tree, "add", "."); git(tree, "commit", "-qm", "local")
    advance()
    row = inst.sync(tree, run=FakeRun())
    assert not row["ok"] and row["verdict"] == "DIVERGED" and "refused" in row["why"]


def test_offline_uses_last_ref_and_says_so(repos):
    _, _, tree, advance = repos
    advance(); git(tree, "fetch", "-q", "origin", "main")
    git(tree, "remote", "set-url", "origin", str(tree / "nowhere"))
    row = inst.inspect(tree, fetch=True)
    assert row["verdict"] == "BEHIND" and row["fetched"] is False and "OFFLINE" in row["why"]
    assert not inst.sync(tree, run=FakeRun())["ok"]


def test_not_found_is_quiet_in_the_hook(monkeypatch, tmp_path):
    monkeypatch.setenv("FIELDKIT_INSTALLED_DIR", str(tmp_path / "nothing"))
    assert inst.hook_line({}, "SessionStart") == ""
    assert inst.inspect(inst.locate())["verdict"] == "NOT-FOUND"


def test_hook_reports_only_at_session_start(repos, monkeypatch):
    _, _, tree, advance = repos
    advance(); git(tree, "fetch", "-q", "origin", "main")
    monkeypatch.setenv("FIELDKIT_INSTALLED_DIR", str(tree))
    assert "FIELDKIT INSTALLED COPY" in inst.hook_line({}, "SessionStart")
    assert inst.hook_line({}, "UserPromptSubmit") == ""
    assert git(tree, "rev-parse", "HEAD") != git(tree, "rev-parse", "origin/main")   # report only


def test_cli_exit_codes(repos, monkeypatch, capsys):
    _, _, tree, advance = repos
    monkeypatch.setenv("FIELDKIT_INSTALLED_DIR", str(tree))
    assert inst.main(["check"]) == 0
    advance(); git(tree, "fetch", "-q", "origin", "main")
    assert inst.main(["check"]) == 3 and "FAIL" in capsys.readouterr().out
    assert inst.main([]) == 2


# ---- in-process reads and caches (hog churn: SessionStart started 12 processes, 2026-11-22) ------------------------
def _revparse(tree):
    return git(tree, "rev-parse", "HEAD"), git(tree, "rev-parse", "origin/main")


def test_read_heads_matches_git(repos):
    _, _, tree, advance = repos
    advance()
    git(tree, "fetch", "-q")
    head, main, _ = inst.read_heads(tree)
    assert (head, main) == _revparse(tree)


def test_read_heads_reads_packed_refs_and_worktrees(repos, tmp_path):
    _, _, tree, advance = repos
    advance()
    git(tree, "fetch", "-q")
    git(tree, "pack-refs", "--all")
    assert inst.read_heads(tree)[:2] == _revparse(tree)
    wt = tmp_path / "wt"
    git(tree, "worktree", "add", "-q", "--detach", str(wt), "origin/main")
    assert inst.read_heads(wt)[:2] == _revparse(wt)


def test_inspect_cached_reuses_the_row_until_head_moves(repos, tmp_path, monkeypatch):
    _, _, tree, advance = repos
    env = {"FIELDKIT_INSTALLED_CACHE": str(tmp_path / "cache.json")}
    calls = []
    real = inst.inspect
    monkeypatch.setattr(inst, "inspect", lambda t, fetch=False: calls.append(t) or real(t))
    first = inst.inspect_cached(tree, env, now=1000)
    again = inst.inspect_cached(tree, env, now=1001)
    assert len(calls) == 1 and again.get("cached") and again["verdict"] == first["verdict"]
    advance()
    git(tree, "fetch", "-q")
    inst.inspect_cached(tree, env, now=1002)
    assert len(calls) == 2
    inst.inspect_cached(tree, env, now=1002 + inst.VERDICT_TTL + 1)
    assert len(calls) == 3


def test_locate_cached_runs_python_once_a_day(repos, tmp_path):
    _, _, tree, _ = repos
    env = {"FIELDKIT_INSTALLED_CACHE": str(tmp_path / "cache.json")}
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, str(tree / "fieldkit" / "__init__.py") + "\n", "")
    assert inst.locate_cached(env, now=1000, run=run) == tree.resolve()
    assert inst.locate_cached(env, now=2000, run=run) == tree.resolve()
    assert len(calls) == 1
    inst.locate_cached(env, now=1000 + inst.LOCATE_TTL + 1, run=run)
    assert len(calls) == 2
