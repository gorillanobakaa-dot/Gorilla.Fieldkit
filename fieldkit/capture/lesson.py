"""Admission and event identity for a workspace-scoped capture lesson."""

import json
import re
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import retention, store

MAX_TRANSCRIPT_ROW = 16 * 1024 * 1024


def transcript_file(path):
    retention.check_root(path.parent.absolute())
    info = path.lstat()
    return stat.S_ISREG(info.st_mode) and not retention.link(info) and info.st_nlink == 1


def transcript_rows(stream):
    while line := stream.readline(MAX_TRANSCRIPT_ROW + 1):
        if len(line) > MAX_TRANSCRIPT_ROW:
            raise ValueError("capture transcript has a row over 16 MiB")
        try:
            row = json.loads(line)
        except (ValueError, RecursionError):
            continue
        if isinstance(row, dict):
            yield row


def lock():
    # The capture directory itself can go to Trash while this lock is held.
    root = store.capture_dir().absolute()
    root.parent.mkdir(parents=True, exist_ok=True)
    return store._locked(root.parent / (root.name + ".lifecycle.lock"))


def _time(value):
    try:
        t = datetime.fromisoformat(value)
        return t if t.tzinfo is not None else None
    except (TypeError, ValueError):
        return None


def session(payload):
    return next((payload[k] for k in ("session_id", "sessionId", "sessionID", "session")
                 if isinstance(payload.get(k), str) and payload[k].strip()), None)


def session_selection(agents, requested, lookback):
    selected = {}
    for value in requested:
        agent, _, sid = value.partition("=")
        if agent not in agents or agent in selected or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", sid):
            raise ValueError("--session requires one AGENT=ID per selected agent")
        selected[agent] = sid
    if lookback and not selected.keys() & {"claude", "codex"}:
        raise ValueError("--lookback requires an explicit Claude Code or Codex transcript session")
    if "codex" in agents and "codex" not in selected:
        raise ValueError("Codex capture requires --session codex=ID")
    return selected


def since_for(lookback, now=None):
    try:
        return ((now or datetime.now(timezone.utc)) - timedelta(seconds=lookback)).isoformat(timespec="microseconds")
    except OverflowError as exc:
        raise ValueError("--lookback exceeds the supported calendar range") from exc


def scoped_source(scope, agent, payload):
    sid = session(payload) if isinstance(payload, dict) else None
    selected = scope.get("sessions", {}).get(agent)
    if agent not in scope["agents"] or not sid or (selected and sid != selected):
        return False
    cwd = payload.get("cwd") or payload.get("workdir") or payload.get("directory")
    if not isinstance(cwd, str) or not Path(cwd).is_absolute():
        return False
    try:
        return retention.within(Path(cwd).resolve(), scope["workspace"])
    except (OSError, RuntimeError, ValueError):
        return False


def admit(scope, agent, payload, observed=None, scanned=False):
    """Require source time for catch-up. Hooks without a source time use receipt time."""
    if not scoped_source(scope, agent, payload):
        return None
    received = observed or datetime.now(timezone.utc)
    source = payload.get("fk_timestamp") if scanned else payload.get("timestamp")
    timestamp = _time(source) if scanned or source is not None else received
    start = _time(scope.get("retrospective_since") if scanned and agent in scope.get("sessions", {}) and scope.get("retrospective_since")
                  else scope.get("since"))
    end = _time(scope.get("until"))
    if not timestamp or not start or not end or not start <= timestamp < end or timestamp > received:
        return None
    return timestamp.isoformat(timespec="microseconds")


def identity(agent, payload):
    call = payload.get("tool_use_id") or payload.get("toolUseId") or payload.get("tool_call_id")
    if call and session(payload):
        return "ev-call-" + store.sha(store.canon([agent, session(payload), str(call)]))
    return None


def prior_identities(payload, agent):
    eid = identity(agent, payload)
    if not eid:
        return ()
    if agent == "claude":
        call = payload.get("tool_use_id") or payload.get("toolUseId") or payload.get("tool_call_id")
        legacy = "ev-scan-" + store.sha(f"claude:{session(payload)}:{call}")[:16]
        return eid, legacy
    return (eid,)


def archived_history(root=None):
    """The archive manifest is committed only after its bundle has been verified."""
    archive = Path(root or store.capture_dir()).parent / "capture-archive"
    if archive.exists():
        retention.check_root(archive.absolute())
    for path in sorted(archive.glob("capture-*.manifest.json")):
        if path.is_file() and not retention.link(path.lstat()):
            yield json.loads(path.read_text(encoding="utf-8")).get("history", {})


def claude_call_time(payload):
    """Find the tool-use time for a Claude hook whose payload has no timestamp."""
    from .scan import claude_root

    transcript = payload.get("transcript_path") or payload.get("transcriptPath")
    if not transcript or not payload.get("tool_use_id"):
        return None
    try:
        path = Path(transcript)
        if (not path.is_absolute() or ".." in path.parts or path.suffix != ".jsonl"
                or not retention.within(path, claude_root()) or not transcript_file(path)):
            return None
        relative = path.relative_to(claude_root())
        if not ((len(relative.parts) == 2 and path.stem == session(payload))
                or (len(relative.parts) == 4 and relative.parts[1:3] == (session(payload), "subagents"))):
            return None
        with path.open("rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 4 * 1024 * 1024))
            tail = f.read().splitlines()
    except (OSError, ValueError, TypeError):
        return None
    for line in reversed(tail):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        message = row.get("message") if isinstance(row, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if (isinstance(content, list) and row.get("sessionId") == session(payload)
                and any(isinstance(b, dict) and b.get("type") == "tool_use"
                        and b.get("id") == payload["tool_use_id"] for b in content)):
            return row.get("timestamp")
    return None


def record(s, scope, agent, payload, observed=None, scanned=False):
    from . import hook

    if not scoped_source(scope, agent, payload):
        return None, False
    eid = identity(agent, payload)
    event = str(hook._first(payload, "hook_event_name", "event", "type") or "").lower()
    is_stop = event in ("stop", "subagentstop", "sessionend", "afteragent")
    name = hook._first(payload, "tool_name", "tool", "toolName")
    if not is_stop and not hook.tool_kind(name if isinstance(name, str) else ""):
        return None, False
    if agent == "claude" and not is_stop:
        if not eid:
            return None, False
        if not scanned and "timestamp" not in payload:
            timestamp = claude_call_time(payload)
            if not timestamp:
                return None, False
            payload = {**payload, "timestamp": timestamp}
    timestamp = admit(scope, agent, payload, observed, scanned)
    if not timestamp:
        return None, False
    # Keep the source timestamp and lesson association even if the process dies after begin_event.
    payload = {**payload, "_fk_lesson": scope["lesson_id"], "_fk_admitted_at": timestamp,
               "fk_prompt": scope.get("lesson", "")}
    with s.batch() as state:
        ids = prior_identities(payload, agent)
        from . import session_history

        if ids and (any(prior in state["events"] for prior in ids)
                    or session_history.events_for(s.root, agent, session(payload)).intersection(ids)
                    or any(prior in history.get("events", ()) for history in archived_history(s.root) for prior in ids)):
            return None, False
        return record_admitted(s, state, agent, payload, eid, scanned)


def record_admitted(s, state, agent, payload, eid, scanned):
    from . import hook

    if eid and eid in state["events"]:
        return None, False
    eid = s.begin_event(payload, agent, origin="scan" if scanned else "hook", eid=eid)
    rec = hook.parse(agent, payload, s)
    return s.record(rec or hook.unparsed(agent, payload), event=eid)
