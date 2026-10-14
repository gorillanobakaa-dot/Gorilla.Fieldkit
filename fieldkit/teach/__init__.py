"""A teaching log from a session transcript: every command a session ran, with what came back.

    fieldkit teach [SESSION|newest] --out LOG.md [--since 2026-10-14T14:00] [--tools Bash,PowerShell] [--max-chars 4000]

# WHY THIS EXISTS

The owner, 2026-10-14: "every command you execute he will execute, every tool you call he will
call ... open a super big .md file in the project folder and pipe everything in there ... it
should be zero effort". It is less than zero effort: the Claude Code transcript on disk already
holds every tool call and every result in order. This command turns one transcript into one
markdown log, in the same shape as `~/.agents/teach.sh` writes live, so a job done once by a
model becomes a record the harness can be taught from: the commands, in order, with their
output and whether they failed.

# WHAT IT IS NOT

It is not understanding. A log of commands is the raw material for a pipeline stage, a
signature or a card; turning it into one is a person's or a later session's job. The log says
what was run; it does not say why. The session's own text is left out on purpose: the owner
asked for the operations, and a model's narration beside its commands is the thing the
recorder (buildh/recorder.py) exists to distrust.
"""
import json
import os
import re
from datetime import datetime

from ..buildh.claude_watch import resolve

TOOLS = ("Bash", "PowerShell")


def _text(block):
    c = block.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "\n".join(x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text")
    return ""


def calls(path, tools=TOOLS, since=""):
    """-> [{"time", "tool", "command", "description", "result", "error"}] in order."""
    pending, out = {}, []
    for line in open(path, encoding="utf-8", errors="replace"):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        t = d.get("timestamp", "")
        content = (d.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        for b in content:
            k = b.get("type")
            if k == "tool_use" and b.get("name") in tools:
                if since and t and t < since:
                    continue
                inp = b.get("input", {}) or {}
                rec = {"time": t, "tool": b.get("name", ""), "command": str(inp.get("command", "")),
                       "description": str(inp.get("description", "")), "result": "", "error": False}
                pending[b.get("id")] = rec
                out.append(rec)
            elif k == "tool_result":
                rec = pending.pop(b.get("tool_use_id"), None)
                if rec is not None:
                    rec["result"] = _text(b)
                    rec["error"] = bool(b.get("is_error"))
    return out


def render(recs, title, source, max_chars=4000):
    """The log, in the shape ~/.agents/teach.sh writes: one heading per command, output fenced, exit noted."""
    out = [f"# {title}", "",
           f"Every shell command of session `{os.path.basename(source)}`, in order, with what came back.",
           f"Written by `fieldkit teach` on {datetime.now().strftime('%Y-%m-%d %H:%M')}. "
           f"{len(recs)} command(s), {sum(1 for r in recs if r['error'])} failed.",
           "The session's own words are left out on purpose: this is what was RUN, not what was said.", ""]
    for i, r in enumerate(recs, 1):
        when = r["time"].replace("T", " ")[:19] if r["time"] else ""
        out.append(f"### {i}. {when}  {r['tool']}" + (f": {r['description']}" if r["description"] else ""))
        out.append("```bash" if r["tool"] == "Bash" else "```powershell")
        out.append(r["command"].rstrip())
        out.append("```")
        res = r["result"].rstrip()
        if len(res) > max_chars:
            res = res[:max_chars] + f"\n... ({len(r['result']) - max_chars} more characters not shown)"
        out.append("```text")
        out.append(res if res else "(no output)")
        out.append("```")
        out.append("FAILED" if r["error"] else "ok")
        out.append("")
    tools = sorted({r["tool"] for r in recs})
    firsts = []
    seen = set()
    for r in recs:
        w = re.sub(r"\s+", " ", r["command"].strip())[:60]
        head = w.split(" ")[0] if w else ""
        if head and head not in seen:
            seen.add(head); firsts.append(head)
    out += ["## Summary", "", f"- tools: {', '.join(tools) or 'none'}",
            f"- distinct first words: {len(firsts)} ({', '.join(firsts[:40])})",
            f"- failed: {sum(1 for r in recs if r['error'])} of {len(recs)}", ""]
    return "\n".join(out)


def write(session, out, since="", tools=TOOLS, max_chars=4000, title=None):
    """-> {"ok", "source", "out", "commands", "failed"}; refuses when the transcript is not found."""
    path = resolve(None if session in (None, "", "newest") else session)
    if not path or not os.path.isfile(path):
        return {"ok": False, "why": f"no transcript found for '{session}' under ~/.claude/projects", "out": out}
    recs = calls(path, tools=tools, since=since)
    text = render(recs, title or "Teaching log", path, max_chars=max_chars)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(text)
    return {"ok": True, "source": path, "out": out, "commands": len(recs),
            "failed": sum(1 for r in recs if r["error"])}
