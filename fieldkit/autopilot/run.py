# Version: 1.1.0 · updated 26-10-22-14-00
"""Autopilot: an unattended agent run with no approval prompts, held by the hard lines and by what it cannot reach.

Owner, 2026-10-18: "we need both Gemini Antigravity and Claude to work automatically and unattended". Two layers:
  1. capability: the run's environment has no GitHub or git credential (GH_CONFIG_DIR is an empty folder, the token
     variables are empty, git's credential helper is off), so publishing fails even if every rule were bypassed;
  2. the hard lines (hardlines.py) through a hook in Claude Code (claude_hook.py) and Antigravity (its guard).
Everything else runs without asking.

    fieldkit autopilot on | off | status       the switch Antigravity's guard reads (on.json)
    fieldkit autopilot stop [why] | resume     the owner's STOP file: only reads until resume
    fieldkit autopilot claude PROMPT [--cwd D] [--max-turns N]   one headless Claude Code run, bypass + hook
        [--job DIR --wall-minutes M --cost-usd X]   (or FIELDKIT_JOB_DIR set): a budgeted job that checkpoints,
                                                    resumes, and exits 75 when paused-resumable (job.py)
"""
import glob
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import hardlines

ON = hardlines.STATE / "on.json"


def state_dir():
    hardlines.STATE.mkdir(parents=True, exist_ok=True)
    return hardlines.STATE


def isolated_env(base=None):
    """The environment of an autopilot run: no way to authenticate to GitHub or any git remote."""
    env = dict(base if base is not None else os.environ)
    empty = state_dir() / "no-credentials"
    empty.mkdir(exist_ok=True)
    for k in ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN"):
        env[k] = ""
    env.update({
        "GH_CONFIG_DIR": str(empty),             # gh finds no hosts.yml, so no account and no keyring lookup
        "GIT_TERMINAL_PROMPT": "0",
        "GCM_INTERACTIVE": "Never",
        "GIT_ASKPASS": "", "SSH_ASKPASS": "",
        "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "credential.helper", "GIT_CONFIG_VALUE_0": "",
        "FIELDKIT_AUTOPILOT": "1",
    })
    return env


def settings(path=None):
    """The per-run Claude Code settings: bypass mode, the hard-line hook (blocking on failure), deny rules as a
    second copy of the worst lines. Written fresh each run; ~/.claude/settings.json is not touched."""
    path = Path(path or state_dir() / "claude-autopilot-settings.json")
    hook = f'"{sys.executable}" -m fieldkit.autopilot.claude_hook'
    data = {
        "permissions": {
            "defaultMode": "bypassPermissions",
            "deny": ["Bash(git push:*)", "Bash(gh release:*)", "Bash(gh pr merge:*)", "Bash(gh repo:*)",
                     "PowerShell(git push:*)", "PowerShell(gh release:*)"],
        },
        "hooks": {"PreToolUse": [{"matcher": "*", "hooks": [
            {"type": "command", "command": hook, "timeout": 30, "onFailure": "block"}]}],
                  # the budget messages at 50 / 80 / 95 % (budget.py); silent outside a job (no FIELDKIT_JOB_DIR)
                  "PostToolUse": [{"matcher": "*", "hooks": [
                      {"type": "command", "command": hook + " --post", "timeout": 30}]}]},
    }
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


CREDENTIAL_ENV_KEYS = ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN", "GH_CONFIG_DIR",
                       "GIT_TERMINAL_PROMPT", "GCM_INTERACTIVE", "GIT_ASKPASS", "SSH_ASKPASS", "GIT_CONFIG_COUNT",
                       "GIT_CONFIG_KEY_0", "GIT_CONFIG_VALUE_0", "FIELDKIT_AUTOPILOT")


def workspace(folder=None):
    """A folder to open Claude Code DESKTOP sessions in for unattended work: its .claude/settings.json sets bypass
    mode, the hard-line hook and (through "env") the same credential removal as `claude()`. Sessions opened anywhere
    else are untouched. -> the settings path."""
    folder = Path(folder or Path.home() / "Documents" / "Autopilot")
    (folder / ".claude").mkdir(parents=True, exist_ok=True)
    data = json.loads(settings().read_text(encoding="utf-8"))
    iso = isolated_env({})
    data["env"] = {k: iso[k] for k in CREDENTIAL_ENV_KEYS if k in iso}
    data["env"]["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])
    p = folder / ".claude" / "settings.json"
    p.write_text(json.dumps(data, indent=2), encoding="utf-8")
    readme = folder / "README.md"
    if not readme.exists():
        readme.write_text(
            "# Autopilot\n\nOpen a Claude Code session in this folder for unattended work (no approval prompts).\n"
            "The hard lines still hold: no push or publish, no deletes outside scratch, no changes to guards, rules,\n"
            "the build harness, leak-gate allowlists or the decision register, no owner approvals, no credential files.\n"
            "GitHub is unreachable from here: publish afterwards with `fieldkit publish`.\n"
            "Stop everything: `fieldkit autopilot stop`.\n", encoding="utf-8")
    return p


def claude_exe():
    """Claude Code: on PATH, else the newest copy the desktop app ships (%APPDATA%\\Claude\\claude-code)."""
    found = shutil.which("claude")
    if found:
        return found
    # the desktop app is an MSIX package: what it writes to %APPDATA% really lives in its package folder, and only
    # processes inside the package see the redirected path (2026-10-18: the owner's PowerShell could not find the
    # %APPDATA% path this session saw). The package folder is real for everyone, so it comes first.
    cands = glob.glob(os.path.join(os.environ.get("LOCALAPPDATA", ""), "Packages", "Claude_*", "LocalCache",
                                   "Roaming", "Claude", "claude-code", "*", "*", "claude.exe"))
    cands = cands or glob.glob(os.path.join(os.environ.get("APPDATA", ""), "Claude", "claude-code", "*", "*", "claude.exe"))
    key = lambda p: [int(x) if x.isdigit() else 0 for x in Path(p).parent.parent.name.split(".")]
    return max(cands, key=key) if cands else None


def paste_command(exe=None):
    """The owner-pasteable PowerShell line that starts Claude Code from its real path (ownercmd rules)."""
    exe = exe or claude_exe()
    if not exe:
        return None
    local = os.environ.get("LOCALAPPDATA", "")
    shown = "$env:LOCALAPPDATA" + exe[len(local):] if local and exe.lower().startswith(local.lower()) else exe
    return f'cd $HOME; & "{shown}"'


def logged_in(run=subprocess.run):
    """True / False / None (unknown): one tiny headless call; 'Not logged in' answers at once and costs nothing."""
    exe = claude_exe()
    if not exe:
        return None
    try:
        r = run([exe, "-p", "reply with the word ok", "--max-turns", "1", "--output-format", "json"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = (r.stdout or "") + (r.stderr or "")
    if "not logged in" in text.lower() or "/login" in text:
        return False
    return r.returncode == 0


def login(popen=subprocess.Popen):
    """Open Claude Code in its own console window so the owner can type /login there (one time per machine).
    -> {ok, command, say}. The window is the owner's; nothing is typed into it for them."""
    exe = claude_exe()
    if not exe:
        return {"ok": False, "why": "no Claude Code executable found (is the Claude desktop app installed?)"}
    say = ("A window titled Claude Code opens. Inside it, at Claude's own prompt (not PowerShell's), type /login "
           "and press Enter; sign in in the browser; then close the window.")
    if os.name == "nt":
        flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0x10)
        p = popen([exe], cwd=str(Path.home()), creationflags=flags)
    else:
        p = popen(["x-terminal-emulator", "-e", exe], cwd=str(Path.home()))
    return {"ok": True, "pid": p.pid, "exe": exe, "command": paste_command(exe), "say": say}


def claude_cmd():
    """The command that starts Claude Code: FIELDKIT_CLAUDE_EXE (a test's fake claude; a .py runs under this
    Python) or the real executable."""
    fake = os.environ.get("FIELDKIT_CLAUDE_EXE")
    if fake:
        return [sys.executable, fake] if fake.endswith(".py") else [fake]
    exe = claude_exe()
    return [exe] if exe else None


def kill_tree(pid, psutil=None):
    """Stop a process and every process it started, by PID (never by name: the owner runs the same programs)."""
    if psutil is None:
        import psutil
    try:
        root = psutil.Process(pid)
        procs = root.children(recursive=True) + [root]
    except psutil.Error:
        return []
    for p in procs:
        try:
            p.kill()
        except psutil.Error:
            pass
    psutil.wait_procs(procs, timeout=10)
    return [p.pid for p in procs]


def claude(prompt, cwd=None, max_turns=10000, timeout=None, env_extra=None, resume=None, model=None,
           popen=subprocess.Popen):
    """One headless Claude Code run in autopilot (bypass mode + the hard-line hook, no credential).
    max_turns 10000 (owner 2026-10-22; 40 ended the first night run's job 2 mid-mutate). timeout = the job's wall
    budget in seconds (None: no limit; the hidden 7200 s is gone): on expiry the whole process tree is killed by
    PID and the result says timed_out. resume = a session id to continue (claude --resume). A long prompt goes on
    stdin (the Windows command line holds 32k characters)."""
    cmd = claude_cmd()
    if not cmd:
        return {"ok": False, "why": "no Claude Code executable found"}
    fk_root = str(Path(__file__).resolve().parents[2])
    env = isolated_env()
    env["PYTHONPATH"] = fk_root + os.pathsep + env.get("PYTHONPATH", "")
    env.update(env_extra or {})
    via_stdin = len(prompt) > 8000
    args = cmd + ["-p"] + ([] if via_stdin else [prompt]) + [
        "--settings", str(settings()), "--permission-mode", "bypassPermissions",
        "--max-turns", str(max_turns), "--output-format", "json"]
    if resume:
        args += ["--resume", resume]
    if model:
        args += ["--model", model]
    t0 = time.time()
    p = popen(args, cwd=cwd or os.getcwd(), env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
              stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
    timed_out = False
    try:
        so, se = p.communicate(prompt if via_stdin else None, timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        kill_tree(p.pid)
        try:
            so, se = p.communicate(timeout=30)
        except subprocess.TimeoutExpired:   # a process that escaped the tree still holds the pipes: never hang on it
            p.kill()
            so, se = "", "killed; a process outside its tree kept the output pipes open"
    out = {"ok": p.returncode == 0 and not timed_out, "rc": 124 if timed_out else p.returncode,
           "seconds": round(time.time() - t0), "timed_out": timed_out}
    try:
        out["result"] = json.loads(so)
    except (ValueError, TypeError):
        out["stdout"] = (so or "")[-2000:]
    out["stderr"] = (se or "")[-1000:]
    return out


def antigravity_exe():
    cand = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "antigravity" / "Antigravity.exe"
    return str(cand) if cand.is_file() else shutil.which("antigravity")


def antigravity(popen=subprocess.Popen, psutil=None):
    """Start Antigravity for an autopilot run: the guard's autopilot mode on, and the app started with no GitHub or
    git credential in its environment (its terminals inherit it). Refused while Antigravity already runs: Electron
    hands a second launch to the open instance, which keeps its old environment."""
    if psutil is None:
        import psutil
    exe = antigravity_exe()
    if not exe:
        return {"ok": False, "why": "Antigravity not found"}
    open_ = [p.pid for p in psutil.process_iter(["name"]) if (p.info.get("name") or "").lower() == "antigravity.exe"]
    if open_:
        return {"ok": False, "why": f"Antigravity is open (PID {open_[0]}): close it first, then run this again; "
                                    "an open instance keeps its environment, credential included"}
    switch(True, "antigravity autopilot")
    flags = (getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    p = popen([exe], env=isolated_env(), creationflags=flags, close_fds=True)
    return {"ok": True, "pid": p.pid, "autopilot": True,
            "note": "the guard's autopilot mode is on; `fieldkit autopilot off` returns it to normal"}


def switch(on, why=""):
    if on:
        state_dir()
        ON.write_text(json.dumps({"on": True, "since": time.strftime("%Y-%m-%d %H:%M:%S"), "why": why}), encoding="utf-8")
    elif ON.exists():
        ON.unlink()
    return status()


def stop(why="the owner said stop"):
    state_dir()
    hardlines.STOP_FILE.write_text(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {why}\n", encoding="utf-8")
    return status()


def resume():
    if hardlines.STOP_FILE.exists():
        hardlines.STOP_FILE.unlink()
    return status()


def status():
    return {"autopilot": ON.exists(), "stopped": hardlines.STOP_FILE.exists(),
            "stop_reason": hardlines.STOP_FILE.read_text(encoding="utf-8").strip() if hardlines.STOP_FILE.exists() else "",
            "state": str(hardlines.STATE), "claude": claude_exe()}
