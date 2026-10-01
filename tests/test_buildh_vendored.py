"""Upstream's own *.orig files are source, not litter. Found by an independent audit on 2026-10-01: the harness
deleted all 560 vendored third_party/rust/*/Cargo.toml.orig files in both working copies."""
import subprocess

import pytest

from fieldkit.buildh import firefox


def git(w, *a):
    subprocess.run(["git", "-C", str(w), "-c", "user.name=t", "-c", "user.email=t@example.com", *a], check=True, capture_output=True)


@pytest.fixture
def tree(tmp_path):
    w = tmp_path / "ff"
    (w / "third_party" / "rust" / "foo").mkdir(parents=True)
    (w / "third_party" / "rust" / "foo" / "Cargo.toml.orig").write_text("[package]\n")     # tracked upstream, on purpose
    (w / "third_party" / "rust" / "foo" / "Cargo.toml").write_text("[package]\n")
    (w / "app.js").write_text("a\n")
    subprocess.run(["git", "init", "-q", str(w)], check=True)
    git(w, "add", "-A")
    git(w, "commit", "-q", "-m", "pristine")
    return w


def test_a_tracked_orig_file_is_not_a_leftover(tree):
    assert firefox.leftovers(tree) == []


def test_a_file_patch_left_behind_is_a_leftover(tree):
    (tree / "app.js.orig").write_text("a\n")
    (tree / "app.js.rej").write_text("x\n")
    names = sorted(p.name for p in firefox.leftovers(tree))
    assert names == ["app.js.orig", "app.js.rej"]


def test_cleaning_up_removes_ours_and_never_upstreams(tree):
    (tree / "app.js.orig").write_text("a\n")
    for p in firefox.leftovers(tree):
        p.unlink()
    assert (tree / "third_party" / "rust" / "foo" / "Cargo.toml.orig").is_file() and not (tree / "app.js.orig").exists()


def _t(tree, harness=None):
    return {"workdir": str(tree), "meta": {"harness_root": str(harness) if harness else None}}


def test_a_pristine_file_that_vanished_is_reported(tree):
    (tree / "third_party" / "rust" / "foo" / "Cargo.toml.orig").unlink()
    git(tree, "add", "-A")
    git(tree, "commit", "-q", "-m", "checkpoint")
    assert firefox.unexplained_deletions(_t(tree)) == ["third_party/rust/foo/Cargo.toml.orig"]


def test_a_deletion_that_a_gorilla_patch_asks_for_is_explained(tree, tmp_path):
    h = tmp_path / "harness" / "patches" / "02.GPU"
    h.mkdir(parents=True)
    (h / "x.patch").write_text("--- a/app.js\n+++ /dev/null\n@@ -1 +0,0 @@\n-a\n")
    (tmp_path / "harness" / "config").mkdir()
    (tmp_path / "harness" / "config" / "patch_policy.json").write_text(
        '{"patchset_root": "patches", "groups": {"02.GPU": {"status": "enabled"}}}')
    (tree / "app.js").unlink()
    git(tree, "add", "-A")
    git(tree, "commit", "-q", "-m", "checkpoint")
    assert firefox.unexplained_deletions(_t(tree, tmp_path / "harness")) == []


def test_an_untouched_tree_reports_nothing(tree):
    assert firefox.unexplained_deletions(_t(tree)) == []


def test_restoring_puts_back_only_the_vanished_pristine_files_as_a_checkpoint(tree, tmp_path, monkeypatch):
    from fieldkit.buildh import task
    monkeypatch.setattr(task, "STATE", tmp_path / "state")
    gone = tree / "third_party" / "rust" / "foo" / "Cargo.toml.orig"
    gone.unlink()
    (tree / "app.js").write_text("a\nGorilla line\n")
    git(tree, "add", "-A")
    git(tree, "commit", "-q", "-m", "checkpoint: a port")
    task.start("r1", "demo", tree, [], budget_tokens=1000, meta={"upstream": {"version": "157.0"}})
    assert firefox.restore_deleted("r1") == 1
    assert gone.is_file() and (tree / "app.js").read_text() == "a\nGorilla line\n"      # the port is untouched
    assert firefox.unexplained_deletions(task.load("r1")) == []
    assert firefox.restore_deleted("r1") == 0                                            # nothing left to do
    log = subprocess.run(["git", "-C", str(tree), "log", "-1", "--format=%an|%s"], capture_output=True, text=True).stdout
    assert log.startswith("build-harness|checkpoint: restored 1 pristine file")


def test_a_git_hook_environment_cannot_redirect_the_harness_to_another_repository():
    """The pre-commit hook runs the tests with GIT_INDEX_FILE set; without scrubbing it, `git -C <other repo>`
    read the Fieldkit index and a clean vault looked damaged (found 2026-10-01 when a commit was refused)."""
    import os
    import sys
    r = subprocess.run([sys.executable, "-c", "import os, fieldkit.buildh; print(os.environ.get('GIT_INDEX_FILE'), os.environ.get('GIT_DIR'))"],
                       capture_output=True, text=True, env={**os.environ, "GIT_INDEX_FILE": "C:/elsewhere/index", "GIT_DIR": "C:/elsewhere/.git"})
    assert r.stdout.strip() == "None None", r.stdout + r.stderr


def test_a_checkpoint_is_signed_by_the_harness_whatever_the_environment_says(tree, tmp_path, monkeypatch):
    """git exports GIT_AUTHOR_NAME inside hooks; the audit tells harness checkpoints by their author."""
    from fieldkit.buildh import task
    monkeypatch.setattr(task, "STATE", tmp_path / "state")
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Somebody Else")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "Somebody Else")
    task.start("s1", "demo", tree, [], budget_tokens=1000, meta={"upstream": {"version": "157.0"}})
    (tree / "app.js").write_text("changed\n")
    task.checkpoint(task.load("s1"), "x")
    log = subprocess.run(["git", "-C", str(tree), "log", "-1", "--format=%an|%cn"], capture_output=True, text=True).stdout.strip()
    assert log == "build-harness|build-harness", log
