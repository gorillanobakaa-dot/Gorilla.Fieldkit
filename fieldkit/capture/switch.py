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
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import store as st

MARK = "capture hook"
MAX_MINUTES = 7 * 24 * 60   # owner 2026-11-10: one week; an extension only when the owner grants it
DEFAULT_MINUTES = 120
EXTEND_WITHIN_H = 12          # P26: under this many hours left, the owner is asked (never extended by a model)
CLAUDE_TOOLS = "Bash|PowerShell|Write|Edit|MultiEdit|NotebookEdit"
OPENCODE_TOOLS = ["bash", "write", "edit", "multiedit", "patch"]
EVENTS = {"claude": {"PostToolUse": CLAUDE_TOOLS, "Stop": None},
          "gemini": {"AfterTool": "run_shell_command|write_file|replace", "SessionEnd": None},
          # Gorilla OpenCode (P26): config.json "hooks" is a LIST of {event, tools, command, timeout_seconds}; an
          # after_tool hook gets the tool, its input (stdin) and result (GORILLA_HOOK_* environment), see capture/hook.py
          "opencode": {"after_tool": OPENCODE_TOOLS}}
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
    if agent == "opencode":
        import os
        xdg = os.environ.get("XDG_CONFIG_HOME")
        base = Path(xdg) if xdg and Path(home) == Path.home() else Path(home) / ".config"
        return base / "gorilla-opencode" / "config.json"
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
    if isinstance(d.get("hooks"), list):                      # Gorilla OpenCode
        return sum(1 for h in d["hooks"] if _ours(h))
    return sum(1 for gs in (d.get("hooks") or {}).values() for g in gs or [] if isinstance(g, dict)
               for h in g.get("hooks") or [] if _ours(h))


def strip_hooks(d):
    """Remove our hooks; groups left empty go too. Returns how many were removed."""
    removed = 0
    if isinstance(d.get("hooks"), list):                      # Gorilla OpenCode
        kept = [h for h in d["hooks"] if not _ours(h)]
        removed = len(d["hooks"]) - len(kept)
        d["hooks"] = kept
        return removed
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
    if agent == "opencode":
        d["hooks"] = list(d.get("hooks") or []) + [
            {"event": ev, "tools": list(tools), "command": hook_command(agent, py), "timeout_seconds": 20}
            for ev, tools in EVENTS[agent].items()]
        return len(EVENTS[agent])
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


SCAN_EVERY_MIN = 30
CAPTURE_JOB = {"task": WIN_TASK, "unit": LINUX_TIMER.removesuffix(".timer"), "label": "com.fieldkit.capture-scan",
               "args": ["-m", "fieldkit", "capture", "scan", "--scheduled"], "every": SCAN_EVERY_MIN,
               "what": "fieldkit capture scan: journal the work calls of agent transcripts that no hook captured"}


def schedule_on(system=None, python=None, root=None, home=None, apply_fn=None):
    """P26: the periodic `capture scan`, only while capture is on (turn_off removes it, the scan itself does nothing
    when the switch is off). Same mechanism as the net check and the hog watch: a systemd user timer, a Task Scheduler
    task, a LaunchAgent (fieldkit/zero/schedule.py). -> "installed" | "ERROR ..."."""
    if os.environ.get("FIELDKIT_CAPTURE_NO_SCHEDULE"):        # tests that run the real CLI against a temp HOME
        return "skipped (FIELDKIT_CAPTURE_NO_SCHEDULE)"
    try:
        from ..core import settings
        from ..zero import schedule
        steps = schedule.plan(platform=system, python=python or python_exe(), root=root or settings.state_root(),
                              home=home, job=CAPTURE_JOB)
        if not steps:
            return "not-this-platform"
        return "installed" if (apply_fn or (lambda x: schedule.apply(x, say=lambda *_: None)))(steps) == 0 else "ERROR install failed"
    except Exception as exc:  # noqa: BLE001 - a schedule problem never stops capture from switching on
        return f"ERROR {type(exc).__name__}: {str(exc)[:100]}"


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
def turn_on(lesson, minutes=DEFAULT_MINUTES, home=None, py=None, now=None, owner=None, cap=MAX_MINUTES, schedule=None):
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
    if schedule:                                              # the CLI passes schedule_on; tests and the build loop pass none
        d["schedule"] = schedule()
    return d


def extend(minutes=MAX_MINUTES, now=None, home=None, py=None, schedule=None):
    """The owner's yes to an extension (`fieldkit capture on --for N` with no --lesson): the same lesson and owner,
    a new end N minutes from now. Only on a switch that is on; hooks are re-added if missing."""
    cur = read()
    t = now or _now()
    if not is_on(cur, t) or cur.get("legacy"):
        raise ValueError("capture is not on: give --lesson WHY to switch it on")
    if not 1 <= minutes <= MAX_MINUTES:
        raise ValueError(f"--for must be 1..{MAX_MINUTES} minutes")
    d = {k: v for k, v in cur.items() if k not in ("until", "asked_until")}
    d["until"] = (t + timedelta(minutes=minutes)).isoformat(timespec="seconds")
    d["extended"] = t.isoformat(timespec="seconds")
    _write(d)
    d["hooks_added"] = edit_settings(home or Path.home(), lambda s, a: add_hooks(s, a, py))
    if schedule:
        d["schedule"] = schedule()
    return d


def extension_due(d=None, now=None):
    """-> hours left when a live capture has under EXTEND_WITHIN_H left, else None."""
    d = read() if d is None else d
    t = now or _now()
    if not is_on(d, t) or not d.get("until"):
        return None
    try:
        left = (datetime.fromisoformat(d["until"]) - t).total_seconds() / 3600
    except ValueError:
        return None
    return left if left < EXTEND_WITHIN_H else None


def ask_extension(now=None, add=None):
    """P26: a capture about to lapse is never extended or dropped silently. Under EXTEND_WITHIN_H h left, ONE owner
    inbox item (kind hardline, the only kind that reaches the owner) names the job and the owner's yes; asked once
    per end time (`asked_until` in switch.json). -> the hook line ("" when nothing is due). Never raises."""
    try:
        d = read()
        left = extension_due(d, now)
        if left is None:
            return ""
        job = d.get("owner") or d.get("lesson") or "a capture"
        until = d["until"]
        line = (f"CAPTURE: {job} ends {until} ({left:.0f} h left); the owner decides an extension: "
                f"`fieldkit capture on --for {MAX_MINUTES}` (never extend it yourself).")
        if d.get("asked_until") != until:
            if add is None:
                from ..autopilot import inbox
                add = inbox.add
            add(f"Extend capture for {job}?",
                f"Capture for {job} (lesson: {d.get('lesson')}) stops at {until}, {left:.0f} h from now, and the job is still "
                "running. Without a yes the lessons after that time are not recorded.",
                f"fieldkit capture on --for {MAX_MINUTES}", kind="hardline", plan="P26",
                what=f"The recording of {job} runs out in {left:.0f} hours.",
                if_approve="Capture runs another week from the moment you run the command.",
                if_leave="Capture stops at the end time; later procedures, errors and fixes of this job are not learned.")
            d["asked_until"] = until
            _write(d)
        return line
    except Exception:  # noqa: BLE001 - a courtesy line must never break the session's hook
        return ""


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
    return {"capture": word, "extension_due_h": extension_due(d, now), "switch": d, "hooks": hooks, "schedule": schedule_state(run, system)}


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
        if not a.lesson and read().get("on") and not read().get("legacy"):
            d = extend(a.minutes, schedule=schedule_on)    # the owner's yes to an extension: same lesson, new end
            d["lesson"] = d.get("lesson")
        else:
            d = turn_on(a.lesson, a.minutes, schedule=schedule_on)
    except ValueError as exc:
        print(f"capture on: {exc}", file=sys.stderr)
        return 2
    errs = _errors(d["hooks_added"]) + _errors({"scan timer": d.get("schedule", "")})
    lines = [f"capture ON until {d['until']} for lesson: {d['lesson']}",
             "hooks added: " + ", ".join(f"{k} {v}" for k, v in d["hooks_added"].items()),
             f"scan timer: {d.get('schedule', 'not installed')} (every {SCAN_EVERY_MIN} min while capture is on)"] + errs
    if d.get("extended"):
        lines[0] = f"capture EXTENDED until {d['until']} for lesson: {d['lesson']}"
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
    if d.get("extension_due_h") is not None:
        lines.append(ask_extension())
    nxt = ("nothing to do" if d["capture"] == "off" and not left else
           "`fieldkit capture off` (expired, hooks still in)" if d["capture"] == "expired" else
           "`fieldkit capture off` when the lesson is done" if d["capture"] == "on" else
           "`fieldkit capture off` to remove the hooks left behind")
    _print(a, d, lines, nxt)
    return 3 if a.check and (d["capture"] != "off" or left) else 0
