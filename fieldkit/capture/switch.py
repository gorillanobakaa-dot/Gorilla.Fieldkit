"""`fieldkit capture on|off|state`: capture runs only while a lesson is being taught (owner, 2026-11-08).

Capturing every tool call of every session filled the store with ~50,000 items of which a few hundred became tools.
Capture is now switched on for one stated lesson and a fixed time, then it stops by itself:

    fieldkit capture on --lesson WHY [--for MINUTES]   hooks in (Claude Code, Gemini), switch.json says until when
    fieldkit capture off                                hooks out, the periodic scan disabled, switch.json says off
    fieldkit capture state [--json]                     on/off/expired, the lesson, the hooks and schedule found

Two layers, so a hook left behind costs nothing: `capture hook` reads switch.json first and records nothing unless
the switch is on and not expired; `off` also removes the hooks so no Python starts on every tool call.
A store with no switch.json behaves as before this file existed (on), so an older install is not silently cut off.
Existing hooks of other tools are never touched; ours are found by the text `capture hook`.
Exit: 0 fine, 1 a settings file could not be changed, 2 bad usage, 3 `state --check` found it on or a hook left.
"""
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import store as st

MARK = "capture hook"
MAX_MINUTES = 7 * 24 * 60   # owner 2026-11-10: one week; an extension only when the owner grants it
DEFAULT_MINUTES = 120
CLAUDE_TOOLS = "Bash|PowerShell|Write|Edit|MultiEdit|NotebookEdit"
EVENTS = {"claude": {"PostToolUse": CLAUDE_TOOLS, "Stop": None},
          "gemini": {"AfterTool": "run_shell_command|write_file|replace", "SessionEnd": None}}
WIN_TASK = "FieldkitCaptureScan"
WIN_RUN_KEY = r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run"
WIN_RUN_NAME = "FieldkitCaptureCatchup"
LINUX_TIMER = "fieldkit-capture.timer"


def switch_path():
    return st.capture_dir() / "switch.json"


def _now():
    return datetime.now(timezone.utc)


def read():
    p = switch_path()
    if not p.is_file():
        return {"on": True, "legacy": True}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {"on": False, "error": "switch.json is not an object"}
    except (OSError, ValueError) as exc:
        return {"on": False, "error": f"switch.json unreadable ({exc}): treated as off"}


def is_on(d=None, now=None):
    """True when capture hooks should record. Fails closed: an unreadable switch is off."""
    d = read() if d is None else d
    if not d.get("on"):
        return False
    until = d.get("until")
    if not until:
        return bool(d.get("legacy"))
    try:
        return (now or _now()) < datetime.fromisoformat(until)
    except ValueError:
        return False


def _write(d):
    p = switch_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(p)


# ---- agent settings (JSON) --------------------------------------------------------------------------------------
def settings_file(agent, home):
    return Path(home) / f".{agent}" / "settings.json"


def python_exe():
    exe = Path(sys.executable)
    if sys.platform.startswith("win") and exe.with_name("pythonw.exe").is_file():
        exe = exe.with_name("pythonw.exe")                    # no console window per tool call
    return str(exe)


def hook_command(agent, py=None):
    return f'"{py or python_exe()}" -m fieldkit {MARK} --agent {agent}'


def _load(p):
    if not p.is_file():
        return None
    d = json.loads(p.read_text(encoding="utf-8") or "{}")
    if not isinstance(d, dict):
        raise ValueError(f"{p} is not a JSON object")
    return d


def _ours(h):
    return isinstance(h, dict) and MARK in str(h.get("command", ""))


def count_hooks(d):
    return sum(1 for gs in (d.get("hooks") or {}).values() for g in gs or [] if isinstance(g, dict)
               for h in g.get("hooks") or [] if _ours(h))


def strip_hooks(d):
    """Remove our hooks; groups left empty go too. Returns how many were removed."""
    removed = 0
    for ev, gs in list((d.get("hooks") or {}).items()):
        kept = []
        for g in gs or []:
            if not isinstance(g, dict):
                kept.append(g)
                continue
            hs = [h for h in g.get("hooks") or [] if not _ours(h)]
            removed += len(g.get("hooks") or []) - len(hs)
            if hs:
                kept.append({**g, "hooks": hs})
        d["hooks"][ev] = kept
    return removed


def add_hooks(d, agent, py=None):
    """Add our hooks (idempotent). Returns how many were added."""
    if count_hooks(d):
        return 0
    hooks = d.setdefault("hooks", {})
    for ev, matcher in EVENTS[agent].items():
        g = {"hooks": [{"type": "command", "command": hook_command(agent, py), "timeout": 20}]}
        if matcher:
            g["matcher"] = matcher
        hooks.setdefault(ev, []).append(g)
    return len(EVENTS[agent])


def edit_settings(home, fn):
    """Apply fn(dict, agent) -> n to every agent's settings that exists; {agent: n or error}."""
    out = {}
    for agent in EVENTS:
        p = settings_file(agent, home)
        try:
            d = _load(p)
            if d is None:
                out[agent] = "no settings file"
                continue
            n = fn(d, agent)
            if n:
                tmp = p.with_suffix(".json.fieldkit-tmp")
                tmp.write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
                tmp.replace(p)
            out[agent] = n
        except (OSError, ValueError) as exc:
            out[agent] = f"ERROR {exc}"
    return out


# ---- the periodic scan (installed by `capture install`, if ever) ------------------------------------------------
def _run(argv):
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=30)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)


def schedule_off(run=_run, system=None):
    """Disable the periodic scan and the log-on catch-up where they exist. Returns {what: result}."""
    system = system or sys.platform
    out = {}
    if system.startswith("win"):
        rc, _ = run(["schtasks", "/query", "/tn", WIN_TASK])
        out["task"] = "absent" if rc else ("disabled" if run(["schtasks", "/change", "/tn", WIN_TASK, "/disable"])[0] == 0
                                           else "ERROR could not disable")
        rc, _ = run(["reg", "query", WIN_RUN_KEY, "/v", WIN_RUN_NAME])
        out["logon"] = "absent" if rc else ("removed" if run(["reg", "delete", WIN_RUN_KEY, "/v", WIN_RUN_NAME, "/f"])[0] == 0
                                            else "ERROR could not remove")
    elif system.startswith("linux"):
        rc, _ = run(["systemctl", "--user", "is-enabled", LINUX_TIMER])
        out["timer"] = "absent" if rc else ("disabled" if run(["systemctl", "--user", "disable", "--now", LINUX_TIMER])[0] == 0
                                            else "ERROR could not disable")
    else:
        out["schedule"] = "not-this-platform"
    return out


def schedule_state(run=_run, system=None):
    system = system or sys.platform
    if system.startswith("win"):
        rc, txt = run(["schtasks", "/query", "/tn", WIN_TASK, "/fo", "csv", "/nh"])
        task = "absent" if rc else ("disabled" if "Disabled" in txt else "enabled")
        logon = "absent" if run(["reg", "query", WIN_RUN_KEY, "/v", WIN_RUN_NAME])[0] else "present"
        return {"task": task, "logon": logon}
    if system.startswith("linux"):
        rc, txt = run(["systemctl", "--user", "is-enabled", LINUX_TIMER])
        return {"timer": txt.strip() if rc == 0 else "absent"}
    return {"schedule": "not-this-platform"}


# ---- commands -------------------------------------------------------------------------------------------------------
def turn_on(lesson, minutes=DEFAULT_MINUTES, home=None, py=None, now=None, owner=None, cap=MAX_MINUTES):
    """owner/cap: a build-scoped capture (fieldkit.capture.loop) names who switched it on and lasts as long as the run;
    a manual lesson keeps the one-week cap (owner 2026-11-10; was 24 h)."""
    if not (lesson or "").strip():
        raise ValueError("--lesson is required: say what the harness is being taught")
    if not 1 <= minutes <= cap:
        raise ValueError(f"--for must be 1..{cap} minutes")
    t = now or _now()
    d = {"on": True, "lesson": lesson.strip(), "since": t.isoformat(timespec="seconds"),
         "until": (t + timedelta(minutes=minutes)).isoformat(timespec="seconds")}
    if owner:
        d["owner"] = owner
    _write(d)
    d["hooks_added"] = edit_settings(home or Path.home(), lambda s, a: add_hooks(s, a, py))
    return d


def turn_off(home=None, run=_run, system=None, now=None):
    prev = read()
    d = {"on": False, "since": (now or _now()).isoformat(timespec="seconds"),
         "last_lesson": prev.get("lesson") or prev.get("last_lesson")}
    _write(d)
    d["hooks_removed"] = edit_settings(home or Path.home(), lambda s, a: strip_hooks(s))
    d["schedule"] = schedule_off(run, system)
    return d


def state(home=None, run=_run, system=None, now=None):
    d = read()
    hooks = {}
    for agent in EVENTS:
        try:
            s = _load(settings_file(agent, home or Path.home()))
            hooks[agent] = count_hooks(s) if s is not None else "no settings file"
        except (OSError, ValueError) as exc:
            hooks[agent] = f"ERROR {exc}"
    on = is_on(d, now)
    word = "on" if on else ("expired" if d.get("on") else "off")
    return {"capture": word, "switch": d, "hooks": hooks, "schedule": schedule_state(run, system)}


def _print(a, data, lines, nxt):
    data["next"] = nxt
    if a.json:
        print(json.dumps(data, indent=1, ensure_ascii=False))
    else:
        print("\n".join(lines))
        print(f"NEXT: {nxt}")


def _errors(res):
    return [f"{k}: {v}" for k, v in res.items() if isinstance(v, str) and v.startswith("ERROR")]


def cmd_on(a):
    try:
        d = turn_on(a.lesson, a.minutes)
    except ValueError as exc:
        print(f"capture on: {exc}", file=sys.stderr)
        return 2
    errs = _errors(d["hooks_added"])
    lines = [f"capture ON until {d['until']} for lesson: {d['lesson']}",
             "hooks added: " + ", ".join(f"{k} {v}" for k, v in d["hooks_added"].items())] + errs
    _print(a, d, lines, "new sessions capture; an open session picks the hooks up at its next start. "
                        "`fieldkit capture off` when the lesson is done")
    return 1 if errs else 0


def cmd_off(a):
    since = read().get("since")
    d = turn_off()
    errs = _errors(d["hooks_removed"]) + _errors(d["schedule"])
    lines = ["capture OFF", "hooks removed: " + ", ".join(f"{k} {v}" for k, v in d["hooks_removed"].items()),
             "schedule: " + ", ".join(f"{k} {v}" for k, v in d["schedule"].items())] + errs
    nxt = "nothing to do" if not errs else "fix the ERROR lines above"
    if getattr(a, "distill", False):
        from . import loop
        r = loop.distil(since, "manual")
        d["distil"] = r
        lines.append(f"distil: {r['episodes']} episode(s), {len(r['candidates'])} candidate tool(s), {len(r['ledger'])} ledger line(s)")
        nxt = "candidates in state/capture/candidates/ (fieldkit mutate, then fieldkit adopt); ledger lines in state/capture/agent-ledger.jsonl"
    _print(a, d, lines, nxt)
    return 1 if errs else 0


def cmd_state(a):
    d = state()
    left = sum(v for v in d["hooks"].values() if isinstance(v, int))
    sw = d["switch"]
    lines = [f"capture {d['capture'].upper()}" + (f" until {sw['until']} - lesson: {sw.get('lesson')}" if sw.get("until") else "")
             + (" (no switch.json: legacy, records as before)" if sw.get("legacy") else ""),
             "hooks: " + ", ".join(f"{k} {v}" for k, v in d["hooks"].items()),
             "schedule: " + ", ".join(f"{k} {v}" for k, v in d["schedule"].items())]
    nxt = ("nothing to do" if d["capture"] == "off" and not left else
           "`fieldkit capture off` (expired, hooks still in)" if d["capture"] == "expired" else
           "`fieldkit capture off` when the lesson is done" if d["capture"] == "on" else
           "`fieldkit capture off` to remove the hooks left behind")
    _print(a, d, lines, nxt)
    return 3 if a.check and (d["capture"] != "off" or left) else 0
