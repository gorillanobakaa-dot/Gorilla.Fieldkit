"""No archive shoots the command post in the foot (owner, 2026-10-31 19:1x, after Zero 16's archive_session "self"
also archived Zero 17, the session it had started: "TO MAKE SURE THE NESTED SUBSESSIONS are not archived as well";
then: "is good for the zero to have the capability to archive_session "blablah1", archive_session "blablah2" ... We
just need to make sure it does not shoot himself in the foot and it does not archive running sessions, newly started
sessions that are controling the other sessions like 0 sessions are. we definitely need a proper deterministic tool").

The tool router asks `verdict()` before every `archive_session` (self or an id). Refused when the target:

    ZERO      is the session that holds the Zero seat now (fieldkit zero), archived by another session (Zero archives
              itself only through fieldkit zero retire, zero/retire.py)
    RUNNING   was active in the last ACTIVE_MIN minutes (the app's own lastActivityAt), unless it is the caller itself
    NESTED    has side sessions it started that are neither detached nor archived: the app archives those with it
              (each one listed; detach_session ID or archive it on its own first)

Archiving finished sessions one by one stays allowed: that is the cleanup. The facts come from the desktop app's own
session records (claude-code-sessions/**/local_*.json: spawnedFrom, lineageDetached, isArchived, lastActivityAt,
cliSessionId); where there are none (another system, no app), from the caller's transcript (start_session results
without a detach_session after them).

    fieldkit sessions-nested check [SESSION_ID|self] [--transcript PATH]   the verdict (exit 3 when refused)
    fieldkit sessions-nested ok SESSION_ID          record a child that detached itself (transcript fallback only)

Zero tokens: plain file reading.
"""
import glob
import json
import os
import re
import sys
import time
from pathlib import Path

ARCHIVE = "archive_session"
STARTS = ("start_session", "hand_off_to_session")
DETACH = "detach_session"
SID = re.compile(r"\blocal_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b")
ACTIVE_MIN = 10


def _ok_dir():
    from ..core.settings import state_root
    return state_root(Path(__file__).resolve().parents[2]) / "state" / "lifecycle" / "detached"


# ------------------------------------------------------------------ the app's records

def records_glob():
    """Where the Claude desktop app keeps one JSON per Code session (Windows; elsewhere none is known: fallback)."""
    if sys.platform != "win32":
        return None
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return os.path.join(base, "Claude-Data", "*", "claude-code-sessions", "*", "*", "local_*.json")


def load_records(pattern=None):
    """-> {local id: record} of the app's sessions (empty when the app keeps none here)."""
    pattern = pattern or records_glob()
    out = {}
    for f in glob.glob(pattern) if pattern else []:
        try:
            r = json.loads(Path(f).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if r.get("sessionId"):
            out[r["sessionId"]] = r
    return out


def _parent(r):
    sf = r.get("spawnedFrom")
    return sf.get("sessionId") if isinstance(sf, dict) else None


def live_children(recs, sid):
    """-> the ids of sessions `sid` started that are neither detached nor archived (the app archives them with it)."""
    return sorted(k for k, r in recs.items() if _parent(r) == sid and not r.get("lineageDetached")
                  and not r.get("isArchived"))


def _by_cli(recs, cli):
    return next((k for k, r in recs.items() if cli and str(r.get("cliSessionId") or "") == str(cli)), None)


def _zero_cli():
    """-> the 8-character transcript id of the station holding the Zero seat (claude:XXXXXXXX@host), or None."""
    try:
        from ..zero import net
        st = str((net.load_net().get("zero") or {}).get("station") or "")
    except Exception:  # noqa: BLE001
        return None
    m = re.match(r"claude:([0-9a-f]{8})@", st)
    return m.group(1) if m else None


def verdict(target, caller_cli, recs, zero_cli=None, now=None):
    """-> [reason] that refuse archiving `target` ("self" or a local id) by the session whose transcript id is
    caller_cli; [] = allowed. Pure: every fact is passed in."""
    now = time.time() if now is None else now
    me = _by_cli(recs, caller_cli)
    tid = me if target == "self" else target
    r = recs.get(tid or "")
    if not r:
        return []                                     # not a session the app knows here: nothing to judge from
    why = []
    cli = str(r.get("cliSessionId") or "")
    if zero_cli and cli.startswith(zero_cli) and tid != me:
        why.append(f"ZERO: {tid} holds the Zero seat now ({r.get('title') or '?'}); Zero leaves only through "
                   "fieldkit zero retire")
    act = (r.get("lastActivityAt") or 0) / 1000
    if tid != me and act and now - act < ACTIVE_MIN * 60:
        why.append(f"RUNNING: {tid} ({r.get('title') or '?'}) was active {int((now - act) / 60)} min ago "
                   f"(< {ACTIVE_MIN}); archive only finished sessions")
    kids = live_children(recs, tid)
    if kids:
        names = ", ".join(f"{k} ({(recs[k].get('title') or '?')[:40]})" for k in kids)
        why.append(f"NESTED: archiving {tid} would also archive the side session(s) it started: {names}. "
                   "detach_session each one (or archive it on its own) first")
    return why


# ------------------------------------------------------------------ fallback: the caller's transcript

def _text(c):
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "\n".join(str(b.get("text") or "") for b in c if isinstance(b, dict))
    return ""


def children(transcript):
    """-> (started, detached): session ids this session started, and the ones it detached, from its transcript."""
    uses, started, detached = {}, [], set()
    try:
        lines = Path(transcript).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return [], set()
    for line in lines:
        try:
            e = json.loads(line)
        except ValueError:
            continue
        content = (e.get("message") or {}).get("content")
        for b in content if isinstance(content, list) else []:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_use":
                uses[b.get("id")] = (str(b.get("name") or ""), b.get("input") or {})
            elif b.get("type") == "tool_result" and b.get("tool_use_id") in uses and not b.get("is_error"):
                name, inp = uses[b["tool_use_id"]]
                out = _text(b.get("content"))
                if name.endswith(STARTS):
                    for s in SID.findall(out):
                        if s not in started:
                            started.append(s)
                elif name.endswith(DETACH):
                    target = str(inp.get("session_id") or "")
                    if target != "self" and ("top-level" in out.lower() or "detached" in out.lower()):
                        detached.add(target)
    return started, detached


def recorded_ok():
    d = _ok_dir()
    return {p.stem for p in d.glob("*.txt")} if d.is_dir() else set()


def nested(transcript, ok=None):
    started, detached = children(transcript)
    ok = recorded_ok() if ok is None else ok
    return [s for s in started if s not in detached and s not in ok]


# ------------------------------------------------------------------ the hook

def pretool(call, ok=None, recs=None, zero_cli=None, now=None):
    """PreToolUse: -> the refusal text for an archive_session call that would archive Zero, a running session or a
    session with live nested children; else None. Never raises (fails open: the hard lines are the safety layer)."""
    try:
        tool = str(call.get("tool_name") or "")
        if not tool.endswith(ARCHIVE):
            return None
        target = str((call.get("tool_input") or {}).get("session_id") or "")
        recs = load_records() if recs is None else recs
        if recs:
            why = verdict(target, call.get("session_id"), recs, _zero_cli() if zero_cli is None else zero_cli, now)
        elif target == "self":
            left = nested(call.get("transcript_path") or "", ok)
            why = [f"NESTED: archiving this session would also archive the side session(s) it started: "
                   f"{', '.join(left)}. detach_session each one first (one that detached itself: "
                   "fieldkit sessions-nested ok ID)"] if left else []
        else:
            why = []
        return ("ARCHIVE REFUSED (fieldkit sessions-nested): " + " | ".join(why)) if why else None
    except Exception:  # noqa: BLE001
        return None


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="fieldkit sessions-nested", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["check", "ok"])
    ap.add_argument("session", nargs="?", default="self")
    ap.add_argument("--transcript")
    ap.add_argument("--me", help="check: the caller's transcript id (default: the newest Claude Code session here)")
    a = ap.parse_args(argv)
    if a.action == "ok":
        if not SID.fullmatch(a.session or ""):
            print("fieldkit sessions-nested ok local_<uuid>", file=sys.stderr)
            return 2
        d = _ok_dir()
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{a.session}.txt").write_text("detached (recorded by fieldkit sessions-nested ok)\n", encoding="utf-8")
        print(f"recorded {a.session} as top-level\nNEXT: archive_session \"self\"")
        return 0
    why = (pretool({"tool_name": ARCHIVE, "tool_input": {"session_id": a.session}, "session_id": a.me,
                    "transcript_path": a.transcript or ""}) or "")
    print(why or f"archive_session {a.session}: allowed (not Zero, not running, nothing nested under it)")
    print("NEXT: " + ("do what the refusal says, then check again" if why else "archive it"))
    return 3 if why else 0
