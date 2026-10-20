# Version: 1.0.0 · updated 26-10-18-21-00
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

BLOCK = re.compile(r"```runbook\s*\n(.*?)```", re.S)


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
    for s in steps:
        if not s.get("owner") and not s.get("run"):
            raise ValueError(f"{path}: step {s['id']} has no run")
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


def _run_step(step, log, say, run=subprocess.Popen):
    cmd = step["run"]
    cwd = os.path.expandvars(step.get("cwd") or os.getcwd())
    t0 = time.time()
    with open(log, "a", encoding="utf-8") as lf:
        lf.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} $ {cmd}\n")
        lf.flush()
        # PowerShell's -Command turns any failure into exit 1 and drops a program's own code (2026-10-18, the test):
        # the step's real exit code is carried out explicitly
        ps = ("$global:LASTEXITCODE = 0; " + cmd + "; if (-not $?) { if ($LASTEXITCODE) { exit $LASTEXITCODE } "
              "else { exit 1 } }; exit $LASTEXITCODE")
        shell = ["powershell", "-NoProfile", "-Command", ps] if os.name == "nt" else ["bash", "-lc", cmd]
        p = run(shell, cwd=cwd, stdout=lf, stderr=subprocess.STDOUT)
        try:
            rc = p.wait(timeout=step.get("timeout"))
        except subprocess.TimeoutExpired:
            p.kill()
            rc = 124
            lf.write(f"\n=== TIMEOUT after {step.get('timeout')} s\n")
    return rc, time.time() - t0


def _tail(log, n=40):
    try:
        return "\n".join(Path(log).read_text(encoding="utf-8", errors="replace").splitlines()[-n:])
    except OSError:
        return ""


def run(path, say=print, claude=None, hold=True):
    """Run the runbook from its first unfinished step. -> {ok, done, failed, blocked, stopped}"""
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
    out = {"ok": True, "done": [], "failed": [], "blocked": [], "stopped": False}
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
        say(f"[{i}/{len(steps)}] {sid}: {step.get('title', step['run'])[:100]}")
        say("    " + eta.human(eta.from_history(f"runbook {sid}")))
        tries = int(step.get("retries", 0)) + 1
        ok, why = False, ""
        for attempt in range(1, tries + 2):
            rc, secs = _run_step(step, log, say)
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
        st["steps"][sid] = {"status": "done" if ok else "failed", "rc": rc, "seconds": int(secs), "evidence": why,
                            "log": str(log), "at": time.strftime("%Y-%m-%d %H:%M:%S")}
        _save(path, st)
        (out["done"] if ok else out["failed"]).append(sid)
        if not ok:
            out["ok"] = False
            if step.get("stop_on_fail", True):
                say(f"    FAILED: the run stops here (the log: {log})")
                break
    _save(path, st)
    report(path, data, st, report_p)
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
