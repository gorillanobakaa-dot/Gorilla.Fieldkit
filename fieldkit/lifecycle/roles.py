"""Workers do the work, the reviewer only judges it (HARNESS-PLAN I5 and I7).

    worker_model(explicit)          the model a job runs on: the explicit one, else the default in roles.yaml;
                                    an expensive model (Opus, Fable) is refused for a worker unless allowed
    review(task, result, accept)    the reviewer sees ONLY the task and the result (small context) and the output of
                                    the job's acceptance command; -> a verdict of exactly one of three kinds:
                                      pass          the acceptance command passed and the reviewer agrees
                                      send_back     the mistake explained, the worker goes again
                                      fix_itself    the worker failed twice: the reviewer repairs it (fix_itself())
    The acceptance command decides first. A failing command is never overruled by the reviewer's words; a passing
    one still needs the reviewer's `VERDICT: PASS`. No verdict line = send back (fail closed).

A task is done on a passing verdict only (lifecycle/tasks.py gate; autopilot/goal.py _end_job).
"""
import os
import re
import shlex
import subprocess
import time
from pathlib import Path

import yaml

KINDS = ("pass", "send_back", "fix_itself")
DATA = Path(__file__).with_name("roles.yaml")
LINE = re.compile(r"^\s*VERDICT:\s*(PASS|SEND_BACK|FIX_ITSELF)\b[ :-]*(.*)$", re.M | re.I)


def load(path=None):
    """-> the roles data. A missing or broken file is an error with the path, never defaults made up."""
    p = Path(path or DATA)
    try:
        d = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as e:
        raise ValueError(f"roles data unreadable: {p}: {e}") from e
    for part, keys in (("worker", ("default", "allowed", "expensive")), ("reviewer", ("model",)),
                       ("verdict", ("worker_failures_before_fix",))):
        miss = [k for k in keys if k not in (d.get(part) or {})]
        if miss:
            raise ValueError(f"roles data {p}: {part} lacks {', '.join(miss)}")
    return d


def _tier(model, names):
    m = (model or "").lower()
    return any(n in m for n in names)


def worker_model(explicit=None, data=None, allow_expensive=False):
    """-> the model id the worker runs on. Raises ValueError for a worker on an expensive model."""
    d = data or load()
    w = d["worker"]
    model = (explicit or w["default"]).strip()
    if _tier(model, w["expensive"]) and not allow_expensive:
        raise ValueError(f"{model} is a reviewer model (roles.yaml worker.expensive): a worker uses "
                         f"{'/'.join(w['allowed'])}; --allow-expensive overrides it for one task")
    return model


def reviewer_model(data=None):
    return (data or load())["reviewer"]["model"]


def run_acceptance(cmd, cwd=None, run=subprocess.run, timeout=None):
    """-> (ok, text): the job's acceptance command; the exit code decides, its tail is the evidence."""
    if not cmd:
        return None, "no acceptance command"
    try:
        # Windows: shlex (POSIX) eats the backslashes of C:\... paths; CreateProcess parses the string itself
        r = run(cmd if os.name == "nt" else shlex.split(cmd), cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except (OSError, ValueError, subprocess.TimeoutExpired) as e:
        return False, f"acceptance command could not finish: {type(e).__name__}: {e}"
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    return r.returncode == 0, f"exit {r.returncode}\n{out[-1500:]}"


def reviewer_prompt(task, result, accept_text, data=None):
    """The whole context the reviewer gets: the task and the result, cut to the sizes in roles.yaml."""
    r = (data or load())["reviewer"]
    return ("You are the REVIEWER, in a fresh small context. You did not do this work. Judge the result against the "
            "task and the acceptance output. Do not redo the work; read files only if you must.\n\n"
            f"TASK:\n{str(task)[:int(r['max_task_chars'])]}\n\nRESULT:\n{str(result)[-int(r['max_result_chars']):]}\n\n"
            f"ACCEPTANCE COMMAND OUTPUT:\n{accept_text}\n\n"
            "End with exactly one line: `VERDICT: PASS <why>`, or `VERDICT: SEND_BACK <the mistake, so the worker "
            "can fix it>`.")


def parse(text):
    """-> (kind, why) from the last VERDICT line, or (None, '')."""
    m = LINE.findall(text or "")
    if not m:
        return None, ""
    k, why = m[-1]
    return k.lower(), why.strip()


def decide(accept_ok, review_kind, review_why, failures, data=None):
    """The verdict from the facts; no model in here. accept_ok: True/False/None (no command). failures: worker
    rounds already failed. -> {kind, why}."""
    need = int((data or load())["verdict"]["worker_failures_before_fix"])
    if accept_ok is not False and review_kind == "pass":
        return {"kind": "pass", "why": review_why or "acceptance passed and the reviewer agrees"}
    if accept_ok is False:
        why = "the acceptance command failed" + (f"; reviewer: {review_why}" if review_why else "")
    elif review_kind is None:
        why = "the reviewer gave no verdict line"
    else:
        why = review_why or "sent back"
    kind = "fix_itself" if int(failures) + 1 >= need else "send_back"
    return {"kind": kind, "why": why[:600]}


def review(task, result, accept=None, failures=0, claude=None, cwd=None, run=subprocess.run, data=None):
    """-> {kind, why, accept_ok, usd}. `claude` is autopilot.run.claude (injected in tests)."""
    d = data or load()
    ok, text = run_acceptance(accept, cwd, run=run, timeout=d["verdict"].get("accept_timeout_s"))
    if claude is None:
        from ..autopilot.run import claude
    r = d["reviewer"]
    out = claude(reviewer_prompt(task, result, text, d), cwd=cwd, max_turns=int(r["turns"]),
                 timeout=float(r["wall_minutes"]) * 60, model=r["model"], role="reviewer")
    res = out.get("result") if isinstance(out.get("result"), dict) else {}
    kind, why = parse(str(res.get("result", "") or out.get("stdout", "")))
    v = decide(ok, kind, why, failures, d)
    v.update(accept_ok=ok, usd=res.get("total_cost_usd"), reviewer=r["model"])
    return v


def verdict_from_checks(checks, failures, data=None):
    """The goal path: a job's own checks already ran (verdicts PASS/FAIL). -> {kind, why}, no model."""
    bad = [f"{c.get('id')}: {c.get('why')}" for c in checks if c.get("verdict") != "PASS"]
    if not bad:
        return {"kind": "pass", "why": f"{len(checks)} check(s) passed"}
    return decide(False, None, "; ".join(bad), failures, data)


def fix_itself(task, result, why, accept=None, cwd=None, claude=None, run=subprocess.run, data=None, check=None):
    """The worker failed twice: the reviewer model repairs the work itself, then the acceptance command decides.
    `check` (a callable -> (ok, text)) replaces the acceptance command (the goal path re-runs the job's own checks).
    -> {kind: pass | fix_itself, why, usd}. Never passes on its own word."""
    d = data or load()
    if claude is None:
        from ..autopilot.run import claude
    r = d["reviewer"]
    out = claude(f"The worker failed this task twice. Fix it yourself, in the working tree, then stop.\n\nTASK:\n"
                 f"{str(task)[:int(r['max_task_chars'])]}\n\nLAST RESULT:\n{str(result)[-int(r['max_result_chars']):]}\n\n"
                 f"WHAT IS WRONG:\n{why}\n", cwd=cwd, max_turns=int(r["turns"]) * 4,
                 timeout=float(r["wall_minutes"]) * 60 * 2, model=r["model"], role="reviewer")
    res = out.get("result") if isinstance(out.get("result"), dict) else {}
    ok, text = check() if check else run_acceptance(accept, cwd, run=run, timeout=d["verdict"].get("accept_timeout_s"))
    kind = "pass" if ok else "fix_itself"
    return {"kind": kind, "usd": res.get("total_cost_usd"), "accept_ok": ok,
            "why": "the reviewer fixed it and the acceptance command passed" if ok else
                   f"the reviewer's fix did not pass acceptance: {text[:300]}"}


def stamp():
    return time.strftime("%Y-%m-%d %H:%M:%S")
