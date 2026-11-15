"""One ask at the start (plan P9; owner, 2026-11-11, verbatim: "This was supposed to be asked for at the beginning of
the session, and in one command approves every thing. Don't do bullshit like this. Halfway through, you wake up, oh,
actually, I might need this one, but only you can run it and hold up the project forever.").

Every owner-only action a long job may need is listed in `onestart.yaml` (the registry: what it is, which job kinds
need it, a read-only pre-check, the action that applies it, the fallback a step takes without it).

    start_check(kind)   run by every entry point of a long job (task start, emerge run, goal run): pre-checks each
                        action for this job kind. Nothing missing: silent, no model, no cost. Something missing and
                        the owner is here (a real terminal, or `fieldkit onestart ask` from any shell): ONE screen,
                        "this job will need A, B, C - approve all?", answered once with the owner's proof (the signing-
                        key passphrase, at the terminal or in the desktop dialog); one yes applies every action that
                        has an apply command, each journaled. The owner not here, or a no: the job starts anyway.
    gap(id, ...)        a step that finds an action missing mid-job takes its fallback and calls this: the NEXT start
                        check asks for it up front. A job never waits and never asks mid-job.
    guard_inbox(...)    `fieldkit inbox add` inside a job with an owner command that is a registry action is refused
                        (the reason is the rule above); the gap is recorded instead.

    fieldkit onestart [check|ask] [--kind task|emerge|goal]

Journal: state/lifecycle/onestart.jsonl (asks, answers, applies); gaps: state/lifecycle/onestart-gaps.jsonl.
"""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import yaml

REGISTRY = Path(__file__).with_name("onestart.yaml")
KINDS = ("task", "emerge", "goal")
REFUSED = ("owner commands are asked once, at the START of a job (fieldkit onestart), never mid-job: this step takes "
           "its fallback and the gap is recorded so the next job asks it up front")


def load(path=None):
    d = yaml.safe_load(Path(path or REGISTRY).read_text(encoding="utf-8")) or {}
    for a in d.get("actions") or []:
        if not a.get("id") or not a.get("what") or not a.get("kinds") or not a.get("fallback"):
            raise ValueError(f"onestart registry: entry {a.get('id')!r} lacks id, what, kinds or fallback")
    return d


def actions(kind=None, data=None):
    out = (data or load())["actions"]
    return [a for a in out if kind is None or "any" in a["kinds"] or kind in a["kinds"]]


def _dir():
    from .friction import state_dir
    return state_dir()


def _append(name, row):
    row = dict(row, at=time.strftime("%Y-%m-%d %H:%M:%S"))
    with open(_dir() / name, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def _rows(name):
    p = _dir() / name
    out = []
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    except OSError:
        pass
    return out


# ---- the read-only pre-checks, by name -----------------------------------------------------------------------------
def _in_force():
    from ..mandate import mandate as M
    out = []
    for p in M.in_force():
        try:
            out.append(M.load(p))
        except (OSError, ValueError):
            continue
    return out


def claude_cli_login():
    """The `claude` CLI authenticates (desktop and CLI logins are separate). None when the CLI is not here."""
    exe = shutil.which("claude")
    if not exe:
        return None
    try:
        r = subprocess.run([exe, "auth", "status"], capture_output=True, text=True, timeout=30)
        return bool(json.loads(r.stdout).get("loggedIn")) if r.stdout.strip() else r.returncode == 0
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def mandate_in_force():
    return bool(_in_force())


def machine_prereqs():
    from ..mandate import machine
    ms = [m for m in _in_force() if machine.entries(m)]
    return all(r["present"] is True for m in ms for r in machine.check(m))


def opus_fable_named():
    names = " ".join(str(n).lower() for m in _in_force() for n in (m.get("models_allowed") or []))
    return "opus" in names and "fable" in names


def owner_steps_door():
    from ..mandate import mandate as M
    for m in _in_force():
        d = M.owner_steps(m)
        if d.get("unblock") and d.get("accept"):
            return True
    return False


CHECKS = {"claude_cli_login": claude_cli_login, "mandate_in_force": mandate_in_force,
          "machine_prereqs": machine_prereqs, "opus_fable_named": opus_fable_named,
          "owner_steps_door": owner_steps_door, "unknown": lambda: None}


# ---- gaps ----------------------------------------------------------------------------------------------------------
def gap(action_id, job="", why=""):
    """A step found `action_id` missing mid-job and took its fallback: the next start check asks for it."""
    return _append("onestart-gaps.jsonl", {"id": action_id, "job": job or job_id(), "why": why, "open": True})


def open_gaps():
    last = {}
    for r in _rows("onestart-gaps.jsonl"):
        last[r.get("id")] = r
    return {k for k, r in last.items() if r.get("open")}


def _resolve(action_id):
    _append("onestart-gaps.jsonl", {"id": action_id, "open": False})


def job_id():
    return os.environ.get("FIELDKIT_TASK_ID") or os.environ.get("FIELDKIT_JOB_DIR") or ""


def in_job():
    return bool(job_id())


def status(kind=None, checks=None, data=None):
    """-> [(action, True|False|None, missing)]: `missing` = False, or unknown with a gap on record."""
    checks = checks or CHECKS
    gaps = open_gaps()
    rows = []
    for a in actions(kind, data):
        try:
            ok = checks.get(a.get("check"), lambda: None)()
        except Exception:                                    # noqa: BLE001 - a broken check is unknown, never present
            ok = None
        if ok is True and a["id"] in gaps:
            _resolve(a["id"])
        rows.append((a, ok, ok is False or (ok is None and a["id"] in gaps)))
    return rows


# ---- the one screen ------------------------------------------------------------------------------------------------
def screen(missing, kind, job=""):
    lines = [f"START CHECK: this {kind} job{(' (' + job + ')') if job else ''} may need {len(missing)} owner-only "
             f"action(s). Approve all, once, now; it is never asked again mid-job."]
    for i, a in enumerate(missing, 1):
        how = (f"applied now: {' '.join(map(str, a['apply']))}" if a.get("apply")
               else f"only you can do it; type once: {a.get('manual', '?')}")
        lines += [f"  {i}. {a['id']}: {a['what']}", f"       {how}", f"       without it: {a['fallback']}"]
    return lines


def _asked_recently(ids, ttl_h, now=None):
    now = now or time.time()
    for r in reversed(_rows("onestart.jsonl")):
        if r.get("event") == "asked" and set(r.get("ids") or []) == set(ids) and r.get("outcome") != "approved":
            try:
                t = time.mktime(time.strptime(r["at"], "%Y-%m-%d %H:%M:%S"))
            except (KeyError, ValueError):
                continue
            return now - t < ttl_h * 3600
    return False


def _run_apply(a, run=subprocess.run):
    from ..core import settings
    argv = [str(x).replace("{state}", str(settings.state_root() / "state")) for x in a["apply"]]
    if argv[0] in ("python3", "python") and argv[1:3] == ["-m", "fieldkit"]:
        argv[0] = sys.executable
    script = Path(argv[-1]) if argv[0] in ("bash", "sh") else None
    if script is not None and not script.is_file():
        return False, f"{script} is not on this machine"
    r = run(argv, capture_output=True, text=True, timeout=900, cwd=str(settings.ROOT))
    return r.returncode == 0, f"exit {r.returncode}: " + ((r.stdout or r.stderr or "").strip().splitlines() or [""])[-1][:160]


def start_check(kind, job="", force_ask=False, ask_present=None, prove=None, say=print, checks=None, data=None,
                run=subprocess.run, now=None):
    """-> {"missing": [ids], "asked": bool, "outcome": ..., "applied": [ids], "lines": [...]}. NEVER raises, NEVER waits
    for anyone: the job starts whatever the answer. FIELDKIT_ONESTART=off skips (tests, nested runs)."""
    out = {"missing": [], "asked": False, "outcome": "none", "applied": [], "lines": []}
    if os.environ.get("FIELDKIT_ONESTART", "").lower() == "off":
        out["outcome"] = "off"
        return out
    try:
        data = data or load()
        miss = [a for a, _ok, m in status(kind, checks, data) if m]
        out["missing"] = [a["id"] for a in miss]
        if not miss:
            return out
        ids = out["missing"]
        if ask_present is None:
            from ..buildh import task
            present = force_ask or task.owner_terminal()
        else:
            present = ask_present or force_ask
        if not present:
            out["outcome"] = "unattended"
            out["lines"] = [f"START CHECK: unattended: {', '.join(ids)} missing; each step that needs one takes its "
                            "fallback (journal onestart.jsonl), the job does not wait"]
            _append("onestart.jsonl", {"event": "unattended", "kind": kind, "ids": ids, "job": job})
        elif not force_ask and _asked_recently(ids, float(data.get("ttl_hours", 12)), now):
            out["outcome"] = "asked-recently"
            out["lines"] = [f"START CHECK: {', '.join(ids)} missing, asked less than {data.get('ttl_hours', 12)} h ago; "
                            "the steps take their fallback"]
        else:
            out["asked"] = True
            for line in screen(miss, kind, job):
                say(line)
            from . import ownerproof
            text = f"approve for this {kind} job: " + ", ".join(ids)
            p = (prove or ownerproof.prove)(text)
            if not p.get("ok"):
                out["outcome"] = "declined"
                out["lines"] = [f"START CHECK: not approved ({p.get('why')}); the job starts, the steps take their fallback"]
                _append("onestart.jsonl", {"event": "asked", "outcome": "declined", "ids": ids, "kind": kind,
                                           "job": job, "why": p.get("why")})
            else:
                out["outcome"] = "approved"
                _append("onestart.jsonl", {"event": "asked", "outcome": "approved", "ids": ids, "kind": kind,
                                           "job": job, "key": p.get("key"), "sig": (p.get("sig") or "")[:32]})
                for a in miss:
                    if not a.get("apply"):
                        out["lines"].append(f"  {a['id']}: not something a program can do for you; once: {a.get('manual', '?')}")
                        continue
                    try:
                        ok, why = _run_apply(a, run)
                    except Exception as x:               # noqa: BLE001 - one failed apply never stops the job
                        ok, why = False, f"{type(x).__name__}: {x}"
                    _append("onestart.jsonl", {"event": "applied", "id": a["id"], "ok": ok, "why": why,
                                               "key": p.get("key"), "job": job})
                    out["lines"].append(f"  {a['id']}: {'applied' if ok else 'FAILED'} ({why})")
                    if ok:
                        out["applied"].append(a["id"])
                        _resolve(a["id"])
        for line in out["lines"]:
            say(line)
    except Exception as x:                                   # noqa: BLE001 - the start check never stops a job
        out["outcome"] = f"error: {type(x).__name__}: {x}"
    return out


def at_start(kind, job="", say=print):
    """The one call an entry point makes. Prints only when something is missing."""
    return start_check(kind, job=job or job_id(), say=say)


# ---- mid-job: an owner command is never filed ---------------------------------------------------------------------
def match(command="", title="", data=None):
    """-> the registry action an owner command (or title) names, or None."""
    text = f"{command} {title}".lower()
    for a in actions(None, data):
        if any(c.lower() in text for c in a.get("commands") or []):
            return a
    return None


def guard_inbox(title, why="", command=""):
    """-> None (allowed) or the refusal dict. Inside a job, `inbox add` with an owner command that is a registry action
    is refused with the reason, and the gap is recorded for the next start check."""
    if not in_job() or os.environ.get("FIELDKIT_ONESTART", "").lower() == "off":
        return None
    a = match(command, title)
    if not a:
        return None
    try:
        gap(a["id"], why=f"inbox add refused mid-job: {title[:80]}")
    except OSError:
        pass
    return {"n": 0, "routed": "refused", "state": "refused", "title": title, "action": a["id"],
            "why": REFUSED, "fallback": a["fallback"]}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    sub = argv[0] if argv and not argv[0].startswith("-") else "check"
    kind = "task"
    if "--kind" in argv:
        kind = argv[argv.index("--kind") + 1]
    if kind not in KINDS:
        print(f"fieldkit onestart: --kind is one of {', '.join(KINDS)}")
        return 2
    if sub == "check":
        for a, ok, m in status(kind):
            print(f"  {'PRESENT' if ok else 'MISSING' if ok is False else 'UNKNOWN':<8} {a['id']:<26}"
                  f"{' (gap on record: asked at the next start)' if m and ok is None else ''}")
        print("NEXT: fieldkit onestart ask --kind " + kind)
        return 0
    if sub == "ask":
        r = start_check(kind, force_ask=True)
        print(f"START CHECK: {r['outcome']}" + (f"; applied: {', '.join(r['applied'])}" if r["applied"] else ""))
        return 0 if r["outcome"] in ("none", "approved") else 3
    print("onestart [check|ask] [--kind task|emerge|goal]")
    return 2


if __name__ == "__main__":
    sys.exit(main())
