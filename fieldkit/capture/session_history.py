"""Retire native transcripts only after proving closure, exact bytes and event identities."""

import hashlib
import json
import os
import re
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from . import codex_scan, lesson, retention


ROLLOUT = re.compile(r"rollout-\d{4}-\d\d-\d\dT\d\d-\d\d-\d\d-(" + codex_scan.ROLLOUT_ID.pattern + r")\.jsonl")
CALLS = {"function_call": "function_call_output", "custom_tool_call": "custom_tool_call_output"}
LIMIT = 64 * 1024 * 1024


def codex_id(path):
    match = ROLLOUT.fullmatch(path.name)
    return match[1] if match else None


def candidate_id(agent, path, root):
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        return None
    if any(c in str(path) for c in "*?"):
        return None
    if agent == "codex":
        if (root.name == "archived_sessions" and len(parts) == 1) or (
            root.name == "sessions" and len(parts) == 4 and re.fullmatch(r"\d{4}/\d{2}/\d{2}", "/".join(parts[:-1]))
        ):
            return codex_id(path)
    if agent == "claude" and root.name == "projects" and len(parts) == 2 and path.suffix == ".jsonl":
        return path.stem if codex_scan.ROLLOUT_ID.fullmatch(path.stem) else None
    if agent == "gemini" and root.name == "tmp" and len(parts) == 3 and parts[1] == "chats":
        match = re.fullmatch(r"session-[0-9T-]+-([0-9a-f]{8})\.json", path.name)
        return match[1] if match else None
    if agent == "kiro-cli" and root.name == "sessions" and len(parts) == 3 and path.name == "messages.jsonl":
        match = re.fullmatch("sess_(" + codex_scan.ROLLOUT_ID.pattern + ")", parts[1])
        return match[1] if match else None
    return None


def fingerprint(info):
    return [info.st_dev, info.st_ino, info.st_mode, info.st_nlink, info.st_size, info.st_mtime_ns,
            info.st_ctime_ns, getattr(info, "st_file_attributes", 0)]


def source_info(path, root, expected, agent="codex"):
    from .footprint import local_root

    local_root(root)
    if not candidate_id(agent, path, root):
        raise ValueError("native source does not match its adapter")
    device = retention.check_root(root).st_dev
    if any(os.path.lexists(directory / marker) for directory in (root, *root.parents)
           for marker in (".git", ".hg", ".svn")):
        raise ValueError("native source is in a checkout or worktree")
    mounts = retention.mount_paths()
    for parent in path.parents:
        if parent == root:
            break
        entry = retention.check_root(parent)
        if entry.st_dev != device or str(parent) in mounts:
            raise ValueError("native source crosses a mount")
        with os.scandir(parent) as entries:
            names = {item.name.lower() for item in entries}
        if retention.protected_directory(parent, root, names):
            raise ValueError("native source lies inside a checkout or owned scratch")
        if agent == "kiro-cli" and any(name.endswith(".lock") or name == "lock" for name in names):
            raise ValueError("Kiro native session has a lock")
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or retention.link(info) or info.st_nlink != 1
            or info.st_dev != device or str(path) in mounts
            or (os.name != "nt" and info.st_uid != os.getuid())
            or fingerprint(info) != expected):
        raise ValueError("native source is unsafe or changed since inventory")
    if agent == "claude" and os.path.lexists(path.with_suffix("")):
        raise ValueError("Claude has a companion session directory; group closure is unverified")
    return info


def quiescent(agent):
    import psutil

    match = {"codex": r"codex|openai\.codex", "claude": r"claude|claude-code",
             "gemini": r"gemini|gemini-cli", "kiro-cli": r"kiro|kiro-cli|qchat|kirocrew"}[agent]
    for process in psutil.process_iter(["name"]):
        name = process.info.get("name") or ""
        if re.match(r"^(?:" + match + r")(?:$|[. _-])", name, re.I):
            return False
        if re.match(r"^(node|bun|deno|electron)(?:$|\.)", name, re.I):
            try:
                args = process.cmdline()
            except (psutil.NoSuchProcess, psutil.ZombieProcess):
                continue
            except psutil.AccessDenied:
                return False
            if any(re.search(r"(?:^|[/\\@])(?:" + match + r")(?:$|[./\\ _-])", arg, re.I) for arg in args[:4]):
                return False
    return True


def codex_window(raw, sid, before, *, native_archive=True):
    """Accept complete JSONL with one session identity, a closed turn and paired calls."""
    if not raw.endswith(b"\n"):
        raise ValueError("transcript has a torn or empty tail")
    earliest = latest = None
    turn = None
    ended = False
    shutdown = False
    pending = {}
    events = set()
    for i, line in enumerate(raw.splitlines()):
        if len(line) > lesson.MAX_TRANSCRIPT_ROW:
            raise ValueError("transcript row exceeds 16 MiB")
        try:
            row = json.loads(line)
        except (ValueError, RecursionError):
            raise ValueError("unrecognized transcript row") from None
        if not isinstance(row, dict) or not isinstance(row.get("payload"), dict):
            raise ValueError("unrecognized transcript row")
        timestamp = lesson._time(row.get("timestamp"))
        if timestamp is None or retention.cutoff(0, timestamp) >= before:
            raise ValueError("transcript event window is unverified or reaches the cutoff")
        earliest = timestamp if earliest is None else min(earliest, timestamp)
        latest = timestamp if latest is None else max(latest, timestamp)
        payload = row["payload"]
        kind = payload.get("type")
        if i == 0 or row.get("type") == "session_meta":
            opened = lesson._time(payload.get("timestamp"))
            if (i != 0 or row.get("type") != "session_meta" or payload.get("id") != sid
                    or opened is None or retention.cutoff(0, opened) >= before):
                raise ValueError("native session metadata does not prove identity and age")
            earliest = min(earliest, opened)
        elif row.get("type") == "event_msg" and kind in ("task_started", "turn_started"):
            if turn is not None or not isinstance(payload.get("turn_id"), str) or not payload["turn_id"]:
                raise ValueError("native turn boundaries are ambiguous")
            turn = payload["turn_id"]
            ended = False
            shutdown = False
        elif row.get("type") == "event_msg" and kind in ("task_complete", "turn_complete", "turn_aborted"):
            if turn is None or payload.get("turn_id") != turn:
                raise ValueError("native turn boundaries are ambiguous")
            turn = None
            ended = True
        elif row.get("type") == "event_msg" and kind == "shutdown_complete":
            shutdown = ended
        elif row.get("type") in ("turn_context", "response_item") and ended:
            raise ValueError("activity follows the final turn without a new turn start")
        if row.get("type") != "response_item" or not isinstance(kind, str):
            continue
        call_id = payload.get("call_id")
        if kind in CALLS:
            if not isinstance(call_id, str) or not call_id or call_id in pending:
                raise ValueError("tool call identity is absent or ambiguous")
            pending[call_id] = CALLS[kind]
        elif kind in CALLS.values():
            if not isinstance(call_id, str) or pending.pop(call_id, None) != kind:
                raise ValueError("tool result lacks a matched call")
            events.add(lesson.identity("codex", {"session_id": sid, "tool_use_id": call_id}))
    if not ended or turn is not None or pending:
        raise ValueError("native transcript has an unfinished turn or call")
    if not native_archive and not shutdown:
        raise ValueError("regular Codex rollout has no native shutdown after its final turn")
    return {"start": earliest.isoformat(), "end": latest.isoformat(), "events": sorted(events)}


def _objects(raw):
    if not raw.endswith(b"\n"):
        raise ValueError("transcript has a torn or empty tail")
    for line in raw.splitlines():
        if len(line) > lesson.MAX_TRANSCRIPT_ROW:
            raise ValueError("transcript row exceeds 16 MiB")
        row = json.loads(line)
        if not isinstance(row, dict):
            raise ValueError("unrecognized native record")
        yield row


def _checked_time(value, before):
    try:
        timestamp = (datetime.fromtimestamp(value / 1000, timezone.utc)
                     if isinstance(value, int) and not isinstance(value, bool) else lesson._time(value))
    except (ValueError, OverflowError, OSError):
        timestamp = None
    if timestamp is None or retention.cutoff(0, timestamp) >= before:
        raise ValueError("transcript event window is unverified or reaches the cutoff")
    return timestamp


def _window(times, events):
    return {"start": min(times).isoformat(), "end": max(times).isoformat(), "events": sorted(events)}


def claude_window(raw, sid, before):
    times, seen, pending = [], set(), set()
    identity = ended = False
    for row in _objects(raw):
        snapshot = row.get("snapshot") if row.get("type") == "file-history-snapshot" else None
        nested = snapshot.get("timestamp") if isinstance(snapshot, dict) else None
        times.append(_checked_time(row.get("timestamp") or nested, before))
        if row.get("sessionId") is not None:
            if row["sessionId"] != sid or row.get("isSidechain"):
                raise ValueError("Claude transcript has mixed session or subagent ownership")
            identity = True
        if row.get("type") not in ("user", "assistant"):
            continue
        message = row.get("message")
        if not isinstance(message, dict) or row.get("sessionId") != sid:
            raise ValueError("unrecognized Claude user or assistant row")
        ended = row["type"] == "assistant" and message.get("stop_reason") in ("end_turn", "stop_sequence", "refusal")
        blocks = message.get("content")
        for block in blocks if isinstance(blocks, list) else ():
            if not isinstance(block, dict):
                raise ValueError("unrecognized Claude content block")
            if block.get("type") == "tool_use":
                call = block.get("id")
                if not isinstance(call, str) or not call:
                    raise ValueError("Claude call identity missing")
                pending.add(call)
            elif block.get("type") == "tool_result":
                call = block.get("tool_use_id")
                if not isinstance(call, str) or call not in pending:
                    raise ValueError("Claude result lacks a matched call")
                pending.remove(call)
                seen.update(lesson.prior_identities({"session_id": sid, "tool_use_id": call}, "claude"))
    if not identity or not ended or pending:
        raise ValueError("Claude transcript lacks a completed final response or has unfinished calls")
    return _window(times, seen)


def gemini_window(raw, prefix, before):
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("unrecognized Gemini session")
    sid = data.get("sessionId")
    if not isinstance(sid, str) or not codex_scan.ROLLOUT_ID.fullmatch(sid) or not sid.startswith(prefix):
        raise ValueError("Gemini session ID does not match its filename")
    times = [_checked_time(data.get(key), before) for key in ("startTime", "lastUpdated")]
    messages = data.get("messages")
    if (data.get("kind") not in (None, "main") or not isinstance(messages, list) or not messages
            or any(not isinstance(msg, dict) for msg in messages)):
        raise ValueError("Gemini session has unknown or linked history")
    events = set()
    turns = [msg for msg in messages if msg.get("type") in ("user", "gemini")]
    if not turns or turns[-1]["type"] != "gemini" or not turns[-1].get("content"):
        raise ValueError("Gemini transcript lacks a completed final response")
    for msg in messages:
        times.append(_checked_time(msg.get("timestamp"), before))
        calls = msg.get("toolCalls", [])
        if not isinstance(calls, list):
            raise ValueError("Gemini tool call list is unverified")
        for call in calls:
            if not isinstance(call, dict) or not isinstance(call.get("id"), str) or not call["id"] or (
                call.get("status") not in ("success", "error", "cancelled")
            ):
                raise ValueError("Gemini transcript has an unfinished tool call")
            times.append(_checked_time(call.get("timestamp"), before))
            events.add(lesson.identity("gemini", {"session_id": sid, "tool_use_id": call["id"]}))
        for thought in msg.get("thoughts") or []:
            times.append(_checked_time(thought.get("timestamp") if isinstance(thought, dict) else None, before))
    return sid, _window(times, events)


def kiro_window(raw, sid, before):
    times, events = [], set()
    pending = set()
    execution = None
    ended = False
    for row in _objects(raw):
        times.append(_checked_time(row.get("timestamp"), before))
        event_id, payload = row.get("id"), row.get("payload")
        if not isinstance(event_id, str) or not event_id or not isinstance(payload, dict):
            raise ValueError("unrecognized Kiro V3 record")
        kind, turn = payload.get("type"), payload.get("executionId")
        if kind == "turn_start":
            if execution is not None or not isinstance(turn, str) or not turn:
                raise ValueError("Kiro turn boundaries are ambiguous")
            execution, ended = turn, False
        elif kind == "turn_end":
            if execution is None or turn != execution or pending:
                raise ValueError("Kiro turn boundaries are ambiguous")
            execution, ended = None, True
        elif kind in ("user", "tool_call", "tool_result", "assistant") and ended and execution is None:
            raise ValueError("Kiro activity follows the final turn")
        if kind in ("tool_call", "tool_result"):
            call = payload.get("toolCallId") or payload.get("toolUseId")
            if not isinstance(call, str) or not call or (call in pending) == (kind == "tool_call"):
                raise ValueError("Kiro call/result identity is ambiguous")
            if kind == "tool_call":
                pending.add(call)
            else:
                pending.remove(call)
        events.add(lesson.identity("kiro-cli", {"session_id": sid, "tool_use_id": event_id}))
    if not ended or execution is not None or pending:
        raise ValueError("Kiro transcript has an unfinished turn")
    return _window(times, events)


def _read(path, expected, limit):
    info = path.lstat()
    if (retention.link(info) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or info.st_size > limit or (expected is not None and fingerprint(info) != expected)):
        raise ValueError("unsafe, oversized or changed archive input")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        if fingerprint(os.fstat(stream.fileno())) != fingerprint(info):
            raise ValueError("archive input changed before reading")
        raw = stream.read(limit + 1)
        if len(raw) != info.st_size or fingerprint(os.fstat(stream.fileno())) != fingerprint(info):
            raise ValueError("archive input changed while reading")
    if fingerprint(path.lstat()) != fingerprint(info):
        raise ValueError("archive input changed while reading")
    return raw


def _persist(path, raw, limit):
    if not os.path.lexists(path):
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".session-", delete=False) as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
            temporary = Path(stream.name)
        # Capture's lifecycle lock serializes writers. Keep a recoverable temporary file if publishing fails.
        os.replace(temporary, path)
        if os.name != "nt":
            fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    if _read(path, None, limit) != raw:
        raise ValueError("archive read-back does not match")


def archive_dir(capture):
    return Path(capture).absolute().parent / "capture-session-archive"


def archive(candidate, before, capture):
    path, root = Path(candidate["path"]), Path(candidate["root"])
    agent = candidate["agent"]
    expected = candidate["fingerprint"]
    info = source_info(path, root, expected, agent)
    if before is not None and info.st_mtime_ns >= before:
        raise ValueError("native source reaches the cutoff")
    raw = _read(path, expected, LIMIT)
    sid = candidate_id(agent, path, root)
    threshold = retention.cutoff(0) if before is None else before
    if agent == "codex":
        window = codex_window(raw, sid, threshold, native_archive=root.name == "archived_sessions")
    elif agent == "claude":
        window = claude_window(raw, sid, threshold)
    elif agent == "gemini":
        sid, window = gemini_window(raw, sid, threshold)
    else:
        window = kiro_window(raw, sid, threshold)
    target = archive_dir(capture)
    from .footprint import local_root

    local_root(target)
    retention.check_root(target.parent)
    target.mkdir(mode=0o700, exist_ok=True)
    retention.check_root(target)
    if os.name != "nt" and (target.stat().st_uid != os.getuid() or target.stat().st_mode & 0o077):
        raise ValueError("session archive must be private to its owner")
    digest = hashlib.sha256(raw).hexdigest()
    stem = agent + "-" + sid + "-" + digest
    bundle = target / (stem + path.suffix)
    manifest = target / (stem + ".manifest.json")
    evidence = {"agent": agent, "session": sid, "source": str(path), "sha256": digest,
                "bytes": len(raw), "event_window": [window["start"], window["end"]],
                "history": {"events": window["events"]}, "cutoff_ns": before,
                "disposition": "native identity, closed session, complete event window and verified original bytes"}
    try:
        _persist(bundle, raw, LIMIT)
        if os.path.lexists(manifest):
            previous = json.loads(_read(manifest, None, LIMIT))
            previous["cutoff_ns"] = before
            if previous != evidence:
                raise ValueError("retirement manifest does not match its verified source")
        else:
            _persist(manifest, json.dumps(evidence, sort_keys=True).encode("utf-8"), LIMIT)
    except (OSError, ValueError, TypeError, RecursionError) as exc:
        raise OSError(f"session archive verification failed: {exc}") from exc
    source_info(path, root, expected, agent)
    return {"archive": str(bundle), "manifest": str(manifest), "sha256": digest, "events": len(window["events"])}


def events_for(capture, agent, sid):
    if agent not in ("codex", "claude", "gemini", "kiro-cli") or not isinstance(sid, str) or not codex_scan.ROLLOUT_ID.fullmatch(sid):
        return set()
    root = archive_dir(capture)
    if not os.path.lexists(root):
        return set()
    retention.check_root(root)
    events = set()
    for manifest in root.glob(f"{agent}-{sid}-*.manifest.json"):
        data = json.loads(_read(manifest, None, LIMIT))
        if data.get("agent") == agent and data.get("session") == sid:
            events.update(data["history"]["events"])
    return events
