# Version: 1.0.0 · updated 26-11-25-12-00
"""shiprefs: a pull request proves a branch only for the same repo, same base, same head commit (2026-11-25).
Regression: fix/repair-rollback was reused; PRs #609, #642, #643 merged with other heads made new work look merged.
Real git repositories in a temp folder; GitHub is a stand-in."""
import json
import subprocess

from fieldkit.method import shiprefs

SLUG = "o/r"


def _g(repo, *a):
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *a], check=True,
                          capture_output=True, text=True).stdout.strip()


def _commit(w, name):
    (w / name).write_text(name, newline="\n")
    _g(w, "add", "-A")
    _g(w, "commit", "-qm", name)
    return _g(w, "rev-parse", "HEAD")


def _repo(tmp_path):
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    w = tmp_path / "w"
    subprocess.run(["git", "clone", "-q", str(origin), str(w)], check=True, capture_output=True)
    _g(w, "checkout", "-q", "-b", "main")
    _commit(w, "a")
    old = _commit(w, "b")                     # like a0fde425: a branch head that main already contains
    _commit(w, "c")
    _g(w, "push", "-q", "origin", "main")
    _g(w, "push", "-q", "origin", f"{old}:refs/heads/old-on-main")
    return w


def _branch(w, name):
    _g(w, "checkout", "-q", "-b", name, "main")
    sha = _commit(w, name.replace("/", "-"))
    _g(w, "push", "-q", "origin", name)
    _g(w, "checkout", "-q", "main")
    return sha


def _pr(n, state, sha, base="main", fork=False):
    return {"number": n, "state": state, "headRefOid": sha, "baseRefName": base, "isCrossRepository": fork,
            "headRepository": {"name": "r"}, "headRepositoryOwner": {"login": "someone" if fork else "o"}}


def _gh(by_name):
    def gh(args, timeout=60):
        name = args[args.index("--head") + 1]
        return json.dumps(by_name.get(name, [])), ""
    return gh


def _run(w, by_name):
    _g(w, "fetch", "-q", "origin")
    res = shiprefs.classify(w, "main", gh=_gh(by_name), slug=SLUG, content_on_base=lambda *a: False)
    return res, {r["name"]: r for r in res["refs"]}


def test_reused_branch_name_with_old_merged_prs_is_unproven_not_merged(tmp_path):
    w = _repo(tmp_path)
    sha = _branch(w, "fix/repair-rollback")
    old = [_pr(609, "MERGED", "1" * 40), _pr(642, "MERGED", "2" * 40), _pr(643, "MERGED", "3" * 40)]
    res, refs = _run(w, {"fix/repair-rollback": old})
    r = refs["fix/repair-rollback"]
    assert r["cls"] == "unproven" and r["sha"] == sha and r["prs"] == []
    assert "#609 MERGED" in r["why"] and "#643 MERGED" in r["why"]
    assert refs["old-on-main"]["cls"] == "on-base"
    assert res["ok"]


def test_same_head_commit_open_or_merged_pr_proves_the_branch(tmp_path):
    w = _repo(tmp_path)
    a, b = _branch(w, "feat/open"), _branch(w, "feat/merged")
    _, refs = _run(w, {"feat/open": [_pr(7, "OPEN", a)], "feat/merged": [_pr(8, "MERGED", b)]})
    assert refs["feat/open"]["cls"] == "open-pr" and refs["feat/open"]["prs"] == [7]
    assert refs["feat/merged"]["cls"] == "merged-pr" and refs["feat/merged"]["prs"] == [8]


def test_fork_or_other_base_pr_with_the_same_commit_proves_nothing(tmp_path):
    w = _repo(tmp_path)
    sha = _branch(w, "fix/x")
    _, refs = _run(w, {"fix/x": [_pr(9, "MERGED", sha, fork=True), _pr(10, "MERGED", sha, base="release-1")]})
    assert refs["fix/x"]["cls"] == "unproven" and refs["fix/x"]["prs"] == []


def test_open_same_repo_pr_with_another_head_is_unknown(tmp_path):
    w = _repo(tmp_path)
    _branch(w, "fix/moved")
    res, refs = _run(w, {"fix/moved": [_pr(11, "OPEN", "4" * 40)]})
    assert refs["fix/moved"]["cls"] == "unknown" and not res["ok"]


def test_unreadable_github_is_unknown_and_offline_reads_no_pr(tmp_path):
    w = _repo(tmp_path)
    _branch(w, "fix/y")
    res = shiprefs.classify(w, "main", gh=lambda args, timeout=60: (None, "HTTP 401"), slug=SLUG,
                            content_on_base=lambda *a: False)
    assert not res["ok"] and {r["name"]: r["cls"] for r in res["refs"]}["fix/y"] == "unknown"
    off = shiprefs.classify(w, "main", gh=None, content_on_base=lambda *a: False)
    assert {r["name"]: r["cls"] for r in off["refs"]}["fix/y"] == "unproven"
