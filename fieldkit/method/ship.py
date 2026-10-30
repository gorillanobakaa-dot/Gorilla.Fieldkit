"""Ship a change the same way every time: the git side (docs/METHODS.md #11).

    fieldkit ship status [--base main]     where the branch stands against its remote and the base, and what to do
    fieldkit ship sync   [--base main]     make the next push a fast-forward, safely (see below); never force
    fieldkit ship push   [--base main]     sync if needed, then push with retries on network errors; the push carries
                                           FIELDKIT_SHIP_PUSH=1, so a repository's pre-push hook asks the mandate gate
    fieldkit ship push --tag vX.Y.Z        push ONE new tag (never moved) under a mandate's tags: pattern, and only when
                                           its commit can publish: version, notes, page, CI green (tagguard.py)
    fieldkit ship release-ready vX.Y.Z     the same checks as a dry run: what is missing, in order, and the fix for each
    fieldkit ship gh-release vX.Y.Z -- ARGS   gh release create --verify-tag of a tag on origin, under a mandate's
                                           release: from_tag, once CI is green on the tag's commit; does nothing when
                                           the repository's release.yml publishes on tags
    fieldkit ship commit --message-file F  commit what is staged; a known hook refusal fixed and tried again, the same
                                           one twice or an unknown one stops and is reported; never --no-verify
    fieldkit ship merge  [--base main]     merge the branch into main ONLY when a project mandate allows it, every
                                           condition proven by the tool (fieldkit/mandate); otherwise the owner does it
    fieldkit ship audit  [--offline]       is the work on main, is the checkout on main, is main released (shiprelease.py)
    fieldkit ship release vX.Y.Z [--scaffold]   tag and publish, only when main, CI, version and notes all agree
    fieldkit ship prune  [--archive-unproven]   delete remote branches git or GitHub prove merged; one source of truth
    fieldkit ship update [--base main]     merge origin/main into the branch the train's way: generated files
                                           regenerated, cards merged entry by entry, proven only a merge (mergeproof.py)
    fieldkit ship proof OURS THEIRS [R]    what each path of a merge is; exit 0 only a merge
    fieldkit ship ci-record SHA            remember GitHub's green CI for SHA, for the offline pre-commit hook
    fieldkit ship ready PR [--pending T]   the session declares its PR final (label + head sha); --pending: not yet
    fieldkit ship train [--dry-run]        merge the declared PRs one at a time, dependencies first, never red, with
                                           --match-head-commit; resumable (train.py); --modes lists what it prevents
    fieldkit ship ci-wait PR [--merge]     one wake when the PR's CI ends (run it in the background): green, or red
                                           with the failed log tail; --merge merges on green (train.py)
    fieldkit ship overlap [--files F...]   open PRs and live stations on the same files as this work
    fieldkit ship freeze|unfreeze          a checkout that must not move: ff pending, then parity baseline --set

The one hard case it handles: a branch whose pull request was SQUASH-merged. The remote branch still holds the old
commits; the base holds the same tree as one new commit; the local branch was restarted from the base. A plain
push is rejected (non-fast-forward), and the tempting fix - force-push - rewrites published history. Instead,
when the remote branch's tree is IDENTICAL to a commit already on the base (so nothing on it is lost), it is
recorded with `git merge -s ours`: history is kept, the local tree is unchanged, and the push fast-forwards.
Anything else that is not a fast-forward (someone else pushed, real divergence) is refused with what to do.

Never: force-push, rebase a published branch, push to the base, or skip the pre-commit hook. The one exception to
"push to the base" is `ship merge`, and only under an approved project mandate (owner, 2026-10-23: one approval per
project): fast-forward (`how: fast-forward`, a push of HEAD to main that git refuses unless it only adds) or the
branch's pull request (`how: pull-request`, merged by gh only if its head is the proven commit). A push or merge the
mandate covers is written to the mandate's log.
The merge commit carries the lines in `git config --get-all fieldkit.trailer` (co-author or session lines).
The pull request itself (open, watch CI, merge) is the host's job; the fieldkit-ship skill says how.
Exit 0 done or nothing to do, 3 refused (with the reason and the next step), 2 bad input.
"""
import argparse
import re
import subprocess
import sys
import time
from pathlib import Path

from . import emit


def git(repo, *args, check=False, timeout=600, env=None):
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=timeout, env=env)
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


def _mandate(action, **kw):
    """-> (verdict, why, mandate path or None); a broken mandate module never opens anything."""
    try:
        from ..mandate import mandate as M
        return M.decide(action, **kw)
    except Exception as e:  # noqa: BLE001
        return "ask", f"the mandate could not be read ({e})", None


def _mandate_log(path, what, detail, **extra):
    if path is not None:
        from ..mandate import mandate as M
        M.log(path, what, detail, **extra)


def push(repo=".", base="main", tries=5, sleep=time.sleep, mandate=_mandate):
    st = sync(repo, base)
    if not st.get("ok"):
        return st
    if st["state"] == "in-step":
        return dict(st, pushed=False)
    from . import strikes
    if strikes.parked_for(st["branch"]):               # TWO STRIKES: no third round
        return dict(st, ok=False, pushed=False, state="parked", error=strikes.refusal([st["branch"]]),
                    next="parked, go on with the plan; nothing was pushed")
    # an unattended push reaches here only through the hard line, which asked the mandate first; here it is logged
    verdict, why, mpath = mandate("push", repo_path=repo)
    delay, last = 2, ""
    for _ in range(tries):
        r = git(repo, "push", "-u", "origin", st["branch"], env=ship_env())
        if r.returncode == 0:
            if verdict == "allow":
                _mandate_log(mpath, "push", why, branch=st["branch"], commit=st.get("local"))
            return dict(status(repo, base), pushed=True, mandate=why if verdict == "allow" else "")
        last = (r.stderr or r.stdout).strip()
        if refused_not_network(last):
            return dict(st, ok=False, pushed=False, error=last[-1500:],
                        next="the push was refused, not lost: fieldkit ship status says why")
        sleep(delay)                                   # network: 2, 4, 8, 16 s
        delay *= 2
    return dict(st, ok=False, pushed=False, error=last[-300:], next="the network kept failing: try again later")


def refused_not_network(out):
    """A push git or a hook REFUSED (never retried: 2026-10-25, a pre-push hook's refusal was retried five times as
    "the network kept failing", its reasons cut to the last 300 characters)."""
    low = out.lower()
    return any(s in low for s in ("rejected", "non-fast-forward", "denied", "refused", "pre-push hook",
                                  "hook declined", "failed to push some refs"))


def _real_gh_config(env):
    """gh's own config folder (where it keeps the account), as it was before an autopilot run hid it."""
    import os
    if env.get("FIELDKIT_REAL_GH_CONFIG_DIR"):
        return env["FIELDKIT_REAL_GH_CONFIG_DIR"]
    if os.name == "nt":
        return os.path.join(env.get("APPDATA") or os.path.expanduser("~"), "GitHub CLI")
    return os.path.join(env.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "gh")


def ship_env():
    """The environment of a push made by this tool: FIELDKIT_SHIP_PUSH=1 tells a repository's pre-push hook to ask the
    mandate gate (fieldkit/mandate/gate.py) instead of wanting the owner's flag. A marker, not a credential.
    FIELDKIT_SHIP_PYTHON (2026-10-26) is the Python running this tool, so the hook asks the SAME Fieldkit's gate even
    where fieldkit is not on PATH (a Mac installed from the zip keeps it in FOLDER/.venv). Same-user, like PATH.

    Inside an autopilot run (FIELDKIT_AUTOPILOT=1, fieldkit/autopilot/run.py hides every git/gh credential) the owner
    decided, 2026-10-28 on inbox #145, "Yes, ship only": "headless autopilot runs may use the GitHub credential for
    `fieldkit ship push` / ship ci-wait --merge only". So for ship's own git and gh calls the hiding is undone: git's
    credential helper and gh's config folder come back, and each tool reads the credential itself at call time. No
    token is ever put in the environment, an argument or a log; the run's own environment stays without one."""
    import os
    env = dict(os.environ, FIELDKIT_SHIP_PUSH="1", FIELDKIT_SHIP_PYTHON=sys.executable)
    if env.get("FIELDKIT_AUTOPILOT") == "1":
        for k in ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN", "GIT_ASKPASS",
                  "SSH_ASKPASS"):
            if not env.get(k):                        # the run's blanks; a real value was never put there
                env.pop(k, None)
        if env.get("GIT_CONFIG_KEY_0") == "credential.helper" and not env.get("GIT_CONFIG_VALUE_0"):
            for k in ("GIT_CONFIG_COUNT", "GIT_CONFIG_KEY_0", "GIT_CONFIG_VALUE_0"):
                env.pop(k, None)
        env["GH_CONFIG_DIR"] = _real_gh_config(env)
        env.pop("FIELDKIT_REAL_GH_CONFIG_DIR", None)
    return env


def push_tag(repo=".", tag="", tries=5, sleep=time.sleep, mandate=_mandate, guard=None):
    """Push ONE new tag, never moved: allowed when a mandate's `tags:` pattern matches and its commit is on origin,
    and the tagged commit can publish (tagguard: version, release notes, page, CI, tag not on origin)."""
    if not tag or not _rev(repo, f"refs/tags/{tag}"):
        return {"ok": False, "state": "no-tag", "tag": tag, "next": f"make the tag first: git tag -a {tag} -m ..."}
    verdict, why, mpath = mandate("tag", repo_path=repo, tag=tag)
    if verdict != "allow":
        return {"ok": False, "state": "not-mandated", "tag": tag, "error": why,
                "next": "pushing this tag is the owner's unless a mandate allows it"}
    from . import tagguard
    g = (guard or tagguard.check)(repo, tag)
    if not g["ok"]:
        bad = [x for x in g["rows"] if not x["ok"]]
        return {"ok": False, "state": "not-release-ready", "tag": tag, "rows": g["rows"],
                "error": "; ".join(f"{x['check']}: {x['say']}" for x in bad),
                "next": bad[0]["run"] or f"fieldkit ship release-ready {tag}"}
    delay, last = 2, ""
    for _ in range(tries):
        r = git(repo, "push", "origin", f"refs/tags/{tag}", env=ship_env())
        if r.returncode == 0:
            _mandate_log(mpath, "tag", why, tag=tag)
            return {"ok": True, "state": "tag-pushed", "tag": tag, "pushed": True, "mandate": why}
        last = (r.stderr or r.stdout).strip()
        if refused_not_network(last):
            return {"ok": False, "state": "refused", "tag": tag, "error": last[-1500:], "next": "nothing was forced"}
        sleep(delay)
        delay *= 2
    return {"ok": False, "state": "network", "tag": tag, "error": last[-300:], "next": "try again later"}


def gh_release(repo=".", tag="", extra=(), mandate=_mandate, run=subprocess.run):
    """gh release create TAG --verify-tag -R <origin> EXTRA..., only when a mandate allows release_tag (the tag is on
    origin, matches, release: from_tag, CI green on its commit). Never creates or moves a tag (--verify-tag)."""
    wf = Path(repo) / ".github" / "workflows" / "release.yml"
    if wf.is_file() and re.search(r"(?m)^\s*tags\s*:", wf.read_text(encoding="utf-8", errors="replace")):
        return {"ok": True, "state": "tag-workflow-publishes", "tag": tag,
                "next": "the tag workflow publishes; do not run gh-release"}
    bad = [x for x in extra if x in ("--target", "-t", "-R", "--repo") or x.startswith(("--target=", "--repo=", "-R"))]
    if bad:
        return {"ok": False, "state": "bad-input", "tag": tag, "error": f"not allowed here: {' '.join(bad)}",
                "next": "the tag's commit and the repository come from git, never from the caller"}
    verdict, why, mpath = mandate("release_tag", repo_path=repo, tag=tag)
    if verdict != "allow":
        return {"ok": False, "state": "not-mandated", "tag": tag, "error": why,
                "next": "publishing a release is the owner's unless a mandate allows it"}
    from ..core import sync
    r = run(["gh", "release", "create", tag, "--verify-tag", "-R", sync._slug(repo), *extra], cwd=str(repo),
            capture_output=True, text=True, env=ship_env())
    if r.returncode != 0:
        return {"ok": False, "state": "refused", "tag": tag, "error": (r.stderr or r.stdout).strip()[-300:],
                "next": "nothing was published"}
    _mandate_log(mpath, "release", why, tag=tag, url=(r.stdout or "").strip()[-200:])
    return {"ok": True, "state": "released", "tag": tag, "mandate": why, "next": (r.stdout or "").strip()[-200:]}


def request_test_job(repo, commit, call=None):
    """Give the worker the `test` job for `commit` under the mandate in force here, once -> (job id, what happened)."""
    from ..mandate import jobs, proofs
    name, _ = proofs.where(repo)
    mp = jobs.mandate_for(name)
    if mp is None:
        return None, "no mandate in force here names this repository"
    return jobs.send("test", name, commit, mp, call=call)


def merge(repo=".", base="main", mandate=_mandate, run=subprocess.run, verdict_call=None):
    """Merge the current branch into `base` under a project mandate, or refuse with what is missing."""
    branch = git(repo, "branch", "--show-current").stdout.strip()
    if not branch or branch == base:
        return {"ok": False, "state": "no-branch", "next": f"check out the branch to merge into {base}"}
    if git(repo, "status", "--porcelain").stdout.strip():
        return {"ok": False, "state": "dirty", "branch": branch, "next": "commit or set aside the changes first"}
    git(repo, "fetch", "--quiet", "origin", base)
    git(repo, "fetch", "--quiet", "origin", branch)
    commit = _rev(repo, "HEAD")
    rep = {"branch": branch, "base": base, "commit": commit}
    if _rev(repo, f"origin/{branch}") != commit:
        return dict(rep, ok=False, state="unpushed", next=f"push {branch} first (fieldkit ship push): the reviewer "
                                                           "and CI must have seen this exact commit")
    verdict, why, mpath = mandate("merge_main", repo_path=repo, commit=commit)
    if verdict != "allow" and re.search(r"(other_machine_reviewed|tests_on)(:[\w-]+)?: no passing RESULT", why) \
            and "full_tests_pass_here:" not in why:
        # a worker's result is missing: give the worker the test job ONCE (a tool runs it there, no session is asked),
        # then wait in one blocking call, not a loop of questions (plan section 16, 2026-10-23)
        jid, what = request_test_job(repo, commit, call=verdict_call)
        return dict(rep, ok=False, state="waiting-result", error=why, job=jid, job_sent=what,
                    next=f"wait for the worker in one call: fieldkit mandate job wait {jid} --seconds 3600; "
                         f"then fieldkit ship merge again")
    if verdict != "allow":
        return dict(rep, ok=False, state="not-mandated", error=why,
                    next=f"a merge into {base} is the owner's unless a mandate allows it; prove what is missing")
    from ..mandate import mandate as M
    from ..mandate import proofs
    name, _ = proofs.where(repo)
    how = ((M.load(mpath)["repos"][name].get("merge_to_main") or {}).get("how") or "fast-forward")
    if how == "fast-forward":
        base_ref = _rev(repo, f"origin/{base}")
        if base_ref and git(repo, "merge-base", "--is-ancestor", base_ref, commit).returncode != 0:
            return dict(rep, ok=False, state="not-fast-forward", error=f"origin/{base} has commits this branch lacks",
                        next=f"merge origin/{base} into {branch}, push, and have it reviewed again")
        r = git(repo, "push", "origin", f"{commit}:refs/heads/{base}", env=ship_env())
    elif how == "pull-request":
        r = run(["gh", "pr", "merge", branch, "--merge", "--match-head-commit", commit],
                cwd=str(repo), capture_output=True, text=True, env=ship_env())
    else:
        return dict(rep, ok=False, state="unknown-how", error=f"merge_to_main.how is {how!r}",
                    next="the mandate says fast-forward or pull-request")
    if r.returncode != 0:
        return dict(rep, ok=False, state="refused", error=(r.stderr or r.stdout).strip()[-300:],
                    next="nothing was forced; read the error")
    git(repo, "fetch", "--quiet", "origin", base)
    landed = git(repo, "merge-base", "--is-ancestor", commit, f"origin/{base}").returncode == 0
    if not landed:                                     # the artefact, not the exit code
        return dict(rep, ok=False, state="not-landed", error=f"{commit[:10]} is not on origin/{base} after the merge",
                    next="look at the pull request or the remote")
    _mandate_log(mpath, "merge_main", why, branch=branch, commit=commit, how=how)
    return dict(rep, ok=True, state="merged", how=how, mandate=why, next=f"{branch} is on {base}")


TRAIN = ("ready", "train", "overlap", "freeze", "unfreeze", "ci-wait")             # train.py
PROOF = {"update": "update", "proof": "prove", "ci-record": "ci-record"}  # mergeproof.py


def main(argv=None, prog="fieldkit ship"):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] and argv[0] in TRAIN:
        from . import train
        return train.main(argv, prog)
    if argv[:1] and argv[0] in PROOF:
        from . import mergeproof
        return mergeproof.main([PROOF[argv[0]], *argv[1:]], f"{prog} {argv[0]}")
    ap = argparse.ArgumentParser(prog=prog, description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["status", "sync", "push", "merge", "commit", "audit", "release", "prune",
                                       "gh-release", "release-ready", *TRAIN, *PROOF])
    ap.add_argument("--tag", dest="push_tag", help="push: this NEW tag (never moved) instead of the branch")
    ap.add_argument("--message-file", help="commit: the commit message file")
    ap.add_argument("tag", nargs="?", help="release: vX.Y.Z")
    ap.add_argument("--offline", action="store_true", help="audit: git only, no network")
    ap.add_argument("--archive-unproven", action="store_true",
                    help="prune: save a branch whose work is not proven on the base as tag archive/<branch>, then delete it")
    ap.add_argument("--scaffold", action="store_true", help="release: write the missing note files and stop")
    ap.add_argument("--wait", type=int, default=900, help="release: seconds to wait for the published release")
    ap.add_argument("--base", default="main")
    ap.add_argument("--repo", default=".")
    ap.add_argument("--json", action="store_true")
    extra = argv[argv.index("--") + 1:] if "--" in argv else []      # gh-release: what gh release create gets
    a = ap.parse_args(argv[:argv.index("--")] if "--" in argv else argv)
    if a.action == "release-ready":
        from . import tagguard
        if not a.tag:
            ap.error("release-ready needs a tag: fieldkit ship release-ready vX.Y.Z")
        r = tagguard.check(a.repo, a.tag, dry_run=True)
        emit(r, a.json, tagguard.lines)
        return 0 if r["ok"] else 3
    if a.action == "gh-release" or (a.action == "push" and a.push_tag):
        if a.action == "gh-release" and not a.tag:
            ap.error("gh-release needs a tag: fieldkit ship gh-release vX.Y.Z -- --title ... --notes-file F FILES")
        r = gh_release(a.repo, a.tag, extra) if a.action == "gh-release" else push_tag(a.repo, a.push_tag)
        emit(r, a.json, lambda d: [f"{d.get('tag')}: {d.get('state')}"]
             + ([f"error: {d['error']}"] if d.get("error") else [])
             + ([f"mandate: {d['mandate']}"] if d.get("mandate") else []))
        return 0 if r.get("ok") else 3
    if a.action == "commit":
        from . import shipcommit
        if not a.message_file:
            ap.error("commit needs --message-file F")
        r = shipcommit.commit(a.repo, a.message_file)
        emit(r, a.json, shipcommit.lines)
        return 0 if r["ok"] else 3
    if a.action in ("audit", "release", "prune"):
        from . import shiprelease as sr
        if a.action == "audit":
            r = sr.audit(a.repo, a.base, offline=a.offline)
        elif a.action == "prune":
            r = sr.prune(a.repo, a.base, archive_unproven=a.archive_unproven)
        elif not a.tag:
            ap.error("release needs a tag: fieldkit ship release vX.Y.Z")
        else:
            r = sr.release(a.tag, a.repo, a.base, scaffold_notes=a.scaffold, wait=a.wait)
        emit(r, a.json, lambda d: [f"SHIP {a.action.upper()} {'OK' if d['ok'] else 'NOT OK'}"] + sr.lines(d))
        return 0 if r["ok"] else 3
    try:
        r = {"status": status, "sync": sync, "push": push, "merge": merge}[a.action](a.repo, a.base)
    except RuntimeError as e:
        r = {"ok": False, "error": str(e), "next": "nothing was pushed"}
    emit(r, a.json, lambda d: [f"{d.get('branch', '?')}: {d.get('state', 'error')}"
                               + (" (synced)" if d.get("synced") else "") + (" (pushed)" if d.get("pushed") else "")]
         + ([f"error: {d['error']}"] if d.get("error") else [])
         + ([f"mandate: {d['mandate']}"] if d.get("mandate") else []))
    return 0 if r.get("ok") else 3
