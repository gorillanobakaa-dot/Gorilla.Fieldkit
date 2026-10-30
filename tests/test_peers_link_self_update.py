"""The listener brings its own checkout to origin/main (2026-10-28: a merged fix reached the listener only when a person
fast-forwarded it). Fixture repositories only."""
import subprocess

from fieldkit.peers import link


def git(repo, *a):
    return subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True, text=True).stdout.strip()


def setup(tmp_path):
    origin, here = tmp_path / "origin.git", tmp_path / "here"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    subprocess.run(["git", "clone", "-q", str(origin), str(here)], check=True, capture_output=True)
    for k, v in (("user.email", "t@example.org"), ("user.name", "t")):
        git(here, "config", k, v)
    (here / "a.txt").write_text("1\n", encoding="utf-8")
    git(here, "add", "a.txt")
    git(here, "commit", "-q", "-m", "one")
    git(here, "push", "-q", "origin", "main")
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(origin), str(other)], check=True, capture_output=True)
    for k, v in (("user.email", "t@example.org"), ("user.name", "t")):
        git(other, "config", k, v)
    return here, other


def advance(other):
    (other / "a.txt").write_text(str(int(git(other, "rev-list", "--count", "HEAD")) + 1) + "\n", encoding="utf-8")
    git(other, "commit", "-q", "-am", "next")
    git(other, "push", "-q", "origin", "main")
    return git(other, "rev-parse", "HEAD")


def test_a_clean_main_checkout_moves_to_origin_main(tmp_path):
    here, other = setup(tmp_path)
    assert link.self_update(here) == "same"
    new = advance(other)
    assert link.self_update(here) == "moved" and git(here, "rev-parse", "HEAD") == new


def test_a_checkout_in_use_is_left_alone(tmp_path):
    here, other = setup(tmp_path)
    advance(other)
    (here / "a.txt").write_text("local edit\n", encoding="utf-8")
    assert "tracked changes" in link.self_update(here)                   # a job's edit is never lost
    git(here, "checkout", "-q", "a.txt")
    git(here, "switch", "-q", "-c", "fix/x")
    assert "not main" in link.self_update(here)                           # a job's branch is never moved
    git(here, "switch", "-q", "main")
    (here / "b.txt").write_text("diverged\n", encoding="utf-8")     # another file: a real merge would succeed
    git(here, "add", "b.txt")
    git(here, "commit", "-q", "-m", "local commit")
    assert "not a fast-forward" in link.self_update(here)                 # never a merge or a reset
    (here / "new.txt").write_text("untracked\n", encoding="utf-8")


def test_untracked_files_do_not_stop_it(tmp_path):
    here, other = setup(tmp_path)
    (here / "state.json").write_text("{}", encoding="utf-8")              # state/ and scratch are untracked
    advance(other)
    assert link.self_update(here) == "moved"
