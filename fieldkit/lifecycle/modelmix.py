"""Tokens per model, per job and per Zero session (plan P34/P35; `fieldkit overhead models`).

Read from the Claude Code transcripts (~/.claude/projects/**/*.jsonl, subagent files included). One assistant message is
written as several lines that carry the same message.id and the full usage each time, so each id is counted ONCE (the
largest usage seen). The model is the one on the message, never the file's: a Haiku subagent inside an Opus session is
Haiku. A group is the job id when the project folder names one (wt-job-T-...), else the session. Zero's own session is
marked: it is where an expensive model reading facts shows up.
"""
import json
import re
from pathlib import Path

JOB = re.compile(r"T-\d{8}-\d{6}-[0-9a-f]{6}")
EXPENSIVE = ("opus", "fable", "mythos")
KEYS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")


def _tier(model):
    m = (model or "").lower()
    return "expensive" if any(x in m for x in EXPENSIVE) else "cheap"


def _session_of(path):
    p = Path(path)
    return p.parent.parent.name if p.parent.name == "subagents" else p.stem


def scan(root, since="2020-01-01"):
    """-> {(group, session, model): {tokens...}} over every transcript under root."""
    seen = {}
    for f in sorted(Path(root).glob("**/*.jsonl")):
        job = JOB.search(str(f.relative_to(root)))
        group = job.group(0) if job else "session"
        sid = _session_of(f)
        try:
            fh = open(f, encoding="utf-8", errors="replace")
        except OSError:
            continue
        with fh:
            for line in fh:
                if '"usage"' not in line:
                    continue
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                m = d.get("message") or {}
                u = m.get("usage")
                if d.get("type") != "assistant" or not isinstance(u, dict) or (d.get("timestamp") or "")[:10] < since:
                    continue
                key = (str(f), m.get("id") or d.get("uuid"))
                tok = {k: int(u.get(k) or 0) for k in KEYS}
                old = seen.get(key)
                if not old or sum(tok.values()) >= sum(old[3].values()):
                    seen[key] = (group, sid, m.get("model") or "?", tok)
    agg = {}
    for group, sid, model, tok in seen.values():
        a = agg.setdefault((group, sid, model), {k: 0 for k in KEYS} | {"calls": 0})
        for k in KEYS:
            a[k] += tok[k]
        a["calls"] += 1
    return agg


def rows(root, since="2020-01-01", zero_session=None):
    """-> per group and model: tokens (input+output, cache apart), calls, tier, zero flag; biggest first."""
    out = {}
    for (group, sid, model), a in scan(root, since).items():
        gid = group if group != "session" else f"session {sid[:8]}"
        r = out.setdefault((gid, model), {"group": gid, "model": model, "tier": _tier(model), "calls": 0, "input": 0,
                                           "output": 0, "cache_read": 0, "cache_write": 0, "zero": False})
        r["calls"] += a["calls"]
        r["input"] += a["input_tokens"]
        r["output"] += a["output_tokens"]
        r["cache_read"] += a["cache_read_input_tokens"]
        r["cache_write"] += a["cache_creation_input_tokens"]
        r["zero"] = r["zero"] or bool(zero_session and sid.startswith(zero_session))
    return sorted(out.values(), key=lambda r: -(r["input"] + r["output"] + r["cache_read"] + r["cache_write"]))


def totals(rs):
    t = {}
    for r in rs:
        x = t.setdefault(r["model"], {"calls": 0, "tokens": 0})
        x["calls"] += r["calls"]
        x["tokens"] += r["input"] + r["output"] + r["cache_read"] + r["cache_write"]
    return t


def lines(rs, limit=25):
    L = []
    for model, x in sorted(totals(rs).items(), key=lambda kv: -kv[1]["tokens"]):
        L.append(f"  {model:<28} {x['calls']:6d} calls {x['tokens']:>14,} tokens  [{_tier(model)}]")
    L.append("per job / session:")
    for r in rs[:limit]:
        L.append(f"  {r['group']:<32} {r['model']:<24} {r['calls']:5d} calls in {r['input']:>10,} out {r['output']:>9,} "
                 f"cache {r['cache_read'] + r['cache_write']:>12,}" + ("  ZERO" if r["zero"] else ""))
    return L
