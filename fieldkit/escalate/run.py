"""The ladder: low effort first, a NEW session at high effort with a short failure summary on a deterministic red,
then optionally xhigh on Opus when the model-spend guard allows it. Never --resume: a retry starts fresh.

Evidence (platform.claude.com/docs/en/about-claude/models/optimizing-for-cost-and-intelligence): low effort with the
failures re-run at high scored about 97% at about $0.17 a task; all-high scored 95.3% at $0.29.
"""
import json
import shlex
import subprocess
import sys
import time

from .. import proc as fkproc
from . import cachecheck

TAIL_LINES = 25          # the failure summary handed to the next rung stays small
TAIL_CHARS = 3000
RUNGS = [("low", None), ("high", None), ("xhigh", "opus")]      # (effort, model; None = the guard's cheap default)


def ledger_path():
    from ..lifecycle.friction import state_dir
    return state_dir() / "escalate.jsonl"


def _log(rec):
    rec = dict(rec, at=time.strftime("%Y-%m-%d %H:%M:%S"))
    with open(ledger_path(), "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def check_argv(test_cmd=None, verify=None, ci_pr=None):
    """The deterministic failure signal as one argv: a test command, a verifier command, or `ship ci-wait PR`."""
    if ci_pr:
        return [sys.executable, "-m", "fieldkit", "ship", "ci-wait", str(ci_pr)]
    cmd = test_cmd or verify
    if not cmd:
        raise ValueError("give one failure signal: --test CMD, --verify CMD or --ci-wait PR")
    if isinstance(cmd, list):
        return cmd
    parts = shlex.split(cmd, posix=(sys.platform != "win32"))
    return [x[1:-1] if len(x) > 1 and x[0] == x[-1] and x[0] in "\"'" else x for x in parts]


def run_check(argv, cwd, timeout=None):
    """-> (passed, tail): exit code 0 = pass; the last lines of the output are the failure summary."""
    try:
        r = fkproc.run(argv, cwd=cwd, capture_output=True, text=True, errors="replace", timeout=timeout)
        text = (r.stdout or "") + (r.stderr or "")
        rc = r.returncode
    except (OSError, subprocess.TimeoutExpired) as e:
        text, rc = f"check could not run: {type(e).__name__}: {e}", 1
    tail = "\n".join(text.strip().splitlines()[-TAIL_LINES:])[-TAIL_CHARS:]
    return rc == 0, tail


def brief_for(brief, tail, attempt):
    if not tail:
        return brief
    return (f"{brief}\n\n---\nATTEMPT {attempt}: the previous attempt did not pass the check. Its last output "
            f"(fix the cause; do not repeat it):\n{tail}\n")


def usage_of(res):
    r = (res or {}).get("result") or {}
    u = r.get("usage") or {}
    toks = sum(int(u.get(k) or 0) for k in ("input_tokens", "output_tokens", "cache_creation_input_tokens",
                                             "cache_read_input_tokens"))
    return {"tokens": toks or None, "cost_usd": r.get("total_cost_usd")}


def ladder(brief, cwd, check, rungs=None, starter=None, wall=None, skip_if_green=False, check_timeout=None):
    """Run the rungs in order until the check passes. -> {ok, attempts, passed_at, model_calls}.
    starter(prompt, cwd, effort, model, env_extra, timeout) -> the autopilot run dict (injectable for tests)."""
    from ..lifecycle import models
    starter = starter or _start
    attempts, tail = [], ""

    def calls():
        return sum(1 for a in attempts if a["result"] in ("pass", "fail"))

    if skip_if_green:
        ok, tail = run_check(check, cwd, check_timeout)
        if ok:
            attempts.append(_log({"effort": None, "model": None, "seconds": 0, "result": "pass",
                                  "note": "check already green: no model call"}))
            return {"ok": True, "attempts": attempts, "passed_at": 0, "model_calls": 0}
    for n, (effort, model) in enumerate(rungs or RUNGS, 1):
        if model:                                       # TWO STRIKES: a stronger model only if the guard allows
            g = models.check_model(model, "worker", "escalate")
            if not g["ok"]:
                attempts.append(_log({"attempt": n, "effort": effort, "model": model, "seconds": 0,
                                      "result": "skipped", "note": g["why"]}))
                continue
        t0 = time.time()
        res = starter(brief_for(brief, tail, n), cwd, effort, model, cachecheck.env_for_children(), wall)
        secs = round(time.time() - t0)
        if res.get("refused"):
            attempts.append(_log({"attempt": n, "effort": effort, "model": model, "seconds": secs,
                                  "result": "refused", "note": str(res.get("why", ""))[:200]}))
            continue
        ok, tail = run_check(check, cwd, check_timeout)
        attempts.append(_log({"attempt": n, "effort": effort, "model": model or "default", "seconds": secs,
                              "result": "pass" if ok else "fail", **usage_of(res)}))
        if ok:
            return {"ok": True, "attempts": attempts, "passed_at": n, "model_calls": calls()}
    return {"ok": False, "attempts": attempts, "passed_at": None, "last_output": tail, "model_calls": calls()}


def _start(prompt, cwd, effort, model, env_extra, wall):
    """The one session starter: autopilot's claude() (guard, container, stall watch), a NEW session each time."""
    from ..autopilot import run as ap
    return ap.claude(prompt, cwd=cwd, timeout=wall, env_extra=env_extra, model=model, effort=effort, resume=None)
