"""`fieldkit capture install [--check] [--dry-run]`: wire capture in so no agent has to remember a command.

Idempotent. Per agent, where it has a hook or plugin mechanism:
  claude    ~/.claude/settings.json          hooks.PostToolUse + hooks.Stop      -> `fieldkit capture hook --agent claude`
  gemini    ~/.gemini/settings.json          hooks.AfterTool + hooks.SessionEnd  (Gemini CLI 0.26+; an older Gemini has no
                                             hooks: the periodic `capture scan` reads its transcripts instead)
  opencode  ~/.config/opencode/plugin/fieldkit-capture.js   tool.execute.after + session.idle plugin
A periodic `capture scan` (every 30 min, plus once at start-up/log-on as the catch-up) by each system's own mechanism:
  Windows   Task Scheduler (FieldkitCaptureScan every 30 min) + an HKCU Run value for the log-on catch-up
  Linux     systemd --user timer (fieldkit-capture.timer); cron when there is no systemd user session
  macOS     launchd LaunchAgent (~/Library/LaunchAgents/dev.fieldkit.capture.plist)
Existing hooks are never touched: ours is added beside them, identified by the text `capture hook`.
Exit: 0 everything installed (or just made so), 3 --check found something missing, 1 could not install.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

MARK = "capture hook"
SCAN_MARK = "capture scan"
EVERY_MIN = 30
TASKS = ("FieldkitCaptureScan",)
RUN_KEY = r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "FieldkitCaptureCatchup"     # log-on catch-up: an HKCU Run value (schtasks ONLOGON needs administrator)
PLIST = "dev.fieldkit.capture.plist"
PLUGIN = "fieldkit-capture.js"
CLAUDE_TOOLS = "Bash|PowerShell|Write|Edit|MultiEdit|NotebookEdit"
# agent -> {hook event: matcher or None}
EVENTS = {"claude": {"PostToolUse": CLAUDE_TOOLS, "Stop": None},
          "gemini": {"AfterTool": "run_shell_command|write_file|replace", "SessionEnd": None}}


def python_exe(system=None):
    system = system or sys.platform
    exe = Path(sys.executable)
    if system.startswith("win"):
        w = exe.with_name("pythonw.exe")                     # no console window per tool call
        return str(w if w.is_file() else exe)
    return str(exe)


def quote(p):
    return f'"{p}"' if " " in p or "\\" in p else p


def hook_command(agent, py=None):
    return f"{quote(py or python_exe())} -m fieldkit {MARK} --agent {agent}"


def scan_argv(py=None):
    return [py or python_exe(), "-m", "fieldkit", "capture", "scan"]


def settings_path(agent, home):
    return {"claude": home / ".claude" / "settings.json", "gemini": home / ".gemini" / "settings.json",
            "opencode": home / ".config" / "opencode" / "plugin" / PLUGIN}[agent]


# ---- JSON settings (Claude Code, Gemini) -------------------------------------------------------------------------
def _load(p):
    if not p.is_file():
        return {}, None
    try:
        d = json.loads(p.read_text(encoding="utf-8") or "{}")
    except ValueError as exc:
        return None, f"{p} is not valid JSON ({exc}); left untouched"
    return (d, None) if isinstance(d, dict) else (None, f"{p} is not a JSON object; left untouched")


def _has(entries, agent):
    for e in entries or []:
        for h in (e.get("hooks") or []) if isinstance(e, dict) else []:
            if isinstance(h, dict) and MARK in str(h.get("command", "")) and f"--agent {agent}" in str(h.get("command")):
                return True
    return False


def json_missing(agent, home):
    """-> events of this agent with no capture hook in its settings file."""
    d, err = _load(settings_path(agent, home))
    if d is None:
        return list(EVENTS[agent])
    hooks = d.get("hooks") if isinstance(d.get("hooks"), dict) else {}
    return [ev for ev in EVENTS[agent] if not _has(hooks.get(ev), agent)]


def install_json(agent, home, py=None, dry=False):
    p = settings_path(agent, home)
    d, err = _load(p)
    if d is None:
        return False, err
    missing = json_missing(agent, home)
    if not missing:
        return True, f"{agent}: already installed in {p}"
    hooks = d.setdefault("hooks", {}) if isinstance(d.get("hooks", {}), dict) else None
    if hooks is None:
        return False, f"{p}: `hooks` is not an object; left untouched"
    for ev in missing:
        entry = {"hooks": [{"type": "command", "command": hook_command(agent, py), "timeout": 20}]}
        if EVENTS[agent][ev]:
            entry["matcher"] = EVENTS[agent][ev]
        if not isinstance(hooks.get(ev), list):
            hooks[ev] = []
        hooks[ev].append(entry)
    if not dry:
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.is_file():
            shutil.copy2(p, p.with_name(p.name + ".fieldkit-bak"))   # the user's file, kept as it was
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, p)
    return True, f"{agent}: added {', '.join(missing)} to {p}"


# ---- OpenCode plugin ---------------------------------------------------------------------------------------------
def plugin_source(py=None):
    py = py or python_exe()
    return f"""// fieldkit capture: sends every finished tool call and each idle session to `fieldkit {MARK}`.
// Written by `fieldkit capture install`; safe to delete (re-created by the next install).
import {{ spawnSync }} from "node:child_process"
const PY = {json.dumps(py)}
const send = (payload) => {{
  try {{
    spawnSync(PY, ["-m", "fieldkit", "capture", "hook", "--agent", "opencode"],
      {{ input: JSON.stringify(payload), timeout: 20000, windowsHide: true }})
  }} catch (_) {{}}
}}
export const FieldkitCapture = async ({{ directory }}) => ({{
  "tool.execute.after": async (input, output) => {{
    send({{ hook_event_name: "PostToolUse", tool_name: input.tool, session_id: input.sessionID, cwd: directory,
      tool_input: input.args ?? output?.args ?? {{}}, tool_response: {{ stdout: output?.output ?? "", title: output?.title }} }})
  }},
  event: async ({{ event }}) => {{
    if (event?.type === "session.idle")
      send({{ hook_event_name: "Stop", session_id: event.properties?.sessionID, cwd: directory }})
  }},
}})
"""


def plugin_ok(home):
    p = settings_path("opencode", home)
    try:
        t = p.read_text(encoding="utf-8")
    except OSError:
        return False
    return f"capture\", \"hook\", \"--agent\", \"opencode" in t


def install_opencode(home, py=None, dry=False):
    p = settings_path("opencode", home)
    if plugin_ok(home):
        return True, f"opencode: already installed in {p}"
    if not dry:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(plugin_source(py), encoding="utf-8")
    return True, f"opencode: wrote plugin {p}"


# ---- schedulers --------------------------------------------------------------------------------------------------
def _run(argv, **kw):
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=60, **kw)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(argv, 127, "", str(exc))


def win_task_argv(name, trigger, py=None):
    cmd = " ".join(quote(x) for x in scan_argv(py))
    return ["schtasks", "/Create", "/F", "/TN", name, "/TR", cmd, "/RL", "LIMITED"] + trigger


def win_triggers():
    return {"FieldkitCaptureScan": ["/SC", "MINUTE", "/MO", str(EVERY_MIN)]}


def win_runkey_argv(py=None):
    return ["reg", "add", RUN_KEY, "/v", RUN_NAME, "/t", "REG_SZ", "/f", "/d", " ".join(quote(x) for x in scan_argv(py))]


def systemd_units(py=None):
    exec_ = " ".join(quote(x) for x in scan_argv(py))
    svc = f"[Unit]\nDescription=fieldkit capture scan (zero-loss capture catch-up)\n\n[Service]\nType=oneshot\nExecStart={exec_}\n"
    tim = (f"[Unit]\nDescription=fieldkit capture scan every {EVERY_MIN} minutes and at start-up\n\n[Timer]\n"
           f"OnBootSec=2min\nOnUnitActiveSec={EVERY_MIN}min\nPersistent=true\n\n[Install]\nWantedBy=timers.target\n")
    return svc, tim


def plist(py=None):
    args = "".join(f"    <string>{x}</string>\n" for x in scan_argv(py))
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
            '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n<plist version="1.0">\n<dict>\n'
            f'  <key>Label</key><string>dev.fieldkit.capture</string>\n  <key>ProgramArguments</key>\n  <array>\n{args}  </array>\n'
            f'  <key>StartInterval</key><integer>{EVERY_MIN * 60}</integer>\n  <key>RunAtLoad</key><true/>\n</dict>\n</plist>\n')


def sched_state(system, home, run=_run):
    """-> (installed?, how). Reads the system's own scheduler, not our own notes."""
    if system.startswith("win"):
        have = [t for t in TASKS if run(["schtasks", "/Query", "/TN", t]).returncode == 0]
        runkey = run(["reg", "query", RUN_KEY, "/v", RUN_NAME]).returncode == 0
        return len(have) == len(TASKS) and runkey, "Task Scheduler " + ("+".join(have) or "none") + (" + Run key" if runkey else "")
    if system == "darwin":
        p = home / "Library" / "LaunchAgents" / PLIST
        return p.is_file(), f"launchd {p}"
    unit = home / ".config" / "systemd" / "user" / "fieldkit-capture.timer"
    if unit.is_file():
        return True, f"systemd --user {unit.name}"
    cron = run(["crontab", "-l"])
    if cron.returncode == 0 and SCAN_MARK in (cron.stdout or ""):
        return True, "cron"
    return False, "no systemd timer or cron entry"


def install_sched(system, home, py=None, dry=False, run=_run):
    ok, how = sched_state(system, home, run)
    if ok:
        return True, f"schedule: already installed ({how})"
    if dry:
        return True, "schedule: would install"
    if system.startswith("win"):
        for name, trig in win_triggers().items():
            r = run(win_task_argv(name, trig, py))
            if r.returncode != 0:
                return False, f"schedule: schtasks {name} failed: {(r.stderr or r.stdout).strip()[-200:]}"
        r = run(win_runkey_argv(py))
        if r.returncode != 0:
            return False, f"schedule: Run key failed: {(r.stderr or r.stdout).strip()[-200:]}"
        return True, "schedule: Task Scheduler " + ", ".join(TASKS) + " + HKCU Run " + RUN_NAME
    if system == "darwin":
        p = home / "Library" / "LaunchAgents" / PLIST
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(plist(py), encoding="utf-8")
        run(["launchctl", "load", "-w", str(p)])
        return True, f"schedule: launchd {p}"
    d = home / ".config" / "systemd" / "user"
    svc, tim = systemd_units(py)
    if shutil.which("systemctl"):
        d.mkdir(parents=True, exist_ok=True)
        (d / "fieldkit-capture.service").write_text(svc, encoding="utf-8")
        (d / "fieldkit-capture.timer").write_text(tim, encoding="utf-8")
        run(["systemctl", "--user", "daemon-reload"])
        r = run(["systemctl", "--user", "enable", "--now", "fieldkit-capture.timer"])
        if r.returncode == 0:
            return True, "schedule: systemd --user fieldkit-capture.timer"
        for f in ("fieldkit-capture.service", "fieldkit-capture.timer"):   # no user session: fall back to cron, tidy ours
            try:
                (d / f).unlink()
            except OSError:
                pass
    cur = run(["crontab", "-l"])
    lines = (cur.stdout or "").rstrip("\n") if cur.returncode == 0 else ""
    new = (lines + "\n" if lines else "") + f"*/{EVERY_MIN} * * * * {' '.join(quote(x) for x in scan_argv(py))}\n" \
        f"@reboot {' '.join(quote(x) for x in scan_argv(py))}\n"
    r = run(["crontab", "-"], input=new)
    return (r.returncode == 0), ("schedule: cron" if r.returncode == 0 else f"schedule: crontab failed: {r.stderr.strip()[-200:]}")


# ---- the whole -----------------------------------------------------------------------------------------------------
def check(home=None, system=None, run=_run):
    """-> {agent: [what is missing]} plus 'schedule'; empty lists = fine. Agents that are not installed on this machine
    (no ~/.claude, ~/.gemini, ~/.config/opencode folder) are 'absent', not missing."""
    home = Path(home) if home else Path.home()
    system = system or sys.platform
    out = {}
    for agent, folder in (("claude", ".claude"), ("gemini", ".gemini"), ("opencode", ".config/opencode")):
        if not (home / folder).is_dir():
            out[agent] = None
        elif agent == "opencode":
            out[agent] = [] if plugin_ok(home) else ["plugin"]
        else:
            out[agent] = json_missing(agent, home)
    out["schedule"] = [] if sched_state(system, home, run)[0] else ["scheduler"]
    return out


def install(home=None, system=None, py=None, dry=False, run=_run):
    home = Path(home) if home else Path.home()
    system = system or sys.platform
    results = []
    for agent in ("claude", "gemini", "opencode"):
        folder = home / (".config/opencode" if agent == "opencode" else f".{agent}")
        if agent != "claude" and not folder.is_dir():
            results.append((True, f"{agent}: not installed on this computer (the periodic scan covers its transcripts if it appears)"))
            continue
        results.append(install_opencode(home, py, dry) if agent == "opencode" else install_json(agent, home, py, dry))
    results.append(install_sched(system, home, py, dry, run))
    return results


def main(argv=None):
    ap = argparse.ArgumentParser(prog="fieldkit capture install", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="change nothing; exit 3 when a hook or the schedule is missing")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--home", help="a fake HOME (tests)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    home = Path(a.home) if a.home else None
    if not a.check:
        res = install(home, dry=a.dry_run)
        for ok, msg in res:
            print(("OK   " if ok else "FAIL ") + msg)
        if not all(ok for ok, _ in res):
            print("NEXT: fix the FAIL line above and run `fieldkit capture install` again")
            return 1
    chk = check(home)
    missing = {k: v for k, v in chk.items() if v}
    if a.json:
        print(json.dumps({"check": chk, "ok": not missing}, indent=1))
    else:
        for k, v in chk.items():
            print(f"{k}: " + ("absent on this computer" if v is None else "installed" if not v else "MISSING " + ", ".join(v)))
        print("NEXT: " + ("nothing to do" if not missing else "`fieldkit capture install`"))
    return 3 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
