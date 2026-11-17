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


def test_materialize_patches_a_pristine_tree_once(tmp_path):
    owner, w = _setup(tmp_path)
    _git(w, "reset", "-q", "--hard", "HEAD~1")              # back to the single pristine commit
    r = replay.materialize(owner, w, say=lambda *_: None)
    assert r["ok"], r
    assert (w / "a/x.txt").read_text(encoding="utf-8") == "one\nTWO\n"
    assert (w / "a/new.txt").is_file() and not (w / "a/gone.txt").exists()
    top = subprocess.run(["git", "-C", str(w), "status", "--porcelain"], capture_output=True, text=True).stdout
    assert top.strip() == ""                                 # committed, nothing left over
    again = replay.materialize(owner, w, say=lambda *_: None)
    assert again["ok"] and "nothing to apply" in again["detail"]


def test_materialize_a_path_created_then_deleted_by_a_later_group(tmp_path):
    owner, w = _setup(tmp_path)
    _git(w, "reset", "-q", "--hard", "HEAD~1")
    (owner / "set" / "01.G" / "NEW_FILES" / "a" / "tmp.car").write_text("x\n", encoding="utf-8", newline=NL)
    g2 = owner / "set" / "02.H"
    g2.mkdir(parents=True)
    (g2 / "DELETED_FILES.manifest.txt").write_text("a/tmp.car\n", encoding="utf-8", newline=NL)
    (owner / "config" / "patch_policy.json").write_text(json.dumps({"patchset_root": "set", "groups": {
        "01.G": {"status": "enabled"}, "02.H": {"status": "enabled"}}}), encoding="utf-8", newline=NL)
    r = replay.materialize(owner, w, say=lambda *_: None)
    assert r["ok"], r
    assert not (w / "a/tmp.car").exists() and (w / "a/new.txt").is_file()
    top = subprocess.run(["git", "-C", str(w), "status", "--porcelain"], capture_output=True, text=True).stdout
    assert top.strip() == ""


def test_materialize_resumes_over_its_own_half_written_leftovers(tmp_path):
    owner, w = _setup(tmp_path)
    _git(w, "reset", "-q", "--hard", "HEAD~1")
    (w / "a/x.txt").write_text("one\nTWO\n", encoding="utf-8", newline=NL)   # an earlier run wrote it, never committed
    r = replay.materialize(owner, w, say=lambda *_: None)
    assert r["ok"], r
    (w / "b.txt").write_text("foreign\n", encoding="utf-8", newline=NL)
    _git(w, "add", "b.txt")
    _git(w, "reset", "-q", "--hard", "HEAD~1")
    (w / "README").write_text("foreign\n", encoding="utf-8", newline=NL)
    _git(w, "add", "README")
    r = replay.materialize(owner, w, say=lambda *_: None)
    assert not r["ok"] and "uncommitted" in r["detail"]
