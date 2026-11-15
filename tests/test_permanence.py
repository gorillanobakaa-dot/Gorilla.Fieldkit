"""fieldkit permanence check: uncommitted, unpushed, unmerged work (fieldkit/lifecycle/permanence.py)."""
import json
import subprocess
from pathlib import Path

from fieldkit.lifecycle import permanence as pm


def git(cwd, *a):
    subprocess.run(["git", "-C", str(cwd), "-c", "user.email=t@t", "-c", "user.name=t", *a], check=True,
                   capture_output=True)


def make(tmp_path):
    """a bare origin with main, and a clone on a feature branch (a fixture the check reads like any worktree)."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    seed = tmp_path / "seed"
    subprocess.run(["git", "init", "-q", "-b", "main", str(seed)], check=True)
    (seed / "a.txt").write_text("a\n")
    git(seed, "add", "-A")
    git(seed, "commit", "-qm", "seed")
    git(seed, "push", "-q", str(origin), "main")
    wt = tmp_path / "wt"
    subprocess.run(["git", "clone", "-q", str(origin), str(wt)], check=True)
    git(wt, "checkout", "-qb", "feat/x")
    return wt


def scan(wt, **kw):
    return pm.scan_worktree(wt, gh=kw.pop("gh", lambda r, b: []), alive=lambda pid: False, parked=lambda b: False, **kw)


def kinds(fs):
    return [f["kind"] for f in fs]


def test_clean_worktree_has_no_finding(tmp_path):
    assert scan(make(tmp_path)) == []


def test_tracked_change_and_untracked_file_are_uncommitted(tmp_path):
    wt = make(tmp_path)
    (wt / "a.txt").write_text("changed\n")
    (wt / "new.py").write_text("x\n")
    fs = scan(wt)
    assert kinds(fs) == ["UNCOMMITTED"] and fs[0]["blocking"] and "2 file(s)" in fs[0]["detail"]
    assert "git add -A" in fs[0]["next"] and "NEXT:" in pm.line(fs[0])


def test_state_and_ignored_files_do_not_count(tmp_path):
    wt = make(tmp_path)
    (wt / ".gitignore").write_text("*.log\n")
    git(wt, "add", "-A")
    git(wt, "commit", "-qm", "ignore")
    (wt / "x.log").write_text("x")
    (wt / "state").mkdir()
    (wt / "state" / "plan.md").write_text("x")
    assert "UNCOMMITTED" not in kinds(scan(wt))


def test_commit_on_no_remote_is_unpushed_and_blocks(tmp_path):
    wt = make(tmp_path)
    (wt / "b.txt").write_text("b")
    git(wt, "add", "-A")
    git(wt, "commit", "-qm", "b")
    fs = scan(wt)
    assert kinds(fs) == ["UNPUSHED"] and fs[0]["blocking"] and "ship push --auto" in fs[0]["next"]


def test_pushed_branch_never_opened_is_unmerged_not_blocking(tmp_path):
    wt = make(tmp_path)
    (wt / "b.txt").write_text("b")
    git(wt, "add", "-A")
    git(wt, "commit", "-qm", "b")
    git(wt, "push", "-q", "-u", "origin", "feat/x")
    fs = scan(wt)
    assert kinds(fs) == ["UNMERGED"] and not fs[0]["blocking"] and "never opened" in fs[0]["detail"]


def test_open_pr_with_a_live_wait_is_waiting_without_one_unmerged(tmp_path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_CIWAIT_DIR", str(tmp_path / "waits"))
    wt = make(tmp_path)
    (wt / "b.txt").write_text("b")
    git(wt, "add", "-A")
    git(wt, "commit", "-qm", "b")
    git(wt, "push", "-q", "-u", "origin", "feat/x")
    gh = lambda r, b: [{"number": 7, "state": "OPEN", "url": "u/7", "headRefOid": "x"}]  # noqa: E731
    assert kinds(scan(wt, gh=gh)) == ["UNMERGED"]
    from fieldkit.method import ci_wake
    ci_wake.begin(7, "s", pid=1234)
    fs = pm.scan_worktree(wt, gh=gh, alive=lambda pid: True, parked=lambda b: False)
    assert kinds(fs) == ["WAITING"] and not fs[0]["blocking"]


def test_merged_pr_at_head_is_permanent_and_gh_down_is_no_finding(tmp_path):
    wt = make(tmp_path)
    (wt / "b.txt").write_text("b")
    git(wt, "add", "-A")
    git(wt, "commit", "-qm", "b")
    git(wt, "push", "-q", "-u", "origin", "feat/x")
    head = subprocess.run(["git", "-C", str(wt), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    assert scan(wt, gh=lambda r, b: [{"number": 7, "state": "MERGED", "url": "u", "headRefOid": head}]) == []
    assert scan(wt, gh=lambda r, b: None) == []


def test_closed_pr_is_unmerged(tmp_path):
    wt = make(tmp_path)
    (wt / "b.txt").write_text("b")
    git(wt, "add", "-A")
    git(wt, "commit", "-qm", "b")
    git(wt, "push", "-q", "-u", "origin", "feat/x")
    fs = scan(wt, gh=lambda r, b: [{"number": 7, "state": "CLOSED", "url": "u/7", "headRefOid": "x"}])
    assert kinds(fs) == ["UNMERGED"] and "closed" in fs[0]["detail"]


def test_stop_reasons_block_only_uncommitted_and_unpushed():
    fs = [{"kind": "UNMERGED", "blocking": False, "root": "r", "branch": "b", "detail": "d", "next": "n", "age_h": 5},
          {"kind": "UNPUSHED", "blocking": True, "root": "r", "branch": "b", "detail": "d", "next": "n", "age_h": 5}]
    r = pm.stop_reasons("t", scan_fn=lambda session: fs)
    assert len(r) == 1 and r[0].startswith("PERMANENCE: UNPUSHED")
    assert pm.stop_reasons("t", scan_fn=lambda session: 1 / 0) == []          # fails open


def test_archive_refused_with_findings_and_open_without():
    bad = [{"kind": "UNCOMMITTED", "blocking": True, "root": "r", "branch": "b", "detail": "d", "next": "n", "age_h": 0}]
    call = {"tool_name": "mcp__x__archive_session", "tool_input": {"session_id": "self"}, "transcript_path": "t"}
    assert "ARCHIVE REFUSED" in pm.archive_refusal(call, scan_fn=lambda session: bad)
    assert pm.archive_refusal(call, scan_fn=lambda session: []) is None
    assert pm.archive_refusal(dict(call, tool_name="Bash"), scan_fn=lambda session: bad) is None
    assert pm.archive_refusal(call, scan_fn=lambda session: 1 / 0) is None


def test_session_scope_reads_edits_from_the_transcript(tmp_path):
    wt = make(tmp_path)
    f = wt / "a.txt"
    tp = tmp_path / "s.jsonl"
    tp.write_text(json.dumps({"cwd": str(tmp_path), "message": {"content": [
        {"type": "tool_use", "name": "Edit", "input": {"file_path": str(f)}}]}}) + "\n")
    roots, edited = pm.session_scope(tp)
    assert wt.resolve() in roots and str(f.resolve()) in edited


def test_main_checkout_counts_only_files_the_session_edited(tmp_path):
    wt = make(tmp_path)
    (wt / "mine.txt").write_text("1")
    (wt / "theirs.txt").write_text("2")
    fs = scan(wt, edited={str((wt / "mine.txt").resolve())}, only_edited=True)
    assert kinds(fs) == ["UNCOMMITTED"] and "1 file(s)" in fs[0]["detail"]


def test_stale_lists_old_unpushed_and_unmerged_only():
    mk = lambda k, a: {"kind": k, "age_h": a}  # noqa: E731
    got = pm.stale([mk("UNPUSHED", 3), mk("UNPUSHED", 1), mk("UNMERGED", 2), mk("UNCOMMITTED", 9)])
    assert [f["kind"] for f in got] == ["UNPUSHED", "UNMERGED"]


def test_keeper_rule_says_stale_work_in_the_room_and_never_starts_a_model():
    from fieldkit.lifecycle import keeper

    class P:
        def permanence(self, now=None):
            return [{"kind": "UNPUSHED", "age_h": 3.0, "root": "/x/wt", "branch": "feat/a", "detail": "2 ahead", "next": "ship"}]
    ctx = {"wake": [], "now": 0}
    assert keeper.rule_permanence(P(), {"stale_after_h": 2}, {}, ctx) == []
    assert ctx["wake"][0]["key"].startswith("stale:") and "3.0 h old" in ctx["wake"][0]["line"]
    ctx = {"wake": [], "now": 0}
    keeper.rule_permanence(P(), {"stale_after_h": 5}, {}, ctx)
    assert ctx["wake"] == []


def test_a_relative_or_foreign_path_is_never_the_current_worktree(tmp_path):
    assert pm._toplevel("C:/work") is None and pm._toplevel("relative/dir") is None
    tp = tmp_path / "s.jsonl"
    tp.write_text(json.dumps({"cwd": "C:/work", "message": {"content": []}}) + "\n")
    assert pm.session_scope(tp) == (set(), set())
