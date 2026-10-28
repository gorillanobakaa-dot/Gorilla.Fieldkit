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
            return str(Path(path))                # git prints C:/x on Windows: a native path
    return None


def _ship_push(repo):
    from ..method import ship
    return ship.push(repo=repo)


def push_branch(cwd, branch, run=subprocess.run, push=_ship_push):
    """-> {"branch", "pushed", "detail"}. Pushes from the worktree holding it, else from a scratch worktree made and
    removed here. A branch origin already has at this commit is done: no push (Zero, 2026-10-28)."""
    if on_origin(cwd, branch, run=run):
        return {"branch": branch, "pushed": True, "detail": ALREADY}
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
    if not ok and on_origin(cwd, branch, run=run):    # another return pushed the same commit first (2026-10-28 18:47)
        return {"branch": branch, "pushed": True, "detail": ALREADY}
    return {"branch": branch, "pushed": ok, "detail": (r.get("error") or r.get("state") or "")[:300]}


ALREADY = "already on origin at this commit"


def on_origin(cwd, branch, run=subprocess.run):
    """True when origin's branch is at the local branch's commit (asked of origin, not of a stale remote ref)."""
    rc, local = _git(cwd, "rev-parse", "--verify", "-q", f"refs/heads/{branch}", run=run)
    rc2, remote = _git(cwd, "ls-remote", "origin", f"refs/heads/{branch}", run=run)
    return rc == 0 and rc2 == 0 and bool(local) and remote.split()[:1] == [local]


def _send(text, needs_you=False):
    from . import link
    return link.call({"verb": "message", "text": text, "needs_you": needs_you})


def bring_home(proc, cwd, before, jobd, brief="", run=subprocess.run, push=_ship_push, send=_send, branches=None):
    """Waits for the started session, then pushes its branches and tells the command post. -> the report dict, also
    written to JOB/branch-return.json. `branches`: the branches to push when there is no `before` snapshot."""
    if hasattr(proc, "wait"):
        proc.wait()
    to_push, local = (list(branches), []) if branches is not None else moved(before, heads(cwd, run=run))
    results = [push_branch(cwd, b, run=run, push=push) for b in to_push]
    m = TASK_ID.search(brief or "")
    rep = {"task": m.group(1) if m else "", "at": time.strftime("%Y-%m-%d %H:%M:%S"), "pushed": results,
           "left_local": local}
    try:
        (Path(jobd) / "branch-return.json").write_text(json.dumps(rep, indent=1), encoding="utf-8")
    except OSError:
        pass
    news = [r for r in results if r["detail"] != ALREADY]   # a branch that was already there is no news
    if news or local:
        words = "; ".join(f"{r['branch']} {'pushed' if r['pushed'] else 'NOT pushed: ' + r['detail']}"
                          for r in news)
        if local:
            words += ("; " if words else "") + "left here (not feat/ or fix/): " + ", ".join(local)
        clean = all(r["pushed"] for r in results) and not local
        try:
            send(f"branch return{' for ' + rep['task'] if rep['task'] else ''}: {words}", needs_you=not clean)
        except Exception:  # noqa: BLE001  (the json file still holds it)
            pass
    return rep


def follow(proc, cwd, before, jobd, brief=""):
    """bring_home on a daemon thread of the listener, so the listener keeps answering. The job folder keeps what is
    needed to finish it (follow.json), so a listener that restarts while the run goes on still brings it home."""
    record(jobd, getattr(proc, "pid", None), cwd, before, brief)
    t = threading.Thread(target=bring_home, args=(proc, cwd, before, jobd, brief), daemon=True,
                         name="link-branch-return")
    t.start()
    return t


# ---- a listener that restarts keeps its promise ---------------------------------------------------------------------
# 2026-10-28: the I9 job ended with feat/i9-owner-actions local only. The listener running then had started at 15:17,
# before this module existed; the checkout moved to new code under it, but a running process keeps the code it
# loaded. A restart alone would not help either: the follow lived only in the old process's memory.
RESUME_HOURS = 24                                    # older job folders are history, never pushed from here
POLL_S = 30


def record(jobd, pid, cwd, before, brief=""):
    try:
        (Path(jobd) / "follow.json").write_text(json.dumps({"pid": pid, "cwd": str(cwd), "before": before,
                                                            "brief": (brief or "")[:4000], "at": time.time()}),
                                                encoding="utf-8")
    except OSError:
        pass


def _checkpoint(jobd):
    try:
        return json.loads((Path(jobd) / "CHECKPOINT.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _pid_alive(pid):
    from .peers import pid_alive
    return pid_alive(int(pid))


def ended(jobd, pid=None, alive=_pid_alive):
    """The run is over: its checkpoint says so, or its process is gone."""
    if _checkpoint(jobd).get("ended_at"):
        return True
    try:
        return bool(pid) and not alive(pid)
    except (OSError, ValueError):
        return False


def started_at(jobd):
    try:
        return time.mktime(time.strptime(Path(jobd).name[:15], "%Y%m%d-%H%M%S"))
    except ValueError:
        return None


def window_branches(cwd, jobd, run=subprocess.run, now=None):
    """A job started before follow.json existed has no `before`: its branches are the feat/ and fix/ ones whose newest
    commit falls inside the job's time and that origin does not have at that commit."""
    start, end = started_at(jobd), _checkpoint(jobd).get("ended_at") or (now or time.time())
    if start is None:
        return []
    out = []
    for b, sha in heads(cwd, run=run).items():
        if not b.startswith(PREFIXES):
            continue
        rc, t = _git(cwd, "log", "-1", "--format=%ct", sha, run=run)
        rc2, remote = _git(cwd, "rev-parse", "--verify", "-q", f"refs/remotes/origin/{b}", run=run)
        if rc == 0 and t.isdigit() and start - 60 <= int(t) <= float(end) + 120 and remote != sha:
            out.append(b)
    return out


def named(text, branches):
    """The branches a job's own words name (its result: "committed on `feat/i2-i4-cost`")."""
    return [b for b in branches if re.search(r"(?<![\w/.-])" + re.escape(b) + r"(?![\w/.-])", text or "")]


def finish(jobd, alive=_pid_alive, sleep=time.sleep, bring=None, run=subprocess.run):
    """Wait (in this thread) for the job to end, then bring its branches home. -> the report, or None."""
    bring = bring or bring_home
    f = Path(jobd) / "follow.json"
    rec = json.loads(f.read_text(encoding="utf-8")) if f.is_file() else None
    cp = _checkpoint(jobd)
    cwd = (rec or {}).get("cwd") or cp.get("cwd")
    if not cwd:
        return None
    while not ended(jobd, (rec or {}).get("pid"), alive):
        sleep(POLL_S)
    if rec:
        return bring(None, cwd, rec.get("before") or {}, jobd, rec.get("brief") or "", run=run)
    # 2026-10-28 18:47: five jobs that ran at once had overlapping windows, so each claimed every branch and four
    # pushes raced; a branch the job's own result names is that job's, and only those then
    window = window_branches(cwd, jobd, run=run)
    said = " ".join(str(cp.get(k) or "") for k in ("result", "last_end"))
    return bring(None, cwd, {}, jobd, cp.get("result") or "", run=run, branches=named(said, window) or window)


def pending(jobs_dir, now=None):
    """The job folders a starting listener still owes a branch return: recent, not returned yet, and either followed
    (follow.json) or a run with a checkpoint (started by an older listener)."""
    now = now or time.time()
    out = []
    for d in sorted(Path(jobs_dir).glob("*")) if Path(jobs_dir).is_dir() else []:
        s = started_at(d)
        if (d / "branch-return.json").exists() or s is None or now - s > RESUME_HOURS * 3600:
            continue
        if (d / "follow.json").is_file() or (d / "CHECKPOINT.json").is_file():
            out.append(d)
    return out


def _pid_of(jobd):
    try:
        return json.loads((Path(jobd) / "follow.json").read_text(encoding="utf-8")).get("pid")
    except (OSError, ValueError):
        return None


def finish_all(jobs, alive=_pid_alive, sleep=time.sleep, fin=None):
    """One at a time, so two returns never push the same branch at once; on each pass the jobs that have ended, so
    a run still going never keeps a finished one from home. -> the job folders in the order they were finished."""
    fin, left, done = fin or finish, list(jobs), []
    while left:
        for d in [d for d in left if ended(d, _pid_of(d), alive)]:
            try:
                fin(d)
            except Exception:  # noqa: BLE001 - one job's failure does not keep the next one from home
                pass
            left.remove(d)
            done.append(d)
        if left:
            sleep(POLL_S)
    return done


def resume(jobs_dir, now=None, start=True):
    """At listener start: one daemon thread that finishes the owed jobs in order. -> the job folders."""
    jobs = pending(jobs_dir, now)
    if start and jobs:
        threading.Thread(target=finish_all, args=(jobs,), daemon=True, name="link-branch-return-resume").start()
    return jobs
