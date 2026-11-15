# Version: 1.0.0 · updated 26-11-15
"""POOL: one command that plans Zero's whole session pool (owner, 2026-11-15: "We have about 7 sessions opened. Check
which ones are still relevant. If they are not relevant, clear their context and use them as reserve ... so you always
have a fresh batch of ready-to-deploy sessions ... merge these capabilities into one unified tool").

    fieldkit pool plan [--sessions FILE.json|-] [--reserve N] [--self ID] [--event-turn] [--clean ID ...]
                       [--no-permanence-lookup] [--tag TAG] [--now ISO] [--json]

Starting, clearing, archiving, renaming sessions and switching Remote Control off are Claude desktop app tools only the
model can call. So this is a deterministic planner: the input is the JSON array `list_sessions {"linked": true}` returns,
the output is an ordered list of exact app-tool calls, one per line, each with its reason. Zero executes the lines in
order without judging. Rules are data (pool.yaml). One action per session:

    KEEP      running; idle with an open PR it watches; the Zero seat or --self; a session with live side sessions;
              a worker whose job is not done (`"done": false` in the input); idle for less than idle_minutes;
              a finished session whose work cannot be proven safe (permanence not clean or not checkable)
    RECYCLE   idle, finished, nothing would be lost: [set_remote_control false], [SendMessage order to TaskStop], 
              clear_session, set_session_title "[TAG] Reserve N (standby for Zero)"
    ARCHIVE   idle reserves beyond --reserve (oldest first): [set_remote_control false], archive_session
    START     fewer reserves than --reserve: start_session, user_asked, fresh. Only an owner turn works; with
              --event-turn the line says WAIT instead (the app refuses start_session on an event turn)

"Nothing would be lost" = `fieldkit permanence check --session` clean for that session (transcript found), or the id
named with --clean after Zero ran that check. Not provable -> KEEP, with the command to run. Never guessed.
A running background task is stopped by the session itself (the order is the first line), never by Zero.

Zero tokens: plain code. Reads the JSON and, per finished session, the permanence check (git, gh). Writes nothing.
Windows and Linux.
"""
import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

RULES = Path(__file__).with_name("pool.yaml")
TOOL_SM = "mcp__ccd_session_mgmt__"
TOOL_S = "mcp__ccd_session__"


def load_rules(path=None):
    import yaml
    return yaml.safe_load(Path(path or RULES).read_text(encoding="utf-8")) or {}


def _ts(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def tag_of(title):
    m = re.match(r"\s*(\[[^\]]+\])", title or "")
    return m.group(1) if m else None


def is_reserve(s, rules):
    return bool(re.search(rules["reserve_pattern"], s.get("title") or ""))


def _default_clean(session_id):
    """-> True clean, False findings, None not provable (no transcript for the id). Never raises."""
    try:
        from . import nested, permanence
        rec = nested.load_records().get(session_id) or {}
        cli = rec.get("cliSessionId") or session_id
        tp = permanence.transcript_for(cli)
        if tp is None:
            return None
        return not permanence.blocking(permanence.scan(session=tp))
    except Exception:  # noqa: BLE001
        return None


def plan(sessions, rules=None, reserve=None, self_id=None, event_turn=False, clean=(), clean_fn=None, now=None, tag=None):
    """-> {"decisions": [{id, title, action, why}], "steps": [{tool, args, why}]}. Pure given clean_fn and now."""
    rules = rules or load_rules()
    want = int(rules["reserve"] if reserve is None else reserve)
    now = now or datetime.now(timezone.utc)
    clean_fn = clean_fn or (lambda sid: None)
    live = [s for s in sessions if not s.get("isArchived")]
    parents = {s.get("startedBy") for s in live if s.get("startedBy")}
    tags = Counter(t for t in (tag_of(s.get("title")) for s in live) if t)
    tag = tag or (tags.most_common(1)[0][0] if tags else "[SESSION]")
    keep_pats = [re.compile(p) for p in rules.get("keep_title_patterns", [])]
    decisions, recycle, reserves = [], [], []

    def dec(s, action, why):
        decisions.append({"id": s["sessionId"], "title": s.get("title", ""), "action": action, "why": why})

    for s in live:
        sid, title = s["sessionId"], s.get("title") or ""
        idle_min = None
        la = _ts(s.get("lastActivityAt"))
        if la is not None:
            idle_min = (now - la).total_seconds() / 60
        if sid == self_id:
            dec(s, "KEEP", "this session (--self)")
        elif s.get("isRunning"):
            dec(s, "KEEP", "running")
        elif any(p.search(title) for p in keep_pats):
            dec(s, "KEEP", "the command post (Zero seat)")
        elif sid in parents:
            dec(s, "KEEP", "has side sessions it started: archiving or clearing it would take them with it")
        elif is_reserve(s, rules):
            reserves.append(s)
            dec(s, "RESERVE", "idle reserve, ready")
        elif s.get("prState") in rules.get("open_pr_states", []):
            dec(s, "KEEP", f"idle with an open PR ({s.get('prNumber')}) it watches")
        elif s.get("done") is False:
            dec(s, "KEEP", "its job is not done (done: false)")
        elif idle_min is not None and idle_min < rules["idle_minutes"]:
            dec(s, "KEEP", f"idle only {idle_min:.0f} min (limit {rules['idle_minutes']})")
        else:
            ok = True if sid in clean else clean_fn(sid)
            if ok is True:
                recycle.append(s)
                dec(s, "RECYCLE", "idle, finished, permanence clean: nothing would be lost")
            elif ok is False:
                dec(s, "KEEP", "work would be lost: fieldkit permanence check --session " + sid)
            else:
                dec(s, "KEEP", f"cannot prove nothing is lost: run `fieldkit permanence check --session {sid}`, "
                               f"then plan again with --clean {sid}")

    used = {int(m.group(1)) for s in reserves for m in [re.search(r"Reserve (\d+)", s.get("title") or "")] if m}
    steps = []

    def free_a_number():
        n = 1
        while n in used:
            n += 1
        used.add(n)
        return n

    def unlink(s, why):
        if s.get("remoteControlActive"):
            steps.append({"tool": TOOL_SM + "set_remote_control", "args": {"session_id": s["sessionId"], "enabled": False},
                          "why": "Remote Control on blocks clear and archive; " + why})

    for s in recycle:
        steps.append({"tool": "SendMessage", "args": {"to": s["sessionId"], "message":
                      "Order from Zero: if you have any background task running, TaskStop it now; reply nothing else. "
                      "You are about to be cleared and used as a reserve."},
                      "why": "only the session itself may stop its own background tasks (skip when it has none)"})
        unlink(s, "recycle " + s["sessionId"])
        steps.append({"tool": TOOL_SM + "clear_session", "args": {"session_id": s["sessionId"]},
                      "why": "recycle: " + (s.get("title") or "")})
        n = free_a_number()
        steps.append({"tool": TOOL_SM + "set_session_title", "args": {"session_id": s["sessionId"],
                      "title": rules["reserve_title"].format(tag=tag, n=n)}, "why": "now a reserve"})

    total = len(reserves) + len(recycle)
    if total > want:
        extra = sorted(reserves, key=lambda s: s.get("lastActivityAt") or "")[:total - want]
        for s in extra:
            for d in decisions:
                if d["id"] == s["sessionId"]:
                    d["action"], d["why"] = "ARCHIVE", f"{total} reserves, want {want}: the one idle longest"
            unlink(s, "archive " + s["sessionId"])
            steps.append({"tool": TOOL_SM + "archive_session", "args": {"session_id": s["sessionId"],
                          "reason": "surplus reserve"}, "why": "more reserves than wanted"})
    for _ in range(max(0, want - total)):
        n = free_a_number()
        title = rules["reserve_title"].format(tag=tag, n=n)
        if event_turn:
            steps.append({"tool": "WAIT", "args": {"title": title},
                          "why": "start_session is refused on an event turn: start it on the next owner turn"})
        else:
            steps.append({"tool": TOOL_S + "start_session", "args": {
                "initiation": "user_asked", "context": "fresh", "title": title, "name": f"reserve-{n}",
                "prompt": "You are a standby reserve for Zero. Do nothing until Zero sends you a brief. "
                          "Then follow it and its DONE PROTOCOL.",
                "background": "Reserve session kept by `fieldkit pool plan`: empty context, ready to be ordered "
                              "by Zero, so a job starts at once on an event turn when start_session is refused."},
                "why": f"{total} reserves, want {want}"})
    return {"want": want, "tag": tag, "decisions": decisions, "steps": steps}


def render(p):
    out = [f"{d['action']:8} {d['id']}  {d['title'][:52]}  - {d['why']}" for d in p["decisions"]]
    out.append("")
    if p["steps"]:
        out.append("EXECUTE IN ORDER (each line is one app-tool call):")
        for i, st in enumerate(p["steps"], 1):
            out.append(f"{i}. {st['tool']} {json.dumps(st['args'], ensure_ascii=False)}   # {st['why']}")
    else:
        out.append("nothing to do: the pool is as wanted")
    out.append(f"POOL: {len(p['steps'])} step(s)\nNEXT: " + ("execute the numbered lines in order, then `list_sessions` once more"
               if p["steps"] else "nothing"))
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="fieldkit pool", description=__doc__.split("\n\n")[0])
    ap.add_argument("action", choices=["plan"])
    ap.add_argument("--sessions", default="-", help="the list_sessions JSON file, or - for stdin")
    ap.add_argument("--reserve", type=int, default=None)
    ap.add_argument("--self", dest="self_id", default=None, help="this session's id (never touched)")
    ap.add_argument("--event-turn", action="store_true", help="the turn is an event: start_session would be refused")
    ap.add_argument("--clean", action="append", default=[], help="a session id whose permanence check you ran clean")
    ap.add_argument("--no-permanence-lookup", action="store_true", help="do not look transcripts up (only --clean counts)")
    ap.add_argument("--tag", default=None)
    ap.add_argument("--now", default=None, help="ISO time (tests)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(sys.argv[1:] if argv is None else argv)
    try:
        raw = sys.stdin.read() if a.sessions == "-" else Path(a.sessions).read_text(encoding="utf-8")
        sessions = json.loads(raw)
        if not isinstance(sessions, list):
            raise ValueError("expected a JSON array")
    except (OSError, ValueError) as e:
        print(f"pool: cannot read the session list: {e}\nNEXT: save the list_sessions result to a file and pass --sessions FILE",
              file=sys.stderr)
        return 2
    p = plan(sessions, reserve=a.reserve, self_id=a.self_id, event_turn=a.event_turn, clean=set(a.clean),
             clean_fn=None if a.no_permanence_lookup else _default_clean, now=_ts(a.now) if a.now else None, tag=a.tag)
    print(json.dumps(p, indent=1, ensure_ascii=False) if a.json else render(p))
    return 0


if __name__ == "__main__":
    sys.exit(main())
