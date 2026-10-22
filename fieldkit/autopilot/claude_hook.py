# Version: 1.1.0 · updated 26-10-22-14-00
"""Claude Code hook for autopilot runs: the hard lines (hardlines.py) as a deny, and inside a job its budget.

PreToolUse (no argument): Claude Code runs it before every tool call, in every permission mode including
bypassPermissions (https://code.claude.com/docs/en/hooks.md). It reads the call on stdin and, for a hard line,
prints {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
"permissionDecisionReason": ...}}. Inside a job (FIELDKIT_JOB_DIR set by job.py) it also counts the call against the
job's budget and, past 80 % with no fresh HANDOFF.md, or past 100 % of the wall or dollar budget, denies everything
but writing the checkpoint (budget.hook_pre). Everything else gets no output, so the run's own mode decides.
A crash denies (fail closed): the settings file also sets "onFailure": "block" for a timeout.

PostToolUse (--post): inside a job, a one-time message as the job crosses 50 / 80 / 95 % of a budget
(hookSpecificOutput.additionalContext); never denies.
Every decision is appended to <autopilot state>/hook.log.
"""
import json
import os
import sys
import time


def _log(hardlines, payload, decision, why):
    try:
        hardlines.STATE.mkdir(parents=True, exist_ok=True)
        with open(hardlines.STATE / "hook.log", "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  claude  {decision.upper():5}  "
                    f"{payload.get('tool_name')}  {why}  {json.dumps(payload.get('tool_input'), ensure_ascii=False)[:300]}\n")
    except OSError:
        pass


def post(stdin=sys.stdin, stdout=sys.stdout):
    from fieldkit.autopilot import budget
    job = os.environ.get("FIELDKIT_JOB_DIR")
    if not job:
        return 0
    try:
        json.load(stdin)
        msg = budget.hook_post(job)
    except Exception:  # noqa: BLE001 - a message is a courtesy; never break the run over it
        return 0
    if msg:
        stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": msg}}))
    return 0


def main(stdin=sys.stdin, stdout=sys.stdout):
    from fieldkit.autopilot import hardlines
    try:
        payload = json.load(stdin)
        tool, args = payload.get("tool_name"), payload.get("tool_input") or {}
        decision, why = hardlines.decide(tool, args)
        job = os.environ.get("FIELDKIT_JOB_DIR")
        if decision == "allow" and job:
            from fieldkit.autopilot import budget
            stop = budget.hook_pre(job, tool, args, transcript=payload.get("transcript_path"),
                                   read_tool=(tool or "").lower() in hardlines.READ_TOOLS)
            if stop:
                decision, why = "deny", stop
    except Exception as e:  # noqa: BLE001 - a broken hook must not open the door
        payload, decision, why = {}, "deny", f"autopilot hook error ({e}); denied to be safe"
    _log(hardlines, payload, decision, why)
    if decision == "deny":
        stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                                        "permissionDecisionReason": why}}))
    return 0


if __name__ == "__main__":
    sys.exit(post() if "--post" in sys.argv[1:] else main())
