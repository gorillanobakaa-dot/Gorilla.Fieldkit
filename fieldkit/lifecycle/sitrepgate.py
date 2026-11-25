"""No archive without a report the owner saw (owner, 2026-11-25: a session ran the DONE protocol and archived itself
without ever showing a report).

archive_session (self or an id) is refused unless the target session has a sitrep that passes `fieldkit sitrep check`,
written after its last code change and inside its current turn, AND its last chat message shows it. `fieldkit sitrep
check` records its result under state/lifecycle/sitrep/<session id>.json; a last message that is itself a passing
report counts too (a session past the ROTATE line cannot run commands outside the handover). Fails CLOSED.
"""
import hashlib
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path

from .. import sitrep
from ..core.settings import state_root
from . import nested, permanence

HOW = ("Do this, in order: `python -m fieldkit sitrep template --lang ro > SITREP.md`, fill it in, `python -m "
       "fieldkit sitrep check SITREP.md` until it passes, then print the report in chat as your last message and "
       "archive again in the same turn. If commands are blocked, print the filled report itself as your last "
       "message. The owner can still archive with the app's button.")
#: an edit under these is bookkeeping (the report, the Board row, scratch, memory), not a code change
BOOKKEEPING = re.compile(r"(^|/)(state|temp|tmp|scratchpad|memory)/", re.I)


def _dir():
    return state_root(Path(__file__).resolve().parents[2]) / "state" / "lifecycle" / "sitrep"


def _file(sid):
    return _dir() / (re.sub(r"[^0-9A-Za-z_-]", "_", str(sid)) + ".json")


def session_id(given=None):
    """The session a `sitrep check` belongs to: --session (an id or a transcript path), else this Claude session."""
    if given:
        return Path(str(given)).stem
    return os.environ.get("CLAUDE_CODE_SESSION_ID") or None


def headline(text):
    items = sitrep.parse(text).get("sections", {}).get("One line") or []
    return items[0] if items else ""


def record(sid, text, ok, lang="en", file="", now=None):
    """Keep the latest check of this session (a later failing check replaces an earlier pass). -> the record path."""
    f = _file(sid)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"session": sid, "at": time.time() if now is None else now, "ok": bool(ok),
                             "headline": headline(text), "text": text, "lang": lang, "file": str(file),
                             "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()},
                            ensure_ascii=False, indent=1), encoding="utf-8")
    return f


def _stamp(d):
    try:
        return datetime.fromisoformat(str(d["timestamp"]).replace("Z", "+00:00")).timestamp()
    except (KeyError, ValueError):
        return None


def _changes_code(block):
    ti = block.get("input") or {}
    if block.get("name") in permanence.EDIT_TOOLS:
        fp = str(ti.get("file_path") or ti.get("notebook_path") or "").replace("\\", "/")
        return not (BOOKKEEPING.search(fp) or fp.endswith("SITREP.md"))
    return block.get("name") in ("Bash", "PowerShell") and bool(permanence.GIT_WORDS.search(str(ti.get("command") or "")))


def read_transcript(path):
    """-> {changed, turn, said}: when the last code change and the current turn started (epoch seconds, 0 if none)
    and the last chat text of the assistant."""
    out = {"changed": 0.0, "turn": 0.0, "said": ""}
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get("isSidechain"):
                continue
            c = (d.get("message") or {}).get("content")
            blocks = c if isinstance(c, list) else []
            if d.get("type") == "user":
                typed = isinstance(c, str) or (blocks and not any(b.get("type") == "tool_result" for b in blocks))
                if typed and not (d.get("isMeta") or d.get("isCompactSummary")):
                    out["turn"] = _stamp(d) or out["turn"]
                    out["said"] = ""                          # what was shown in an earlier turn does not count
            elif d.get("type") == "assistant":
                text = "\n".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()
                if text:
                    out["said"] = text
                if any(b.get("type") == "tool_use" and _changes_code(b) for b in blocks):
                    out["changed"] = _stamp(d) or time.time()
                    out["said"] = ""                          # a report shown before the change is stale
    return out


def _plain(text):
    return " ".join(re.sub(r"[*_#>`]|^\s*(?:[-+]|\d+[.)])\s+", " ", text, flags=re.M).split()).lower()


def verdict(sid, transcript):
    """-> None when the session may archive, else why not."""
    t = read_transcript(transcript)
    if t["said"] and sitrep.check(t["said"]).get("ok"):
        return None                                           # the last message is itself a passing report
    try:
        r = json.loads(_file(sid).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "this session has no checked sitrep"
    if not r.get("ok"):
        return "the last `sitrep check` of this session FAILED"
    if r["at"] < t["changed"]:
        return "the sitrep is stale: code changed after it was checked"
    if r["at"] < t["turn"]:
        return "the sitrep is stale: it was checked in an earlier turn"
    said, head = _plain(t["said"]), _plain(r.get("headline") or "")
    if not ((head and head in said) or (_plain(r["text"]) and _plain(r["text"]) in said)):
        return "the sitrep was not shown: your last chat message has neither its one-line headline nor its body"
    return None


def archive_refusal(call, records=None):
    """PreToolUse on archive_session: -> the refusal text, or None. Any error refuses."""
    if not str(call.get("tool_name") or "").endswith(nested.ARCHIVE):
        return None
    try:
        target = str((call.get("tool_input") or {}).get("session_id") or "self")
        sid, tp = call.get("session_id"), call.get("transcript_path")
        if target != "self":
            recs = nested.load_records() if records is None else records
            other = (recs.get(target) or {}).get("cliSessionId") or target
            if other != sid:
                sid, tp = other, permanence.transcript_for(other)
        if not (sid and tp):
            why = "its transcript cannot be found, so no report can be proven"
        else:
            why = verdict(sid, tp)
    except Exception as e:  # noqa: BLE001 - fail closed: an unproven report is no report
        why = f"the sitrep gate could not read the evidence ({type(e).__name__})"
    return f"ARCHIVE REFUSED (SITREP): {why}. {HOW}" if why else None
