"""Zero's read runs (plan P34, owner 2026-11-12): a strong model making a dozen read-only Bash calls to gather facts
pays for the whole conversation on every call. The room hook counts Zero's read-only Bash calls in a row and, past
`zero_reads.max_run` (models.yaml), says once per `every` more: delegate to a Haiku subagent, or run
`fieldkit sitrep gather`. Only Zero's own session is counted (another session is somebody else's job).

Cheap on purpose (it runs on every tool call): one small JSON file in the lifecycle state folder, no transcript read.
Any call that is not a read-only Bash call (an edit, a write, a Bash command that changes something, an Agent call:
the delegation itself) ends the run. Read / Grep / Glob calls are neutral: they neither count nor end it.

    note(call)       -> the line the hook adds, or ""; call is the hook's JSON ({session_id, tool_name, tool_input})
    is_read_only(cmd) -> True when every part of a (compound) command only reads
"""
import json
import re

NEUTRAL = ("Read", "Grep", "Glob", "TodoWrite", "ToolSearch")
SPLIT = re.compile(r"\s*(?:&&|\|\||;|\|)\s*")


def cfg():
    from . import models
    c = dict(models.load().get("zero_reads") or {})
    c.setdefault("max_run", 4)
    c.setdefault("every", 5)
    return c


def is_read_only(cmd):
    """Every part of `cmd` (split on && || ; |) only reads: a `cd` or an `echo` is neutral, anything else must match
    refusals_policy.yaml `readonly`. A heredoc, a redirect or a command substitution is not read-only."""
    c = (cmd or "").strip()
    if not c or "<<" in c or re.search(r"[^>]>[^&>]|>>|\$\(|`", c):
        return False
    parts = [p for p in SPLIT.split(c) if p]
    if not parts:
        return False
    from .. import refusals
    seen = False
    for p in parts:
        if re.match(r"(cd|echo|true)(\s|$)", p):
            continue
        if not refusals.is_readonly(p):
            return False
        seen = True
    return seen


def _file():
    from .friction import state_dir
    return state_dir() / "readrun.json"


def _load():
    try:
        return json.loads(_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(d):
    p = _file()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d), encoding="utf-8")


def is_zero(session_id):
    try:
        from ..zero import net
        z = net.zero_of(net.load_net()) or ""            # a station name: claude:SESSION8@machine
        return bool(session_id) and bool(z) and net.hook_station(session_id) == z
    except Exception:  # noqa: BLE001 - no net, no Zero: nothing is counted
        return False


FIRST = {"ls", "cat", "head", "tail", "grep", "rg", "find", "wc", "stat", "pwd", "which", "git", "fieldkit", "gh",
         "du", "df", "ps", "file", "diff", "sort", "uniq", "jq", "sed", "awk", "tree", "echo", "cd"}
LEAD_CD = re.compile(r"^\s*cd\s+\S+\s*&&\s*")


def _maybe_read(cmd):
    """Cheap prefilter (no YAML): the first word of the command, after a leading `cd X &&`, is a reader's name."""
    w = LEAD_CD.sub("", cmd or "", count=1).split(None, 1)
    return bool(w) and w[0].rsplit("/", 1)[-1] in FIRST


def note(call, zero=None, c=None):
    """-> the hook line when Zero has made too many read-only Bash calls in a row, else "". Never raises.
    Loads no YAML until a run is about to speak (the hook must stay fast)."""
    try:
        sid = call.get("session_id") or ""
        tool = call.get("tool_name") or ""
        if not sid or tool in NEUTRAL:
            return ""
        cmd = (call.get("tool_input") or {}).get("command", "") if tool == "Bash" else ""
        read = tool == "Bash" and _maybe_read(cmd)
        if not read:
            d = _load()
            if (d.get(sid) or {}).get("n"):
                _save({sid: {"n": 0}})
            return ""
        if not (is_zero(sid) if zero is None else zero):
            return ""
        d = _load()
        n = int((d.get(sid) or {}).get("n", 0))
        if n < 3 and not is_read_only(cmd):             # cheap path: a run that cannot speak yet
            if n:
                _save({sid: {"n": 0}})
            return ""
        n += 1
        if n > 3:
            if not is_read_only(cmd):
                _save({sid: {"n": 0}})
                return ""
            c = c or cfg()
        else:
            c = c or {"max_run": 4, "every": 5}
        _save({sid: {"n": n}})                           # one session at a time: the file never grows
        mx, every = int(c["max_run"]), max(1, int(c["every"]))
        if n > mx and (n - mx - 1) % every == 0:
            return (f"ZERO READ RUN: {n} read-only Bash calls in a row, each one re-sends the whole conversation to "
                    "this model. Delegate the gathering to a haiku subagent (Agent model=haiku), or for a status run "
                    "`fieldkit sitrep gather --render`.")
        return ""
    except Exception:  # noqa: BLE001 - a counter never breaks a tool call
        return ""
