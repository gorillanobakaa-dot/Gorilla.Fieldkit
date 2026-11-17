"""Admission and event identity for a workspace-scoped capture lesson."""

import json
from datetime import datetime, timezone
from pathlib import Path

from . import retention, store


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


def admit(scope, agent, payload, observed=None, scanned=False):
    """Require source time for catch-up. Hooks without a source time use receipt time."""
    if not isinstance(payload, dict) or agent not in scope["agents"] or not session(payload):
        return None
    cwd = payload.get("cwd") or payload.get("workdir") or payload.get("directory")
    if not isinstance(cwd, str) or not Path(cwd).is_absolute():
        return None
    try:
        if not retention.within(Path(cwd).resolve(), scope["workspace"]):
            return None
    except (OSError, RuntimeError, ValueError):
        return None
    received = observed or datetime.now(timezone.utc)
    source = payload.get("fk_timestamp") if scanned else payload.get("timestamp")
    timestamp = _time(source) if scanned or source is not None else received
    start, end = _time(scope.get("since")), _time(scope.get("until"))
    if not timestamp or not start or not end or not start <= timestamp < end or timestamp > received:
        return None
    return timestamp.isoformat(timespec="microseconds")


def identity(agent, payload):
    call = payload.get("tool_use_id") or payload.get("toolUseId") or payload.get("tool_call_id")
    if call and session(payload):
        return "ev-call-" + store.sha(store.canon([agent, session(payload), str(call)]))
    return None


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
    transcript = payload.get("transcript_path") or payload.get("transcriptPath")
    if not transcript or not payload.get("tool_use_id"):
        return None
    try:
        path = Path(transcript)
        if path.suffix != ".jsonl" or not retention.within(path.resolve(), Path.home() / ".claude" / "projects"):
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
        if isinstance(content, list) and any(isinstance(b, dict) and b.get("type") == "tool_use"
                                            and b.get("id") == payload["tool_use_id"] for b in content):
            return row.get("timestamp")
    return None


def record(s, scope, agent, payload, observed=None, scanned=False):
    from . import hook

    if not isinstance(payload, dict):
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
        return record_admitted(s, state, agent, payload, eid, scanned)


def record_admitted(s, state, agent, payload, eid, scanned):
    from . import hook

    if eid and eid in state["events"]:
        return None, False
    eid = s.begin_event(payload, agent, origin="scan" if scanned else "hook", eid=eid)
    rec = hook.parse(agent, payload, s)
    return s.record(rec or hook.unparsed(agent, payload), event=eid)
