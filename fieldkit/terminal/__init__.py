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


def bash_script(command, mode, log, title=None):
    """What the window runs. `ask`: show, wait for Enter, run; `run`: run at once. Both keep the window open after."""
    q = shlex.quote
    cmd = " ".join(q(a) for a in command) if not isinstance(command, str) else command
    lines = [f"log={q(str(log))}",
             f"printf '\\n  %s\\n\\n' {q(title or 'Fieldkit')}",
             f"printf '  The command:\\n\\n    %s\\n\\n' {q(cmd)}"]
    if mode == "ask":
        lines.append("read -r -p '  Press Enter to run it. Close this window to refuse. ' _ || exit 130")
        lines.append("printf '\\n'")
    lines += [f"{cmd} 2>&1 | tee -a \"$log\"", "code=${PIPESTATUS[0]}",
              "echo \"exit $code\" >> \"$log\"",
              "printf '\\n  Finished (exit %s). The log: %s\\n' \"$code\" \"$log\"",
              "read -r -p '  Press Enter to close this window. ' _"]
    return "\n".join(lines) + "\n"


def powershell_script(command, mode, log, title=None):
    cmd = " ".join(command) if not isinstance(command, str) else command
    ps = [f"$host.UI.RawUI.WindowTitle = '{(title or 'Fieldkit').replace(chr(39), chr(39) * 2)}'",
          f"Write-Host ''; Write-Host '  The command:'; Write-Host ''; Write-Host ('    ' + '{cmd.replace(chr(39), chr(39) * 2)}'); Write-Host ''"]
    if mode == "ask":
        ps.append("Read-Host '  Press Enter to run it. Close this window to refuse' | Out-Null")
    ps += [f"& {{ {cmd} }} 2>&1 | Tee-Object -FilePath '{str(log).replace(chr(39), chr(39) * 2)}' -Append",
           "$code = $LASTEXITCODE",
           f"Add-Content -Path '{str(log).replace(chr(39), chr(39) * 2)}' -Value (\"exit \" + $code)",
           "Write-Host ''; Write-Host ('  Finished (exit ' + $code + ').')"]
    return "; ".join(ps)


def open_window(command, mode="ask", title=None, popen=subprocess.Popen, which=None, env=None, platform=None, now=None):
    """-> {"ok", "terminal", "log", "pid"} or {"ok": False, "why", "command"}."""
    env = os.environ if env is None else env
    platform = platform or sys.platform
    if mode not in ("ask", "run"):
        return {"ok": False, "why": f"mode must be ask or run, not {mode!r}", "command": command}
    log = log_path(now)
    title = title or "Fieldkit: " + (" ".join(command) if not isinstance(command, str) else command)[:60]
    if platform.startswith("win"):
        argv = ["cmd", "/c", "start", "", "powershell", "-NoExit", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-Command", powershell_script(command, mode, log, title)]
        p = popen(argv)
        return {"ok": True, "terminal": "powershell (start)", "log": str(log), "pid": getattr(p, "pid", None)}
    if platform == "darwin":
        script = bash_script(command, mode, log, title)
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
    argv = build(title, bash_script(command, mode, log, title))
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


def lines(d):
    if d.get("ok") and "log" in d:
        return [f"  opened a {d['terminal']} window (pid {d.get('pid')}); what it prints goes to {d['log']}"]
    if d.get("ok"):
        return [f"  terminal: {d['terminal']}; display: {'yes' if d['display'] else 'no'}", "  OK: a window can open here"]
    out = [f"  REFUSED: {d['why']}"]
    if d.get("command"):
        c = d["command"]; out.append("  the command: " + (" ".join(c) if not isinstance(c, str) else c))
    return out + [f"  {p}" for p in d.get("problems", [])]
