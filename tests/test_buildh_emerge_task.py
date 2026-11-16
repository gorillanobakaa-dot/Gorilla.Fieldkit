"""build-harness start firefox --emerge: the task record of a tree `fieldkit emerge` built (2026-11-16, P27 160)."""
import subprocess

import pytest

from fieldkit.buildh import cli, task


def _git(w, *a):
    subprocess.run(["git", "-C", str(w), *a], check=True, capture_output=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@x", "PATH": __import__("os").environ["PATH"],
                        "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", "")})
    return subprocess.run(["git", "-C", str(w), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()


@pytest.fixture
def tree(tmp_path, monkeypatch):
    monkeypatch.setattr(task, "STATE", tmp_path / "state")
    w = tmp_path / "src"
    w.mkdir()
    subprocess.run(["git", "init", "-q", str(w)], check=True)
    (w / "a.txt").write_text("pristine\n")
    _git(w, "add", "-A")
    root = _git(w, "commit", "-qm", "release tag")
    (w / "a.txt").write_text("patched\n")
    _git(w, "add", "-A")
    head = _git(w, "commit", "-qm", "checkpoint: public patch set replayed")
    return w, root, head, tmp_path / "harness"


def test_records_the_built_tree_unapproved(tree):
    w, root, head, hr = tree
    t, created = cli.start_firefox_emerge("160.0", w, hr)
    assert created and t["id"] == "firefox-160.0" and t["workflow"] == "firefox-emerge"
    assert t["approved"] is False                      # only the owner approves a plan
    assert [s["id"] for s in t["steps"]] == ["final-checks", "owner-preflight"]
    assert t["meta"]["upstream"] == {**t["meta"]["upstream"], "version": "160.0", "commit": root,
                                     "tag": "FIREFOX_160_0_RELEASE"}
    assert [c["commit"] for c in t["checkpoints"]] == [root, head]
    assert task.load("firefox-160.0")["checkpoints"][0]["label"] == "workcopy"
    with pytest.raises(task.Refused):
        task.advance("firefox-160.0")                  # unapproved: no step runs


def test_idempotent(tree):
    w, _, _, hr = tree
    cli.start_firefox_emerge("160.0", w, hr)
    t, created = cli.start_firefox_emerge("160.0", w, hr)
    assert not created and t["id"] == "firefox-160.0"


def test_refuses_an_uncommitted_tree(tree):
    w, _, _, hr = tree
    (w / "a.txt").write_text("hand edit\n")
    with pytest.raises(task.Refused, match="uncommitted"):
        cli.start_firefox_emerge("160.0", w, hr)
    assert not (task.STATE / "firefox-160.0").exists()
