# Version: 1.0.0 · updated 26-11-15
"""AUTO-REPORT: every session holding an order from Zero tells Zero, at the end of each turn, in ONE net line.

Model (owner order 2026-11-15, Anthropic Managed Agents session threads): the primary thread (Zero) gets each child's
report when the child's turn ends; an interrupted child sends none, so Zero never waits on one (the keeper rule
`silent_child` watches for that silence). Military meaning: mission command, "fire and forget" with a report back.

Called from the one Stop hook process (`fieldkit rotate --stop-hook`, fieldkit/tokens/rotate.py `stop_hook`); no second
Python process. Never raises, never blocks a turn (fail open). Zero itself is exempt; a session with no order sends nothing.

The line: `REPORT STATION [status] order ID: OBJECTIVE | PR n,m | first ~300 characters of the last assistant message`.
Status: working, done (the order is closed, or the message says DONE), blocked (the message says BLOCKED), rotating (a
hand-over was written: the relief is pending; the line then carries the successor brief path).
The same status and text as the last report: nothing is sent (debounce). The record is kept in the station's row of
net-stations.json (`report`: at, status, text, prs, brief); the keeper reads it. Same on Windows and Linux.
"""
import hashlib
import json
import re
import time
from pathlib import Path

TEXT_MAX = 300
TAIL_BYTES = 262144


def last_text(transcript, n=TEXT_MAX):
    """The first n characters of the last assistant message with text in a Claude Code transcript ('' when none)."""
    try:
        p = Path(transcript)
        size = p.stat().st_size
        with open(p, "rb") as f:
            f.seek(max(0, size - TAIL_BYTES))
            lines = f.read().decode("utf-8", "replace").splitlines()
    except (OSError, TypeError, ValueError):
        return ""
    for ln in reversed(lines):
        try:
            d = json.loads(ln)
        except ValueError:
            continue
        if d.get("type") != "assistant":
            continue
        c = (d.get("message") or {}).get("content")
        text = c if isinstance(c, str) else " ".join(b.get("text", "") for b in c or [] if isinstance(b, dict) and b.get("type") == "text")
        text = " ".join(text.split())
        if text:
            return text[:n]
    return ""


def prs_in(text):
    return sorted({int(m) for m in re.findall(r"/pull/(\d+)|\bPR\s*#?(\d+)", text or "") for m in m if m})


def status_of(order, text, relieved):
    if order.get("closed") or re.search(r"(?i)^\W*(done|finished)\b", text or ""):
        return "done"
    if relieved:
        return "rotating"
    if re.search(r"(?i)\bblocked\b", (text or "")[:80]):
        return "blocked"
    return "working"


def line_of(station, order, status, text, prs, brief=""):
    tail = f" | successor brief: {brief}" if brief else ""
    pr = f" | PR {','.join(map(str, prs))}" if prs else ""
    return f"REPORT {station} [{status}] order {order['id']}: {order['objective'][:80]}{pr} | {text}{tail}"[:700]


def report(call, now=None, send=None, brief_of=None):
    """-> the line sent, or '' (no net, Zero itself, no order, same as last time, refused). Never raises."""
    try:
        from . import command as C
        from . import net as N
        now = time.time() if now is None else now
        sid = call.get("session_id") or Path(call.get("transcript_path") or "").stem
        net = N.load_net()
        zero = N.zero_of(net)
        if not sid or not zero:
            return ""
        me = N.hook_station(sid)
        if me == zero:
            return ""
        st = N.load_stations()
        rec = st["stations"].get(me) or {}
        o = rec.get("order")
        if not o:
            return ""
        text = last_text(call.get("transcript_path"))
        relieved = me in (st["meta"].get("relief") or {}) and not o.get("closed")
        status = status_of(o, text, relieved)
        digest = hashlib.sha1(f"{status}|{text}".encode("utf-8", "replace")).hexdigest()[:12]
        if (rec.get("report") or {}).get("hash") == digest:
            return ""
        brief = (brief_of or C._brief_of)(sid)[0] if status == "rotating" else ""
        prs = prs_in(text)
        line = line_of(me, o, status, text, prs, brief)
        with N._Lock():
            st = N.load_stations()
            r = st["stations"].get(me)
            if r is None:
                return ""
            r["report"] = {"at": now, "status": status, "text": text, "prs": prs, "brief": brief, "hash": digest}
            N.save_stations(st)
        (send or (lambda: N.send(me, line, to=zero, want_ack=False, channels=C.targeted(zero, net), now=now)))()
        return line
    except Exception:  # noqa: BLE001 - a Stop hook must never break the session
        return ""
