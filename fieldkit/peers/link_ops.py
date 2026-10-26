# Version: 1.0.0 · updated 26-10-23-18-04
"""Running the cable link without improvising: what each step of 2026-10-22 night was done by hand, as one command.

    fieldkit peers link toolset [--peer]          this computer's tools (python, pytest, PyYAML, psutil, git, claude,
                                                  the link's commit); --peer: the other computer's too, and each gap
                                                  with the line that closes it
    fieldkit peers link startcheck                PRECHECK before a started session is wasted: the start command from
                                                  link.yaml, which fieldkit it loads, autopilot's options, claude,
                                                  the prompt on stdin
    fieldkit peers link restart                   stop the listener and start it on THIS code; checks the port
    fieldkit peers link verify-start --since N    wait for a started session's [DONE] note with evidence: the only
                                                  proof a session ran (a live process is not)

Owner, 2026-10-22 (translated from Romanian): "everything you did tonight to get to this point will have to be
turned into deterministic tools with their cards, with their prechecks". What each replaces:
  - toolset: two machines compared by chat, line by line (the only gap was claude 2.1.296 against 2.1.302)
  - startcheck: three start failures found one per round trip, each costing a session: autopilot options that did
    not exist (--job, --wall-minutes), a brief starting with "---" read by claude as an option (prompt in argv), and
    `python -m fieldkit` loading the working folder's checkout instead of the link's (cwd beats PYTHONPATH)
  - restart: kill + start_serve + port check typed by hand twice
  - verify-start: "started (alive after 2 s)" was reported for a session whose claude had already failed
Exit 0 all good, 3 something to fix (each line says what), 2 bad input.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import link

MODULES = ("pytest", "yaml", "psutil")
INSTALL = {"pytest": "pytest", "yaml": "PyYAML", "psutil": "psutil"}
PROBE = ("import json,sys\nout={'python':sys.version.split()[0]}\nfor m in %r:\n try:\n  mod=__import__(m)\n"
         "  out[m]=getattr(mod,'__version__','?')\n except Exception:\n  out[m]=None\nprint(json.dumps(out))")


# ---- toolset ------------------------------------------------------------------------------------------------------
def _run(argv, **kw):
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=kw.pop("timeout", 30), **kw)
        return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, "", str(e)


def venv_python(root=link.LINK_ROOT):
    """The test interpreter: this checkout's .venv, else the main checkout's (a worktree has none of its own)."""
    rel = Path("Scripts", "python.exe") if os.name == "nt" else Path("bin", "python")
    cands = [Path(root) / ".venv" / rel]
    rc, common, _ = _run(["git", "-C", str(root), "rev-parse", "--path-format=absolute", "--git-common-dir"])
    if rc == 0 and common:
        cands.append(Path(common).parent / ".venv" / rel)
    return next((str(c) for c in cands if c.is_file()), None)


def toolset_local(run=_run):
    """-> dict: what this computer has, for the peer to compare. Called by hello on the other side."""
    py = venv_python() or sys.executable
    rc, out, _ = run([py, "-c", PROBE % (MODULES,)])
    mods = json.loads(out) if rc == 0 and out.startswith("{") else {}
    rc, git, _ = run(["git", "--version"])
    claude = shutil.which("claude")
    if not claude:
        try:
            from ..autopilot.run import claude_exe
            claude = claude_exe()
        except Exception:  # noqa: BLE001
            claude = None
    rc_c, cv, _ = run([claude, "--version"]) if claude else (None, "", "")
    rc_h, head, _ = run(["git", "-C", str(link.LINK_ROOT), "rev-parse", "--short", "HEAD"])
    return {"os": sys.platform, "python": sys.version.split()[0], "test_python": py,
            "pytest": mods.get("pytest"), "yaml": mods.get("yaml"), "psutil": mods.get("psutil"),
            "git": (git.split()[2] if rc == 0 and len(git.split()) > 2 else None),
            "claude": (re.findall(r"\d+\.\d+\.\d+", cv) or [None])[0] if rc_c == 0 else None,
            "link_commit": head if rc_h == 0 else None}


def compare(mine, theirs):
    """-> list of (what, mine, theirs, how to close it). Empty = the two computers can sync."""
    gaps = []
    for k in ("pytest", "yaml", "psutil", "claude", "link_commit"):
        a, b = mine.get(k), theirs.get(k)
        if a != b:
            how = {"claude": "claude update (then check: claude --version)",
                   "link_commit": "fieldkit sync, or git fetch + fast-forward feat/agent-link on the older side"}.get(
                k, f"{mine.get('test_python') or 'python'} -m pip install {INSTALL.get(k, k)}=={b or a}")
            gaps.append((k, a, b, how))
    for side, d in (("this computer", mine), ("the peer", theirs)):
        v = tuple(int(x) for x in re.findall(r"\d+", d.get("python") or "0")[:2])
        if v < (3, 11):
            gaps.append(("python", mine.get("python"), theirs.get("python"),
                         f"{side} needs Python 3.11 or newer (the start command uses -P)"))
    return gaps


def toolset_lines(mine, theirs=None):
    keys = ("os", "python", "pytest", "yaml", "psutil", "git", "claude", "link_commit")
    if theirs is None:
        return [f"  {k:12} {mine.get(k)}" for k in keys]
    rows = [f"  {'':12} {'this computer':22} the peer"] + [f"  {k:12} {str(mine.get(k)):22} {theirs.get(k)}" for k in keys]
    gaps = compare(mine, theirs)
    rows += [f"GAP {k}: {a} here, {b} there -> {how}" for k, a, b, how in gaps] or ["SAME TOOLSET  (git and python "
                                                                                    "minor versions may differ)"]
    return rows


# ---- startcheck (precheck) ----------------------------------------------------------------------------------------
def startcheck(agent="claude", run=_run):
    """-> list of (check, ok, detail). Every way a started session failed on 2026-10-22, tested before it starts."""
    rows = []
    reg, why = link.approvals()
    allowed = agent in (reg.get("allow_start") or [])
    rows.append(("approved in link.yaml", allowed, why if not allowed else f"allow_start {reg.get('allow_start')}"))
    cwd = reg.get("cwd")
    rows.append(("working folder exists", bool(cwd) and Path(cwd).is_dir(), str(cwd)))
    v = sys.version_info[:2]
    rows.append(("python 3.11+ (for -P)", v >= (3, 11), f"{sys.executable} {sys.version.split()[0]}"))
    if cwd and Path(cwd).is_dir():
        rc, out, err = run([sys.executable, "-P", "-c", "import fieldkit; print(fieldkit.__file__)"], cwd=cwd,
                           env=link.link_env())
        ours = rc == 0 and Path(out).resolve().is_relative_to(link.LINK_ROOT.resolve())
        rows.append(("loads the link's own fieldkit", ours, out or err))
    flags = link.autopilot_flags()
    rows.append(("autopilot answers --help", bool(flags), ", ".join(sorted(flags)) or "no answer"))
    if agent == "claude":
        tmpl = (reg.get("start_commands") or {}).get(agent) or link.DEFAULT_START[agent]
        used = link.fit_command([a for a in tmpl], flags) if tmpl is link.DEFAULT_START[agent] else tmpl
        unknown = [a for a in used if a.startswith("--") and a not in flags]
        rows.append(("every start option exists", not unknown, "unknown: " + " ".join(unknown) if unknown else "ok"))
        from ..autopilot import run as aprun
        exe = aprun.claude_exe()
        rows.append(("claude found", bool(exe), exe or "not on PATH or in the desktop app"))
        seen = {}

        class Fake:                                       # the runner's popen, recording argv and stdin, running nothing
            returncode, pid = 0, 0

            def __init__(self, args, **kw):
                seen["args"] = args

            def communicate(self, input=None, timeout=None):
                seen["input"] = input
                return "{}", ""
        probe = "--- a brief that starts with dashes ---"
        try:
            old = aprun.claude_cmd
            aprun.claude_cmd = lambda: [exe or "claude"]
            aprun.claude(probe, popen=Fake)
        finally:
            aprun.claude_cmd = old
        rows.append(("brief goes on stdin, not argv", seen.get("input") == probe and probe not in seen.get("args", []),
                     "argv would read '---' as an option" if probe in seen.get("args", []) else "ok"))
    return rows


# ---- restart ------------------------------------------------------------------------------------------------------
def restart(wait=10):
    """-> (code, lines). Stops the listener named in serve.pid (only if it is a `peers link serve`), starts it on this
    code from this checkout, waits for the port."""
    from . import link_setup
    lines = []
    pidf = link.home() / "serve.pid"
    try:
        pid = int(pidf.read_text().strip())
    except (OSError, ValueError):
        pid = None
    if pid:
        try:
            import psutil
            p = psutil.Process(pid)
            cmd = " ".join(p.cmdline())
            if "peers link serve" in cmd or ("peers" in cmd and "serve" in cmd):
                p.terminate()
                p.wait(5)
                lines.append(f"stopped the listener PID {pid}")
            else:
                lines.append(f"left PID {pid} alone: it is not a link listener ({cmd[:80]})")
        except Exception as e:  # noqa: BLE001  (gone already, or no psutil)
            lines.append(f"no running listener at PID {pid} ({type(e).__name__})")
    here = os.getcwd()
    try:
        os.chdir(link.LINK_ROOT)                 # serve imports from its working folder too: start it from ours
        new = link_setup.start_serve()
    finally:
        os.chdir(here)
    cfg = link.config() or {}
    me = cfg.get("me")
    for _ in range(int(wait * 2)):
        if me and link_setup.listening(me):
            lines.append(f"listening on {me}:{link.PORT} (PID {new}, code {link.LINK_ROOT})")
            return 0, lines
        time.sleep(0.5)
    lines.append(f"NOT LISTENING after {wait:g} s (PID {new}); read {link.home() / 'serve.log'}")
    return 3, lines


# ---- verify-start -------------------------------------------------------------------------------------------------
def verify_start(since, seconds=300, now=time.time, sleep=time.sleep):
    """-> (code, note). Waits for a message after `since` that says [DONE] and carries evidence."""
    end = now() + seconds
    while True:
        for item in link.inbox(0, since):
            if item.get("kind") == "message" and item.get("evidence") and "[DONE]" in item.get("text", ""):
                return 0, item
        if now() >= end:
            return 3, None
        sleep(2)


def main(action, argv):
    ap = argparse.ArgumentParser(prog=f"fieldkit peers link {action}", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--peer", action="store_true", help="toolset: also ask the other computer and show the gaps")
    ap.add_argument("--agent", default="claude", help="startcheck: the agent a start is checked for")
    ap.add_argument("--since", type=int, help="verify-start: the inbox number to wait after (required)")
    ap.add_argument("--seconds", type=float, default=300, help="verify-start: how long to wait (default 300)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if action == "toolset":
        mine = toolset_local()
        theirs = None
        if a.peer:
            r = link.call({"verb": "hello"})
            if not r.get("ok"):
                print(f"PEER: {r.get('error')}")
                return 3
            theirs = r.get("toolset")
            if not theirs:
                print("PEER: its hello does not carry a toolset yet (link.py handle(): add toolset_local())")
                print("\n".join(toolset_lines(mine)))
                return 3
        if a.json:
            print(json.dumps({"mine": mine, "peer": theirs, "gaps": compare(mine, theirs) if theirs else None}, indent=1))
        else:
            print("\n".join(toolset_lines(mine, theirs)))
        return 3 if theirs and compare(mine, theirs) else 0
    if action == "startcheck":
        rows = startcheck(a.agent)
        for name, ok, detail in rows:
            print(f"  [{'ok' if ok else 'NO'}] {name:34} {detail}")
        bad = [r for r in rows if not r[1]]
        print("STARTCHECK  " + ("OK: a started session will run this computer's link code" if not bad else
                                f"NOT READY: {len(bad)} to fix before asking this computer to start a session"))
        return 3 if bad else 0
    if action == "restart":
        code, lines = restart()
        print("\n".join(lines))
        return code
    if action == "verify-start":
        if a.since is None:
            print("verify-start --since N (the last inbox number before the ask)")
            return 2
        code, item = verify_start(a.since, a.seconds)
        if code:
            print(f"NO PROOF: no [DONE] note with evidence after #{a.since} within {a.seconds:g} s; read the job's "
                  f"session.log in {link.home() / 'jobs'} on the computer that started it")
        else:
            print(f"PROVEN by #{item['n']} at {item['at']}: {item['text'][:200]}\n  evidence: {item['evidence'][:300]}")
        return code
    return 2
