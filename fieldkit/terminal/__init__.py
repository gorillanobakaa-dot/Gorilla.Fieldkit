"""Open a terminal window in front of a person, with a command ready, on Linux, Windows or macOS.

    fieldkit terminal doctor                 which terminal program this desktop has, and whether a window can open
    fieldkit terminal ask -- COMMAND ...     open a window, show the command, wait for Enter, run it, keep the window
    fieldkit terminal run -- COMMAND ...     open a window and run the command at once, keep the window

# WHY THIS EXISTS

The owner, 2026-10-14: "some of these cabbages won't even know where or how to open the terminal,
both on Windows and on Linux, so we might as well open it for them". A command handed to a
person already carries its full path (settings.self_cmd); this removes the step before it: finding
the terminal. `ask` is for a command that is a person's decision (an approve, an install): the
window opens, the command is printed, nothing happens until they press Enter, and closing the
window is a refusal. `run` is for a command they asked for already.

The window is created by the desktop (gnome-terminal and the others on Linux, `start` on Windows,
the Terminal app on macOS), so it outlives the session that opened it, like buildh/window.py does
for long builds. Everything the command prints also goes to state/terminal/<stamp>.log.

# WHAT IT REFUSES

No desktop session (no DISPLAY or WAYLAND_DISPLAY on Linux): refused, with the command printed so
the person can be told it another way. No terminal program found: refused and the doctor names
the ones it looked for. It never runs a command with no window: that would be a hidden action.
"""
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

from fieldkit.core import settings

# Linux terminal programs and how each takes a command. Order within a desktop family first, then the rest.
LINUX_TERMINALS = [
    ("gnome-terminal", lambda title, script: ["gnome-terminal", "--title", title, "--", "bash", "-c", script]),
    ("xfce4-terminal", lambda title, script: ["xfce4-terminal", "--title", title, "-e", "bash -c " + shlex.quote(script)]),
    ("konsole", lambda title, script: ["konsole", "-p", f"tabtitle={title}", "-e", "bash", "-c", script]),
    ("mate-terminal", lambda title, script: ["mate-terminal", "--title", title, "--", "bash", "-c", script]),
    ("tilix", lambda title, script: ["tilix", "-t", title, "-e", "bash -c " + shlex.quote(script)]),
    ("kitty", lambda title, script: ["kitty", "--title", title, "bash", "-c", script]),
    ("alacritty", lambda title, script: ["alacritty", "--title", title, "-e", "bash", "-c", script]),
    ("foot", lambda title, script: ["foot", "--title", title, "bash", "-c", script]),
    ("xterm", lambda title, script: ["xterm", "-T", title, "-e", "bash", "-c", script]),
    ("x-terminal-emulator", lambda title, script: ["x-terminal-emulator", "-e", "bash", "-c", script]),
]
DESKTOP_FIRST = {"GNOME": "gnome-terminal", "UBUNTU": "gnome-terminal", "XFCE": "xfce4-terminal", "KDE": "konsole",
                 "MATE": "mate-terminal"}



def split_args(rest, title=None):
    """The command after `ask`/`run`, with a leading `--title X` taken as the title and a lone `--` dropped.
    2026-10-14: argparse's REMAINDER swallowed `--title` given after the action, and the window ran
    `--title` as a program ("command not found"). Options may now come before or after the action."""
    rest = list(rest)
    while rest and rest[0] == "--":
        rest.pop(0)
    while len(rest) >= 2 and rest[0] == "--title":
        title = rest[1]; rest = rest[2:]
        while rest and rest[0] == "--":
            rest.pop(0)
    return [c for c in rest if c != "--"], title

def log_path(now=None):
    d = settings.ROOT / "state" / "terminal"
    d.mkdir(parents=True, exist_ok=True)
    # 2026-10-14: strftime refuses None; the first real run crashed here while the test had stubbed this function out
    stamp = time.strftime("%Y%m%d-%H%M%S", now) if now else time.strftime("%Y%m%d-%H%M%S")
    return d / f"{stamp}.log"


def pick_linux(which=None, env=None):
    """-> (name, builder) for this desktop, or (None, None). $TERMINAL first, then the desktop's own, then any."""
    which = which or __import__("shutil").which
    env = os.environ if env is None else env
    table = dict(LINUX_TERMINALS)
    wanted = os.path.basename(env.get("TERMINAL", "") or "")
    order = ([wanted] if wanted in table else []) + \
            [DESKTOP_FIRST[k] for k in (env.get("XDG_CURRENT_DESKTOP", "") or "").upper().split(":") if k in DESKTOP_FIRST] + \
            [n for n, _ in LINUX_TERMINALS]
    for name in order:
        if which(name):
            return name, table[name]
    return None, None


def has_display(env=None):
    env = os.environ if env is None else env
    return bool(env.get("DISPLAY") or env.get("WAYLAND_DISPLAY"))


DEFAULT_SAY = "A terminal window has opened for you. Read its first line."


def say_settings(local=None):
    """-> (always: bool, line: str). fieldkit.local.json: {"terminal": {"say": "the words Amy shouts",
    "always_say": true}}. The owner, 2026-10-14: "we can get Amy to shout at them". The words are the
    owner's to choose per machine; the default names the thing to look at and nothing else."""
    local = settings.local_settings() if local is None else local
    t = (local.get("terminal") or {}) if isinstance(local, dict) else {}
    return bool(t.get("always_say")), str(t.get("say") or DEFAULT_SAY)


def bash_script(command, mode, log, title=None, say=False):
    """What the window runs. Shaped for a reader with ten seconds (owner, 2026-10-14): the ONE thing to do is the
    first line on the screen, the command is the second, nothing before them; after the run the LAST thing on the
    screen is a one-line verdict and the one thing to do next. `ask`: wait for Enter, then run; `run`: run at once.
    The person's acts, the timings and the exit go to the log ("forensic value"). `say` speaks the first line
    through the harness's herald when the machine has a voice."""
    q = shlex.quote
    cmd = " ".join(q(a) for a in command) if not isinstance(command, str) else command
    short = (title or cmd)[:60]
    stamp = 'date "+%Y-%m-%d %H:%M:%S"'
    lines = [f"log={q(str(log))}",
             f"trap 'echo \"window closed by the person at $({stamp}) without answering the prompt\" >> \"$log\"' EXIT",
             f"echo \"window opened at $({stamp}) mode={mode} command: {cmd}\" >> \"$log\"",
             "clear 2>/dev/null"]
    first = "PRESS ENTER to run it. Close this window to say no." if mode == "ask" else "Running it now. Wait for the last line."
    if say:
        words = say if isinstance(say, str) else say_settings()[1]
        # The herald is DETACHED from the window (nohup): the voice takes seconds, and a quick person who closed the
        # window killed it with the terminal's hang-up before it could speak or write (2026-10-14, the 17:22 window:
        # allowed at :10, closed at :14, no herald line). "asked" is written at once; the herald's own verdict
        # (spoken, or why not) follows when it is done, so the record says whether anyone was called.
        lines.append(f"echo \"herald asked at $({stamp}) to say: {words}\" >> \"$log\"")
        lines.append(f"nohup {q(sys.executable)} -m fieldkit.buildh.herald --say {q(words)} >> \"$log\" 2>&1 </dev/null &")
    lines += [f"printf '\\n  %s\\n' {q(first)}",
              f"printf '  %s\\n\\n' {q(short)}",
              f"printf '    %s\\n\\n' {q(cmd)}"]
    if mode == "ask":
        lines.append("read -r _ || exit 130")
        lines.append(f"echo \"run allowed by the person at $({stamp})\" >> \"$log\"")
        # the person pressed Enter: from here the command is theirs, so the agent markers the window inherited from
        # the session that opened it are dropped (2026-10-23: release-publish send refused the owner's own Enter as
        # "agent environment (CLAUDECODE)"). `run` keeps them: nobody chose to run that command.
        from ..buildh.task import AGENT_ENV
        lines.append("unset " + " ".join(AGENT_ENV))
    lines += ["t0=$(date +%s)", f"echo \"command started at $({stamp})\" >> \"$log\"",
              f"{cmd} 2>&1 | tee -a \"$log\"", "code=${PIPESTATUS[0]}",
              f"echo \"command finished at $({stamp}) after $(( $(date +%s) - t0 )) s\" >> \"$log\"",
              "echo \"exit $code\" >> \"$log\"",
              "if [ \"$code\" = 0 ]; then verdict='DONE. It worked.'; else verdict=\"FAILED (exit $code). The harness has the log.\"; fi",
              "printf '\\n  %s\\n  PRESS ENTER to close this window.\\n' \"$verdict\"",
              "read -r _",
              "trap - EXIT",
              f"echo \"closed by the person at $({stamp})\" >> \"$log\""]
    return "\n".join(lines) + "\n"


def powershell_script(command, mode, log, title=None):
    cmd = " ".join(command) if not isinstance(command, str) else command
    logq = str(log).replace("'", "''")
    titleq = (title or "Fieldkit").replace("'", "''")
    cmdq = cmd.replace("'", "''")
    ps = [
        f"$host.UI.RawUI.WindowTitle = '{titleq}'",
        "[Console]::OutputEncoding = [Text.Encoding]::UTF8",
        "$env:PYTHONUNBUFFERED = '1'",
        "$env:PYTHONUTF8 = '1'",
        f"Set-Location '{str(settings.ROOT).replace(chr(39), chr(39) * 2)}'",
        f"Add-Content -Path '{logq}' -Value ('window opened at ' + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + ' mode={mode} command: {cmdq}') -Encoding UTF8",
        "Write-Host ''",
        "Write-Host '  The command:'",
        "Write-Host ''",
        f"Write-Host '    {cmdq}'",
        "Write-Host ''",
    ]
    if mode == "ask":
        ps.append("Read-Host '  Press Enter to run it. Close this window to refuse' | Out-Null")
        ps.append(f"Add-Content -Path '{logq}' -Value ('run allowed by the person at ' + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss')) -Encoding UTF8")
    ps += [
        f"& {{ {cmd} }} 2>&1 | ForEach-Object {{ $s = \"$_\"; Add-Content -Path '{logq}' -Value $s -Encoding UTF8; $s }}",
        "$code = $LASTEXITCODE",
        f"Add-Content -Path '{logq}' -Value ('exit ' + $code) -Encoding UTF8",
        "Write-Host ''",
        "Write-Host ('  Finished (exit ' + $code + ').')",
        "Read-Host '  Press Enter to close this window' | Out-Null",
        f"Add-Content -Path '{logq}' -Value ('closed by the person at ' + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss')) -Encoding UTF8",
    ]
    return "\n".join(ps)


def open_window(command, mode="ask", title=None, popen=subprocess.Popen, which=None, env=None, platform=None, now=None, say=False):
    """-> {"ok", "terminal", "log", "pid"} or {"ok": False, "why", "command"}."""
    env = os.environ if env is None else env
    platform = platform or sys.platform
    if mode not in ("ask", "run"):
        return {"ok": False, "why": f"mode must be ask or run, not {mode!r}", "command": command}
    always, words = say_settings()
    if say is False and always:
        say = words
    elif say is True:
        say = words
    log = log_path(now)
    title = title or "Fieldkit: " + (" ".join(command) if not isinstance(command, str) else command)[:60]
    if platform.startswith("win"):
        import base64
        enc = base64.b64encode(powershell_script(command, mode, log, title).encode("utf-16-le")).decode("ascii")
        if popen is not subprocess.Popen:
            argv = ["cmd", "/c", "start", "", "powershell", "-NoExit", "-NoProfile", "-ExecutionPolicy", "Bypass",
                    "-EncodedCommand", enc]
            p = popen(argv)
            return {"ok": True, "terminal": "powershell (start)", "log": str(log), "pid": getattr(p, "pid", None)}
        cl = f"powershell.exe -NoProfile -ExecutionPolicy Bypass -EncodedCommand {enc}"
        clq = "'" + cl.replace("'", "''") + "'"
        cwdq = "'" + str(settings.ROOT).replace("'", "''") + "'"
        ps_wmi = (f"$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create "
                  f"-Arguments @{{CommandLine={clq}; CurrentDirectory={cwdq}}}; "
                  f"Write-Output ($r.ReturnValue.ToString() + ' ' + $r.ProcessId)")
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps_wmi],
                             capture_output=True, text=True, timeout=60).stdout.strip().split()
        pid = int(out[1]) if len(out) == 2 and out[0] == "0" else None
        # 2026-10-18 (review): a window that did not start is not "ok" (WMI ReturnValue other than 0, or no PID)
        if pid is None:
            return {"ok": False, "terminal": "powershell (start)", "log": str(log), "pid": None,
                    "why": f"Windows did not start the window (WMI Create answered {' '.join(out) or 'nothing'})"}
        return {"ok": True, "terminal": "powershell (start)", "log": str(log), "pid": pid}
    if platform == "darwin":
        script = bash_script(command, mode, log, title, say=say)
        path = log.with_suffix(".sh"); path.write_text(script, encoding="utf-8"); os.chmod(path, 0o700)
        argv = ["osascript", "-e", f'tell application "Terminal" to do script {shlex.quote(str(path))!r}'.replace("'", '"')]
        p = popen(argv)
        return {"ok": True, "terminal": "Terminal.app", "log": str(log), "pid": getattr(p, "pid", None)}
    if not has_display(env):
        return {"ok": False, "why": "no desktop session here (no DISPLAY or WAYLAND_DISPLAY): a window cannot open. "
                                    "Tell the person the command instead", "command": command}
    name, build = pick_linux(which=which, env=env)
    if not name:
        return {"ok": False, "why": "no terminal program found; looked for " + ", ".join(n for n, _ in LINUX_TERMINALS),
                "command": command}
    argv = build(title, bash_script(command, mode, log, title, say=say))
    p = popen(argv, start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {"ok": True, "terminal": name, "log": str(log), "pid": getattr(p, "pid", None)}


def doctor(which=None, env=None, platform=None):
    env = os.environ if env is None else env
    platform = platform or sys.platform
    if platform.startswith("win"):
        return {"ok": True, "terminal": "powershell (start)", "display": True, "problems": []}
    if platform == "darwin":
        return {"ok": True, "terminal": "Terminal.app", "display": True, "problems": []}
    name, _ = pick_linux(which=which, env=env)
    problems = []
    if not has_display(env):
        problems.append("no DISPLAY or WAYLAND_DISPLAY: not a desktop session, a window cannot open")
    if not name:
        problems.append("no terminal program found; looked for " + ", ".join(n for n, _ in LINUX_TERMINALS))
    return {"ok": not problems, "terminal": name, "display": has_display(env), "problems": problems}


OUTCOME_LINES = ("window opened at", "run allowed by the person at", "command started at", "command finished at",
                 "exit ", "closed by the person at", "window closed by the person at")


def history(limit=20, root=None):
    """Every window newest first, as the harness or a session reads it: when it opened, whether the person allowed
    the run, when the command started and finished, its exit, and how the window ended. A log with an 'opened'
    line and nothing after it is a window somebody closed, or that is still open."""
    d = Path(root) if root else settings.ROOT / "state" / "terminal"
    out = []
    for p in sorted(d.glob("*.log"), reverse=True)[:limit]:
        rec = {"log": str(p), "opened": None, "command": "", "allowed": None, "started": None, "finished": None,
               "exit": None, "ended": None}
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("window opened at"):
                rec["opened"] = line[17:36]
                rec["command"] = line.split(" command: ", 1)[1][:80] if " command: " in line else ""
                rec["mode"] = line.split(" mode=", 1)[1].split(" ", 1)[0] if " mode=" in line else ""
            elif line.startswith("run allowed by the person at"):
                rec["allowed"] = line[-19:]
            elif line.startswith("command started at"):
                rec["started"] = line[-19:]
            elif line.startswith("command finished at"):
                rec["finished"] = line[20:39]
            elif line.startswith("exit ") and line[5:].strip().isdigit():
                rec["exit"] = int(line[5:].strip())
            elif line.startswith("closed by the person at"):
                rec["ended"] = "closed by the person at " + line[-19:]
            elif line.startswith("window closed by the person at"):
                rec["ended"] = "closed without answering at " + line[31:50]
        if rec["opened"] and rec["ended"] is None and rec["exit"] is None:
            rec["ended"] = "still open, or closed before the record could say"
        out.append(rec)
    return out


def history_lines(rows):
    if not rows:
        return ["  no windows recorded yet"]
    out = []
    for r in rows:
        what = f"exit {r['exit']}" if r["exit"] is not None else ("never ran" if r.get("mode") == "ask" and not r["allowed"] else "no exit recorded")
        out.append(f"  {r['opened'] or '(no opened line)':<19}  {what:<16}  {r['command'] or Path(r['log']).name}")
        if r["allowed"]:
            out.append(f"      allowed by the person at {r['allowed']}")
        if r["ended"]:
            out.append(f"      {r['ended']}")
    return out


def lines(d):
    if d.get("ok") and "log" in d:
        return [f"  opened a {d['terminal']} window (pid {d.get('pid')}); what it prints goes to {d['log']}"]
    if d.get("ok"):
        return [f"  terminal: {d['terminal']}; display: {'yes' if d['display'] else 'no'}", "  OK: a window can open here"]
    out = [f"  REFUSED: {d['why']}"]
    if d.get("command"):
        c = d["command"]; out.append("  the command: " + (" ".join(c) if not isinstance(c, str) else c))
    return out + [f"  {p}" for p in d.get("problems", [])]
