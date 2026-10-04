"""The replay proof: public patch set + pristine upstream = the compiled tree, or FAIL with the reason."""
import json
import subprocess

from fieldkit.buildh import replay

NL = chr(10)          # LF on Windows too: the patches are LF


def _git(w, *a):
    subprocess.run(["git", "-C", str(w), "-c", "user.name=t", "-c", "user.email=t@t", *a], check=True, capture_output=True)


def _setup(tmp_path):
    w = tmp_path / "tree"
    (w / "a").mkdir(parents=True)
    (w / "a/x.txt").write_text("one\ntwo\n", encoding="utf-8", newline=NL)
    (w / "a/gone.txt").write_text("bye\n", encoding="utf-8", newline=NL)
    _git(w.parent, "init", "-q", str(w))
    _git(w, "add", "-A")
    _git(w, "commit", "-qm", "pristine")
    owner = tmp_path / "owner"
    g = owner / "set" / "01.G"
    (g / "NEW_FILES" / "a").mkdir(parents=True)
    (g / "NEW_FILES" / "a" / "new.txt").write_text("new\n", encoding="utf-8", newline=NL)
    (g / "DELETED_FILES.manifest.txt").write_text("a/gone.txt\n", encoding="utf-8", newline=NL)
    (g / "x.patch").write_text("--- a/a/x.txt\n+++ b/a/x.txt\n@@ -1,2 +1,2 @@\n one\n-two\n+TWO\n", encoding="utf-8", newline=NL)
    (owner / "config").mkdir()
    (owner / "config" / "patch_policy.json").write_text(json.dumps({"patchset_root": "set", "groups": {"01.G": {"status": "enabled"}}}), encoding="utf-8", newline=NL)
    # the "built" tree: the same changes made by hand and committed
    (w / "a/x.txt").write_text("one\nTWO\n", encoding="utf-8", newline=NL)
    (w / "a/new.txt").write_text("new\n", encoding="utf-8", newline=NL)
    (w / "a/gone.txt").unlink()
    _git(w, "add", "-A")
    _git(w, "commit", "-qm", "built")
    return owner, w


def test_the_set_on_pristine_gives_the_built_tree(tmp_path):
    owner, w = _setup(tmp_path)
    r = replay.replay(owner, w, say=lambda *_: None)
    assert r["ok"] and r["replayed"] == r["built"] and not r["failures"]


def test_a_stale_extra_patch_or_a_dirty_tree_fails(tmp_path):
    owner, w = _setup(tmp_path)
    (owner / "set" / "01.G" / "y.patch").write_text("--- a/a/x.txt\n+++ b/a/x.txt\n@@ -1,2 +1,2 @@\n one\n-two\n+TWO\n", encoding="utf-8", newline=NL)
    r = replay.replay(owner, w, say=lambda *_: None)
    assert not r["ok"] and r["failures"]                    # the duplicate cannot apply on top: caught
    (owner / "set" / "01.G" / "y.patch").unlink()
    (w / "a/x.txt").write_text("edited after the build\n", encoding="utf-8", newline=NL)
    r = replay.replay(owner, w, say=lambda *_: None)
    assert not r["ok"] and r["dirty"]
