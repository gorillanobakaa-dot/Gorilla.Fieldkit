"""`fieldkit capture scan`: journal the tool calls an agent's transcripts hold that no hook captured (P26).

The hook sees a call as it happens; a session that ran while capture was off, or with its hooks stripped, left only its
transcript. The scan reads the transcript, turns each work call (Bash, PowerShell, Write, Edit, MultiEdit,
NotebookEdit) and its result into the same payload the hook gets, and journals it through the same parse + record path,
so an item looks the same whichever way it arrived.

Idempotent: every call has the event id `ev-scan-` + sha(agent:session:tool_use_id). A call whose event id is already
in the journal is skipped, so a second run writes nothing (the hash chain is only ever appended to). A session that
already has items from a hook is left to the hook (a scan of it would count its calls twice); `--all` is not offered.

Readers: Claude Code (~/.claude/projects/*/*.jsonl and subagents/, the same layout on Windows). Scoped lessons
also read the explicitly selected Codex rollout. Gemini and OpenCode use hooks.
"""
import os
from pathlib import Path
from datetime import datetime, timezone

from . import codex_scan, hook
from . import lesson
from . import store as st

WORK = {"bash", "powershell", "write", "edit", "multiedit", "notebookedit"}


def session_of(root, p):
    parts = p.relative_to(root).parts
    return parts[1] if len(parts) >= 3 and parts[2] == "subagents" else p.stem


def _text(c):
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "\n".join(x["text"] for x in c if isinstance(x, dict) and x.get("type") == "text"
                         and isinstance(x.get("text"), str))
    return ""


def calls(path, scope=None, observed=None):
    """Yield one hook-style payload per work call in a Claude Code transcript, in order. A call with no result in the
    file (the session died mid-call) is still yielded, with an empty result."""
    pending, order, prompt = {}, [], ""
    try:
        if scope and not lesson.transcript_file(path):
            raise ValueError("selected Claude Code transcript is linked or is not a regular file")
        f = path.open("rb")
    except OSError:
        if scope:
            raise
        return
    with f:
        for e in lesson.transcript_rows(f):
            msg = e.get("message")
            if not isinstance(msg, dict):
                continue
            content = msg.get("content")
            if not scope and e.get("type") == "user" and not e.get("isSidechain") and isinstance(content, (str, list)):
                is_result = isinstance(content, list) and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content)
                t = content.strip() if isinstance(content, str) else _text(content).strip()
                if t and not t.startswith("<") and not is_result:
                    prompt = st.redact_text(t)[:2000]
            if not isinstance(content, list):
                continue
            for b in content:
                if not isinstance(b, dict):
                    continue
                call_id = b.get("id") if b.get("type") == "tool_use" else b.get("tool_use_id")
                if not isinstance(call_id, str) or not call_id:
                    continue
                if b.get("type") == "tool_use" and str(b.get("name", "")).lower() in WORK:
                    p = {"hook_event_name": "PostToolUse", "tool_name": b["name"], "tool_input": b.get("input") or {},
                         "tool_use_id": call_id, "session_id": e.get("sessionId") or "", "cwd": e.get("cwd") or "",
                         "fk_timestamp": e.get("timestamp"), "fk_prompt": prompt, "tool_response": {"stdout": ""}}
                    # For scoped scans retain only metadata for rejected calls so results can be counted without
                    # retaining their content. Admitted pairs are yielded as soon as their result arrives.
                    if scope and not lesson.admit(scope, "claude", p, observed, scanned=True):
                        p = None
                    if scope and len(pending) >= 4096 and call_id not in pending:
                        raise ValueError("Claude Code transcript has more than 4096 unpaired work calls")
                    pending[call_id] = p
                    if not scope:
                        order.append(call_id)
                elif b.get("type") == "tool_result" and call_id in pending:
                    p = pending.pop(call_id) if scope else pending[call_id]
                    if scope and not _claude_result_in_scope(scope, p, e, observed):
                        yield {}
                        continue
                    p["_fk_complete"] = True
                    out = _text(b.get("content"))
                    if b.get("is_error"):
                        p["error"] = out or "tool error"
                    p["tool_response"] = {"stdout": "" if b.get("is_error") else out}
                    if scope:
                        yield p
    for i in order:
        yield pending[i]


def _claude_result_in_scope(scope, call, result, observed):
    if not call or result.get("sessionId") != call["session_id"]:
        return False
    started = lesson._time(call["fk_timestamp"])
    ended = lesson._time(result.get("timestamp"))
    return bool(ended and started <= ended and lesson.admit(
        scope, "claude", {**call, "fk_timestamp": result.get("timestamp"), "cwd": result.get("cwd", call["cwd"])},
        observed, scanned=True))


def scan(store=None, home=None, limit=None, say=None, scope=None):
    """-> counts. Journals every not yet captured work call of every Claude Code transcript under home."""
    store = store or st.Store()
    codex_home = Path(home) if home is not None else None
    root = claude_root(home)
    if scope:
        return _scoped_scan(store, root, scope, limit, codex_home)
    state = store.state()
    hooked = {d.get("session") for d in state["items"].values()          # a scan's own items never mark a session done
              if d.get("session") and not str(d.get("origin", "")).startswith("scan")}
    seen = set(state["events"])
    for history in lesson.archived_history(store.root):
        seen.update(history.get("events", ()))
        hooked.update(history.get("hooked_claude", ()))
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
            pl.pop("_fk_complete", None)
            first = first or pl
            pl["session_id"] = sid
            eid = "ev-scan-" + st.sha(f"claude:{sid}:{pl['tool_use_id']}")[:16]
            if eid in seen or lesson.identity("claude", pl) in seen:
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


def claude_root(home=None):
    if home is not None:
        return Path(home) / ".claude" / "projects"
    return Path(os.getenv("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "projects"


def _claude_paths(root, sid):
    try:
        lesson.retention.check_root(root.absolute())
    except OSError:
        return
    for path in sorted(root.glob(f"*/{sid}.jsonl")):
        try:
            if lesson.transcript_file(path):
                yield path
        except OSError:
            continue


def verify_sessions(scope):
    errors = {}
    for agent, sid in scope.get("sessions", {}).items():
        if agent == "codex":
            try:
                path = codex_scan.session_path(sid)
                if path and not scope.get("retrospective_since"):
                    scope["codex_cursor"] = codex_scan.cursor(path)
                cwd = codex_scan.metadata(path, sid).get("cwd") if path else None
                matches = isinstance(cwd, str) and Path(cwd).is_absolute() and lesson.retention.within(
                    Path(cwd).resolve(), scope["workspace"])
                if matches and "codex_cursor" in scope:
                    scope["codex_cursor"]["cwd"] = cwd
            except (OSError, ValueError, RuntimeError):
                matches = False
            if not matches:
                errors[agent] = "selected Codex rollout is missing, ambiguous, or outside the lesson workspace"
        elif agent == "claude" and scope.get("retrospective_since"):
            if len(list(_claude_paths(claude_root(), sid))) != 1:
                errors[agent] = "selected Claude Code transcript was not found or is ambiguous"
    return errors


def _sources(root, home, scope, observed):
    selected = scope.get("sessions", {})
    if scope.get("retrospective_since") and "claude" in scope["agents"] and (sid := selected.get("claude")):
        paths = list(_claude_paths(root, sid))
        if len(paths) != 1:
            raise ValueError("selected Claude Code transcript was not found or is ambiguous")
        for path in paths:
            yield "claude", calls(path, scope, observed)
    if "codex" in scope["agents"] and (sid := selected.get("codex")):
        cursor = scope.get("codex_cursor")
        path = Path(cursor["path"]) if cursor else codex_scan.session_path(sid, home)
        if not path:
            raise ValueError("selected Codex rollout is missing or ambiguous")
        if cursor or scope.get("retrospective_since"):
            yield "codex", codex_scan.calls(path, sid, scope, observed, cursor)


def _scoped_scan(store, root, scope, limit, home=None):
    out = {"files": 0, "sessions": 0, "skipped_hooked": 0, "calls": 0, "new_items": 0,
           "repeats": 0, "already": 0, "out_of_scope": 0}
    if not scope.get("retrospective_since") and not scope.get("codex_cursor"):
        return out
    sessions = set()
    observed = datetime.now(timezone.utc)
    seen = {eid for history in lesson.archived_history(store.root) for eid in history.get("events", ())}
    with store.batch() as state:
        for agent, payloads in _sources(root, home, scope, observed):
            out["files"] += 1
            if agent == "codex":
                from . import session_history

                seen.update(session_history.events_for(store.root, agent, scope["sessions"][agent]))
            for payload in payloads:
                timestamp = lesson.admit(scope, agent, payload, observed, scanned=True)
                if not timestamp:
                    out["out_of_scope"] += 1
                    continue
                ids = lesson.prior_identities(payload, agent)
                if any(eid in state["events"] or eid in seen for eid in ids):
                    out["already"] += 1
                    continue
                payload.update(_fk_lesson=scope["lesson_id"], _fk_admitted_at=timestamp,
                               fk_prompt=scope.get("lesson", ""))
                _, created = lesson.record_admitted(store, state, agent, payload, ids[0], True)
                out["calls"] += 1
                out["new_items" if created else "repeats"] += 1
                sessions.add(lesson.session(payload))
                if limit is not None and out["calls"] >= limit:
                    return {**out, "sessions": len(sessions), "limited": True}
    return {**out, "sessions": len(sessions)}
