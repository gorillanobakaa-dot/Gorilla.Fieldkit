# Version: 1.0.0 · updated 26-10-23-19-43
"""While a mandate is in force, no session asks the owner: it decides within the mandate, or queues the question for
the one morning report, and carries on.

    fieldkit mandate ask "the question" [--why "..."]   queue it (ledger event `ask` + the owner's inbox); the report's
                                                        NEEDS YOU lists it; the session carries on with the rest
    fieldkit mandate quiet --install                    add the two hooks to ~/.claude/settings.json (a copy kept first)
    fieldkit mandate quiet --stop-hook | --pre-hook     what Claude Code runs

Owner, 2026-10-23: "everything is already on the bypass permissions on both machines, but i am still getting called
like a monkey to jump between 3 devices and 15 sessions ans nested subsessions 'approve this, i need you to have a
look at this'. this is bullshit. counterproductive." The app's permission dialogs were not the bottleneck: the sessions
were, each asking. The owner signed the mandate once; that IS the answer to every "may I". So, only while a mandate is
in force on this computer:
  Stop hook        a turn whose last words ask the owner something is sent back once: decide, or `fieldkit mandate
                   ask`, then finish the turn without the question
  PreToolUse hook  AskUserQuestion, a question in the app's next-step box (wrap_up `ask`), a [NEEDS YOU] note (link
                   --needs-you, SendMessage) are refused with the same reason
No mandate in force: nothing changes. Both hooks fail open (a broken check never blocks work) and the Stop hook sends a
turn back once at most.
"""
import json
import re
import sys
import time
from pathlib import Path

REASON = ("A project mandate is in force on this computer: the owner signed it once and is NOT asked during the run "
          "(owner, 2026-10-23). Decide within the mandate and carry on. If something truly needs the owner, queue it "
          "with `fieldkit mandate ask \"...\"` (it goes into the one morning report) and continue with everything "
          "that does not depend on it. Then finish your answer without a question to the owner.")

# the last words of a turn that hand the owner a decision (English and Romanian, as the sessions write them)
ASKING = re.compile(
    r"\[NEEDS YOU\]|\bneeds? (?:you|your (?:ok|yes|approval|decision|call))\b|\bwaiting (?:on|for) (?:you|the owner)\b|"
    r"\b(?:shall|should|may|can) I\b[^.\n]{0,120}\?|\bdo you want\b|\bwould you like\b|\byour (?:call|decision|approval)\b|"
    r"\bapprove\b[^.\n]{0,80}\?|\bconfirm\b[^.\n]{0,80}\?|"
    r"\bvrei s[aă]\b|\bs[aă] (?:continui|merg|fac|urc|unesc|pornesc)\b[^.\n]{0,80}\?|\bdecizia (?:e a )?ta\b|"
    r"\bam nevoie de (?:tine|aprobarea|r[aă]spunsul)\b|\bconfirm[aă]\b[^.\n]{0,80}\?|\baprob[aă]\b[^.\n]{0,80}\?",
    re.I)


def in_force():
    try:
        from . import mandate as M
        return bool(M.in_force())
    except Exception:  # noqa: BLE001 - fail open
        return False


def asks_owner(text):
    """The closing part of a turn (its last 12 non-empty lines) hands the owner a question or a decision."""
    tail = "\n".join([ln for ln in str(text or "").splitlines() if ln.strip()][-12:])
    return bool(ASKING.search(tail))


def queue(question, why="", jobs_mod=None, inbox_add=None):
    """`fieldkit mandate ask`: the ledger's `ask` event (the morning report's NEEDS YOU) and the owner's inbox."""
    from . import jobs as J
    n = (jobs_mod or J).event("ask", why=str(question)[:500], detail=str(why)[:500])
    try:
        if inbox_add is None:
            from ..autopilot.inbox import add as inbox_add
        inbox_add(str(question)[:120], str(why or question)[:1000], kind="mandate")
    except Exception:  # noqa: BLE001 - the ledger holds it
        pass
    return n


def stop_hook(stdin=sys.stdin, stdout=sys.stdout, force=None):
    try:
        payload = json.load(stdin)
        if payload.get("stop_hook_active"):
            return 0                                      # sent back once already: never loop
        if not (in_force() if force is None else force):
            return 0
        from ..autopilot.ownercmd import _last_assistant_text
        if not asks_owner(_last_assistant_text(payload["transcript_path"])):
            return 0
    except Exception:  # noqa: BLE001 - a broken check never blocks an answer
        return 0
    stdout.write(json.dumps({"decision": "block", "reason": REASON}))
    return 0


def _asks(tool, args):
    name = (tool or "").lower()
    args = args if isinstance(args, dict) else {}
    if name == "askuserquestion":
        return True
    if name.endswith("wrap_up") and str(args.get("ask") or "").strip():
        return True
    text = json.dumps(args, ensure_ascii=False)
    if name in ("sendmessage",) and "[NEEDS YOU]" in text:
        return True
    cmd = str(args.get("command") or "")
    return bool(re.search(r"\bpeers\b[^\n]*\blink\b[^\n]*\bsend\b[^\n]*--needs-you\b", cmd))


def pre_hook(stdin=sys.stdin, stdout=sys.stdout, force=None):
    try:
        payload = json.load(stdin)
        if not _asks(payload.get("tool_name"), payload.get("tool_input") or {}):
            return 0
        if not (in_force() if force is None else force):
            return 0
    except Exception:  # noqa: BLE001 - fail open
        return 0
    stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                                    "permissionDecisionReason": REASON}}))
    return 0


def install(settings_path=None, python=None, keep_dir=None):
    """Add the Stop and PreToolUse hooks to the user's Claude Code settings, once; the old file is copied aside first
    (house rule: overwriting is deleting). -> (code, lines)."""
    import shutil
    p = Path(settings_path or Path.home() / ".claude" / "settings.json")
    py = python or sys.executable
    # the code is pinned to THIS checkout: `python -m fieldkit` loads whatever branch the main checkout (or the
    # session's own folder) is on, which may not have this module at all (2026-10-23: the main checkout was on another
    # session's unfinished branch). Install from a clean checkout of main (Fieldkit-main).
    root = str(Path(__file__).resolve().parents[2])
    run_ = (f'"{py}" -c "import sys; sys.path.insert(0, {root!r}); from fieldkit.mandate.quiet import main; '
            'sys.exit(main([\'--{}-hook\']))"')
    cmd_stop, cmd_pre = run_.format("stop"), run_.format("pre")
    s = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
    keep = Path(keep_dir or Path.home() / "Agents.Work.Trash" / "claude-settings")
    if p.is_file():
        keep.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, keep / f"settings.json.{time.strftime('%y-%m-%d-%H-%M-%S')}")
    hooks = s.setdefault("hooks", {})
    added = []
    for event, cmd, matcher in (("Stop", cmd_stop, None), ("PreToolUse", cmd_pre, "*")):
        groups = hooks.setdefault(event, [])
        if any("mandate.quiet" in json.dumps(g) or "mandate quiet" in json.dumps(g) for g in groups):
            continue
        g = {"hooks": [{"type": "command", "command": cmd}]}
        if matcher:
            g["matcher"] = matcher
        groups.append(g)
        added.append(event)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(s, indent=2) + "\n", encoding="utf-8")
    back = json.loads(p.read_text(encoding="utf-8"))           # the artefact: read back
    ok = all("mandate.quiet" in json.dumps(back["hooks"].get(e, [])) for e in ("Stop", "PreToolUse"))
    return (0 if ok else 3), [f"{p}: " + (f"added {', '.join(added)}" if added else "already there"),
                              f"previous copy in {keep}" if p.is_file() else "", "checked: both hooks present" if ok
                              else "NOT FOUND after writing"]


def main(argv):
    if "--stop-hook" in argv:
        return stop_hook()
    if "--pre-hook" in argv:
        return pre_hook()
    if "--install" in argv:
        code, lines = install()
        print("\n".join(x for x in lines if x))
        return code
    print("quiet --install | --stop-hook | --pre-hook")
    return 2
