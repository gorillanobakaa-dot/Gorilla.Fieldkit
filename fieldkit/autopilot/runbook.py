# Version: 1.1.0 · updated 26-10-22-14-00
"""fieldkit runbook: the harness runs the job; a model is called only when a step fails.

Owner, 2026-10-18: 40-50 hours per release, a human pressing Yes. The overnight model also ran ~100 PID polls
that used up its command budget, typed a PASS it had not seen, and estimated "1-2 minutes" for 8. Here the order of
work, the waiting, the retries, the ETA and the verdict are code:

  - a runbook (markdown) carries one ```runbook block (YAML): steps with `run` (a command), `cwd`, `retries`,
    `needs` (earlier step ids), `done_if` (a file that exists / a line a log must hold), `timeout` (s),
    `owner: true` (the owner's step: goes to the inbox, never run) and `fix: true` (on failure, one headless Claude
    run in autopilot diagnoses and fixes it, then the step is tried once more);
  - state is kept beside the runbook (<name>.state.json): a run resumes after the last finished step;
  - between steps the owner's STOP file halts the run; the machine is held awake for the whole run;
  - every step's output goes to its own log; the report (<name>.report.md) is written from exit codes and log
    lines only.

    fieldkit runbook run FILE        fieldkit runbook status FILE        fieldkit runbook start FILE (own window)
    fieldkit runbook check FILE      the runbook parses, its needs, budgets and fallback chains are valid

Agent steps (2026-10-22, long horizon step 1): `agent: BRIEF` (+ `cwd`, `budget: {turns, wall_minutes, cost_usd}`,
`max_continuations`, `fallback`, `model`) runs a budgeted job (job.py) whose state lives in
state/jobs/<runbook>/<step>/; a `run:` step that calls `fieldkit autopilot claude` gets the same job folder through
FIELDKIT_JOB_DIR (and its budget through FIELDKIT_JOB_BUDGET). Exit 75 = paused-resumable: not a failure; the next
`runbook run` resumes the job from its checkpoint, and the steps that need it wait. Agent steps default to
`stop_on_fail: false` (the night of 2026-10-22: one failed job stopped seven independent ones).
"""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import yaml

from . import hardlines
from .budget import EXIT_PAUSED

BLOCK = re.compile(r"```runbook\s*\n(.*?)```", re.S)
JOBS = Path(__file__).resolve().parents[2] / "state" / "jobs"
PAUSED = "paused-resumable"


def is_agent(step):
    return bool(step.get("agent")) or "autopilot claude" in str(step.get("run") or "")


def job_dir(path, step):
    return JOBS / Path(path).stem / step["id"]


def load(path):
    text = Path(path).read_text(encoding="utf-8")
    m = BLOCK.search(text)
    if not m:
        raise ValueError(f"{path}: no ```runbook block")
    data = yaml.safe_load(m.group(1)) or {}
    steps = data.get("steps") or []
    ids = [s.get("id") for s in steps]
    if not steps or any(not i for i in ids) or len(set(ids)) != len(ids):
        raise ValueError(f"{path}: every step needs a unique id")
    from . import job
    for s in steps:
        if not s.get("owner") and not s.get("run") and not s.get("agent"):
            raise ValueError(f"{path}: step {s['id']} has no run (or agent)")
        bad = [k for k in (s.get("budget") or {}) if k not in ("turns", "wall_minutes", "cost_usd")]
        if bad:
            raise ValueError(f"{path}: step {s['id']}: unknown budget key(s) {bad} (turns, wall_minutes, cost_usd)")
        problems = job.check_fallback(s.get("fallback") or data.get("fallback"))
        if problems:
            raise ValueError(f"{path}: step {s['id']}: " + "; ".join(problems))
        for n in s.get("needs") or []:
            if n not in ids[:ids.index(s["id"])]:
                raise ValueError(f"{path}: step {s['id']} needs {n}, which is not an earlier step")
    return data


def _paths(path):
    p = Path(path)
    return p.with_suffix(".state.json"), p.with_suffix(".report.md"), p.parent / (p.stem + "-logs")


def state(path):
    sp, _, _ = _paths(path)
    return json.loads(sp.read_text(encoding="utf-8")) if sp.is_file() else {"steps": {}}


def _save(path, st):
    sp, _, _ = _paths(path)
    sp.write_text(json.dumps(st, indent=1), encoding="utf-8")


def done_if(cond, cwd):
    """`file: P` exists, or `log: P` + `line: REGEX` - the step's own evidence, checked after it ran."""
    if not cond:
        return True, "no done_if: the exit code decides"
    base = Path(cwd or ".")
    if "file" in cond:
        f = base / os.path.expandvars(cond["file"])
        return f.exists(), f"{f} {'exists' if f.exists() else 'missing'}"
    if "log" in cond:
        f = base / os.path.expandvars(cond["log"])
        if not f.is_file():
            return False, f"{f} missing"
        hit = re.search(cond.get("line", "."), f.read_text(encoding="utf-8", errors="replace"), re.M)
        return bool(hit), f"{f}: {'has' if hit else 'lacks'} /{cond.get('line')}/"
    return False, f"unknown done_if {cond}"


STEP_CAP_S = 12 * 3600               # a command step with no timeout of its own still has a wall deadline


def _run_step(step, log, say, run=subprocess.Popen, env=None):
    cmd = step["run"]
    cwd = os.path.expandvars(step.get("cwd") or os.getcwd())
    timeout = step.get("timeout")
    if timeout is None and is_agent(step):
        # an agent step's wall budget is enforced by the job itself; this is only the backstop
        from .budget import merge
        timeout = merge(step.get("budget"))["wall_minutes"] * 60 * (1 + int(step.get("max_continuations", 3))) + 600
    t0 = time.time()
    with open(log, "a", encoding="utf-8") as lf:
        lf.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} $ {cmd}\n")
        lf.flush()
        # PowerShell's -Command turns any failure into exit 1 and drops a program's own code (2026-10-18, the test):
        # the step's real exit code is carried out explicitly
        ps = ("$global:LASTEXITCODE = 0; " + cmd + "; if (-not $?) { if ($LASTEXITCODE) { exit $LASTEXITCODE } "
              "else { exit 1 } }; exit $LASTEXITCODE")
        shell = ["powershell", "-NoProfile", "-Command", ps] if os.name == "nt" else ["bash", "-lc", cmd]
        if run is subprocess.Popen:
            # in a container with a wall deadline (none given: STEP_CAP_S): the whole tree stops on expiry, and what
            # the step leaves running when it ends is stopped too (fieldkit/core/contain.py, FM-1/FM-2)
            from ..core import contain, liveness
            deadline = liveness.deadline_for((), timeout, cap=max(STEP_CAP_S, timeout or 0))
            lf.close()
            o = contain.run(shell, label=f"runbook step {step.get('id')}", deadline_s=deadline, cwd=cwd, env=env,
                            stdout_path=log, merge=True)
            with open(log, "a", encoding="utf-8") as lf2:
                if o.timed_out:
                    lf2.write(f"\n=== TIMEOUT after {deadline:.0f} s: the container stopped PIDs {o.killed}\n")
                elif o.leftovers:
                    lf2.write(f"\n=== {o.why}\n")
            return o.rc, time.time() - t0
        p = run(shell, cwd=cwd, stdout=lf, stderr=subprocess.STDOUT, env=env)
        try:
            rc = p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            from .run import kill_tree
            killed = kill_tree(p.pid)            # the whole tree by PID: killing the shell alone left its child
            rc = 124
            lf.write(f"\n=== TIMEOUT after {timeout} s: stopped PIDs {killed}\n")
    return rc, time.time() - t0


def _tail(log, n=40):
    try:
        return "\n".join(Path(log).read_text(encoding="utf-8", errors="replace").splitlines()[-n:])
    except OSError:
        return ""


def run(path, say=print, claude=None, hold=True, job_runner=None):
    """Run the runbook from its first unfinished step. -> {ok, done, failed, blocked, stopped}
    Under a liveness beacon (heartbeat + open steps + their processes), so `fieldkit liveness check` sees it."""
    from ..core import liveness
    with liveness.beacon("runbook", Path(path).stem, str(path)):
        return _run(path, say, claude, hold, job_runner)


def _run(path, say=print, claude=None, hold=True, job_runner=None):
    from ..core import eta
    data = load(path)
    st = state(path)
    _, report_p, logs = _paths(path)
    logs.mkdir(exist_ok=True)
    if hold:
        try:
            from ..buildh.window import hold_awake
            hold_awake(os.getpid())
        except Exception as e:  # noqa: BLE001
            say(f"  [WARN] stay awake: not held ({e})")
    steps = data["steps"]
    out = {"ok": True, "done": [], "failed": [], "blocked": [], "paused": [], "stopped": False}
    for i, step in enumerate(steps, 1):
        sid = step["id"]
        rec = st["steps"].get(sid, {})
        if rec.get("status") == "done":
            out["done"].append(sid)
            continue
        if hardlines.STOP_FILE.exists():
            say(f"STOPPED by the owner before step {sid}: {hardlines.STOP_FILE.read_text(encoding='utf-8').strip()}")
            out["stopped"] = True
            break
        unmet = [n for n in step.get("needs") or [] if st["steps"].get(n, {}).get("status") != "done"]
        if unmet:
            st["steps"][sid] = {"status": "blocked", "why": f"needs {unmet}"}
            out["blocked"].append(sid)
            say(f"[{i}/{len(steps)}] {sid}: BLOCKED (needs {', '.join(unmet)})")
            continue
        if step.get("owner"):
            from . import inbox
            if rec.get("status") != "inbox":
                inbox.add(step.get("title", sid), step.get("why", "a step only the owner may do"), step.get("run", ""))
            st["steps"][sid] = {"status": "inbox"}
            out["blocked"].append(sid)
            say(f"[{i}/{len(steps)}] {sid}: the owner's step - in the inbox, not run")
            _save(path, st)
            continue
        log = logs / f"{i:02d}-{sid}.log"
        say(f"[{i}/{len(steps)}] {sid}: {step.get('title', step.get('run') or step.get('agent'))[:100]}")
        say("    " + eta.human(eta.from_history(f"runbook {sid}")))
        if step.get("agent"):
            rc, secs, why = _run_agent(path, step, data, say, job_runner)
            ok = rc == 0
            say(f"    exit {rc}, {int(secs)} s; {why[:200]}")
            _record(path, st, out, step, rc, secs, why, log, ok, say)
            if not ok and rc != EXIT_PAUSED and step.get("stop_on_fail", False):
                say(f"    FAILED: the run stops here (stop_on_fail)")
                break
            continue
        env = None
        if is_agent(step):
            env = dict(os.environ, FIELDKIT_JOB_DIR=str(job_dir(path, step)),
                       FIELDKIT_JOB_BUDGET=json.dumps(step.get("budget") or {}),
                       FIELDKIT_JOB_CONTINUATIONS=str(step.get("max_continuations", 3)))
        tries = int(step.get("retries", 0)) + 1
        ok, why = False, ""
        for attempt in range(1, tries + 2):
            rc, secs = _run_step(step, log, say, env=env)
            if rc == EXIT_PAUSED and is_agent(step):
                why = "paused-resumable (exit 75): the job checkpointed; the next run resumes it"
                say(f"    attempt {attempt}: exit 75, {int(secs)} s; {why}")
                break
            good, why = done_if(step.get("done_if"), step.get("cwd"))
            ok = rc == 0 and good
            say(f"    attempt {attempt}: exit {rc}, {int(secs)} s; {why}")
            if ok:
                eta.record(f"runbook {sid}", secs)
                break
            if attempt == tries and step.get("fix") and claude is not None:
                say("    asking one autopilot model run to diagnose and fix it, then one more try")
                claude(f"In {step.get('cwd') or os.getcwd()}, the runbook step '{sid}' ({step['run']}) failed with exit "
                       f"{rc}. Its log ends:\n{_tail(log)}\nFind the cause and fix it. Do not run the step yourself, do "
                       "not weaken a check to make it pass; if only the owner can fix it, add it to the inbox "
                       "(fieldkit inbox add). Say what you changed.", cwd=step.get("cwd"))
                continue
            if attempt >= tries:
                break
        _record(path, st, out, step, rc, secs, why, log, ok, say)
        if not ok and rc != EXIT_PAUSED and step.get("stop_on_fail", not is_agent(step)):
            say(f"    FAILED: the run stops here (the log: {log})")
            break
    _save(path, st)
    report(path, data, st, report_p)
    return out


def _record(path, st, out, step, rc, secs, why, log, ok, say):
    sid = step["id"]
    paused = rc == EXIT_PAUSED and is_agent(step)
    status = "done" if ok else PAUSED if paused else "failed"
    prev = st["steps"].get(sid, {})
    st["steps"][sid] = {"status": status, "rc": rc, "seconds": int(secs), "evidence": why, "log": str(log),
                        "at": time.strftime("%Y-%m-%d %H:%M:%S"), "pauses": prev.get("pauses", 0) + paused}
    if is_agent(step):
        st["steps"][sid]["job"] = str(job_dir(path, step))
    _save(path, st)
    (out["done"] if ok else out["paused"] if paused else out["failed"]).append(sid)
    if not ok:
        out["ok"] = False


def _run_agent(path, step, data, say, job_runner=None):
    """An `agent:` step: one budgeted job (job.py) in state/jobs/<runbook>/<step>."""
    from . import job
    t0 = time.time()
    r = (job_runner or job.run_job)(
        job_dir(path, step), step["agent"], os.path.expandvars(step.get("cwd") or os.getcwd()),
        budget=step.get("budget"), job_id=f"{Path(path).stem}-{step['id']}",
        max_continuations=int(step.get("max_continuations", 3)), fallback=step.get("fallback") or data.get("fallback"),
        model=step.get("model"), say=say)
    return r["rc"], time.time() - t0, f"{r['status']}: {r.get('why', '')}"


def check(path):
    """-> list of lines; raises ValueError on the first problem (load does the checking)."""
    data = load(path)
    out = [f"RUNBOOK {Path(path).name}: {len(data['steps'])} step(s) valid"]
    from .budget import merge
    for s in data["steps"]:
        kind = "owner" if s.get("owner") else "agent" if is_agent(s) else "command"
        b = merge(s.get("budget")) if kind == "agent" else None
        out.append(f"  {s['id']:<24} {kind:<7} " + (f"budget {b['turns']} turns, {int(b['wall_minutes'])} min"
                                                    + (f", ${b['cost_usd']}" if b.get("cost_usd") else "")
                                                    + f"; stop_on_fail {s.get('stop_on_fail', False)}" if b else ""))
    return out


def report(path, data, st, report_p):
    """The verdict, from exit codes and log lines only."""
    rows = [f"# {Path(path).stem}: run report", "", f"Written {time.strftime('%Y-%m-%d %H:%M:%S')} by fieldkit runbook "
            "from exit codes and log lines (no model wrote it).", "", "| step | state | exit | time | evidence |",
            "|---|---|---|---|---|"]
    for s in data["steps"]:
        r = st["steps"].get(s["id"], {})
        rows.append(f"| {s['id']} | {r.get('status', 'not run')} | {r.get('rc', '')} | "
                    f"{(str(r['seconds'] // 60) + ' min') if 'seconds' in r else ''} | {str(r.get('evidence', r.get('why', '')))[:120]} |")
    report_p.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return report_p


def lines(path):
    data, st = load(path), state(path)
    out = [f"RUNBOOK {Path(path).name}"]
    for i, s in enumerate(data["steps"], 1):
        r = st["steps"].get(s["id"], {})
        out.append(f"  [{i}] {s['id']:<24} {r.get('status', 'not run'):<8} {r.get('evidence', r.get('why', ''))[:90]}")
    return out
