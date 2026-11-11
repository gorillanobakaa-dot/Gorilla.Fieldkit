# Version: 1.0.0 · updated 26-11-11-18-20
"""Can the `claude` CLI authenticate? Asked before a headless model job starts, so a lost login makes the job WAIT
and resume by itself instead of dying (P9 f).

2026-11-11 17:26: the CLI's OAuth token expired while the desktop app stayed logged in; three headless jobs and an
emerge fix-run died with "OAuth session expired and could not be refreshed" and sat as `died`.

    check()            -> {ok, why, fix, cached}. Free: `claude auth status --json` (no model call, no tokens),
                          cached OK_TTL seconds. After a run failed on auth (mark_failed) the answer stays "no" until
                          the login changes (loginExpiresAt differs) or RETRY_AFTER passed: a call that fails
                          authentication bills nothing, so the retried job is itself the probe.
    mark_failed(text)  remember the failure (state/modelauth.json) and file ONE hardline inbox item (same title ->
                       the same item, updated) naming the exact fix.
    is_auth_failure(text)  the CLI's auth error lines.
    log_says_auth(path)    the tail of a worker log ends in an auth error.

Who uses it: `fieldkit task start` (a job is recorded `waiting-auth`, not launched into a certain death) and the task
ledger (tasks.settle marks a worker that died of auth `waiting-auth`, not `died`; tasks.resume_waiting relaunches
it from its checkpoint once check() is ok; `fieldkit task list` / `task watch` run it, no tokens).
Model-free stages never call this.

Parallel refresh (the brief's question): Claude's OAuth refresh tokens rotate; several workers starting at once can
refresh the same token and invalidate each other. resume_waiting relaunches at most ONE waiting task per pass so a
recovered login is not hit by a burst of simultaneous refreshes. The launcher itself (fieldkit/autopilot/run.py) is
behind HARD LINE 3 for this run and is not changed here.
"""
import json
import re
import subprocess
import time
from pathlib import Path

OK_TTL = 300                 # a good answer is trusted for 5 minutes
RETRY_AFTER = 1800           # after an auth failure with an unchanged login, a job is retried every 30 minutes
WAITING = "waiting-auth"
AUTH = re.compile(r"OAuth (?:session|token)[^\n]{0,40}(?:expired|revoked|invalid)|could not be refreshed|"
                  r"Please run /login|Invalid API key|authentication_error|401 Unauthorized|Not logged in",
                  re.I)
TITLE = "Claude CLI login lost: model jobs are waiting"


def state_file():
    from .friction import state_dir
    return state_dir() / "modelauth.json"


def _read():
    try:
        return json.loads(state_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write(d):
    try:
        state_file().parent.mkdir(parents=True, exist_ok=True)
        state_file().write_text(json.dumps(d, indent=1), encoding="utf-8")
    except OSError:
        pass


def is_auth_failure(text):
    return bool(AUTH.search(text or ""))


def log_says_auth(path, tail=8000):
    try:
        p = Path(path)
        with open(p, "rb") as f:
            f.seek(max(0, p.stat().st_size - tail))
            return is_auth_failure(f.read().decode("utf-8", "replace"))
    except (OSError, TypeError, ValueError):
        return False


def fix_command():
    from ..autopilot.run import claude_exe
    exe = claude_exe() or "claude"
    return f'"{exe}" auth login' if " " in exe else f"{exe} auth login"


def status(run=subprocess.run):
    """-> {loggedIn, loginExpiresAt, authMethod} from `claude auth status --json`, or {"error": why}. Never a model
    call. Only what decides is kept: the account's e-mail and ids are dropped."""
    from ..autopilot.run import claude_cmd
    cmd = claude_cmd()
    if not cmd:
        return {"error": "no Claude Code executable found"}
    try:
        p = run(cmd + ["auth", "status", "--json"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as e:
        return {"error": f"claude auth status: {e}"}
    try:
        d = json.loads(p.stdout or "")
    except ValueError:
        return {"error": f"claude auth status: unreadable output (exit {p.returncode})"}
    if not isinstance(d, dict):
        return {"error": "claude auth status: unexpected output"}
    return {"loggedIn": bool(d.get("loggedIn")), "loginExpiresAt": d.get("loginExpiresAt"),
            "authMethod": d.get("authMethod")}


def check(now=None, run=subprocess.run, fresh=False):
    now = time.time() if now is None else now
    st = _read()
    if not fresh and st.get("ok") and now - float(st.get("at") or 0) < OK_TTL:
        return {"ok": True, "why": st.get("why", "logged in"), "fix": "", "cached": True}
    s = status(run)
    out = {k: st.get(k) for k in ("failed_at", "failed_login", "failed_text")}
    out["at"] = now
    if s.get("error"):
        # cannot tell: let the job go; its own output decides (a failed auth bills nothing)
        out.update(ok=True, why=f"status unknown ({s['error']}); the job itself decides")
    elif not s["loggedIn"]:
        out.update(ok=False, why="the claude CLI is not logged in")
    elif st.get("failed_at") and s.get("loginExpiresAt") == st.get("failed_login") \
            and now - float(st["failed_at"]) < RETRY_AFTER:
        nxt = time.strftime("%H:%M", time.localtime(float(st["failed_at"]) + RETRY_AFTER))
        out.update(ok=False, why=f"the last job failed authentication ({(st.get('failed_text') or '')[:100]}) and "
                                 f"the login has not changed since; next try {nxt}")
    else:
        out.update(ok=True, why="logged in", failed_at=None, failed_login=None, failed_text=None)
    _write(out)
    return {"ok": out["ok"], "why": out["why"], "fix": "" if out["ok"] else fix_command(), "cached": False}


def mark_failed(text, now=None, run=subprocess.run, inbox_add=None):
    now = time.time() if now is None else now
    s = status(run)
    d = _read()
    d.update(ok=False, at=now, failed_at=now, failed_login=s.get("loginExpiresAt"),
             failed_text=(text or "").strip()[:300], why="a model job failed authentication")
    _write(d)
    file_inbox(text, inbox_add)


def file_inbox(text, inbox_add=None):
    if inbox_add is None:
        from ..autopilot.inbox import add as inbox_add
    try:
        inbox_add(TITLE, f"A headless claude job could not authenticate: {(text or '').strip()[:200]}. Model-free "
                  "stages keep running; jobs that need a model wait (status waiting-auth) and resume by themselves "
                  "once the CLI can log in again (checked by fieldkit task list / task watch, no tokens). "
                  "Fix: log the CLI in again in a terminal.", command=fix_command(), kind="hardline")
    except Exception:  # noqa: BLE001 - the inbox must never turn a wait into a crash
        pass


def wait_line(r):
    return f"MODEL AUTH: WAITING - {r['why']}; the job waits and resumes by itself; fix: {r['fix']}"
