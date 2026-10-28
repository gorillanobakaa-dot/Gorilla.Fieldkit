"""The PID registry: the only processes the hog may ever stop are the ones written here.

An entry is (pid, creation time) plus the session process that started it (pid, creation time) and a cap. The hog
stops an entry only while BOTH numbers still match the live process, so a PID reused by one of the owner's programs
is never touched (memory: stop-only-what-I-started). Never by name.

How entries get here, all deterministic:
  observe()   the tool-call hook (fieldkit.autopilot.room.pretool) calls it before every shell call: the
              session process is the nearest claude/opencode/gemini ancestor of the hook; its live descendants of an
              orphan class (thresholds.json orphan_classes: test runners, conhost/tui_shots, link listeners,
              browsers) are recorded, and so is any PID the call's output printed (pid=N, PID N, Id : N) that was
              created in the last 15 minutes and is of an orphan class (Start-Process detaches from the tree).
  register    `fieldkit hog register PID [--cap 3h] [--why TEXT]` for a tool that starts something long-lived.
Local file state/hog/pids.jsonl (git-ignored). Dead entries are pruned by every watch pass.
"""
import json
import os
import re
import time
from pathlib import Path

import psutil

SESSION_NAMES = ("claude", "claude.exe", "opencode", "opencode.exe", "gemini", "gemini.exe", "antigravity.exe")
PID_IN_TEXT = re.compile(r"(?:\bpid\s*[=:]?\s*|\bProcessId\s*[=:]?\s*|\bId\s*:\s*)(\d{2,7})\b", re.I)
RECENT_S = 900


def ct(x):
    """Creation times compared to 2 decimals everywhere (the registry, the live table): a float round trip through
    JSON must not make the same process look like a different one. Here, not in probe.py: the tool-call hook imports
    this module and probe.py pulls in urllib (C6, ~30 ms per call)."""
    return round(float(x), 2)


def state_dir():
    from ..core import settings
    d = Path(os.environ.get("FIELDKIT_HOG_DIR") or (settings.state_root() / "state" / "hog"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def path():
    return state_dir() / "pids.jsonl"


def load():
    p = path()
    if not p.is_file():
        return []
    out = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def save(entries):
    tmp = path().with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")
    os.replace(tmp, path())


def add(entries):
    """Append the entries not already registered (same pid and creation time). -> the ones added."""
    have = {(e["pid"], e["ctime"]) for e in load()}
    new = [e for e in entries if (e["pid"], e["ctime"]) not in have]
    if new:
        with open(path(), "a", encoding="utf-8") as f:
            for e in new:
                f.write(json.dumps(e) + "\n")
    return new


def prune(alive):
    alive = {(a[0], a[1]) for a in alive}
    keep = [e for e in load() if (e["pid"], e["ctime"]) in alive]
    save(keep)
    return keep


def entry(proc, cls, session=None, owner=None, cap_s=None, source="", why="", now=None):
    try:
        cmd = " ".join(proc.cmdline())[:200]
    except psutil.Error:
        cmd = ""
    return {"pid": proc.pid, "ctime": ct(proc.create_time()), "name": proc.name(), "cls": cls, "cmd": cmd,
            "session": session, "owner_pid": owner[0] if owner else None, "owner_ctime": owner[1] if owner else None,
            "cap_s": cap_s, "source": source, "why": why, "t": now or time.time()}


def session_process(me=None):
    """-> psutil.Process of the agent session that runs this hook, or None."""
    try:
        for a in (me or psutil.Process()).parents():
            if a.name().lower() in SESSION_NAMES:
                return a
    except psutil.Error:
        pass
    return None


#: a shell's command line holds the whole command it was given ("... | grep pytest"): the runner it starts is its child,
#: and that child is what gets registered (measured 2026-10-26: the Bash tool's bash.exe matched "pytest")
SHELLS = {"bash", "bash.exe", "sh", "sh.exe", "zsh", "cmd.exe", "powershell.exe", "pwsh", "pwsh.exe"}


def _cls(proc, classes):
    from .rules import orphan_class
    try:
        if proc.name().lower() in SHELLS:
            return None
        return orphan_class(proc.name(), " ".join(proc.cmdline()), classes)
    except psutil.Error:
        return None


def _ppid(pid):
    try:
        return psutil.Process(pid).ppid()
    except psutil.Error:
        return None


def observe(call, classes, now=None):
    """The room hook's half: register this session's orphan-class processes. Never raises."""
    try:
        now = now or time.time()
        sess = session_process()
        if not sess:
            return []
        owner = (sess.pid, ct(sess.create_time()))
        sid = call.get("session_id")
        me = os.getpid()
        found = []
        direct = {c.pid for c in sess.children()}           # the agent's own shells and MCP servers
        for p in sess.children(recursive=True):
            if p.pid == me or p.pid in direct:
                continue
            c = _cls(p, classes)
            if c:
                found.append(entry(p, c, sid, owner, source="tree", now=now))
        # the console host of a registered process (a windowless start gets one) dies with it: not its own entry
        pids = {e["pid"] for e in found}
        found = [e for e in found if not (e["cls"] == "conhost" and _ppid(e["pid"]) in pids)]
        out = json.dumps(call.get("tool_response") or "")[:20000]
        for m in PID_IN_TEXT.finditer(out):
            try:
                p = psutil.Process(int(m.group(1)))
                if now - p.create_time() > RECENT_S:
                    continue
                c = _cls(p, classes)
                if c:
                    found.append(entry(p, c, sid, owner, source="printed", now=now))
            except (psutil.Error, ValueError):
                continue
        return add(found)
    except Exception:
        return []
