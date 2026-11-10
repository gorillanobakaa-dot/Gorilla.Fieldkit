"""`fieldkit capture hook --agent claude|gemini|opencode`: one agent hook event in on stdin, one journaled record out.

Order is the point (owner requirement 2, preserve before processing): the raw payload is journaled and fsync'd FIRST,
then parsed into a record, then recorded. A crash between the two leaves an event the next run turns into its record.
The hook never fails the agent: any internal error is journaled and the exit code is 0.
"""
import json
import os
import re
from pathlib import Path

from . import store as st

BASH = {"bash", "shell", "run_shell_command", "execute_bash", "powershell", "run_command", "terminal"}
WRITE = {"write", "write_file", "create", "create_file"}
EDIT = {"edit", "multiedit", "replace", "edit_file", "patch", "notebookedit", "apply_patch"}
CODE_EXT = {".py": "python", ".sh": "bash", ".bash": "bash", ".ps1": "powershell", ".rs": "rust", ".go": "go",
            ".js": "javascript", ".mjs": "javascript", ".ts": "typescript", ".java": "java", ".cmd": "batch",
            ".bat": "batch", ".rb": "ruby", ".pl": "perl", ".yaml": "yaml", ".yml": "yaml", ".json": "json"}
_IMPORT = re.compile(r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w., ]+)|use\s+([\w:]+)|require\(['\"]([^'\"]+)|"
                     r"import\s+.*?from\s+['\"]([^'\"]+)|Import-Module\s+(\S+)|source\s+(\S+))", re.M)
_SPLIT = re.compile(r"\s*(?:&&|\|\||;|\||\n)\s*")


def tool_kind(name):
    n = (name or "").lower()
    return "bash" if n in BASH else "write" if n in WRITE else "edit" if n in EDIT else None


def _first(d, *keys):
    for k in keys:
        if isinstance(d, dict) and d.get(k) is not None:
            return d[k]
    return None


def last_prompt(transcript, limit=2000):
    """The last thing the user typed, from the tail of the agent's transcript (JSONL); empty when unreadable."""
    try:
        p = Path(transcript)
        with open(p, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 400_000))
            lines = f.read().decode("utf-8", "replace").splitlines()
    except (OSError, TypeError, ValueError):
        return ""
    for line in reversed(lines):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        msg = e.get("message") if isinstance(e, dict) else None
        role = (msg or e).get("role") if isinstance(msg or e, dict) else None
        if e.get("type") != "user" and role != "user":
            continue
        c = (msg or e).get("content")
        if isinstance(c, list):
            c = " ".join(x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text")
        if isinstance(c, str) and c.strip() and not c.lstrip().startswith("<"):
            return st.redact_text(c.strip())[:limit]
    return ""


def deps_of(kind, command, content, path):
    if kind == "bash":
        out = []
        for seg in _SPLIT.split(command or ""):
            ws = seg.split()
            while ws and re.match(r"^\w+=\S*$", ws[0]):
                ws.pop(0)
            if ws and ws[0] not in out:
                out.append(ws[0])
        return out
    found = []
    for m in _IMPORT.finditer(content or ""):
        v = next((g for g in m.groups() if g), "")
        for part in v.split(","):
            part = part.strip().split(" ")[0].split(".")[0].split("::")[0]
            if part and part not in found:
                found.append(part)
    return found


def inputs_of(command, cwd, extra=()):
    out = list(extra)
    for tok in re.findall(r"""[^\s'"=<>|;&]+""", command or ""):
        if ("/" in tok or "\\" in tok or "." in tok) and not tok.startswith("-"):
            try:
                if (Path(cwd or ".") / tok).exists() and tok not in out:
                    out.append(tok)
            except OSError:
                pass
    return out


def _exit_code(resp, err, event_name):
    code = _first(resp, "exit_code", "exitCode", "returncode", "exit", "code")
    if isinstance(code, str) and code.lstrip("-").isdigit():
        code = int(code)
    if isinstance(code, int):
        return code
    text = err if isinstance(err, str) else ""
    m = re.match(r"\s*Exit code (-?\d+)", text)
    if m:
        return int(m.group(1))
    if err or "failure" in (event_name or "").lower() or _first(resp, "interrupted") is True:
        return 1
    return 0


def _text(v):
    if v is None:
        return ""
    return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)


def parse(agent, p, store=None):
    """Normalise one agent hook payload into a record. None = an event type that is not work (a Read, a Grep)."""
    if not isinstance(p, dict) or "raw" in p and len(p) == 1:
        return unparsed(agent, p)
    ev = _first(p, "hook_event_name", "event", "type") or ""
    cwd = _first(p, "cwd", "workdir", "directory") or os.getcwd()
    session = str(_first(p, "session_id", "sessionId", "sessionID", "session") or "")
    base = {"agent": agent, "session": session, "cwd": cwd, "env": dict(os.environ), "timestamp": st.now(),
            "origin": f"hook:{agent}:{ev}"}
    prompt = last_prompt(_first(p, "transcript_path", "transcriptPath")) or ""
    if str(ev).lower() in ("stop", "subagentstop", "sessionend", "afteragent"):
        return dict(base, kind="session", source=prompt or "(no prompt found)", command="session-stop", args=[],
                    deps=[], inputs=[_first(p, "transcript_path", "transcriptPath") or ""], outputs={}, exit_code=None,
                    errors="", problem=prompt, path=None)
    name = _first(p, "tool_name", "tool", "toolName")
    tk = tool_kind(name if isinstance(name, str) else "")
    if tk is None:
        return None
    ti = _first(p, "tool_input", "args", "input", "params") or {}
    resp = _first(p, "tool_response", "output", "result", "tool_result") or {}
    if not isinstance(resp, dict):
        resp = {"stdout": _text(resp)}
    err = _first(p, "error") or _first(resp, "error", "stderr") or ""
    err = _text(err)
    out = _text(_first(resp, "stdout", "llmContent", "output", "returnDisplay", "content"))
    desc = _first(ti, "description", "explanation") or ""
    problem = prompt or desc
    code = _exit_code(resp, _first(p, "error"), ev)
    base.update(problem=problem, description=desc, exit_code=code, errors=err if code else "")
    if tk == "bash":
        cmd = st.redact_text(_text(_first(ti, "command", "cmd", "script")))   # before args are split out of it
        return dict(base, kind="command", source=cmd, command=cmd, args=cmd.split(), deps=deps_of("bash", cmd, "", None),
                    inputs=inputs_of(cmd, cwd), outputs={"stdout": out, "stderr": err if code else _text(resp.get("stderr"))},
                    path=None)
    path = _text(_first(ti, "file_path", "path", "filePath", "notebook_path"))
    ext = Path(path).suffix.lower()
    if tk == "write":
        content = _text(_first(ti, "content", "text", "file_text"))
        kind = "script" if ext in CODE_EXT else "file"
        return dict(base, kind=kind, source=content, command=f"write {path}", args=[path], language=CODE_EXT.get(ext),
                    deps=deps_of("write", "", content, path), inputs=[], outputs={"files": [path], "stdout": out}, path=path)
    old, new = _text(_first(ti, "old_string", "old", "oldText")), _text(_first(ti, "new_string", "new", "newText", "edits"))
    return dict(base, kind="edit", source=f"--- old\n{old}\n+++ new\n{new}", command=f"edit {path}", args=[path],
                language=CODE_EXT.get(ext), deps=deps_of("write", "", new, path), inputs=[path],
                outputs={"files": [path], "stdout": out}, path=None)


def unparsed(agent, payload):
    raw = payload.get("raw") if isinstance(payload, dict) else payload
    return {"agent": agent, "session": "", "cwd": os.getcwd(), "env": {}, "timestamp": st.now(), "kind": "unparsed",
            "origin": f"hook:{agent}", "source": _text(raw), "command": None, "args": [], "deps": [], "inputs": [],
            "outputs": {}, "exit_code": None, "errors": "", "problem": "", "path": None}


def handle(agent, text, store=None):
    """Journal one hook event. Returns the item id (None when the event is not work). Never raises."""
    store = store or st.Store()
    try:
        try:
            payload = json.loads(text) if text and text.strip() else {"raw": text or ""}
        except ValueError:
            payload = {"raw": text}
        try:
            rec = parse(agent, payload, store)
        except Exception as exc:                           # noqa: BLE001
            store.log_error("hook.parse", exc)
            rec = unparsed(agent, payload)
        if rec is None:
            return None
        store.recover()                                    # finish what a crashed run left
        eid = store.begin_event(payload, agent)
        iid, _ = store.record(rec, event=eid)
        return iid
    except Exception as exc:                               # noqa: BLE001 - the agent must never see a failure
        try:
            store.log_error("hook.handle", exc, {"agent": agent})
        except Exception:                                  # noqa: BLE001
            pass
        return None
