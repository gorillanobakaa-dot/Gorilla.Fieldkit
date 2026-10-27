# Version: 1.0.0 · updated 26-10-27-00-30
"""Session jobs: the boss gives ANY device of the household a brief + repository + branch; that device runs a headless
agent on its own worktree of the branch and pushes the branch itself. Nobody clicks anything on either computer.

    fieldkit orchestrate job session --device W --repo REPO --branch feat/x --brief FILE [--base main] [--wall M]
                                     [--wait S]               the boss: one signed job, one line back
    fieldkit orchestrate job refusals                         jobs this device refused (each is also an inbox row)
    fieldkit orchestrate session-work JOB-ID                  (the worker, started by its listener: not for people)

Owner, 2026-10-26 23:2x: "connected remote linux (or windows) can not start a job because it wants me to click on
some bullshit approval ... the mile long warehouse setup with devices in every remote corner? you can't be expected to
run 3 miles up and down to click accept on a stupid prompt". Measured that night: a Claude Desktop session on the VAIO
was told to start 4 jobs and could not (the desktop app refuses to start a session on a turn another session's message
started). That rule is the app's; this path routes device work AROUND app sessions: a tool starts the agent.

What authorizes a job (all checked on the WORKER, every one fail closed; any doubt = refused + an inbox row there):
  1. the cable: the household HMAC key (paired once per device), 120 s stamp, nonce never seen (link.Verifier);
  2. the boss: the job is also signed by the boss's own Ed25519 machine key, pinned on the worker ONCE at enrolment
     (fieldkit mandate trust-reviewer). The household key is shared by every device, so it cannot tell devices
     apart; this signature can. A device that has not pinned the boss refuses: enrolment is the only per-device step;
  3. the owner: the owner-wide mandate the owner sealed ONCE at mandate start (Android-style pre-approval). The job
     names its sha256; the worker must hold that same file, sealed by a trusted owner key, not expired, not stopped;
  4. the scope: the repository is in that mandate, the branch matches its push patterns (never a base branch), the
     agent is one this path can start (claude), the brief is at most 32 KB, the boss is the mandate's orchestrator
     when the mandate names one.
What the worker does, as a tool (never a session asking a person): fetch, a worktree of the branch under the link's
work folder (the device's own checkout is never touched), `fieldkit autopilot claude` in it (hard-line hook, no
GitHub credential in the run, wall cap), then `fieldkit ship push` of the branch from the TOOL (the agent cannot
push: no credential, hard line). The result - branch, head sha, pushed or not, evidence, concerns - is signed by
the worker's Ed25519 key and delivered to the boss (fieldkit/mandate/jobs.py: ledger, outbox when the boss is down).

Doors (owner threat model): no new port, no new key type, no new trust store. The new power is "a paired, enrolled
boss under a sealed mandate can make this device run an agent on a mandated branch and push it" - exactly what the
mandate already lets a session on this device do; the boss's signature names who asked; every refusal is logged and
in the inbox. Not covered: what the agent does inside its worktree beyond the hard lines (the same as any autopilot
run), and a device whose own Fieldkit is older than this file (it answers "unknown kind": update it first).
"""
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

from .mandate import jobs
from .peers import link

KIND = "session"
STARTABLE = ("claude",)                  # only an agent `fieldkit autopilot` runs headless with the hard-line hook
MAX_BRIEF = 32 * 1024
SIGNED = ("id", "kind", "repo", "branch", "base", "agent", "brief", "wall_minutes", "mandate", "from_host")
REFUSAL_REPEAT_S = 3600                  # the same refusal from the same device makes one inbox row an hour, not a flood


def brief_sha(brief):
    return hashlib.sha256(brief.encode("utf-8")).hexdigest()


def job_id(repo, branch, brief):
    return f"{KIND}-{repo}-{re.sub(r'[^A-Za-z0-9._-]', '_', branch)}-{brief_sha(brief)[:10]}"[:120]


# ---- the boss's signature ---------------------------------------------------------------------------------------------
def _canon(req):
    return json.dumps({k: req.get(k) for k in SIGNED}, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")


def boss_sign(req, key_fn=None):
    """Sign the job with this computer's Ed25519 machine key (mandate.review_key): who asked, provably."""
    from .mandate import mandate as M
    k, fp = (key_fn or M.review_key)()
    return dict(req, boss_key=fp, boss_sig=k.sign(_canon(req)).hex())


def boss_check(req, pinned=None):
    """-> None (the job is signed by a boss key pinned here) or the refusal."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from .mandate import mandate as M
    keys = M.reviewer_keys() if pinned is None else pinned
    fp = str(req.get("boss_key") or "")
    if not fp or not req.get("boss_sig"):
        return "the job is not signed by a boss key"
    if fp not in keys:
        return (f"the boss key {fp} is not pinned on this device (enrolment, once per device: "
                f"fieldkit mandate trust-reviewer with the boss's machine_review.pub)")
    try:
        Ed25519PublicKey.from_public_bytes(keys[fp]).verify(bytes.fromhex(str(req["boss_sig"])), _canon(req))
    except (InvalidSignature, ValueError):
        return "the boss signature does not match the job (altered on the way, or another key)"
    return None


# ---- scope ------------------------------------------------------------------------------------------------------------
def check_scope(req, mpath):
    """-> None or why this job is outside what the sealed mandate lets this device do."""
    from .mandate import mandate as M
    agent, branch, base, brief = (req.get(k) for k in ("agent", "branch", "base", "brief"))
    if agent not in STARTABLE:
        return f"agent {agent!r} cannot be started headless here (only {', '.join(STARTABLE)})"
    if not isinstance(brief, str) or not brief.strip() or len(brief) > MAX_BRIEF:
        return f"a brief of 1 to {MAX_BRIEF} characters"
    try:
        wall = int(req.get("wall_minutes"))
    except (TypeError, ValueError):
        wall = 0
    if not 1 <= wall <= 1440:
        return "wall_minutes: 1 to 1440"
    if not M._plain_ref(str(base or "")):
        return f"base {base!r} is not a plain branch name"
    cfg = M.repo_cfg(M.load(mpath), str(req.get("repo")))
    if cfg is None:
        return f"the mandate does not name {req.get('repo')}"
    ok, why = M.branch_ok(cfg, str(branch or ""))
    return None if ok else f"out of scope: {why}"


# ---- refusals go to this device's owner inbox ---------------------------------------------------------------------------
def refusals_path():
    return link.home() / "job-refusals.jsonl"


def refused(req, why, inbox_add=None, now=time.time):
    """Record a refused job (always) and put it in this device's owner inbox (once an hour per device + reason)."""
    host = str((req or {}).get("from_host") if isinstance(req, dict) else "?")
    row = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "t": now(), "from": host, "why": why,
           "id": (req or {}).get("id") if isinstance(req, dict) else None,
           "kind": (req or {}).get("kind") if isinstance(req, dict) else None}
    p = refusals_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    recent = [r for r in _rows(p) if r.get("from") == host and r.get("why") == why
              and now() - float(r.get("t") or 0) < REFUSAL_REPEAT_S and r.get("inboxed")]
    row["inboxed"] = not recent
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    if row["inboxed"]:
        if inbox_add is None:
            from .autopilot.inbox import add as inbox_add
        inbox_add(f"a job from {host} was REFUSED on {socket.gethostname()}", f"{why} (job {row['id']}). Nothing ran. "
                  "If it should have run: fix what the line says (enrol the boss, share the mandate in force, or "
                  "send a branch the mandate covers); otherwise nothing to do.", kind="mesh-job")
    return row


def _rows(p):
    out = []
    for line in p.read_text(encoding="utf-8").splitlines() if p.is_file() else []:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


# ---- the worker: accept ------------------------------------------------------------------------------------------------
def work_root():
    return link.home() / "work"


def accept(req, mpath, lock, cap=3600, popen=subprocess.Popen, pinned=None, find_repo=None, inbox_add=None):
    """The worker's side, inside jobs.handle_job after the cable, mandate and boss-name checks."""
    why = boss_check(req, pinned) or check_scope(req, mpath)
    if why:
        refused(req, why, inbox_add=inbox_add)
        return {"ok": False, "error": why}
    from .mandate import verdict as V
    path = (find_repo or V.find_repo)(str(req["repo"]))
    if path is None:
        return {"ok": False, "error": f"{req['repo']} is not checked out here (fix: add it to sync.repos on this device)"}
    if jobs.busy(lock, cap):
        return {"ok": False, "error": "a job is running here already: one at a time"}
    jid = req["id"]
    d = work_root() / jid
    d.mkdir(parents=True, exist_ok=True)
    job = {k: req.get(k) for k in SIGNED + ("boss_key",)}
    job.update(repo_path=str(path), mandate_path=str(mpath), lock=str(lock), accepted=time.strftime("%Y-%m-%d %H:%M:%S"))
    (d / "job.json").write_text(json.dumps(job, indent=1, ensure_ascii=False), encoding="utf-8")
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(f"{jid} {time.strftime('%Y-%m-%d %H:%M:%S')}\n", encoding="utf-8")
    popen(launch_argv(jid), env=link.link_env(),
          stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
          **({"creationflags": 0x00000008 | 0x00000200} if sys.platform == "win32" else {"start_new_session": True}))
    return {"ok": True, "started": jid}


def launch_argv(jid, platform=None, which=None):
    """The worker process. On Linux it runs as its own transient systemd user unit when systemd-run is there: the
    listener is a service with KillMode=control-group, so a child left in its cgroup dies whenever the watchdog or the
    rescue door restarts the listener (an agent job runs for hours; systemd.kill(5))."""
    import shutil
    me = [sys.executable, "-P", "-m", "fieldkit", "orchestrate", "session-work", jid]
    sr = (which or shutil.which)("systemd-run") if (platform or sys.platform).startswith("linux") else None
    if not sr:
        return me
    unit = "fieldkit-job-" + re.sub(r"[^A-Za-z0-9_.-]", "_", jid)[:80]
    return [sr, "--user", "--collect", "--quiet", f"--unit={unit}", f"--setenv=PYTHONPATH={link.LINK_ROOT}",
            f"--setenv=PATH={os.environ.get('PATH', '')}", f"--working-directory={work_root()}", *me]


# ---- the worker: the job itself ----------------------------------------------------------------------------------------
def git(repo, *args, run=subprocess.run, timeout=300):
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    r = run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=timeout, env=env)
    return r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()


def free_branch(repo, branch, run=subprocess.run):
    """-> None when `branch` is checked out nowhere (an earlier job's CLEAN worktree is removed), else why not."""
    _, out = git(repo, "worktree", "list", "--porcelain", run=run)
    for block in out.split("\n\n"):
        lines = dict(x.split(" ", 1) for x in block.splitlines() if " " in x)
        if lines.get("branch") != f"refs/heads/{branch}":
            continue
        wt = Path(lines.get("worktree", ""))
        try:
            ours = wt.resolve().is_relative_to(work_root().resolve())
        except OSError:
            ours = False
        if not ours:
            return f"{branch} is checked out in {wt} on this device: not taken over"
        _, dirty = git(wt, "status", "--porcelain", run=run)
        if dirty:
            return f"an earlier job left uncommitted work on {branch} in {wt}: not overwritten"
        git(repo, "worktree", "remove", str(wt), run=run)
    return None


def prepare(repo, tree, branch, base, run=subprocess.run):
    """A worktree of `branch` at `tree`: the remote branch when it exists, else a new branch from origin/base.
    -> (start sha, None) or (None, why)."""
    rc, out = git(repo, "fetch", "--quiet", "origin", run=run)
    if rc:
        return None, f"git fetch origin failed: {out[-300:]}"
    rc, _ = git(repo, "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{branch}", run=run)
    start = f"origin/{branch}" if rc == 0 else f"origin/{base}"
    rc, sha = git(repo, "rev-parse", "--verify", start, run=run)
    if rc:
        return None, f"{start} does not exist on origin"
    rc, local = git(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}", run=run)
    if rc == 0 and local != sha:
        if git(repo, "merge-base", "--is-ancestor", sha, local, run=run)[0] == 0:
            sha = local                        # an earlier job's commits a push did not land: continue on top of them
        elif git(repo, "merge-base", "--is-ancestor", local, sha, run=run)[0]:
            return None, f"this device's local {branch} ({local[:10]}) and {start} have diverged: not overwritten"
    why = free_branch(repo, branch, run=run)
    if why:
        return None, why
    rc, out = git(repo, "worktree", "add", "-B", branch, str(tree), sha, run=run)
    if rc:
        return None, f"git worktree add failed: {out[-300:]}"
    return sha, None


def prompt(job):
    return (f"Job {job['id']} from the household's boss {job.get('from_host')}, under the owner-wide mandate "
            f"{str(job.get('mandate'))[:12]} (the owner's one approval). You are in a worktree of {job['repo']} on "
            f"branch {job['branch']} (base {job['base']}). Do the job below here, commit your work on this branch "
            "with clear messages, and stop. Do not push: the tool that started you pushes the branch after you finish. "
            "End with one line: DONE <what you did> or BLOCKED <why>.\n\n--- the job ---\n" + job["brief"] + "\n--- end ---\n")


def agent_argv(job, tree, jobd, flags_fn=None):
    argv = [sys.executable, "-P", "-m", "fieldkit", "autopilot", "claude", prompt(job), "--cwd", str(tree),
            "--job", str(jobd), "--wall-minutes", str(job["wall_minutes"])]
    return link.fit_command(argv, (flags_fn or link.autopilot_flags)())


def work(jid, run=subprocess.run, ship_push=None, deliver=None, sign=None, flags_fn=None):
    """The job (a detached process on the worker). -> the signed result, delivered to the boss."""
    deliver = deliver or jobs.deliver
    sign = sign or jobs.sign_result
    d = work_root() / jid
    job = json.loads((d / "job.json").read_text(encoding="utf-8"))
    tree, log = d / "tree", d / "agent.log"
    concerns, t0, head, pushed, rc, start = [], time.time(), "0" * 40, False, None, None
    try:
        start, why = prepare(job["repo_path"], tree, job["branch"], job["base"], run=run)
        if why:
            raise RuntimeError(why)
        from .mandate import health
        wall = int(job["wall_minutes"]) * 60
        with health.awake(f"fieldkit session job {jid}"), open(log, "w", encoding="utf-8") as out:
            try:
                rc = run(agent_argv(job, tree, d / "job", flags_fn), cwd=str(tree), env=link.link_env(),
                         stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT, timeout=wall + 600).returncode
            except subprocess.TimeoutExpired:
                concerns.append({"text": f"the agent passed its wall cap ({wall // 60} min) and was stopped",
                                 "fix_offered": "send it again with a larger --wall; its commits so far are pushed"})
        _, head = git(tree, "rev-parse", "HEAD", run=run)
        _, n = git(tree, "rev-list", "--count", f"{start}..HEAD", run=run)
        new = int(n) if n.isdigit() else 0
        _, dirty = git(tree, "status", "--porcelain", run=run)
        if dirty:
            concerns.append({"text": f"the agent left uncommitted changes ({len(dirty.splitlines())} path(s)): not pushed",
                             "fix_offered": f"they stay in {tree}"})
        if new:
            if ship_push is None:
                from .method import ship
                ship_push = ship.push
            res = ship_push(repo=str(tree), base=job["base"])
            _, remote = git(tree, "ls-remote", "origin", f"refs/heads/{job['branch']}", run=run)
            pushed = bool(res.get("ok")) and remote.split()[:1] == [head]
            if not pushed:
                concerns.append({"text": f"fieldkit ship push did not land {head[:10]}: "
                                         f"{res.get('error') or res.get('next') or res.get('state')}",
                                 "fix_offered": f"the commits stay on {job['branch']} in {tree}"})
        else:
            concerns.append({"text": "the agent made no commit", "fix_offered": "read agent.log; send a clearer brief"})
        status = "pass" if rc == 0 and new and pushed else "fail"
        evidence = (f"agent exit {rc} in {time.time() - t0:.0f} s; {new} new commit(s) on {job['branch']}, head "
                    f"{head[:12]}, {'pushed (origin has it)' if pushed else 'NOT pushed'}\n{_tail(log)}")
        if not dirty:                          # this job's own scratch; its commits live on in the branch
            git(job["repo_path"], "worktree", "remove", str(tree), run=run)
    except Exception as e:  # noqa: BLE001 - every failure becomes a result the boss sees, never silence
        status, evidence = "not-run", f"the job could not run here: {type(e).__name__}: {e}"
    finally:
        try:
            Path(job.get("lock") or "").unlink(missing_ok=True)
        except OSError:
            pass
    head = head if re.fullmatch(r"[0-9a-f]{40}", head or "") else "0" * 40
    r = sign({"id": jid, "kind": KIND, "repo": job["repo"], "commit": head, "status": status,
              "evidence": evidence[:1900], "concerns": concerns, "os": link.os_tag(), "host": socket.gethostname(),
              "at": time.strftime("%Y-%m-%d %H:%M:%S"), "branch": job["branch"], "pushed": pushed})
    (d / "result.json").write_text(json.dumps(r, indent=1, ensure_ascii=False), encoding="utf-8")
    deliver(r)
    return r


def _tail(path, n=12, chars=1200):
    try:
        return "\n".join(Path(path).read_text(encoding="utf-8", errors="replace").splitlines()[-n:])[-chars:]
    except OSError:
        return ""


# ---- the boss -----------------------------------------------------------------------------------------------------------
def send(device, repo, branch, brief, base="main", wall=120, agent="claude", call=None, find_mandate=None,
         key_fn=None, inbox_add=None):
    """-> (job id, what happened). The boss checks the same scope first, so a job it would refuse never leaves."""
    from .mandate import mandate as M
    mpath = (find_mandate or jobs.mandate_for)(repo)
    if mpath is None:
        return None, f"no mandate in force here names {repo}: fieldkit mandate status"
    jid = job_id(repo, branch, brief)
    req = {"verb": "job", "id": jid, "kind": KIND, "repo": repo, "branch": branch, "base": base, "agent": agent,
           "brief": brief, "wall_minutes": int(wall), "mandate": M._sha(mpath), "from_host": socket.gethostname()}
    why = check_scope(req, mpath)
    if why:
        return jid, f"not sent: {why}"
    led = jobs._ledger()
    if led.get(jid, {}).get("status") in ("sent", "pass"):
        return jid, f"already {led[jid]['status']} at {led[jid].get('sent')}"
    # from_host is in the boss signature, and link.sign sets it again to this host: the same value
    res = (call or link.call)(boss_sign(req, key_fn), device=device)
    if not res.get("ok"):
        jobs.event("job-refused", id=jid, worker=device, why=res.get("error"))
        if inbox_add is None:
            from .autopilot.inbox import add as inbox_add
        inbox_add(f"{device} did not take job {jid}", str(res.get("error")), kind="mesh-job")
        return jid, f"not taken: {res.get('error')}"
    led[jid] = {"kind": KIND, "repo": repo, "commit": "0" * 40, "branch": branch, "mandate": req["mandate"],
                "status": "sent", "sent": time.strftime("%Y-%m-%d %H:%M:%S"), "worker_name": device,
                "worker_said": "started"}
    jobs._save(led)
    jobs.event("job-sent", id=jid, kind=KIND, repo=repo, branch=branch, worker=device)
    return jid, "sent"


def verified_result(jid):
    """The stored result of that job, signed by a pinned worker key; None otherwise."""
    p = jobs.results_dir() / f"{jid}.json"
    try:
        r = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return next((x for x in jobs.verified_results(r.get("repo"), r.get("commit")) if x.get("id") == jid), None)


def main(argv):
    """fieldkit orchestrate job session|refusals ... and fieldkit orchestrate session-work ID."""
    import argparse
    if argv[:1] == ["session-work"]:
        if len(argv) != 2:
            print("session-work JOB-ID")
            return 2
        r = work(argv[1])
        print(f"RESULT {r['id']} {r['status']}")
        return 0 if r["status"] == "pass" else 3
    rest = argv[1:] if argv[:1] == ["job"] else argv
    ap = argparse.ArgumentParser(prog="fieldkit orchestrate job", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("kind", choices=["session", "refusals"])
    ap.add_argument("--device", help="the worker (fieldkit peers link devices)")
    ap.add_argument("--repo", help="the repository as the owner's mandate names it (e.g. Gorilla.Fieldkit)")
    ap.add_argument("--branch", help="the branch the job works on and pushes (a mandate push pattern)")
    ap.add_argument("--base", default="main")
    ap.add_argument("--brief", help="a file with the job")
    ap.add_argument("--wall", type=int, default=120, help="minutes the agent may run")
    ap.add_argument("--wait", type=float, default=0, help="seconds to wait for the result here (one blocking wait)")
    a = ap.parse_args(rest)
    if a.kind == "refusals":
        rows = _rows(refusals_path())
        for r in rows[-40:]:
            print(f"  {r['at']}  from {r['from']:<20} {'INBOX ' if r.get('inboxed') else 'repeat'} {r['why']}")
        print(f"VERDICT  {len(rows)} refused job(s) recorded on this device")
        return 0
    if not (a.device and a.repo and a.branch and a.brief):
        print("job session needs --device, --repo, --branch and --brief")
        return 2
    jid, said = send(a.device, a.repo, a.branch, Path(a.brief).read_text(encoding="utf-8"), a.base, a.wall)
    print(f"JOB   {jid or '-'}: {said}")
    if said != "sent":
        return 3
    if not a.wait:
        print("NEXT: fieldkit orchestrate job status, or wait: --wait SECONDS")
        return 0
    e = jobs.wait(jid, a.wait)
    if not e or e.get("status") in (None, "sent"):
        print(f"VERDICT  no result within {a.wait:g} s (the job may still run: fieldkit orchestrate job status)")
        return 3
    r = verified_result(jid)
    if r is None:
        print(f"VERDICT  {e.get('status')} reported, but no result signed by a pinned worker key: not trusted")
        return 3
    print(f"RESULT {r['status']} on {r['host']} ({r['os']}): {r['evidence'].splitlines()[0]}")
    for c in r.get("concerns") or []:
        print(f"  CONCERN: {c.get('text')}  FIX OFFERED: {c.get('fix_offered')}")
    good = r["status"] == "pass" and r.get("pushed")
    print(f"VERDICT  {'PASS' if good else 'FAIL'}: {r.get('branch')} at {r['commit'][:12]}, "
          f"{'pushed' if r.get('pushed') else 'not pushed'}")
    return 0 if good else 3
