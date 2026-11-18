"""Read paired work calls from one verified Codex rollout."""

import json
import os
import re
from contextlib import closing
from pathlib import Path

from . import lesson, retention

ROLLOUT_ID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")
EXIT = re.compile(r"(?m)^Process exited with code (-?[0-9]+)\s*$")
PATCH_PATH = re.compile(r"(?m)^\*\*\* (?:Add|Delete|Update) File: (.+)$")
PATCH_MOVE = re.compile(r"(?m)^\*\*\* Move to: (.+)$")


def _json_object(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, RecursionError):
            return {}
    return value if isinstance(value, dict) else {}


def cursor(path):
    if not lesson.transcript_file(path):
        raise ValueError("selected Codex rollout is linked or is not a regular file")
    info = path.lstat()
    with path.open("rb") as stream:
        if not info.st_size:
            raise ValueError("selected Codex rollout is empty")
        stream.seek(info.st_size - 1)
        if stream.read(1) != b"\n":
            raise ValueError("selected Codex rollout has an incomplete tail")
    return {"path": str(path), "device": info.st_dev, "inode": info.st_ino, "offset": info.st_size}


def _read_rows(path, start=None):
    if not lesson.transcript_file(path):
        raise ValueError("selected Codex rollout is linked or is not a regular file")
    with path.open("rb") as stream:
        if start:
            info = os.fstat(stream.fileno())
            if (info.st_dev, info.st_ino) != (start["device"], start["inode"]) or info.st_size < start["offset"]:
                raise ValueError("registered Codex rollout was replaced or truncated")
            stream.seek(start["offset"])
        for row in lesson.transcript_rows(stream):
            if isinstance(row.get("payload"), dict):
                yield row


def session_path(session, home=None):
    """Use the session ID only for a literal filename suffix under the Codex sessions tree."""
    if not ROLLOUT_ID.fullmatch(session):
        return None
    codex_home = Path(home) / ".codex" if home is not None else Path(os.getenv("CODEX_HOME") or Path.home() / ".codex")
    root = (codex_home / "sessions").absolute()
    try:
        retention.check_root(root)
    except OSError:
        return None
    matches = []
    for day in root.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/[0-9][0-9]"):
        try:
            retention.check_root(day)
            for path in day.glob(f"rollout-*-{session}.jsonl"):
                if lesson.transcript_file(path):
                    matches.append(path)
        except OSError:
            continue
    return matches[0] if len(matches) == 1 else None


def metadata(path, session):
    with closing(_read_rows(path)) as rows:
        first = next(rows, {})
    meta = first.get("payload", {})
    return meta if first.get("type") == "session_meta" and meta.get("id") == session else {}


def _work_call(item, cwd, workspace):
    name = item.get("name")
    if name in ("exec_command", "shell_command"):
        args = _json_object(item.get("arguments"))
        command = args.get("cmd") or args.get("command")
        if isinstance(command, str) and command.strip():
            return "shell", {"command": command}, args.get("workdir", cwd)
    if name == "apply_patch":
        patch = item.get("input") or _json_object(item.get("arguments")).get("patch")
        if isinstance(patch, str) and isinstance(cwd, str):
            paths = PATCH_PATH.findall(patch)
            touched = paths + PATCH_MOVE.findall(patch)
            if paths and all(retention.within((Path(cwd) / path.rstrip("\r")).resolve(), workspace) for path in touched):
                return "apply_patch", {"path": paths[0].rstrip("\r"), "new_string": patch}, cwd
    return None


def calls(path, session, scope, observed, start=None):
    """Pair canonical response items by call ID and ignore event-message mirrors."""
    if not start and not metadata(path, session):
        return
    cwd = start["cwd"] if start else ""
    pending_size = 0
    pending = {}
    for row in _read_rows(path, start):
        item = row["payload"]
        row_type = row.get("type")
        if row_type in ("session_meta", "turn_context"):
            cwd = item.get("cwd", cwd)
            continue
        if row_type != "response_item":
            continue
        call_id, kind = item.get("call_id"), item.get("type")
        if not isinstance(call_id, str) or not call_id:
            continue
        if kind in ("function_call", "custom_tool_call"):
            try:
                work = _work_call(item, cwd, scope["workspace"])
            except (OSError, ValueError, RuntimeError):
                continue
            if work:
                name, args, workdir = work
                payload = {"session_id": session, "cwd": workdir, "fk_timestamp": row.get("timestamp"),
                           "tool_name": name, "tool_input": args, "tool_use_id": call_id,
                           "hook_event_name": "PostToolUse"}
                if call_id not in pending and lesson.admit(scope, "codex", payload, observed, scanned=True):
                    size = len(json.dumps(payload))
                    pending_size += size
                    if pending_size > 64 * 1024 * 1024:
                        raise ValueError("selected Codex rollout has over 64 MiB of unpaired work calls")
                    pending[call_id] = payload, size
        elif kind in ("function_call_output", "custom_tool_call_output") and call_id in pending:
            payload, size = pending.pop(call_id)
            pending_size -= size
            start = lesson._time(payload["fk_timestamp"])
            end = lesson._time(row.get("timestamp"))
            if not end or not start <= end < lesson._time(scope["until"]) or end > observed:
                continue
            output = item.get("output")
            response = _json_object(output)
            if not response and isinstance(output, str):
                response = {"stdout": output}
                if match := EXIT.search(output):
                    response["exit_code"] = int(match[1]) if len(match[1]) <= 12 else 1
            payload["tool_response"] = response
            yield payload
