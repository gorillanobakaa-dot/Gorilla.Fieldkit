# Version: 1.0.0 · updated 26-10-23-11-10
"""Claude Code PreToolUse hook for autopilot runs: the hard lines (hardlines.py) as a deny, nothing else.

Claude Code runs it before every tool call, in every permission mode including bypassPermissions
(https://code.claude.com/docs/en/hooks.md). It reads the call on stdin and, for a hard line, prints
{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": ...}}.
Everything else gets no output, so the run's own mode decides (autopilot: bypassPermissions, no prompt).
A crash denies (fail closed): the settings file also sets "onFailure": "block" for a timeout.
Every decision is appended to <autopilot state>/hook.log.
"""
import json
import sys
import time


def main(stdin=sys.stdin, stdout=sys.stdout):
    from fieldkit.autopilot import hardlines
    try:
        payload = json.load(stdin)
        decision, why = hardlines.decide(payload.get("tool_name"), payload.get("tool_input") or {},
                                         cwd=payload.get("cwd"))
    except Exception as e:  # noqa: BLE001 - a broken hook must not open the door
        payload, decision, why = {}, "deny", f"autopilot hook error ({e}); denied to be safe"
    try:
        hardlines.STATE.mkdir(parents=True, exist_ok=True)
        with open(hardlines.STATE / "hook.log", "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  claude  {decision.upper():5}  "
                    f"{payload.get('tool_name')}  {why}  {json.dumps(payload.get('tool_input'), ensure_ascii=False)[:300]}\n")
    except OSError:
        pass
    if decision == "deny":
        stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                                        "permissionDecisionReason": why}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
