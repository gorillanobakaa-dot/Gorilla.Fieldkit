"""Fixed, read-only probes the link listener runs for the other computer: no AI, no shell, no arbitrary command.

Owner, 2026-10-31: finding where `claude` lives on the VAIO took a hand-written probe script, which the no-AI run job
refused. Each probe here is code with a test; the name is looked up in PROBES and nothing else is ever run. The only
programs executed are `PROGRAM --version` (5 s cap) of tools found by name. A new probe is added here, never typed.

    fieldkit peers link probe NAME [ARG] [--device DEVICE]      (no device: this computer)
"""
from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
from fieldkit import proc as fkproc
import sys
import time

from . import agentfind

VERSION = 1
NAME_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+ ()-]{0,63}$")
TOOLS = (("python", None), ("git", ["--version"]), ("go", ["version"]), ("gh", ["--version"]))


def _agents(arg=None, **kw):
    rows = agentfind.find_all(**kw)
    lines = [f"{r['agent']:<17} {(r['path'] or 'not found'):<60} {r['version'][:30]:<30} {r['how']}"
             + (f" running {','.join(map(str, r['running'][:4]))}" if r["running"] else "") for r in rows]
    return lines, rows


def _where(arg=None, finder=None, **_):
    """PROGRAM anywhere the system knows it: the one app finder (fieldkit/app/find.py). The name is checked first."""
    if not arg or not NAME_OK.match(arg):
        raise ValueError("where needs one program name (letters, digits, spaces, . _ + - and parentheses)")
    if finder is None:
        from ..app import find as finder_mod
        finder = finder_mod.find
    rows = finder(arg)
    from ..app import find as F
    return F.render(rows) if rows else [f"{arg}: not found"], rows


DEFAULT_APPS = ("claude", "codex", "gemini", "opencode", "gorilla-opencode", "antigravity", "agy", "TPFanControl")


def start_app(name, allowed=None, finder=None, starter=None):
    """-> {ok, why|error, ...}. Starts NAME, no arguments, only when it is on THIS computer's allowlist
    (link.yaml allow_apps, approved; default DEFAULT_APPS) by its whole name, and never when it is ambiguous."""
    from ..app import find as F
    if not isinstance(name, str) or not NAME_OK.match(name):
        return {"ok": False, "error": "start-app needs one program name (letters, digits, spaces, . _ + - and parentheses)"}
    if allowed is None:
        from . import link
        reg, _why = link.approvals()
        allowed = reg.get("allow_apps") or DEFAULT_APPS
    if F.norm(name) not in {F.norm(x) for x in allowed}:
        return {"ok": False, "error": f"{name!r} is not on this computer's start-app allowlist "
                                      f"({', '.join(allowed)}); the owner adds it to allow_apps in link.yaml and approves"}
    matches = (finder or F.find)(name)
    m, why = F.pick([x for x in matches if x["score"] == 2] or matches)
    if m is None:
        return {"ok": False, "error": f"not started: {name!r}: {why}"}
    if starter is None:
        from ..app import start as S
        starter = S.start
    r = starter(m, ())
    return {**r, "host": socket.gethostname()}


def _disk(arg=None, usage=shutil.disk_usage, **_):
    roots = [os.path.abspath(os.sep), os.path.expanduser("~")]
    seen, rows = set(), []
    for r in roots:
        if r in seen:
            continue
        seen.add(r)
        u = usage(r)
        rows.append({"path": r, "total": u.total, "free": u.free})
    return [f"{r['path']:<30} free {r['free'] / 2**30:.1f} GiB of {r['total'] / 2**30:.1f} GiB" for r in rows], rows


def _temps(arg=None, temp_fn=None, **_):
    if temp_fn is None:
        from .. import admission
        temp_fn = admission.temperature
    t = temp_fn()
    return [f"cpu temperature: {t} C" if t is not None else "cpu temperature: not readable here"], {"temp_c": t}


def _load(arg=None, report_fn=None, **_):
    if report_fn is None:
        from .. import orchestrate_load
        report_fn = orchestrate_load.report
    r = report_fn()
    keys = ("cores", "cpu_pct", "load1", "ram_total", "ram_free", "temp_c", "idle")
    return [f"{k:<10} {r.get(k)}" for k in keys] + [f"heavy      {r.get('heavy')}"], r


def _versions(arg=None, which=shutil.which, run=fkproc.run, **_):
    rows = []
    for name, vargs in TOOLS:
        if name == "python":
            rows.append({"tool": name, "path": sys.executable, "version": sys.version.split()[0]})
            continue
        path = which(name)
        rows.append({"tool": name, "path": path or "", "version": agentfind.version_of(path, vargs, run) if path else ""})
    import fieldkit
    rows.append({"tool": "fieldkit", "path": os.path.dirname(fieldkit.__file__), "version": fieldkit.__version__})
    return [f"{r['tool']:<9} {r['version'] or 'not found':<40} {r['path']}" for r in rows], rows


def _sessions(arg=None, sessions_fn=None, **_):
    if sessions_fn is None:
        from . import peers
        sessions_fn = peers.sessions
    rows = sessions_fn()
    return [str(r.get("name") if isinstance(r, dict) else r) for r in rows] or ["no messageable session"], rows


def _antigravity_sessions(arg=None, sessions_fn=None, **_):
    if arg is not None and arg not in ("all", "desktop", "cli"):
        raise ValueError("antigravity-sessions source must be all, desktop, or cli")
    source = arg or "all"
    if sessions_fn is None:
        from ..antigravity import bridge
        sessions_fn = bridge.list_sessions
    rows = sessions_fn(source)
    return [f"{r['source']:<8} {r['id']}" for r in rows] or ["no antigravity sessions"], rows


def _install_verify(arg=None, **_):
    """`fieldkit install verify PRODUCT` on this computer, from the release itself: lines + {ok, os, rows}."""
    from ..method import installverify
    if arg not in installverify.PRODUCTS:
        raise ValueError("install-verify needs one product: " + ", ".join(installverify.PRODUCTS))
    return installverify.probe_lines(arg)


def _choice(arg=None, **_):
    """P39: this computer's agent > provider > model > reasoning menu, measured here (its agents, its quota blocks, its
    local LLM server). Besides `--version` it runs only an agent's own read-only model list (`agy models`, 10 s cap)."""
    from ..lifecycle import choice
    rows = choice.menu()
    return choice.render(rows, socket.gethostname()).splitlines(), rows


def _clean_slate(arg=None, **_):
    from ..capture import audit

    if arg is not None:
        raise ValueError("clean-slate-audit accepts no argument")
    report = audit.local()
    return [], report


HEALTH_TIMEOUT = 90
HEALTH_PROMPT = "Reply with the single word OK."
TTL_ENV = ("CLAUDE_CODE_PROMPT_CACHE_TTL", "CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL", "ENABLE_PROMPT_CACHING_1H")
TTL_TOP = ("promptCacheTtl", "subagentPromptCacheTtl")


def _tail(text, n=400):
    return " | ".join(x.strip() for x in (text or "").strip().splitlines() if x.strip())[-n:]


def _claude_health(arg=None, finder=None, run=fkproc.run, **_):
    """Starts ONE real `claude -p` session here (fixed prompt, one turn, 90 s cap) and reports its exit code and the tail
    of its output, so an expired login or a quota error is read over the cable instead of guessed. No argument."""
    if arg is not None:
        raise ValueError("claude-health accepts no argument")
    row = (finder or agentfind.find_one)("claude")
    path = row.get("path") if row else ""
    if not path:
        return ["claude: not found"], {"ok": False, "found": False}
    t0 = time.time()
    try:
        r = run([path, "-p", HEALTH_PROMPT, "--max-turns", "1"], capture_output=True, text=True,
                timeout=HEALTH_TIMEOUT, stdin=subprocess.DEVNULL)
        rc, out, err = r.returncode, r.stdout or "", r.stderr or ""
    except subprocess.TimeoutExpired:
        rc, out, err = None, "", f"no answer within {HEALTH_TIMEOUT} s"
    ok = rc == 0 and bool(out.strip())
    d = {"ok": ok, "found": True, "path": path, "exit": rc, "seconds": round(time.time() - t0, 1),
         "stdout": _tail(out), "stderr": _tail(err)}
    return [f"claude {'WORKS' if ok else 'FAILS'}: exit {rc}, {d['seconds']} s",
            f"stdout: {d['stdout'] or '(empty)'}", f"stderr: {d['stderr'] or '(empty)'}"], d


def _claude_settings(arg=None, home=None, **_):
    """Reads ONLY the five prompt-cache TTL keys of ~/.claude/settings.json (nothing else of the file is returned)."""
    import json
    if arg is not None:
        raise ValueError("claude-settings accepts no argument")
    f = os.path.join(home or os.path.expanduser("~"), ".claude", "settings.json")
    try:
        with open(f, encoding="utf-8") as fh:
            j = json.load(fh)
    except (OSError, ValueError) as e:
        return [f"{f}: not readable ({type(e).__name__})"], {"ok": False}
    env = j.get("env") if isinstance(j.get("env"), dict) else {}
    got = {**{f"env.{k}": env.get(k) for k in TTL_ENV}, **{k: j.get(k) for k in TTL_TOP}}
    return [f"{k} = {v!r}" for k, v in got.items()], {"ok": all(v in ("1h", "1") for v in got.values()), "keys": got}


PROBES = {"claude-health": _claude_health, "claude-settings": _claude_settings, "clean-slate-audit": _clean_slate, "choice": _choice, "install-verify": _install_verify, "agents": _agents, "where": _where, "disk": _disk, "temps": _temps, "load": _load, "versions": _versions,
          "sessions": _sessions, "antigravity-sessions": _antigravity_sessions}


def run(name, arg=None, **kw):
    """-> {ok, probe, version, host, text, data, at}; a name outside PROBES is refused and nothing runs."""
    fn = PROBES.get(name) if isinstance(name, str) else None
    if fn is None:
        return {"ok": False, "error": f"unknown probe {name!r}; the fixed set: {', '.join(PROBES)}"}
    try:
        lines, data = fn(arg, **kw)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:  # noqa: BLE001 - a probe that fails says so; it never takes the listener down
        return {"ok": False, "error": f"probe {name} failed: {type(e).__name__}: {e}"}
    return {"ok": True, "probe": name, "version": VERSION, "host": socket.gethostname(), "text": "\n".join(lines),
            "data": data, "at": time.strftime("%Y-%m-%d %H:%M:%S")}


def render(r):
    if not r.get("ok"):
        return f"PROBE REFUSED/FAILED: {r.get('error')}"
    return f"PROBE {r['probe']} v{r['version']} on {r['host']} ({r['at']})\n{r['text']}"
