# Version: 1.1.0 · updated 26-11-25-14-00
"""ship audit / ship release: work reaches main and main is released (2026-10-16: main 135 commits past v0.3.0 and no
tool said so). Real git repositories in a temp folder; GitHub is a stand-in."""
import json
import subprocess

from fieldkit.method import shiprelease as sr
from fieldkit.method import tagguard


def _g(repo, *a):
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *a], check=True,
                          capture_output=True, text=True).stdout.strip()


def _origin_and_clone(tmp_path):
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    w = tmp_path / "w"
    subprocess.run(["git", "clone", "-q", str(origin), str(w)], check=True, capture_output=True)
    _g(w, "checkout", "-q", "-b", "main")
    (w / "pyproject.toml").write_text('[project]\nname = "x"\nversion = "0.2.0"\n', newline="\n")
    _g(w, "add", "-A")
    _g(w, "commit", "-qm", "first")
    _g(w, "tag", "-a", "v0.1.0", "-m", "r")
    for i in range(3):
        (w / f"f{i}").write_text(str(i))
        _g(w, "add", "-A")
        _g(w, "commit", "-qm", f"work {i}")
    _g(w, "push", "-q", "origin", "main", "--tags")
    return w


def test_audit_says_a_release_is_due_and_which_branch_never_reached_main(tmp_path):
    w = _origin_and_clone(tmp_path)
    _g(w, "checkout", "-q", "-b", "lost")
    (w / "lost.txt").write_text("x")
    _g(w, "add", "-A")
    _g(w, "commit", "-qm", "never merged")
    _g(w, "push", "-q", "origin", "lost")
    _g(w, "checkout", "-q", "main")
    gh = lambda args, timeout=60: ("", "") if args[:2] == ["pr", "list"] and "merged" in args else ("[]", "")
    _g(w, "remote", "set-url", "origin", str(tmp_path / "origin.git"))
    r = sr.audit(w, max_commits=2, gh=gh)
    rows = {x["check"]: x for x in r["rows"]}
    assert rows["checkout"]["state"] == "OK"
    assert rows["branches"]["state"] == "DO" and "lost (work not proven on main" in rows["branches"]["say"]
    assert "branches" not in {x["check"] for x in sr.audit(w, offline=True)["rows"]}   # offline: never a false alarm
    assert rows["release"]["state"] == "DO" and "3 commit(s)" in rows["release"]["say"] and "v0.1.0" in rows["release"]["say"]
    assert sr.audit(w, offline=True, max_commits=5)["rows"][-1]["state"] == "OK"


def test_release_refuses_until_ci_version_and_notes_agree_then_tags(tmp_path):
    w = _origin_and_clone(tmp_path)
    runs = {"ci": "completed\tfailure"}

    def gh(args, timeout=60):
        if args[0] == "api":
            return f"test\t{runs['ci']}\n", ""
        if args[:2] == ["release", "view"]:
            return (w / "releases/notes/v0.2.0/page.md").read_text(), ""
        return "[]", ""
    r = sr.release("v0.2.0", w, gh=gh, wait=0)
    assert not r["ok"] and r["rows"][-1]["check"] == "ci"
    runs["ci"] = "completed\tsuccess"
    assert sr.release("v0.3.0", w, gh=gh, wait=0)["rows"][-1]["check"] == "version"
    r = sr.release("v0.2.0", w, gh=gh, wait=0)
    assert r["rows"][-1]["check"] == "notes"
    made = sr.scaffold(w, "v0.2.0")
    assert len(made) == 3 and "work 2" in (w / "releases/notes/v0.2.0/developer.md").read_text()
    assert "v0.2.0" in json.dumps(sr.release("v0.2.0", w, gh=gh, wait=0, scaffold_notes=True))


def test_slug_keeps_dots_in_the_repository_name(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    _g(tmp_path, "remote", "add", "origin", "https://github.com/someone/Gorilla.Fieldkit.git")
    assert sr.repo_slug(tmp_path) == "someone/Gorilla.Fieldkit"


def test_release_parity_reads_the_candidate_commit(tmp_path, monkeypatch):
    from fieldkit.desk import osparity

    repo = _origin_and_clone(tmp_path)
    for path in ("fieldkit/__init__.py", "skills/example", "hooks/example",
                 ".github/example", "docs/PLATFORMS.md"):
        file = repo / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("committed", encoding="utf-8")
    _g(repo, "add", "-A")
    _g(repo, "commit", "-qm", "Fieldkit candidate")
    (repo / "docs/PLATFORMS.md").write_text("changed after commit", encoding="utf-8")
    monkeypatch.setattr(osparity, "check", lambda tree: [
        (tree / "docs/PLATFORMS.md").read_text(encoding="utf-8")])
    assert tagguard.parity_row(repo, _g(repo, "rev-parse", "HEAD")) == (False, "committed")


def test_release_waits_while_ci_is_still_running(tmp_path):
    w = _origin_and_clone(tmp_path)
    states = iter(["in_progress\t", "in_progress\t", "completed\tsuccess"])
    seen = []

    def gh(args, timeout=60):
        if args[0] == "api":
            return f"test\t{next(states)}\n", ""
        return "[]", ""
    r = sr.release("v0.2.0", w, gh=gh, wait=600, sleep=lambda s: seen.append(s))
    assert seen == [30, 30] and [x["check"] for x in r["rows"]][:2] == ["checkout", "ci"] and r["rows"][1]["state"] == "OK"


def _branch_on_origin(w, name, merge=False):
    _g(w, "checkout", "-q", "-b", name, "main")
    (w / f"{name}.txt").write_text(name)
    _g(w, "add", "-A")
    _g(w, "commit", "-qm", name)
    _g(w, "push", "-q", "origin", name)
    _g(w, "checkout", "-q", "main")
    if merge:
        _g(w, "merge", "-q", "--no-ff", name, "-m", f"merge {name}")
        _g(w, "push", "-q", "origin", "main")


def test_prune_deletes_only_proven_branches_logs_them_and_the_restore_line_works(tmp_path):
    # owner, 2026-11-25: the agent cleans up GitHub; proof by commit, logged, restorable, tags untouched
    w = _origin_and_clone(tmp_path)
    _branch_on_origin(w, "done", merge=True)
    _branch_on_origin(w, "open-work")
    gh = lambda args, timeout=60: ("", "")
    assert {x["check"]: x for x in sr.audit(w, gh=gh, max_commits=99)["rows"]}["merged-branches"]["state"] == "DO"
    log = tmp_path / "deleted.jsonl"
    r = sr.prune(w, gh=gh, log=log)
    assert r["deleted"] == ["done"] and r["kept"] == ["open-work"] and r["refused"] == [] and not r["ok"]
    after = _g(w, "ls-remote", "origin")
    assert "refs/heads/done" not in after and "refs/heads/open-work" in after
    assert "refs/heads/main" in after and "refs/tags/v0.1.0" in after
    entry = json.loads(log.read_text(encoding="utf-8"))
    assert entry["branch"] == "done" and entry["restore"] in r["rows"][0]["say"]
    _g(w, *entry["restore"].split()[1:])
    assert "refs/heads/done" in _g(w, "ls-remote", "origin")


def test_audit_finds_what_is_left_behind_locally(tmp_path):
    # owner, 2026-10-23: when the release is finished nothing stays forgotten on a stray branch or worktree
    w = _origin_and_clone(tmp_path)
    rows = lambda: {x["check"]: x for x in sr.audit(w, offline=True)["rows"]}
    assert rows()["worktrees"]["state"] == "OK" and rows()["local-branches"]["state"] == "OK"
    _g(w, "branch", "done-already")                                    # merged, still lying here
    _g(w, "checkout", "-q", "-b", "stray")
    (w / "stray.txt").write_text("x")
    _g(w, "add", "-A")
    _g(w, "commit", "-qm", "on no remote")
    _g(w, "checkout", "-q", "main")
    _g(w, "worktree", "add", "-q", str(tmp_path / "wt"), "-b", "side")
    r = rows()
    assert r["local-branches"]["state"] == "DO"
    assert "stray (1)" in r["local-branches"]["say"] and "done-already" in r["local-branches"]["say"]
    assert "side" not in r["local-branches"]["say"]                   # checked out in a worktree: reported there
    assert r["worktrees"]["state"] == "DO" and "side: its work is on main" in r["worktrees"]["say"]
    assert sr.audit(w, offline=True)["rows"][-1]["check"] == "release"


def test_prune_reports_a_refused_delete_in_gits_words(tmp_path):
    w = _origin_and_clone(tmp_path)
    _branch_on_origin(w, "done", merge=True)
    hook = tmp_path / "origin.git" / "hooks" / "pre-receive"
    hook.write_text('#!/bin/sh\nwhile read o n r; do case "$n" in 0000*) echo "mandate: deletes refused" >&2; exit 1;; esac; done\n',
                    newline="\n")
    hook.chmod(0o755)
    r = sr.prune(w, gh=lambda args, timeout=60: ("", ""), log=tmp_path / "d.jsonl")
    assert not r["ok"] and r["deleted"] == [] and r["refused"] == ["done"] and not (tmp_path / "d.jsonl").exists()
    assert "mandate: deletes refused" in json.dumps(r["rows"])
    assert "refs/heads/done" in _g(w, "ls-remote", "origin")


def test_prune_keeps_open_pr_and_protected_branches_even_when_on_the_base(tmp_path, monkeypatch):
    w = _origin_and_clone(tmp_path)
    for name in ("pr-head", "pr-base", "locked", "squashed", "done"):
        _branch_on_origin(w, name, merge=name != "squashed")
    monkeypatch.setattr(sr, "repo_slug", lambda repo: "o/r")

    def gh(args, timeout=60):
        if args[0] == "api":
            return "locked\n", ""
        if "--head" in args:
            return "[]", ""
        return json.dumps([{"headRefName": "pr-head", "baseRefName": "pr-base"}]), ""

    r = sr.prune(w, gh=gh, log=tmp_path / "d.jsonl")
    assert r["deleted"] == ["done"] and sorted(r["kept"]) == ["locked", "pr-base", "pr-head", "squashed"]


def test_prune_never_deletes_a_branch_that_moved_after_the_proof(tmp_path, monkeypatch):
    w = _origin_and_clone(tmp_path)
    _branch_on_origin(w, "done", merge=True)
    real = sr.shiprefs.classify

    def classify_then_someone_pushes(*a, **kw):
        res = real(*a, **kw)
        _g(w, "checkout", "-q", "done")
        (w / "more.txt").write_text("more")
        _g(w, "add", "-A")
        _g(w, "commit", "-qm", "more")
        _g(w, "push", "-q", "origin", "done")
        _g(w, "checkout", "-q", "main")
        return res

    monkeypatch.setattr(sr.shiprefs, "classify", classify_then_someone_pushes)
    r = sr.prune(w, gh=lambda args, timeout=60: ("", ""), log=tmp_path / "d.jsonl")
    assert r["deleted"] == [] and r["refused"] == ["done"]
    assert _g(w, "ls-remote", "origin", "refs/heads/done").split()[0] == _g(w, "rev-parse", "done")


def test_prune_deletes_nothing_when_github_is_unreadable(tmp_path, monkeypatch):
    # without GitHub, open PRs and protection are unknown: prune deletes nothing
    w = _origin_and_clone(tmp_path)
    _g(w, "checkout", "-q", "-b", "open-pr", "main")
    (w / "o.txt").write_text("o")
    _g(w, "add", "-A")
    _g(w, "commit", "-qm", "open")
    _g(w, "push", "-q", "origin", "open-pr")
    _g(w, "checkout", "-q", "main")
    monkeypatch.setattr(sr, "repo_slug", lambda repo: "o/r")
    r = sr.prune(w, gh=lambda args, timeout=60: (None, "gh hidden"))
    assert not r["ok"] and r["deleted"] == [] and r["rows"][0]["check"] == "github"
    assert "refs/heads/open-pr" in _g(w, "ls-remote", "origin")
