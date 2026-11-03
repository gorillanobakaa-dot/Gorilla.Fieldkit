"""P18 stages 0 and 1: coverage of the agents' transcripts, a clean request next to the noisy `problem`, and episodes.

An episode is one session working on one request: the ordered steps (each linked to its capture item) with purpose,
exit code and failure. Nothing here calls a model, rewrites an item or deletes anything: the clean request is computed
from `problem` when read (the original stays), and episodes are appended to the journal as `episode` entries.
"""
import collections
import json
import os
import re
import socket
import sys
from pathlib import Path

from . import store as st

_TAGS = re.compile(r"</?[A-Za-z][^>]*>")
_XSESSION = re.compile(r"^Another Claude session sent a message:?\s*", re.I)
_BRIEF = re.compile(r"^(You are |Background \(|Repo:|ROUND \d|Read [A-Za-z]:[\\/]|Task:|Title yourself)", re.I)
_RETRY = re.compile(r"^(try again|continue|yes|ok|okay|go on|proceed|do it)[.! ]*$", re.I)
NO_REQUEST = "(no stated request)"


def first_line(text):
    for ln in str(text or "").splitlines():
        ln = ln.strip().lstrip("#").strip()
        if ln:
            return ln[:200]
    return ""


def clean_request(problem):
    """(request, source) from the noisy `problem`. source: user | brief | cross-session | scan-note | none | noise:KIND.
    The request is "" when there is none; the original `problem` is never changed."""
    text = str(problem or "").strip()
    if not text:
        return "", "none"
    if _XSESSION.match(text):
        body = first_line(_TAGS.sub("\n", _XSESSION.sub("", text, count=1)))
        return (body, "cross-session") if body else ("", "noise:cross-session")
    low = text.lower()
    if low.startswith("tool loaded"):
        return "", "noise:tool-loaded"
    if low.startswith("[request interrupted"):
        return "", "noise:interrupted"
    if low.startswith("caveat:"):
        return "", "noise:caveat"
    if low.startswith("this session is being continued"):
        return "", "noise:continuation"
    if low.startswith(("left in ", "seen in ")):
        return "", "scan-note"
    if text.startswith("<"):
        body = first_line(_TAGS.sub("\n", text))
        return ("", "noise:markup") if not body else (body, "user")
    fl = first_line(text)
    if _RETRY.match(fl):
        return "", "noise:retry"
    return fl, ("brief" if _BRIEF.match(fl) else "user")


def _failed(d):
    return str(d.get("exit_code")) not in ("None", "0", "") or bool(d.get("errors"))


def build_episodes(state):
    """{episode id: record} from a folded state. Items with no session (disk scans) belong to no episode: counted apart."""
    groups = collections.defaultdict(list)
    unattributed = 0
    for iid, d in state["items"].items():
        if not d.get("session"):
            unattributed += 1
            continue
        req, src = clean_request(d.get("problem"))
        # a noisy request still keys its own episode by the original first line: nothing merges, nothing is lost
        key = req or first_line(d.get("problem")) or NO_REQUEST
        groups[(d["session"], key)].append((str(d.get("timestamp")), iid, d, src))
    out = {}
    for (session, req), rows in groups.items():
        rows.sort(key=lambda r: (r[0], r[1]))
        steps = [[iid, d.get("kind"), d.get("exit_code"), int(_failed(d))] for _, iid, d, _ in rows]
        failed_idx = [i for i, s in enumerate(steps) if s[1] == "command" and s[3]]
        recovered = sum(1 for i in failed_idx if any(s[1] == "command" and not s[3] for s in steps[i + 1:]))
        eid = "ep-" + st.sha(session + "\0" + req)[:12]
        body = st.canon(steps)
        srcs = collections.Counter(r[3] for r in rows)
        out[eid] = {
            "id": eid, "session": session, "request": req, "real_request": bool(rows[0][3] in ("user", "brief", "cross-session")),
            "request_source": srcs.most_common(1)[0][0], "agent": rows[0][2].get("agent"), "cwd": rows[0][2].get("cwd"),
            "start": rows[0][0], "end": rows[-1][0], "n_steps": len(steps), "n_failed": len(failed_idx),
            "n_recovered": recovered, "kinds": dict(collections.Counter(s[1] for s in steps)),
            "steps": body, "digest": st.sha(req + body)}
    return out, unattributed


def store_episodes(store):
    """Append the episodes that are new or changed (idempotent). Returns the counts."""
    state = store.state()
    eps, unattributed = build_episodes(state)
    have = state.get("episodes", {})
    new = [e for k, e in eps.items() if k not in have or have[k].get("digest") != e["digest"]]
    # a big step list moves to a blob exactly as items do
    store.append_many("episode", [store.spill(e) for e in new])
    return {"episodes_found": len(eps), "episodes_written": len(new), "episodes_stored": len(eps),
            "with_real_request": sum(1 for e in eps.values() if e["real_request"]),
            "with_3plus_steps": sum(1 for e in eps.values() if e["n_steps"] >= 3),
            "items_in_episodes": sum(e["n_steps"] for e in eps.values()), "items_without_session": unattributed}


def steps_of(store, ep):
    s = ep["steps"]
    if isinstance(s, dict) and "blob" in s:
        s = store.blob(s)
    return json.loads(s)


def request_report(state):
    """How noisy `problem` is, and how much of it the clean request recovers (stage 0 measure)."""
    src = collections.Counter()
    reqs = set()
    for d in state["items"].values():
        req, kind = clean_request(d.get("problem"))
        src[kind] += 1
        if req:
            reqs.add(req)
    n = len(state["items"])
    noise = sum(v for k, v in src.items() if k.startswith("noise:") or k in ("none", "scan-note"))
    return {"items": n, "by_source": dict(src), "items_without_clean_request": noise,
            "items_with_clean_request": n - noise, "distinct_requests": len(reqs)}


# ---- stage 0: coverage ------------------------------------------------------------------------------------------
def _has_tool_calls(path):
    try:
        with open(path, "rb") as f:
            while True:
                chunk = f.read(1 << 20)
                if not chunk:
                    return False
                if b'"tool_use"' in chunk or b'"toolUse"' in chunk:
                    return True
    except OSError:
        return False


def coverage(state, home=None):
    """Every transcript store on this computer, per agent: read by capture, or listed with the reason it is not."""
    home = Path(home or os.path.expanduser("~"))
    captured = {d.get("session") for d in state["items"].values() if d.get("session")}
    out = {"machine": socket.gethostname(), "platform": sys.platform, "agents": {}}
    claude = {"store": "~/.claude/projects", "on_disk": 0, "captured": 0, "uncaptured": []}
    for p in sorted((home / ".claude" / "projects").glob("*/**/*.jsonl")):
        parts = p.relative_to(home / ".claude" / "projects").parts
        sid = parts[1] if len(parts) >= 3 and parts[2] == "subagents" else p.stem
        claude["on_disk"] += 1
        if sid in captured:
            claude["captured"] += 1
        else:
            claude["uncaptured"].append({"session": sid, "file": p.name,
                                         "reason": "read-only chat: no tool call, nothing to capture" if not _has_tool_calls(p)
                                         else "GAP: has tool calls, no item captured yet (run the capture scan)"})
    out["agents"]["claude"] = claude
    gem = home / ".gemini" / "antigravity" / "conversations"
    pb = sorted(gem.glob("*.pb")) if gem.is_dir() else []
    out["agents"]["gemini"] = {"store": "~/.gemini/antigravity/conversations", "on_disk": len(pb), "captured": 0,
                               "uncaptured": [{"session": p.stem, "file": p.name,
                                               "reason": "binary protobuf of the app: no reader built, listed (hook captures new sessions)"}
                                              for p in pb]}
    tried = [home / ".local/share/opencode", home / "AppData/Roaming/opencode", home / "AppData/Local/opencode"]
    found = [p for p in tried if p.is_dir()]
    out["agents"]["opencode"] = {"store": ", ".join(p.name for p in found) or "none found", "on_disk": 0, "captured": 0,
                                 "uncaptured": [], "reason": "" if found else "no session store on this computer"}
    out["other_machines"] = "not scanned: no peer is reachable (the Linux laptop is shut down)"
    return out
