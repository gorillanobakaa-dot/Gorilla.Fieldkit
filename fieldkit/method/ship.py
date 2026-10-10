"""Ship a change the same way every time: the git side (docs/METHODS.md #11).

    fieldkit ship status [--base main]     where the branch stands against its remote and the base, and what to do
    fieldkit ship sync   [--base main]     make the next push a fast-forward, safely (see below); never force
    fieldkit ship push   [--base main]     sync if needed, then push with retries on network errors

The one hard case it handles: a branch whose pull request was SQUASH-merged. The remote branch still holds the old
commits; the base holds the same tree as one new commit; the local branch was restarted from the base. A plain
push is rejected (non-fast-forward), and the tempting fix - force-push - rewrites published history. Instead,
when the remote branch's tree is IDENTICAL to a commit already on the base (so nothing on it is lost), it is
recorded with `git merge -s ours`: history is kept, the local tree is unchanged, and the push fast-forwards.
Anything else that is not a fast-forward (someone else pushed, real divergence) is refused with what to do.

Never: force-push, rebase a published branch, push to the base, or skip the pre-commit hook.
The merge commit carries the lines in `git config --get-all fieldkit.trailer` (co-author or session lines).
The pull request itself (open, watch CI, merge) is the host's job; the fieldkit-ship skill says how.
Exit 0 done or nothing to do, 3 refused (with the reason and the next step), 2 bad input.
"""
import argparse
import subprocess
import time

from . import emit


def git(repo, *args, check=False, timeout=600):
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=timeout)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {(r.stderr or r.stdout).strip()[-300:]}")
    return r


def _rev(repo, ref):
    r = git(repo, "rev-parse", "--verify", "--quiet", ref)
    return r.stdout.strip() or None


def _tree(repo, ref):
    return _rev(repo, f"{ref}^{{tree}}")


def status(repo=".", base="main", fetch=True):
    branch = git(repo, "branch", "--show-current").stdout.strip()
    if not branch:
        return {"ok": False, "state": "detached", "next": "check out a branch first"}
    if branch == base:
        return {"ok": False, "state": "on-base", "branch": branch,
                "next": f"never push to {base}: git switch -c NEW-BRANCH"}
    if fetch:
        git(repo, "fetch", "--quiet", "origin", base)
        git(repo, "fetch", "--quiet", "origin", branch)
    local, remote, base_ref = _rev(repo, "HEAD"), _rev(repo, f"origin/{branch}"), _rev(repo, f"origin/{base}")
    dirty = bool(git(repo, "status", "--porcelain").stdout.strip())
    rep = {"branch": branch, "base": base, "local": local, "remote": remote, "dirty": dirty}
    if remote is None:
        rep.update(ok=True, state="new-branch", next="push: fieldkit ship push")
    elif remote == local:
        rep.update(ok=True, state="in-step", next="nothing to push")
    elif git(repo, "merge-base", "--is-ancestor", remote, local).returncode == 0:
        rep.update(ok=True, state="ahead", next="push: fieldkit ship push")
    elif git(repo, "merge-base", "--is-ancestor", local, remote).returncode == 0:
        rep.update(ok=False, state="behind", next=f"someone pushed to {branch}: git merge --ff-only origin/{branch}")
    else:
        tree = _tree(repo, remote)
        squashed = base_ref and any(_tree(repo, c) == tree for c in
                                    git(repo, "rev-list", "--max-count=200", base_ref).stdout.split())
        if squashed:
            rep.update(ok=True, state="squash-merged", next="fieldkit ship sync records it (merge -s ours), then push")
        else:
            rep.update(ok=False, state="diverged",
                       next=f"the remote branch has work that is not on {base}: merge origin/{branch} into yours and "
                            "resolve; never force-push")
    return rep


def sync(repo=".", base="main"):
    st = status(repo, base)
    if st.get("state") != "squash-merged":
        return dict(st, synced=False)
    msg = f"Record the squash-merged head of {st['branch']} (tree unchanged)"
    trailers = git(repo, "config", "--get-all", "fieldkit.trailer").stdout.strip()   # e.g. Co-Authored-By lines
    if trailers:
        msg += "\n\n" + trailers
    git(repo, "merge", "-s", "ours", "--no-edit", f"origin/{st['branch']}", "-m", msg, check=True)
    if _tree(repo, "HEAD") != _tree(repo, "HEAD~1"):
        raise RuntimeError("the merge changed the tree: stop and look (git show HEAD)")
    return dict(status(repo, base, fetch=False), synced=True)


def push(repo=".", base="main", tries=5, sleep=time.sleep):
    st = sync(repo, base)
    if not st.get("ok"):
        return st
    if st["state"] == "in-step":
        return dict(st, pushed=False)
    delay, last = 2, ""
    for _ in range(tries):
        r = git(repo, "push", "-u", "origin", st["branch"])
        if r.returncode == 0:
            return dict(status(repo, base), pushed=True)
        last = (r.stderr or r.stdout).strip()
        if "rejected" in last or "non-fast-forward" in last or "denied" in last.lower():
            return dict(st, ok=False, pushed=False, error=last[-300:],
                        next="the push was refused, not lost: fieldkit ship status says why")
        sleep(delay)                                   # network: 2, 4, 8, 16 s
        delay *= 2
    return dict(st, ok=False, pushed=False, error=last[-300:], next="the network kept failing: try again later")


def main(argv=None, prog="fieldkit ship"):
    ap = argparse.ArgumentParser(prog=prog, description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["status", "sync", "push"])
    ap.add_argument("--base", default="main")
    ap.add_argument("--repo", default=".")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        r = {"status": status, "sync": sync, "push": push}[a.action](a.repo, a.base)
    except RuntimeError as e:
        r = {"ok": False, "error": str(e), "next": "nothing was pushed"}
    emit(r, a.json, lambda d: [f"{d.get('branch', '?')}: {d.get('state', 'error')}"
                               + (" (synced)" if d.get("synced") else "") + (" (pushed)" if d.get("pushed") else "")]
         + ([f"error: {d['error']}"] if d.get("error") else []))
    return 0 if r.get("ok") else 3
