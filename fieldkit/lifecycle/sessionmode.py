"""The session mode (owner decision O4, 2026-11-22): how many agents a session may run, enforced at the tool layer.

The owner's five modes, word for word from the sabotage findings (sabotage-why-the-harness-failed.md):

    1  Economy         single agent, single model, zero sub-agents
    2  Time-Sensitive  max 6 total (3 agents and 3 sub-agents)
    3  Fast            max 1000 agents, sub-agents and swarms
    4  Single Player   (local active device only)  4a Economy, 4b Time-Sensitive (max 6), 4c Fast (max 1000)
    5  Multiplayer     (2 to 100 computers via a Zero coordinator)  5a Economy (zero sub-agents per node),
                       5b Time-Sensitive (max 6 per node), 5c Fast (max 1000)

Economy is the default: no mode file means Economy, cap 0, before the model's first turn. The choice is one file per
session, state/mode/<session>.json, written by `fieldkit route choose --session-mode CODE`. The file carries the caps,
so the PreToolUse hook (fieldkit/autopilot/toolrouter.py) reads one small JSON and needs no yaml and no lifecycle
import (hook speed bar). The caps come from routing.yaml `session_modes` when the file is written. A rotated session and
a peer session inherit the file (rotate.handover and the link `ask` verb carry `mode_record()`).

Stdlib only: this module runs inside the hook.
"""
import json
import os
import re
import time
from pathlib import Path

#: code -> (scope, speed). The caps themselves are data (routing.yaml session_modes); CAPS is the fallback the file
#: writer uses when the yaml cannot be read, and the test pins both to the owner's numbers.
MODES = {
    "1": ("any", "economy"), "2": ("any", "time-sensitive"), "3": ("any", "fast"),
    "4a": ("single", "economy"), "4b": ("single", "time-sensitive"), "4c": ("single", "fast"),
    "5a": ("multiplayer", "economy"), "5b": ("multiplayer", "time-sensitive"), "5c": ("multiplayer", "fast"),
}
CAPS = {"economy": 0, "time-sensitive": 6, "fast": 1000}
DEFAULT = "1"
QUESTION = "How would you like this session to be run?"
LABELS = {
    "1": "Economy: single agent, single model, zero sub-agents",
    "2": "Time-Sensitive: max 6 total (3 agents and 3 sub-agents)",
    "3": "Fast: max 1000 agents, sub-agents and swarms",
    "4": "Single Player (local active device only): 4a Economy, 4b Time-Sensitive (max 6 total), 4c Fast (max 1000)",
    "5": "Multiplayer (2 to 100 computers via a Zero coordinator): 5a Economy (single agent, single model per node, "
         "zero sub-agents), 5b Time-Sensitive (max 6 total per node), 5c Fast (max 1000)",
}
SAFE = re.compile(r"[^A-Za-z0-9._-]")


def mode_dir():
    env = os.environ.get("FIELDKIT_MODE_DIR")
    if env:
        return Path(env)
    from ..core import settings
    return Path(settings.state_root()) / "state" / "mode"


def session_id(call):
    """The session of a hook call: its session_id, else the transcript's file stem, else the environment."""
    sid = str((call or {}).get("session_id") or "")
    if not sid:
        sid = Path(str((call or {}).get("transcript_path") or "")).stem
    return SAFE.sub("_", sid or os.environ.get("CLAUDE_SESSION_ID", "") or "unknown")[:80]


def _path(sid, suffix=".json"):
    return mode_dir() / (SAFE.sub("_", sid) + suffix)


def record(code, caps=None):
    """-> the mode file's content for a code. caps maps speed -> total cap (routing.yaml session_modes)."""
    code = str(code).strip().lower()
    if code == "4":
        code = "4a"
    if code == "5":
        code = "5a"
    if code not in MODES:
        raise ValueError(f"session mode is one of {', '.join(MODES)}")
    scope, speed = MODES[code]
    cap = int((caps or CAPS).get(speed, CAPS[speed]))
    return {"mode": code, "scope": scope, "speed": speed, "cap": cap, "per_node": scope == "multiplayer",
            "at": time.strftime("%Y-%m-%d %H:%M:%S")}


def default_record():
    d = record(DEFAULT)
    d["default"] = True
    return d


def current(sid):
    """-> the session's mode record. A missing or unreadable file is Economy (fail closed: the cheapest mode)."""
    try:
        d = json.loads(_path(sid).read_text(encoding="utf-8"))
        if d.get("mode") in MODES and isinstance(d.get("cap"), int):
            return d
    except (OSError, ValueError, TypeError):
        pass
    return default_record()


def asked(sid):
    return _path(sid).exists()


def write(sid, code, caps=None):
    rec = record(code, caps)
    p = _path(sid)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(rec), encoding="utf-8")
    return rec


def spawned(sid):
    try:
        return int(_path(sid, ".spawns").read_text(encoding="utf-8").strip() or 0)
    except (OSError, ValueError):
        return 0


def count_spawn(sid):
    try:
        p = _path(sid, ".spawns")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(str(spawned(sid) + 1), encoding="utf-8")
    except OSError:
        pass


def inherit(src_record, sid):
    """A rotated or peer session starts under the same mode (section D of the findings): write the carried record."""
    code = (src_record or {}).get("mode")
    if code not in MODES:
        return None
    p = _path(sid)
    p.parent.mkdir(parents=True, exist_ok=True)
    rec = dict(src_record, inherited=True)
    p.write_text(json.dumps(rec), encoding="utf-8")
    return rec


def spawn_refusal(call):
    """-> the refusal text for an Agent/Task spawn the session's mode does not allow, or None. Counts an allowed spawn.
    An Explore sub-agent must run on Haiku (F2.3): any other model is refused."""
    ti = call.get("tool_input") or {}
    kind = str(ti.get("subagent_type") or "").strip().lower()
    model = str(ti.get("model") or "").strip().lower()
    if kind == "explore" and "haiku" not in model:
        return ("EXPLORE ON HAIKU ONLY (rules/CORE.md SUB-AGENTS): an Explore sub-agent is a search, and searches run "
                "on the cheapest model. Pass model \"haiku\", or search with the harness: "
                "`python -m fieldkit agent run pfind --input query=needle --input content=true` (text) or "
                "`python -m fieldkit size find --name '*.py' --paths .` (files).")
    sid = session_id(call)
    rec = current(sid)
    cap = int(rec["cap"])
    n = spawned(sid)
    if n >= cap:
        who = "zero sub-agents" if cap == 0 else f"at most {cap} in all"
        return (f"SESSION MODE {rec['mode']} ({rec['speed']}): {who}; {n} already spawned. Do the work yourself with "
                "the harness tools (pfind for text, `fieldkit size find` for files). Only the owner changes the mode: "
                "`python -m fieldkit route choose --session-mode 2|3|4a|4b|4c|5a|5b|5c` after they say so; never raise it "
                "yourself.")
    count_spawn(sid)
    return None
