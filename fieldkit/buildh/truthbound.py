"""Is every change the port made explained by the owner's truth tree or by a recorded step?

For each file the ported tree changes against pristine upstream (the repo's root commit):
  * no truth delta  - the owner's 155 truth equals pristine 155 for this file, yet the port changed it: a hunk the
                      owner never carried (kept in a patch file), or a step. Unexplained unless a step names the file.
  * beyond truth    - the port removed/added lines that the truth delta does not (more than 3, comments excluded).
                      Unexplained unless a step names the file. .ftl files are judged by the Fluent rows instead.
Base = the ROOT COMMIT of the 155 repo, never its working tree (2026-10-02: the 155 working tree carries port
checkpoints; read as "upstream" it made 173 good files look unexplained).
Reads 1,500+ files through `git show`, so it is a command (`truthbound`), not a gate row.
"""
import difflib
import json
import subprocess
from pathlib import Path

from . import task


def _root(repo):
    return subprocess.run(["git", "-C", str(repo), "rev-list", "--max-parents=0", "HEAD"], capture_output=True, text=True).stdout.split()[0]


def _show(repo, commit, rel):
    r = subprocess.run(["git", "-C", str(repo), "show", f"{commit}:{rel}"], capture_output=True)
    return r.stdout.decode("utf-8", "replace").splitlines() if r.returncode == 0 else None


def _read(p):
    try:
        return Path(p).read_text(encoding="utf-8", errors="replace").splitlines()
    except (FileNotFoundError, NotADirectoryError):
        return None


def delta(a, b):
    """(removed, added) line sets of a -> b, stripped; None when either side is missing."""
    if a is None or b is None:
        return None
    rem, add = set(), set()
    for l in difflib.unified_diff(a, b, n=0, lineterm=""):
        if l.startswith("-") and not l.startswith("---"):
            rem.add(l[1:].strip())
        elif l.startswith("+") and not l.startswith("+++"):
            add.add(l[1:].strip())
    return rem, add


def _code(lines):
    return {l for l in lines if l and not l.startswith(("//", "#", "*", "/*"))}


def judge(rel, pristine_now, now, pristine_truth, truth, stepped, limit=3):
    """-> None when explained, else (kind, extra_removed, extra_added)."""
    d_now = delta(pristine_now, now)
    d_truth = delta(pristine_truth, truth)
    if d_now is None or d_truth is None or not (d_now[0] or d_now[1]):
        return None
    if not d_truth[0] and not d_truth[1]:
        return None if rel in stepped else ("no-truth", len(d_now[0]), len(d_now[1]))
    if rel.endswith(".ftl") or rel in stepped:
        return None
    er, ea = _code(d_now[0] - d_truth[0]), _code(d_now[1] - d_truth[1])
    if len(er) + len(ea) > limit:
        return ("beyond", len(er), len(ea))
    return None


def run(task_id, truth_repo_155, truth_tree_155, say=print):
    """-> {"ok", "unexplained": [...], "changed": n}. truth_repo_155: the 155 repo (its root commit is pristine
    155); truth_tree_155: the owner's built 155 tree."""
    t = task.load(task_id)
    w = Path(t["workdir"])
    root_now, root_155 = _root(w), _root(truth_repo_155)
    changed = subprocess.run(["git", "-C", str(w), "diff", "--name-only", root_now, "HEAD"], capture_output=True, text=True).stdout.split()
    stepped = {s.get("args", {}).get("file") for s in t["steps"]}
    out = []
    for i, rel in enumerate(changed, 1):
        v = judge(rel, _show(w, root_now, rel), _read(w / rel), _show(truth_repo_155, root_155, rel), _read(Path(truth_tree_155) / rel), stepped)
        if v:
            out.append({"file": rel, "kind": v[0], "removed": v[1], "added": v[2]})
            say(f"  {v[0]:9s} -{v[1]} +{v[2]}  {rel}")
        if i % 300 == 0:
            say(f"  ... {i}/{len(changed)}")
    task.journal(t, "truthbound", changed=len(changed), unexplained=[(o['file'], o['kind']) for o in out][:50])
    return {"ok": not out, "unexplained": out, "changed": len(changed)}
