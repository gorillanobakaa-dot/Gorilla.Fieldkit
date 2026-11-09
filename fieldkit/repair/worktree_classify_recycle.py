"""Which git worktrees are done: each one against origin/main and the PR state, so DONE PROTOCOL step 4 needs no
hand-work. Verdicts: merged (its HEAD is in origin/main, or in a merged PR, which also covers squash merges),
open-pr, unmerged; "+dirty" when it has uncommitted changes. Only a clean `merged` one that is neither the main
checkout nor the one you are standing in is listed as recyclable, with the command; nothing is deleted here
(`fieldkit size recycle PATH` does that, a recycle, never a permanent delete).

Live: `git worktree list --porcelain`, `git status`, `gh pr list --state all` (skipped with a note when gh is missing).
`--fixture-json FILE` classifies a saved {"worktrees": [...], "prs": [...]} instead (tests, other machines).

    fieldkit repair worktree-classify-recycle [--repo PATH] [--fixture-json F] [--json]
Exit: 0 classified, 1 error.
"""
import argparse
import json
import os
import subprocess
import time
from pathlib import Path

from ._common import emit


def parse_worktree_porcelain(text):
    out, cur = [], {}
    for line in text.splitlines() + [""]:
        if not line:
            if cur:
                out.append(cur)
            cur = {}
            continue
        k, _, v = line.partition(" ")
        cur[k] = v
    return out


def classify(path, branch, head, dirty_lines, is_ancestor_of_main, extra_commits, age_h, prs):
    merged_heads = {p.get("headRefOid") for p in prs if p.get("state") == "MERGED"}
    merged_names = {p.get("headRefName") for p in prs if p.get("state") == "MERGED"}
    open_names = {p.get("headRefName") for p in prs if p.get("state") == "OPEN"}
    br = branch.removeprefix("refs/heads/")
    if is_ancestor_of_main or head in merged_heads:
        verdict = "merged"
    elif br in open_names:
        verdict = "open-pr"
    else:
        verdict = "unmerged"
    if dirty_lines:
        verdict += "+dirty"
    return {"path": path, "branch": br, "verdict": verdict, "extra": extra_commits, "dirty": len(dirty_lines),
            "age_h": round(age_h, 1), "pr_merged_by_name": br in merged_names}


def _git(repo, *args, run=subprocess.run):
    r = run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=120)
    return r.returncode, (r.stdout or "").strip()


def live(repo, run=subprocess.run, now=None):
    """-> (worktrees as fixture dicts, prs, notes)."""
    rc, out = _git(repo, "worktree", "list", "--porcelain", run=run)
    if rc != 0:
        raise ValueError("not a git repository (or git failed)")
    notes, prs = [], []
    try:
        r = run(["gh", "pr", "list", "--state", "all", "--limit", "300", "--json", "headRefName,headRefOid,state"],
                capture_output=True, text=True, timeout=120, cwd=str(repo))
        prs = json.loads(r.stdout or "[]") if r.returncode == 0 else []
        if r.returncode != 0:
            notes.append("gh pr list failed: PR state not used, only the ancestry check")
    except (OSError, ValueError, subprocess.SubprocessError):
        notes.append("gh not available: PR state not used, only the ancestry check")
    base = "origin/main" if _git(repo, "rev-parse", "--verify", "-q", "origin/main", run=run)[0] == 0 else "main"
    now = now or time.time()
    wts = []
    for w in parse_worktree_porcelain(out):
        path, head = w.get("worktree", ""), w.get("HEAD", "")
        if "bare" in w or not path:
            continue
        anc = _git(repo, "merge-base", "--is-ancestor", head, base, run=run)[0] == 0
        extra = _git(repo, "rev-list", "--count", f"{base}..{head}", run=run)[1]
        dirty = _git(path, "status", "--porcelain", run=run)[1].splitlines() if Path(path).is_dir() else []
        try:
            age = (now - Path(path).stat().st_mtime) / 3600
        except OSError:
            age = 0.0
        wts.append({"path": path, "branch": w.get("branch", "(detached)"), "head": head, "dirty_lines": dirty,
                    "is_ancestor_of_main": anc, "extra_commits": int(extra) if extra.isdigit() else 0, "age_h": age})
    return wts, prs, notes


def main(argv=None):
    ap = argparse.ArgumentParser(prog="fieldkit repair worktree-classify-recycle", description=__doc__.split("\n")[0])
    ap.add_argument("--repo", default=".")
    ap.add_argument("--fixture-json")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    notes = []
    try:
        if a.fixture_json:
            data = json.loads(Path(a.fixture_json).read_text(encoding="utf-8"))
            wts, prs = data.get("worktrees", []), data.get("prs", [])
        else:
            wts, prs, notes = live(a.repo)
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        emit(a.json, "FAIL", f"cannot classify: {e}", "run it inside a git repository, or pass --fixture-json")
        return 1
    here = os.path.normcase(os.path.realpath(a.repo))
    rows = []
    for i, w in enumerate(wts):
        r = classify(w["path"], w.get("branch", ""), w["head"], w.get("dirty_lines", []), w.get("is_ancestor_of_main", False),
                     w.get("extra_commits", 0), w.get("age_h", 0.0), prs)
        r["main_checkout"] = i == 0 and not a.fixture_json
        r["current"] = not a.fixture_json and os.path.normcase(os.path.realpath(w["path"])) == here
        r["recyclable"] = r["verdict"] == "merged" and not r["main_checkout"] and not r["current"]
        rows.append(r)
        if not a.json:
            print(f"{r['verdict']:16} extra={r['extra']:3} dirty={r['dirty']:3} age={r['age_h']:6}h {r['branch']:34} {r['path']}")
    if not a.json:
        for n in notes:
            print("note:", n)
    ready = [r for r in rows if r["recyclable"]]
    emit(a.json, "PASS", f"classified {len(rows)} worktree(s), {len(ready)} clean and merged",
         "; ".join(f"fieldkit size recycle \"{r['path']}\"" for r in ready[:5]) or "none", {"worktrees": rows, "notes": notes})
    return 0
