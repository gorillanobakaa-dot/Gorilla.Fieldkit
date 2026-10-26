"""What the Claude desktop app keeps about its Code sessions, read-only: the session records, the cards a session
proposed that nobody started, the questions waiting on a person, and the processes each live session holds.

Sources (nothing here writes to any of them):
    <app data>/claude-code-sessions/<account>/<org>/local_*.json   one record per Code session (title, isArchived,
                                                                   permissionMode, lastActivityAt, prs, bridgeSessionIds
                                                                   = Remote Control, resolvedBackgroundTaskSuggestions)
    <app data>/claude-code-sessions/<account>/<org>/archived-sessions.idx
    <claude config>/projects/*/<cliSessionId>.jsonl                 the session's transcript (cards, pending questions)
    the processes: each session's claude CLI carries CLAUDE_CODE_HOST_SESSION_ID in its environment. ONLY that one
    variable is read; the rest of the environment (it holds the session's OAuth token) is never kept, logged or shown.

App data folders by OS (FIELDKIT_CLAUDE_APP_DIRS, a path list, overrides them for tests):
    Windows  %LOCALAPPDATA%\\Claude-Data\\*\\ (the MSIX app) and %APPDATA%\\Claude\\
    Linux    ~/.config/Claude/
    macOS    ~/Library/Application Support/Claude/
"""
import json
import os
import re
import sys
import time
from pathlib import Path

CARD = re.compile(r'Proposed "(?P<title>.+?)" \(task_id: (?P<id>task_\w+)\)')
CARD_DAYS = 7                                   # the app drops an unstarted card after a week
WAITING_TOOLS = ("AskUserQuestion", "ExitPlanMode")
HOST_ENV = "CLAUDE_CODE_HOST_SESSION_ID"
CLI_NAMES = ("claude", "claude.exe")


def app_dirs(platform=None, env=None):
    env = os.environ if env is None else env
    if env.get("FIELDKIT_CLAUDE_APP_DIRS"):
        return [Path(p) for p in env["FIELDKIT_CLAUDE_APP_DIRS"].split(os.pathsep) if p]
    platform = platform or sys.platform
    home = Path(env.get("HOME") or env.get("USERPROFILE") or Path.home())
    if platform == "win32":
        out = []
        local = env.get("LOCALAPPDATA")
        if local:
            out += sorted(Path(local, "Claude-Data").glob("*"))
        if env.get("APPDATA"):
            out.append(Path(env["APPDATA"], "Claude"))
        return out
    if platform == "darwin":
        return [home / "Library" / "Application Support" / "Claude"]
    return [home / ".config" / "Claude"]


def store_dirs(dirs=None):
    """-> the folders that hold local_*.json session records."""
    out = []
    for d in dirs if dirs is not None else app_dirs():
        out += [p for p in Path(d).glob("claude-code-sessions/*/*") if p.is_dir()]
    return out


def _archived_idx(folder):
    try:
        return set(json.loads((folder / "archived-sessions.idx").read_text(encoding="utf-8")).get("archived") or [])
    except (OSError, ValueError):
        return set()


def sessions(dirs=None):
    """-> [record] for every Code session the app knows here, with the fields the audit and the sweep use."""
    out = []
    for folder in store_dirs(dirs):
        arch = _archived_idx(folder)
        for p in sorted(folder.glob("local_*.json")):
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            sid = d.get("sessionId") or p.stem
            out.append({
                "id": sid, "title": d.get("title") or "", "cli": d.get("cliSessionId") or "",
                "archived": bool(d.get("isArchived")) or sid in arch,
                "mode": d.get("permissionMode") or "",
                "last": (d.get("lastActivityAt") or 0) / 1000.0,
                "awaiting": d.get("interruptedAwaitingPerson"),
                "remote": bool(d.get("bridgeSessionIds")),
                "prs": [{"url": x.get("url") or "", "state": (x.get("state") or "").upper(),
                         "branch": x.get("branch") or ""} for x in d.get("prs") or [] if isinstance(x, dict)],
                "resolved": dict(d.get("resolvedBackgroundTaskSuggestions") or {}),
                "parent": (d.get("spawnedFrom") or {}).get("sessionId") or "",
                "exempt": bool(d.get("autoArchiveExempt")),
                "cwd": d.get("cwd") or "", "file": str(p),
            })
    return out


def config_dirs(env=None):
    env = os.environ if env is None else env
    out = []
    if env.get("CLAUDE_CONFIG_DIR"):
        out.append(Path(env["CLAUDE_CONFIG_DIR"]))
    out.append(Path(env.get("HOME") or env.get("USERPROFILE") or Path.home()) / ".claude")
    return out


def transcript(cli, env=None):
    if not cli:
        return None
    for c in config_dirs(env):
        for p in c.glob(f"projects/*/{cli}.jsonl"):
            return p
    return None


def _ts(rec):
    t = rec.get("timestamp")
    if not t:
        return None
    try:
        from datetime import datetime
        return datetime.fromisoformat(str(t).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def scan(path, now=None):
    """One pass over a transcript -> {cards: [{id, title, at}], waiting: tool name or ""}.
    A card is a start_session result that says "Proposed ... (task_id: X)" and was neither started (start_session with
    proposal_id X), dismissed (dismiss_task X) nor older than a week. `waiting` is a question tool (AskUserQuestion,
    ExitPlanMode) the session called last and that has no answer yet."""
    now = now or time.time()
    proposed, gone, uses, last_use, answered = {}, set(), {}, None, set()
    try:
        fh = open(path, encoding="utf-8", errors="replace")
    except OSError:
        return {"cards": [], "proposals": [], "waiting": ""}
    with fh:
        for line in fh:
            if '"tool_' not in line:                     # cheap filter: only lines with tool calls or results
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            content = (d.get("message") or {}).get("content")
            if not isinstance(content, list):
                continue
            for b in content:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "tool_use":
                    name, inp = str(b.get("name") or ""), b.get("input") or {}
                    uses[b.get("id")] = name
                    last_use = (b.get("id"), name.rsplit("__", 1)[-1])
                    if name.endswith("start_session") and inp.get("proposal_id"):
                        gone.add(inp["proposal_id"])
                    if name.endswith("dismiss_task") and inp.get("task_id"):
                        gone.add(inp["task_id"])
                elif b.get("type") == "tool_result":
                    answered.add(b.get("tool_use_id"))
                    if str(uses.get(b.get("tool_use_id")) or "").endswith("start_session"):
                        text = json.dumps(b.get("content"))
                        m = CARD.search(text.replace('\\"', '"'))
                        if m:
                            proposed[m["id"]] = {"id": m["id"], "title": m["title"], "at": _ts(d)}
    cards = [c for k, c in proposed.items() if k not in gone and (c["at"] is None or now - c["at"] < CARD_DAYS * 86400)]
    waiting = last_use[1] if last_use and last_use[0] not in answered and last_use[1] in WAITING_TOOLS else ""
    return {"cards": cards, "proposals": list(proposed.values()), "waiting": waiting}


def processes(interval=0.0, psutil_mod=None):
    """-> {host session id: {pids, rss, cpu_pct, cpu_s}} for every live session's claude CLI and its children.
    cpu_pct is measured over `interval` seconds (0 = not measured, only the CPU seconds so far)."""
    try:
        import psutil
    except ImportError:
        return {}
    psutil = psutil_mod or psutil
    roots = {}
    for p in psutil.process_iter(["name"]):
        if (p.info.get("name") or "").lower() not in CLI_NAMES:
            continue
        try:
            sid = p.environ().get(HOST_ENV)       # one variable, never the rest of the environment
        except (psutil.Error, OSError):
            continue
        if sid:
            roots.setdefault(sid, []).append(p)
    out = {}
    for sid, procs in roots.items():
        tree = []
        for p in procs:
            try:
                tree += [p] + p.children(recursive=True)
            except psutil.Error:
                tree.append(p)
        out[sid] = {"procs": tree}
    if interval:
        for v in out.values():
            for p in v["procs"]:
                try:
                    p.cpu_percent(None)
                except psutil.Error:
                    pass
        time.sleep(interval)
    for sid, v in out.items():
        rss = cpu_s = pct = 0.0
        pids = []
        for p in v.pop("procs"):
            try:
                with p.oneshot():
                    rss += p.memory_info().rss
                    t = p.cpu_times()
                    cpu_s += t.user + t.system
                    if interval:
                        pct += p.cpu_percent(None)
                    pids.append(p.pid)
            except psutil.Error:
                continue
        v.update(pids=pids, rss=int(rss), cpu_s=round(cpu_s, 1), cpu_pct=round(pct, 1) if interval else None)
    return out
