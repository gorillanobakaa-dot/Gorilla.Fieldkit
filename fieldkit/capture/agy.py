"""Antigravity CLI's named PostToolUse hook and scoped payload adapter."""

import json
import os
import tempfile
from pathlib import Path

from . import retention, store

NAME = "fieldkit-capture-agy"
TOOLS = "run_command|write_to_file|replace_file_content|multi_replace_file_content"


def config_path(home):
    return Path(home) / ".gemini" / "config" / "hooks.json"


def load(home):
    return _read(home)[0]


def _read(home):
    path = config_path(home)
    try:
        retention.check_root(path.parent.absolute())
        info = path.lstat()
    except FileNotFoundError:
        return {}, None
    if retention.link(info) or info.st_nlink != 1 or not path.is_file():
        raise ValueError("Antigravity hook file is not a regular unlinked file")
    with path.open("rb") as stream:
        raw = stream.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise ValueError("Antigravity hook settings exceed 1 MiB")
    try:
        data = json.loads(raw)
    except RecursionError as exc:
        raise ValueError("Antigravity hook settings are nested too deeply") from exc
    if not isinstance(data, dict):
        raise ValueError("Antigravity hook settings must be an object")
    return data, info


def spec(command):
    return {"PostToolUse": [{"matcher": TOOLS, "hooks": [
        {"type": "command", "command": command, "timeout": 20}
    ]}]}


def owned(existing):
    try:
        command = existing["PostToolUse"][0]["hooks"][0]["command"]
    except (KeyError, IndexError, TypeError):
        return False
    return isinstance(command, str) and command.endswith('" -m fieldkit capture hook --agent agy') and existing == spec(command)


def edit(home, command=None):
    data, prior = _read(home)
    if NAME in data and not owned(data[NAME]):
        raise ValueError("Antigravity hook name is already used by an unrelated tool")
    if command:
        data[NAME] = spec(command)
    elif NAME in data:
        del data[NAME]
    else:
        return 0
    path = config_path(home)
    for directory in (path.parent.parent, path.parent):
        retention.check_root(directory.parent.absolute())
        directory.mkdir(exist_ok=True)
        retention.check_root(directory.absolute())
    fd, name = tempfile.mkstemp(prefix=".fieldkit-hooks-", suffix=".tmp", dir=path.parent)
    tmp = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        try:
            current = path.lstat()
        except FileNotFoundError:
            current = None
        if _revision(prior) != _revision(current):
            raise OSError("Antigravity hook settings changed during the edit")
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)
    return 1


def _revision(info):
    if info is not None:
        return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_nlink


def payload(data, workspace, home=None):
    """Translate only documented CLI work calls. The global hook is also visible to the IDE and app."""
    if not isinstance(data, dict):
        return {}
    transcript = data.get("transcriptPath")
    sid, step = data.get("conversationId"), data.get("stepIdx")
    if not isinstance(transcript, str) or not isinstance(sid, str) or type(step) is not int or step < 0:
        return {}
    path = Path(transcript).expanduser()
    if not path.is_absolute() or path.suffix != ".jsonl" or not retention.within(
            path.resolve(), Path(home or Path.home()) / ".gemini" / "antigravity-cli"):
        return {}
    workspaces, call = data.get("workspacePaths"), data.get("toolCall")
    if not isinstance(workspaces, list) or not isinstance(call, dict):
        return {}
    if not any(isinstance(root, str) and Path(root).is_absolute()
               and retention.normalized(Path(root).resolve()) == retention.normalized(workspace) for root in workspaces):
        return {}
    name, args = call.get("name"), call.get("args")
    if name not in TOOLS.split("|") or not isinstance(args, dict):
        return {}
    cwd = args.get("Cwd") or workspace
    if not isinstance(cwd, str):
        return {}
    if name == "run_command":
        normalized = {"command": args.get("CommandLine")}
        if not isinstance(normalized["command"], str) or not normalized["command"].strip():
            return {}
    else:
        target = args.get("TargetFile")
        if not isinstance(target, str) or not Path(target).is_absolute() or not retention.within(Path(target).resolve(), workspace):
            return {}
        normalized = {"path": target, "new_string": args.get("ReplacementContent", args.get("ReplacementChunks", "")),
                      "content": args.get("CodeContent", "")}
    call_id = store.sha(store.canon([step, name, normalized, str(cwd)]))
    name = name if name == "run_command" else "write" if name == "write_to_file" else "edit"
    return {"hook_event_name": "PostToolUse", "tool_name": name, "cwd": cwd, "session_id": sid,
            "tool_use_id": call_id, "tool_input": normalized, "error": data.get("error", "")}
