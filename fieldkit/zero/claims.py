# Version: 1.0.0 · updated 26-10-26
"""Ground claims, enforced: a write outside a station's ordered area, or on ground another station holds, is REFUSED
before it runs (Claude Code PreToolUse). The owner, 2026-10-26: "overwriting eachother's work, undoing eachother's
works ... someone has to be in command".

Rides on the tool router's PreToolUse hook (fieldkit/autopilot/toolrouter.py): one Python start per call, not two.
The decision reads three small local files and nothing else: no subprocess, no network, no git, no YAML parser
(measured in tests/test_zero_command.py, well under 100 ms):

    NET.yaml            only the line "net: open" (a closed net enforces nothing)
    net-claims.json     written by `fieldkit zero task / release / checkin --relieve` (fieldkit/zero/command.py):
                        {station: {order, area: [paths], at, objective, relief}} + "free" (always writable: the
                        coordination folder, temp/scratchpads, agent memory and notes)
    net-stations.json   the leases (a holder that signed off or is MISSING holds nothing)

What is judged: Edit / Write / MultiEdit / NotebookEdit (the file) and the git commands that change a tree or its
history (commit, cherry-pick, revert, merge, reset, rebase, checkout -b, switch, stash, restore, tag, branch -d,
worktree: room.SHELL_VERBS, at the folder they act in). Reads and every other command pass.

    1. ground held by another live station          -> REFUSED ("ground held by X, order O")
    2. the station has an order and the target is shared ground (room.SHARED_PATHS) outside its area -> REFUSED
    3. otherwise (no net, no claims file, no order, a malformed call, any error) -> allowed: the hook must never lock
       sessions out of normal work. It refuses and logs; it never asks anyone anything.

Areas are folders (a worktree is a branch: give each station its own worktree). A git command at a repository root
that CONTAINS the station's own area is its own (a station with area repo/sub commits at repo/).
"""
import json
import os
import re
import socket
import time
from pathlib import Path
from ..core.settings import state_root as _state_root

ROOT = Path(__file__).resolve().parents[2]
PENDING_TTL_S = 24 * 3600            # a pre-tasked station, or a rotated station's relief, has a day to check in
WRITE_TOOLS = {"edit", "write", "multiedit", "notebookedit"}
SHELL_TOOLS = {"bash", "powershell"}
GIT_VERBS = ("commit", "ff", "rebase")
#: the read-only forms the room's git verbs also match
GIT_READ = re.compile(r"\bgit\b[^|;\n]*\b(worktree\s+list|stash\s+(list|show)|tag\s+(-l|--list|--contains)|"
                      r"branch\s+(-[avr]+|--list)|tag\s*($|[|;&)]))", re.I)
OPEN = re.compile(r"^net:\s*open\s*$", re.M)


def coord():
    return Path(os.environ.get("FIELDKIT_COORDINATION_DIR") or (_state_root(ROOT) / "state" / "coordination"))


def norm(p, cwd=""):
    """One spelling per folder: absolute, forward slashes, no trailing slash, lower case on Windows; Git Bash /c/x is
    C:/x; ~ and $HOME are the home folder."""
    s = str(p or "").strip().strip("\"'")
    if not s or s == "-":
        return ""
    s = s.replace("%20", " ")
    if s.startswith("$HOME"):
        s = str(Path.home()) + s[5:]
    m = re.match(r"^/([A-Za-z])(/|$)", s)
    if m and os.name == "nt":
        s = f"{m.group(1).upper()}:/{s[3:]}"
    s = os.path.expanduser(s)
    if cwd and not os.path.isabs(s):
        s = os.path.join(norm(cwd) or cwd, s)
    s = os.path.normpath(os.path.abspath(s)).replace("\\", "/")
    s = s.lower() if os.name == "nt" else s
    return s if re.fullmatch(r"[a-z]:/|/", s) else s.rstrip("/")


def within(t, a):
    """t is a or under a."""
    return bool(t and a) and (t == a or t.startswith(a.rstrip("/") + "/"))


def overlap(a, b):
    return within(a, b) or within(b, a)


def misses(rec, now):
    lu, ttl = float(rec.get("lease_until") or 0), float(rec.get("ttl") or 1800)
    return 0 if now <= lu else 1 + int((now - lu) // ttl)


def live(station, claim, stations, now):
    """Does this claim hold ground now? Not when its station signed off or is MISSING (two missed check-ins) - unless
    it was tasked and has not checked in yet, or it rotated and its relief has not taken over yet (a day each)."""
    rec = stations.get(station)
    relief = float(claim.get("relief") or 0)
    if relief and now - relief < PENDING_TTL_S:
        return True
    if not rec or not rec.get("last"):
        return now - float(claim.get("at") or 0) < PENDING_TTL_S
    if rec.get("off"):
        return False
    return misses(rec, now) < 2


def _read_json(p):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def net_open(c=None):
    try:
        return bool(OPEN.search(((c or coord()) / "NET.yaml").read_text(encoding="utf-8")))
    except OSError:
        return False


def station_of(session_id, host=None):
    return f"claude:{(session_id or '?')[:8]}@{host or socket.gethostname()}"


def targets(call):
    """-> [(kind, normalized folder or file)] the call writes: ("edit", file) or ("git", folder)."""
    tool = (call.get("tool_name") or "").lower()
    ti = call.get("tool_input") or {}
    cwd = call.get("cwd") or ""
    if tool in WRITE_TOOLS:
        p = norm(ti.get("file_path") or ti.get("notebook_path"), cwd)
        return [("edit", p)] if p else []
    if tool in SHELL_TOOLS:
        cmd = ti.get("command") or ""
        if GIT_READ.search(cmd):
            return []
        from ..autopilot import room                  # stdlib only at import: json, os, re, sys, time, pathlib
        c = room.classify(call)
        if c and c[0] in GIT_VERBS:
            p = norm(c[1], cwd)
            return [("git", p)] if p else []
    return []


def decide(call, now=None, c=None, host=None):
    """-> None (allowed) or {"station", "target", "why", "holder", "order"} (REFUSED). Never raises."""
    try:
        now = time.time() if now is None else now
        c = c or coord()
        sid = call.get("session_id")
        if not sid or not net_open(c):
            return None
        claims = _read_json(c / "net-claims.json")
        if not claims or not claims.get("claims"):
            return None
        ts = targets(call)
        if not ts:
            return None
        stations = (_read_json(c / "net-stations.json") or {}).get("stations") or {}
        me = station_of(sid, host)
        free = [norm(f) for f in claims.get("free") or []]
        mine = claims["claims"].get(me) or {}
        own = [norm(a) for a in mine.get("area") or []]
        held = {s: x for s, x in claims["claims"].items() if s != me and live(s, x, stations, now)}
        for kind, t in ts:
            if any(within(t, f) for f in free):
                continue
            is_mine = any(within(t, a) or (kind == "git" and within(a, t)) for a in own)
            for s, x in held.items():
                for a in (norm(y) for y in x.get("area") or []):
                    if within(t, a) or (kind == "git" and within(a, t) and not is_mine):
                        return {"station": me, "target": t, "holder": s, "order": x.get("order"),
                                "why": f"ground held by {s} (order {x.get('order')}: {x.get('objective', '')[:120]})"}
            if own and not is_mine:
                from ..autopilot import room
                if room.SHARED_PATHS.search(t + "/"):
                    return {"station": me, "target": t, "holder": None, "order": mine.get("order"),
                            "why": f"outside your ordered area (order {mine.get('order')}: "
                                   f"{', '.join(mine.get('area') or [])})"}
        return None
    except Exception:  # noqa: BLE001 - fail open: an error never locks a session out
        return None


def message(r):
    return (f"GROUND CLAIM (fieldkit zero): REFUSED {r['target']}: {r['why']}. You are station {r['station']}. "
            "Do not work around it: tell Zero (fieldkit zero send --to ZERO \"need ...\") and carry on inside your "
            "own area; Zero re-tasks with fieldkit zero task / release.")


def refuse_logged(r, call, now=None):
    """The refusal goes on the net log (kind "refused"). Only on a refusal: the YAML-reading net code loads here,
    never on an allowed call. Never raises."""
    try:
        from . import net
        net.append("refused", r["station"], to=r.get("holder") or "*", text=r["why"][:300],
                   data={"target": r["target"], "tool": call.get("tool_name"), "order": r.get("order")}, now=now)
    except Exception:  # noqa: BLE001
        pass


def pretool(call, now=None):
    """The PreToolUse verdict: the deny JSON, or None. Called from toolrouter.hook."""
    r = decide(call, now)
    if not r:
        return None
    refuse_logged(r, call, now)
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": message(r)}}
