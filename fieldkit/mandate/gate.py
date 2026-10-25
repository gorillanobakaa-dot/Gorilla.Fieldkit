# Version: 1.0.0 · updated 26-10-25-23-40
"""The pre-push gate a repository's hook calls, so ONE owner approval covers every repository (2026-10-25).

    fieldkit mandate gate [--repo .]     the pre-push lines on stdin -> exit 0 (allowed) or 3 (refused, reasons)

Owner, 2026-10-25: "the approval has to come once, at the beginning" and "has to cover all our projects". That day
the owner pasted about twelve GORILLA_OWNER_APPROVED push lines over five hours for two releases. A hook that wants
the owner's flag now also accepts a push when ALL of these hold, checked here from git and the sealed mandate, never
from the caller's word:

  1. it was made by `fieldkit ship push` (FIELDKIT_SHIP_PUSH=1, set only by that tool). A marker, not a lock: any
     program running as the user can set it. It keeps the push on the path that logs it and that the hard lines
     open; what no program can forge is the next point.
  2. a mandate in force on this computer (seal, trusted key, expiry, stop: mandate.verify) names this repository
     (by the name of origin's URL).
  3. every line is a branch the repository's block lets sessions push, fast-forward only, pushed under its own name,
     never a base branch (main, master, windows-port); or a NEW tag matching its tag patterns whose commit GitHub
     already has, or that a branch in this same push carries.
  4. nothing is deleted, no tag is moved, no other kind of ref is pushed.

Anything else is refused, and the hook falls back to what it did before (the owner's flag). The hook's own secret
and privacy scans run on every push either way: this gate only answers "may this go without the owner's flag".
Every allowed push is written to the mandate's log.
"""
import argparse
import os
import subprocess
import sys

ZERO = "0" * 40
MARKER = "FIELDKIT_SHIP_PUSH"


def _git(repo, *a):
    return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, timeout=120)


def _ancestor(repo, a, b):
    return _git(repo, "merge-base", "--is-ancestor", a, b).returncode == 0


def _mandate_for(repo_name, today=None):
    """-> (path, mandate, cfg) of the first mandate in force naming the repository, or (None, None, why)."""
    from . import mandate as M
    why = []
    for p in M.mandates():
        ok, w = M.verify(p, today)
        if not ok:
            why.append(f"{p.stem}: {w}")
            continue
        m = M.load(p)
        cfg = M.repo_cfg(m, repo_name)
        if cfg is None:
            why.append(f"{p.stem}: does not name {repo_name}")
            continue
        return p, m, cfg
    return None, None, "; ".join(why) or "no mandate on this computer"


def check(repo_path, stdin_text, env=None, today=None, where=None):
    """-> (ok, reasons, mandate path or None). `where` replaces proofs.where in tests."""
    from . import mandate as M
    from . import proofs
    env = os.environ if env is None else env
    if env.get(MARKER) != "1":
        return False, ["not pushed through fieldkit ship push (the only door a mandate opens)"], None
    lines = [ln.split() for ln in stdin_text.splitlines() if ln.strip()]
    if not lines or any(len(x) != 4 for x in lines):
        return False, ["no push lines, or a line git did not write (4 fields each)"], None
    repo, _ = (where or proofs.where)(repo_path)
    path, m, cfg = _mandate_for(repo, today)
    if path is None:
        return False, [f"no mandate in force names {repo}: {cfg}"], None
    refused, branches = [], []
    for local_ref, local_sha, remote_ref, remote_sha in lines:
        if local_sha == ZERO:
            refused.append(f"deleting {remote_ref} is the owner's")
        elif remote_ref.startswith("refs/heads/"):
            name = remote_ref[len("refs/heads/"):]
            ok, why = M.branch_ok(cfg, name)
            if not ok:
                refused.append(why)
            elif local_ref != remote_ref:
                refused.append(f"{local_ref} pushed under another name ({remote_ref})")
            elif remote_sha != ZERO and not _ancestor(repo_path, remote_sha, local_sha):
                refused.append(f"{name}: not a fast-forward of origin (history rewrite is the owner's)")
            else:
                branches.append(local_sha)
    for local_ref, local_sha, remote_ref, remote_sha in lines:
        if local_sha == ZERO or remote_ref.startswith("refs/heads/"):
            continue
        if not remote_ref.startswith("refs/tags/"):
            refused.append(f"{remote_ref} is neither a branch nor a tag")
            continue
        tag = remote_ref[len("refs/tags/"):]
        ok, why = M.tag_ok(cfg, tag)
        if not ok:
            refused.append(why)
            continue
        if remote_sha != ZERO:
            refused.append(f"{tag} exists on origin: a tag is never moved")
            continue
        commit = _git(repo_path, "rev-parse", "--verify", "--quiet", f"{local_sha}^{{commit}}").stdout.strip()
        if not commit:
            refused.append(f"{tag} does not point at a commit")
        elif not (proofs._origin_has(repo_path, commit) or any(_ancestor(repo_path, commit, b) for b in branches)):
            refused.append(f"{tag}'s commit {commit[:10]} is on no branch of origin nor in this push")
    if refused:
        return False, refused, path
    M.log(path, "push-gate", f"{repo}: " + ", ".join(x[2] for x in lines), by="pre-push gate",
          refs=[f"{x[2]} {x[1][:12]}" for x in lines])
    return True, [f"allowed by the mandate {path.stem} for {repo}: " + ", ".join(x[2] for x in lines)], path


def main(argv=None):
    ap = argparse.ArgumentParser(prog="fieldkit mandate gate", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default=".")
    a = ap.parse_args(argv)
    try:
        ok, reasons, _p = check(a.repo, sys.stdin.read())
    except Exception as e:  # noqa: BLE001 - a broken gate never opens anything
        ok, reasons = False, [f"the gate failed ({type(e).__name__}: {e})"]
    for r in reasons:
        print(("  MANDATE: " if ok else "  - ") + r, file=sys.stderr)
    return 0 if ok else 3
