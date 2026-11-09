"""ship tags / --all: tags classed, only real candidates flagged, GitHub tags never deleted by the tool (2026-11-09)."""
import subprocess

from fieldkit.method import shiptags as st
from tests.test_ship_audit_release import _g, _origin_and_clone


def _gh(releases=(), repos=()):
    def gh(args, timeout=60):
        if args[:2] == ["release", "list"]:
            return "\n".join(releases), ""
        if args[:2] == ["repo", "list"]:
            import json
            return json.dumps(list(repos)), ""
        return "0", ""
    return gh


def test_tags_classes_and_only_real_candidates(tmp_path, monkeypatch):
    monkeypatch.setattr(st.sr, "repo_slug", lambda repo: "o/r")
    w = _origin_and_clone(tmp_path)                       # v0.1.0 annotated, pushed
    _g(w, "tag", "-a", "v0.1.1", "-m", "r")               # twin of v0.1.0? no: a new commit target below
    _g(w, "tag", "-a", "v0.1.0b", "-m", "r", "v0.1.0")
    _g(w, "tag", "stray")                                 # local only
    _g(w, "push", "-q", "origin", "v0.1.1", "v0.1.0b")
    _g(w, "branch", "-q", "gone")
    _g(w, "checkout", "-q", "-b", "side")
    (w / "s.txt").write_text("s")
    _g(w, "add", "-A")
    _g(w, "commit", "-qm", "side")
    _g(w, "tag", "-a", "v9.9.9", "-m", "x")
    _g(w, "push", "-q", "origin", "v9.9.9")               # its commit is on no remote branch
    _g(w, "checkout", "-q", "main")
    _g(w, "tag", "-a", "archive/old", "-m", "a", "v0.1.0")
    _g(w, "push", "-q", "origin", "archive/old")
    r = st.tags(w, gh=_gh(releases=["v0.1.0"]))
    kinds = {(c["tag"], c["kind"]) for c in r["candidates"]}
    assert ("v9.9.9", "unreachable") in kinds and ("stray", "local-only") in kinds
    assert ("v0.1.0b", "duplicate") in kinds and ("v0.1.0", "duplicate") in kinds
    assert not any(t == "archive/old" for t, _ in kinds)              # archive tags are never candidates
    assert r["counts"] == {"release": 1, "version-no-release": 2, "archive": 1, "other": 2}
    assert not r["ok"] and "push origin --delete" in r["owner_command"] and "stray" not in r["owner_command"]
    assert "refs/tags/archive/old" not in r["owner_command"]
    assert "refs/tags/v9.9.9" in _g(w, "ls-remote", "--tags", "origin")   # read-only: nothing deleted on the remote


def test_fix_local_removes_only_a_never_pushed_tag(tmp_path):
    w = _origin_and_clone(tmp_path)
    _g(w, "tag", "stray")
    r = st.tags(w, gh=_gh(releases=["v0.1.0"]), fix_local=True)
    assert r["removed"] == ["stray"] and r["ok"]
    assert "stray" not in _g(w, "tag") and "v0.1.0" in _g(w, "tag")
    assert "v0.1.0" in _g(w, "ls-remote", "--tags", "origin")


def test_run_all_one_table_and_one_failure_does_not_stop_the_rest(tmp_path, monkeypatch):
    monkeypatch.setattr(st.sr, "repo_slug", lambda repo: "o/r")
    w = _origin_and_clone(tmp_path)
    repos = lambda gh: [{"slug": "o/good", "base": "main"}, {"slug": "o/lost", "base": "main"}]
    find = lambda slug: w if slug == "o/good" else None
    r = st.run_all("tags", gh=_gh(releases=["v0.1.0"]), repos=repos, find=find, clone=lambda s: (None, str(tmp_path / "x")))
    good, lost = r["table"]
    assert good["ok"] and good["before"]["tags"] == 1 and good["after"]["tags"] == 1 and good["counts"]["release"] == 1
    assert not lost["ok"] and "scratch clone failed" in lost["say"] and not r["ok"]
    assert "o/lost" in "\n".join(st.all_lines(r))


def test_unreadable_releases_are_never_guessed(tmp_path):
    w = _origin_and_clone(tmp_path)
    r = st.tags(w, gh=lambda args, timeout=60: (None, "no auth"))
    assert r["counts"] == {"version-unchecked": 1}


def test_owner_repos_lists_non_archived_with_their_default_branch():
    gh = _gh(repos=[{"nameWithOwner": "o/a", "defaultBranchRef": {"name": "master"}}, {"nameWithOwner": "o/b", "defaultBranchRef": None}])
    assert st.owner_repos(gh) == [{"slug": "o/a", "base": "master"}, {"slug": "o/b", "base": "main"}]


def test_run_all_without_github_says_so_and_touches_nothing():
    r = st.run_all("prune", gh=lambda args, timeout=60: (None, "gh hidden"))
    assert not r["ok"] and r["table"] == [] and "gh repo list failed" in "\n".join(st.all_lines(r))


def test_all_lines_survives_a_repo_with_before_but_no_after():
    # 2026-11-09: a repo that failed after its "before" snapshot crashed the whole --all report (TypeError on None)
    d = {"table": [{"repo": "o/half", "where": "local", "ok": False, "say": "RuntimeError: boom",
                    "before": {"branches": 3, "tags": 2, "prs": 0, "worktrees": 1}}]}
    text = "\n".join(st.all_lines(d))
    assert "o/half" in text and "branches 3->?" in text


def test_git_output_is_decoded_as_utf8(tmp_path):
    # 2026-11-09: byte 0x90 (in "\u0150", UTF-8 c5 90) is undefined in cp1252 and crashed `ship prune --all` on Windows
    import subprocess
    from fieldkit.method import ship
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q",
                    "--allow-empty", "-m", "\u0150 check"], check=True)
    assert ship.git(tmp_path, "log", "-1", "--format=%s").stdout.strip() == "\u0150 check"
