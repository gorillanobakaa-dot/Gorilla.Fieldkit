# Version: 1.0.0 · updated 26-10-22-14-00
"""One budgeted agent job that ends in a checkpoint, never in lost work (long horizon step 1; DESIGN.md 1, 2, 0b, 5).

Owner, 2026-10-22: "stopped halfway. Re-running it from scratch wastes money and time on context the agent never
lost." The night run's job 2 hit its 40-turn cap after 31 min, mid-mutate, and left nothing committed.

    run_job(job_dir, brief, cwd, budget, ...) -> {status, rc, ...}
      status done              the run ended on its own (rc 0)
             paused-resumable  a budget ran out (turns, wall, dollars) more than max_continuations times, the rate
                               limit outlasted its backoff, or a usage window is closed past max_sleep: rc 75
             failed            a real failure (a crash or an API error) after its fallback chain: rc 3

Every non-success end first checkpoints: HANDOFF.md (the model's own kept, the harness's facts appended) + a WIP
commit on refs/heads/wip/<job> (checkpoint.py; HEAD, index and branch untouched). Then the fallback chain for that
end decides (validated names; the defaults below are DESIGN.md section 5's):

    budget_exhausted  resume_session_if_fresh -> resume_from_checkpoint -> handoff_inbox   (max_continuations, 3)
    rate_limit        backoff (retry-after honored, exponential + jitter, capped) -> pause_and_inbox
    usage_limit       wait_window (sleep until the reset; the checkpoint says paused_until for a supervisor)
    failed            one_fix_run (a fresh session from the checkpoint, told what broke) -> handoff_inbox
"""
import json
import os
import random
import time
from pathlib import Path

from . import budget as bud
from . import checkpoint as ck

DEFAULT_FALLBACK = {
    "budget_exhausted": ["resume_session_if_fresh", "resume_from_checkpoint", "handoff_inbox"],
    "rate_limit": ["backoff", "pause_and_inbox"],
    "usage_limit": ["wait_window", "pause_and_inbox"],
    "failed": ["one_fix_run", "handoff_inbox"],
}
KNOWN = {"resume_session_if_fresh", "resume_from_checkpoint", "handoff_inbox", "backoff", "wait_window",
         "pause_and_inbox", "one_fix_run", "switch_model"}
BACKOFF_BASE, BACKOFF_CAP, BACKOFF_TRIES = 30, 900, 6
MAX_SLEEP = 8 * 3600                 # longer usage pauses end the call; the supervisor resumes after paused_until
SLEEP_CHUNK = 60                     # sleeping is done in chunks so a STOP file or a heartbeat is seen


def check_fallback(fb):
    """-> list of problems (empty = valid). switch_model needs a model: {"switch_model": NAME}."""
    out = []
    for event, chain in (fb or {}).items():
        if event not in DEFAULT_FALLBACK:
            out.append(f"fallback event '{event}' is not one of {sorted(DEFAULT_FALLBACK)}")
            continue
        for a in chain or []:
            name = next(iter(a)) if isinstance(a, dict) else a
            if name not in KNOWN:
                out.append(f"fallback '{event}': unknown action '{name}' (known: {sorted(KNOWN)})")
            if name == "switch_model" and not (isinstance(a, dict) and a.get("switch_model")):
                out.append(f"fallback '{event}': switch_model needs a model name")
    return out


def _chain(fb, event):
    return list((fb or {}).get(event) or DEFAULT_FALLBACK[event])


def _names(chain):
    return [next(iter(a)) if isinstance(a, dict) else a for a in chain]


def _model_in(chain):
    return next((a["switch_model"] for a in chain if isinstance(a, dict) and a.get("switch_model")), None)


def resume_prompt(brief, job_dir, job_id, cp, mode):
    wip = cp.get("wip") or {}
    where = f" and snapshotted at git ref {wip['ref']} ({wip['sha'][:12]})" if wip.get("ok") else ""
    if mode == "session":
        return (f"Continue job {job_id} where you stopped ({cp.get('last_end')}). Do not start over: first re-read "
                f"{Path(job_dir) / 'HANDOFF.md'}, then carry on with the brief.")
    fix = (f"\nThe last attempt ended in a failure ({cp.get('last_end')}): find the cause first, fix it, then carry on. "
           "Do not weaken a check to make it pass.") if cp.get("fixing") else ""
    return (f"You are continuing job {job_id}; an earlier session stopped ({cp.get('last_end')}). Do NOT start over. "
            f"Read {Path(job_dir) / 'HANDOFF.md'} and {Path(job_dir) / 'CHECKPOINT.json'} first: the work so far is in "
            f"the working tree{where}.{fix}\n\nThe original brief:\n{brief}")


def first_prompt(brief, job_dir, job_id, b):
    return (f"{brief}\n\n---\nHarness (job {job_id}): your budget per session is {b['turns']} turns, "
            f"{int(b['wall_minutes'])} min wall time" + (f", ${b['cost_usd']:.2f}" if b.get("cost_usd") else "") +
            f". At 80 % you must write {Path(job_dir) / 'HANDOFF.md'} (done / next / blockers / commands that worked / "
            "evidence) and commit your work in progress on your branch; the harness then lets you carry on. If a "
            "session ends before the job is done, the next one starts from that HANDOFF.md, so keep it current at "
            "every milestone (tests green, a commit, before a long command).")


def _sleep_until(t, sleep, now, stop_file, say):
    while now() < t:
        if stop_file and Path(stop_file).exists():
            return False
        sleep(min(SLEEP_CHUNK, max(0.0, t - now())))
    return True


def run_job(job_dir, brief, cwd, budget=None, job_id=None, max_continuations=3, fallback=None, model=None,
            claude=None, sleep=time.sleep, now=time.time, say=print, inbox_add=None, stop_file=None,
            max_sleep=MAX_SLEEP, on_spend=None):
    """Run (or resume) one job to done, paused-resumable or failed. Every attempt is recorded in CHECKPOINT.json."""
    from . import hardlines
    if claude is None:
        from .run import claude
    if inbox_add is None:
        from .inbox import add as inbox_add
    stop_file = stop_file or hardlines.STOP_FILE
    d = Path(job_dir)
    d.mkdir(parents=True, exist_ok=True)
    job_id = job_id or d.name
    b = bud.merge(budget)
    problems = check_fallback(fallback)
    if problems:
        return {"status": "failed", "rc": 2, "why": "; ".join(problems)}
    cp = ck.load(d)
    if cp.get("status") == "done":
        return {"status": "done", "rc": 0, "why": "already done", "checkpoint": cp}
    cp.setdefault("start_head", ck.head(cwd) if ck.is_repo(cwd) else None)
    cp.update(job=job_id, cwd=str(cwd), budget=b)
    rate_tries = 0
    use_model = model or cp.get("model")
    while True:
        if Path(stop_file).exists():
            cp["status"] = "paused-resumable"
            cp["last_end"] = "the owner's STOP file"
            ck.save(d, cp)
            return {"status": "paused-resumable", "rc": bud.EXIT_PAUSED, "why": "STOP file", "checkpoint": cp}
        if (cp.get("paused_until") or 0) > now():
            wait = cp["paused_until"] - now()
            if wait > max_sleep:
                ck.save(d, cp)
                return {"status": "paused-resumable", "rc": bud.EXIT_PAUSED, "checkpoint": cp,
                        "why": f"paused until {time.strftime('%Y-%m-%d %H:%M', time.localtime(cp['paused_until']))}"}
            say(f"    {job_id}: waiting {int(wait // 60)} min ({cp.get('pause_why')})")
            if not _sleep_until(cp["paused_until"], sleep, now, stop_file, say):
                continue
        cp.pop("paused_until", None)
        mode, sid = ck.resume_mode(cp, now(), ck.session_context(cp.get("session_id")))
        if mode == "session" and "resume_session_if_fresh" not in _names(_chain(fallback, "budget_exhausted")):
            mode, sid = "checkpoint", None
        if cp.get("fixing"):
            mode, sid = "checkpoint", None
        prompt = first_prompt(brief, d, job_id, b) if mode == "start" else resume_prompt(brief, d, job_id, cp, mode)
        t0 = now()
        bud.start(d, b, t0)
        say(f"    {job_id}: attempt {len(cp['attempts']) + 1} ({mode}{' ' + sid[:8] if sid else ''})")
        r = claude(prompt, cwd=str(cwd), max_turns=b["turns"], timeout=b["wall_minutes"] * 60,
                   env_extra={"FIELDKIT_JOB_DIR": str(d)}, resume=sid, model=use_model)
        meter = bud._read(d / "meter.json")
        kind, detail = bud.classify(r, meter)
        res = r.get("result") if isinstance(r.get("result"), dict) else {}
        usd = res.get("total_cost_usd")
        if usd and on_spend:
            on_spend(float(usd), job_id)
        cp["session_id"] = res.get("session_id") or cp.get("session_id")
        cp["ended_at"] = now()
        cp["attempts"].append({"mode": mode, "end": kind, "detail": detail[:300], "rc": r.get("rc"),
                               "seconds": r.get("seconds"), "turns": res.get("num_turns"), "usd": usd,
                               "calls": meter.get("calls"), "at": time.strftime("%Y-%m-%d %H:%M:%S")})
        cp["last_end"] = f"{kind}: {detail[:200]}"
        cp.pop("fixing", None)                      # the fix run is one attempt; fix_used stops a second one
        if kind == "success":
            cp["status"] = "done"
            cp["result"] = detail
            ck.save(d, cp)
            return {"status": "done", "rc": 0, "why": detail, "checkpoint": cp}
        # every other end: the checkpoint first
        wip = ck.wip_commit(cwd, d, job_id, f"{kind}: {detail[:120]}")
        cp["wip"] = wip
        ck.handoff(d, cwd, job_id, f"{kind}: {detail}", str(res.get("result", "") or r.get("stdout", "")),
                   since=t0, wip=wip, start_head=cp.get("start_head"))
        say(f"    {job_id}: {kind} - checkpoint written" + (f", WIP {wip['sha'][:12]}" if wip.get("ok") else ""))
        if kind == "rate_limit":
            chain = _names(_chain(fallback, "rate_limit"))
            if "backoff" in chain and rate_tries < BACKOFF_TRIES:
                ra = bud.retry_after(detail)
                delay = ra if ra else min(BACKOFF_CAP, BACKOFF_BASE * 2 ** rate_tries) * (0.5 + random.random() / 2)
                rate_tries += 1
                cp.update(paused_until=now() + delay, pause_why=f"rate limited, backoff {rate_tries}/{BACKOFF_TRIES}")
                ck.save(d, cp)
                continue
            if _model_in(_chain(fallback, "rate_limit")) and use_model != _model_in(_chain(fallback, "rate_limit")):
                use_model = cp["model"] = _model_in(_chain(fallback, "rate_limit"))
                rate_tries = 0
                ck.save(d, cp)
                continue
            cp.update(status="paused-resumable", paused_until=now() + 1800, pause_why="rate limited past the backoff")
            ck.save(d, cp)
            inbox_add(f"job {job_id}: rate limited past its backoff", f"{detail[:300]}; resumable from {d}",
                      kind="paused")
            return {"status": "paused-resumable", "rc": bud.EXIT_PAUSED, "why": "rate limit", "checkpoint": cp}
        rate_tries = 0
        if kind == "usage_limit":
            t, how = bud.reset_at(detail, now())
            cp.update(paused_until=t + 60, pause_why=f"usage window closed; reopens {time.strftime('%Y-%m-%d %H:%M', time.localtime(t))} ({how})")
            cp["status"] = "paused-resumable"
            ck.save(d, cp)
            if "wait_window" in _names(_chain(fallback, "usage_limit")):
                continue
            return {"status": "paused-resumable", "rc": bud.EXIT_PAUSED, "why": cp["pause_why"], "checkpoint": cp}
        if kind in ("turns", "wall", "budget"):
            cp["continuations"] = cp.get("continuations", 0) + 1
            chain = _names(_chain(fallback, "budget_exhausted"))
            resumable = {"resume_session_if_fresh", "resume_from_checkpoint"} & set(chain)
            if resumable and cp["continuations"] <= max_continuations:
                cp["status"] = "paused-resumable"
                ck.save(d, cp)
                continue
            cp["status"] = "paused-resumable"
            ck.save(d, cp)
            if "handoff_inbox" in chain:
                inbox_add(f"job {job_id}: budget exhausted {cp['continuations']} times",
                          f"{kind}: {detail[:200]}. Resumable: HANDOFF.md in {d}; WIP {wip.get('ref', 'none')}",
                          kind="handoff", files=[str(d / "HANDOFF.md")])
            return {"status": "paused-resumable", "rc": bud.EXIT_PAUSED, "why": f"{kind} x{cp['continuations']}",
                    "checkpoint": cp}
        # crash / api_error: a real failure
        chain = _names(_chain(fallback, "failed"))
        if "one_fix_run" in chain and not cp.get("fix_used"):
            cp.update(fix_used=True, fixing=True, status="paused-resumable")
            ck.save(d, cp)
            continue
        cp["status"] = "failed"
        ck.save(d, cp)
        if "handoff_inbox" in chain:
            inbox_add(f"job {job_id}: failed", f"{kind}: {detail[:300]}. HANDOFF.md in {d}", kind="failed",
                      files=[str(d / "HANDOFF.md")])
        return {"status": "failed", "rc": 3, "why": f"{kind}: {detail}", "checkpoint": cp}
