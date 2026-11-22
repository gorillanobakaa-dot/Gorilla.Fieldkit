"""Stale generated-file check (fieldkit/repair/ps_compat_check.py): stale = generator output differs from the index,
not 'differs from HEAD' (that refused every commit carrying a correctly regenerated file)."""
import subprocess
import sys

from fieldkit.repair import ps_compat_check as pc


def _git(repo, *a):
    subprocess.run(["git", *a], cwd=repo, check=True, capture_output=True)


def test_regenerated_and_staged_passes_unstaged_or_not_regenerated_fails(tmp_path, monkeypatch):
    repo = tmp_path
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    out = repo / "gen.json"
    want = repo / "want.txt"
    want.write_text("v1", encoding="utf-8")
    gen = [sys.executable, "-c", "import pathlib;pathlib.Path('gen.json').write_text(pathlib.Path('want.txt').read_text())"]
    monkeypatch.setattr(pc, "GENERATORS", {"gen.json": gen})
    out.write_text("v1", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    assert pc.check_stale_generated(repo) == []
    # input changed, file regenerated and staged: differs from HEAD but is fresh
    want.write_text("v2", encoding="utf-8")
    out.write_text("v2", encoding="utf-8")
    _git(repo, "add", "-A")
    assert pc.check_stale_generated(repo) == []
    # input changed again, file not regenerated: stale
    want.write_text("v3", encoding="utf-8")
    _git(repo, "add", "want.txt")
    f = pc.check_stale_generated(repo)
    assert [x["rule"] for x in f] == ["generated-file-stale"]
