"""`fieldkit capture scan`: journal the tool calls an agent's transcripts hold that no hook captured (P26).

The hook sees a call as it happens; a session that ran while capture was off, or with its hooks stripped, left only its
transcript. The scan reads the transcript, turns each work call (Bash, PowerShell, Write, Edit, MultiEdit,
NotebookEdit) and its result into the same payload the hook gets, and journals it through the same parse + record path,
so an item looks the same whichever way it arrived.

Idempotent: every call has the event id `ev-scan-` + sha(agent:session:tool_use_id). A call whose event id is already
in the journal is skipped, so a second run writes nothing (the hash chain is only ever appended to). A session that
already has items from a hook is left to the hook (a scan of it would count its calls twice); `--all` is not offered.

Readers: Claude Code (~/.claude/projects/*/*.jsonl and subagents/, the same layout on Windows). Gemini keeps binary
protobuf and OpenCode's store is not on every computer: they are listed by `capture coverage`, no reader yet.
"""
import json
import os
from pathlib import Path

from . import hook
from . import store as st

WORK = {"bash", "powershell", "write", "edit", "multiedit", "notebookedit"}


def session_of(root, p):
    parts = p.relative_to(root).parts
    return parts[1] if len(parts) >= 3 and parts[2] == "subagents" else p.stem


def _text(c):
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "\n".join(x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text")
    return ""


def calls(path):
    """Yield one hook-style payload per work call in a Claude Code transcript, in order. A call with no result in the
    file (the session died mid-call) is still yielded, with an empty result."""
    pending, order, prompt = {}, [], ""
    try:
        f = open(path, "r", encoding="utf-8", errors="replace")
    except OSError:
        return
    with f:
        for line in f:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            msg = e.get("message") if isinstance(e, dict) else None
            if not isinstance(msg, dict):
                continue
            content = msg.get("content")
            if e.get("type") == "user" and not e.get("isSidechain") and isinstance(content, (str, list)):
                is_result = isinstance(content, list) and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content)
                t = content.strip() if isinstance(content, str) else _text(content).strip()
                if t and not t.startswith("<") and not is_result:
                    prompt = st.redact_text(t)[:2000]
            if not isinstance(content, list):
                continue
            for b in content:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "tool_use" and str(b.get("name", "")).lower() in WORK and b.get("id"):
                    pending[b["id"]] = {"hook_event_name": "PostToolUse", "tool_name": b["name"],
                                        "tool_input": b.get("input") or {}, "tool_use_id": b["id"],
                                        "session_id": e.get("sessionId") or "", "cwd": e.get("cwd") or "",
                                        "fk_timestamp": e.get("timestamp"), "fk_prompt": prompt,
                                        "tool_response": {"stdout": ""}}
                    order.append(b["id"])
                elif b.get("type") == "tool_result" and b.get("tool_use_id") in pending:
                    p = pending[b["tool_use_id"]]
                    out = _text(b.get("content"))
                    if b.get("is_error"):
                        p["error"] = out or "tool error"
                    p["tool_response"] = {"stdout": "" if b.get("is_error") else out}
    for i in order:
        yield pending[i]


def scan(store=None, home=None, limit=None, say=None):
    """-> counts. Journals every not yet captured work call of every Claude Code transcript under home."""
    store = store or st.Store()
    home = Path(home or os.path.expanduser("~"))
    root = home / ".claude" / "projects"
    state = store.state()
    hooked = {d.get("session") for d in state["items"].values()          # a scan's own items never mark a session done
              if d.get("session") and not str(d.get("origin", "")).startswith("scan")}
    seen = set(state["events"])
    out = {"files": 0, "sessions": 0, "skipped_hooked": 0, "calls": 0, "new_items": 0, "repeats": 0, "already": 0}
    sessions = set()
    pending = []                                           # (session, first payload, new calls) of transcripts with news

    def flush():
        """One lock and one index write for a group of transcripts (the index is 100+ MB: 15 s per write)."""
        if not pending:
            return
        with store.batch():
            for sid, first, new_calls in pending:
                for pl, eid in new_calls:
                    rec = hook.parse("claude", pl, store)
                    if rec is None:
                        continue
                    store.begin_event(pl, "claude", origin="scan", eid=eid)
                    _iid, new = store.record(rec, event=eid)
                    seen.add(eid)
                    sessions.add(sid)
                    out["calls"] += 1
                    out["new_items" if new else "repeats"] += 1
                eid = "ev-scan-" + st.sha(f"claude:{sid}:session")[:16]
                if eid not in seen:                        # the session marker: coverage counts a session by its items
                    pl = {"hook_event_name": "Stop", "session_id": sid, "cwd": first["cwd"],
                          "fk_timestamp": first["fk_timestamp"], "fk_prompt": first["fk_prompt"]}
                    store.begin_event(pl, "claude", origin="scan", eid=eid)
                    store.record(hook.parse("claude", pl, store), event=eid)
                    seen.add(eid)
                    sessions.add(sid)
        pending.clear()

    taken = 0
    for p in sorted(root.glob("*/**/*.jsonl")):
        sid = session_of(root, p)
        if sid in hooked:
            out["skipped_hooked"] += 1
            continue
        out["files"] += 1
        first, new_calls, limited = None, [], False
        for pl in calls(p):                                # parsed outside the lock: nothing new, no lock, no write
            first = first or pl
            pl["session_id"] = sid
            eid = "ev-scan-" + st.sha(f"claude:{sid}:{pl['tool_use_id']}")[:16]
            if eid in seen:
                out["already"] += 1
                continue
            if limit is not None and taken >= limit:
                limited = True
                break
            taken += 1
            new_calls.append((pl, eid))
        marker = "ev-scan-" + st.sha(f"claude:{sid}:session")[:16]
        if first is not None and (new_calls or marker not in seen):
            pending.append((sid, first, new_calls))
            if len(pending) >= 8 or limited:
                flush()
        if limited:
            return dict(out, sessions=len(sessions), limited=True)
        if say:
            say(f"scanned {p.name}")
    flush()
    out["sessions"] = len(sessions)
    return out
