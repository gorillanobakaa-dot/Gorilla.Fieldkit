"""A job started over the cable brings its branch home.

A session the other computer starts here (`fieldkit task start BRIEF --on peer`) is an autopilot run: no GitHub
credential, and the hard-line hook refuses a push. Its commit stayed on a local branch the command post could not
reach (2026-10-28: 23aaa23 on fix/mutate-158-answer-kept). So the LISTENER, which started it and is not inside the
run, waits for it to end and pushes each feat/ or fix/ branch the run made or moved, with `fieldkit ship push` (the
owner-wide mandate covers those prefixes; anything else stays local and is named in the report).

Why push and not a git bundle over the cable: one machine, one step, the existing push path with its mandate log and
pre-push gate. A bundle needs code on both computers, a 50 MB file limit, and the command post would still have to
push it the same way.
"""
import json
import re
import subprocess
import tempfile
import threading
import time
from pathlib import Path

PREFIXES = ("feat/", "fix/")                      # what the owner-wide mandate covers
TASK_ID = re.compile(r"fieldkit-task (T-[\w.-]+)")


def _git(cwd, *args, run=subprocess.run):
    try:
        r = run(["git", "-C", str(cwd), *args], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return 1, ""
    return r.returncode, (r.stdout or "").strip()


def heads(cwd, run=subprocess.run):
    """-> {branch: sha} of the local branches; {} when cwd is not a git repository."""
    rc, out = _git(cwd, "for-each-ref", "--format=%(refname:short) %(objectname)", "refs/heads", run=run)
    if rc != 0:
        return {}
    return dict(line.split(" ", 1) for line in out.splitlines() if " " in line)


def moved(before, after):
    """-> (to push, left local): branches the run made or moved, split by whether the mandate covers them."""
    changed = sorted(b for b, sha in after.items() if before.get(b) != sha)
    return [b for b in changed if b.startswith(PREFIXES)], [b for b in changed if not b.startswith(PREFIXES)]


def worktree_of(cwd, branch, run=subprocess.run):
    """-> the worktree that has `branch` checked out, or None."""
    rc, out = _git(cwd, "worktree", "list", "--porcelain", run=run)
    path = None
    for line in out.splitlines() if rc == 0 else []:
        if line.startswith("worktree "):
            path = line[len("worktree "):]
        elif line == f"branch refs/heads/{branch}" and path:
            return path
    return None


def _ship_push(repo):
    from ..method import ship
    return ship.push(repo=repo)


def push_branch(cwd, branch, run=subprocess.run, push=_ship_push):
    """-> {"branch", "pushed", "detail"}. Pushes from the worktree holding it, else from a scratch worktree made and
    removed here."""
    path, made = worktree_of(cwd, branch, run=run), None
    if not path:
        made = tempfile.mkdtemp(prefix="fk-return-")
        rc, out = _git(cwd, "worktree", "add", made, branch, run=run)
        if rc != 0:
            return {"branch": branch, "pushed": False, "detail": f"no worktree for it: {out[:200]}"}
        path = made
    try:
        r = push(path)
    except Exception as e:  # noqa: BLE001  (a crash is reported, never silent)
        r = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    finally:
        if made:
            _git(cwd, "worktree", "remove", "--force", made, run=run)
    ok = bool(r.get("ok")) and (r.get("pushed") or r.get("state") == "in-step")
    return {"branch": branch, "pushed": ok, "detail": (r.get("error") or r.get("state") or "")[:300]}


def _send(text, needs_you=False):
    from . import link
    return link.call({"verb": "message", "text": text, "needs_you": needs_you})


def bring_home(proc, cwd, before, jobd, brief="", run=subprocess.run, push=_ship_push, send=_send):
    """Waits for the started session, then pushes its branches and tells the command post. -> the report dict, also
    written to JOB/branch-return.json."""
    if hasattr(proc, "wait"):
        proc.wait()
    to_push, local = moved(before, heads(cwd, run=run))
    results = [push_branch(cwd, b, run=run, push=push) for b in to_push]
    m = TASK_ID.search(brief or "")
    rep = {"task": m.group(1) if m else "", "at": time.strftime("%Y-%m-%d %H:%M:%S"), "pushed": results,
           "left_local": local}
    try:
        (Path(jobd) / "branch-return.json").write_text(json.dumps(rep, indent=1), encoding="utf-8")
    except OSError:
        pass
    if results or local:
        words = "; ".join(f"{r['branch']} {'pushed' if r['pushed'] else 'NOT pushed: ' + r['detail']}"
                          for r in results)
        if local:
            words += ("; " if words else "") + "left here (not feat/ or fix/): " + ", ".join(local)
        clean = all(r["pushed"] for r in results) and not local
        try:
            send(f"branch return{' for ' + rep['task'] if rep['task'] else ''}: {words}", needs_you=not clean)
        except Exception:  # noqa: BLE001  (the json file still holds it)
            pass
    return rep


def follow(proc, cwd, before, jobd, brief=""):
    """bring_home on a daemon thread of the listener, so the listener keeps answering."""
    t = threading.Thread(target=bring_home, args=(proc, cwd, before, jobd, brief), daemon=True,
                         name="link-branch-return")
    t.start()
    return t
