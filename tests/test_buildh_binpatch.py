# Version: 1.0.0 · updated 26-10-15-21-00
"""binpatch.py: a GIT binary patch in the set is found missing from a tree, restored byte for byte (git hash checked),
and an exempt one is shown with its reason (2026-10-15: 0 of 31 had reached the 158 tree, nothing noticed)."""
import os
import subprocess
from pathlib import Path

from fieldkit.buildh import binpatch

ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
       "GIT_COMMITTER_EMAIL": "t@t"}


def _git(d, *a, **k):
    return subprocess.run(["git", "-C", str(d), *a], capture_output=True, env=ENV, check=True, **k)


def _binary_patch(tmp_path, old, new, name="branding/logo.png"):
    src = tmp_path / "src"
    (src / Path(name).parent).mkdir(parents=True)
    _git(tmp_path, "init", "-q", str(src))
    (src / name).write_bytes(old)
    _git(src, "add", "-A"); _git(src, "commit", "-qm", "a")
    (src / name).write_bytes(new)
    return _git(src, "diff", "--binary").stdout.decode()


def _set(tmp_path, patch_text, group="22.LOOK"):
    s = tmp_path / "set" / group
    s.mkdir(parents=True)
    (s / "001-logo.patch").write_text("# header line\n" + patch_text)
    return tmp_path / "set"


def _tree(tmp_path, old, name="branding/logo.png"):
    t = tmp_path / "tree"
    (t / Path(name).parent).mkdir(parents=True)
    _git(tmp_path, "init", "-q", str(t))
    (t / name).write_bytes(old)
    _git(t, "add", "-A"); _git(t, "commit", "-qm", "pristine")
    return t


def test_missing_picture_is_found_and_restored_by_hash(tmp_path):
    old, new = os.urandom(3000), os.urandom(5000)          # random: git stores a literal, not a delta
    sets = _set(tmp_path, _binary_patch(tmp_path, old, new))
    tree = _tree(tmp_path, old)
    r = binpatch.audit(tree, sets)
    assert not r["ok"] and r["bad"][0]["state"] == "not-applied"
    got = binpatch.restore(tree, sets)
    assert got["ok"] and len(got["restored"]) == 1
    assert (tree / "branding/logo.png").read_bytes() == new
    assert binpatch.audit(tree, sets)["ok"]
    assert "binary-patches" in _git(tree, "log", "-1", "--format=%s").stdout.decode()


def test_exempt_is_shown_with_its_reason_not_silently_passed(tmp_path):
    old, new = os.urandom(3000), os.urandom(5000)
    sets = _set(tmp_path, _binary_patch(tmp_path, old, new))
    tree = _tree(tmp_path, old)
    r = binpatch.audit(tree, sets, {"binary_exempt": {"branding/logo.png": "Windows only"}})
    assert r["ok"] and r["rows"][0]["state"] == "exempt"
    assert "Windows only" in "\n".join(binpatch.lines(r))


def test_a_disabled_group_is_not_judged(tmp_path):
    old, new = os.urandom(3000), os.urandom(5000)
    sets = _set(tmp_path, _binary_patch(tmp_path, old, new))
    tree = _tree(tmp_path, old)
    assert binpatch.audit(tree, sets, {"groups": {"22.LOOK": {"status": "disabled"}}})["rows"] == []
