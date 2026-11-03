# Version: 1.0.0 · updated 26-11-03
"""Session budget: a warning at half the budget, and at 90% the handover prepared and the fresh session named.

Owner, 2026-11-03: "rotate check budget warnings: 50% 'you are at half the budget'; 90% 'you used N tokens, stop
writing: I am preparing the handover and starting a new VISIBLE session automatically'". Built on rotate.py (the same
meters, the same handover note and PLAN.md section, the same session-name numbering), so there is no second rotation
mechanism.

    fraction = the worse of  context per call / ctx_rotate   and   session dollars / usd_rotate
    (the budget is the ROTATE line of rotate.py; the defaults are 120k tokens per call and $75)
    HALF  at >= 50%: one line in the session's context: "you are at half the budget"
    STOP  at >= 90%: the tool writes the handover (rotate.handover), writes the successor's brief, and lists the
          finished sessions to archive; the session is told to start the successor visibly with that brief. The
          tool cannot press start_session itself (that is the desktop app's own tool), so the line says so.
    Each line is said once per session. Inside an autopilot job the budget hook decides instead (rotate.hook_line).

Session names: "[L15-WIN] #NN HH:MM <job>" - the machine tag, an increasing counter (state/rotate/counter.json) and the
start time. `fieldkit sessions tag --check` accepts it: it only needs the tag in front (machine.tagged).

Writes state/rotate/ only (budget marks, successor briefs, the counter). Reads transcripts, never edits one.
"""
import json
import os
import time
from pathlib import Path

from . import rotate
from . import session_cost as sc

HALF, STOP = 0.5, 0.9
HALF_TEXT = "you are at half the budget"
STOP_TEXT = "you used {tokens:,} tokens, stop writing: I am preparing the handover and starting a new VISIBLE session automatically"
#: how many finished sessions the STOP line names for archiving (the rest stay in `rotate finished`)
ARCHIVE_MAX = 12


def fraction(row, cfg=None):
    """The share of the budget (the ROTATE line) this session has used: the worse of its two meters, 0.0 .. n."""
    cfg = cfg or rotate.config()
    return max(row.get("context_last", 0) / cfg["ctx_rotate"], (row.get("usd") or 0.0) / cfg["usd_rotate"])


def level(frac):
    """-> "STOP", "HALF" or None."""
    if frac >= STOP:
        return "STOP"
    if frac >= HALF:
        return "HALF"
    return None


def job_title(row):
    """The short job name for the title: the first line of the task the session was started with, else its title."""
    task = " ".join(str(row.get("task") or "").split())
    base = task or str(row.get("title") or "").strip() or "session"
    return base[:60].rstrip()


def numbered_title(job, now=None, tag=None, state=None):
    """-> "[TAG] #NN HH:MM job" with NN one more than the last number handed out here (never reused)."""
    from ..peers import machine
    now = time.time() if now is None else now
    f = (state or rotate.state_dir()) / "counter.json"
    try:
        n = int(json.loads(f.read_text(encoding="utf-8")).get("n", 0))
    except (OSError, ValueError, AttributeError):
        n = 0
    n += 1
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps({"n": n}), encoding="utf-8")
    os.replace(tmp, f)
    tag = machine.current() if tag is None else tag
    head = f"{tag} " if tag else ""
    return f"{head}#{n:02d} {time.strftime('%H:%M', time.localtime(now))} {job}".strip()


def handed_over(sid):
    """True once the 90% handover was written for this session (its successor is named in the BUDGET line)."""
    return _mark(sid).get("told") == "STOP"


def _mark_file(sid):
    return rotate.state_dir() / "budget" / (sid + ".json")


def _mark(sid):
    try:
        return json.loads(_mark_file(sid).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"told": None}


def _set_mark(sid, m):
    f = _mark_file(sid)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(m), encoding="utf-8")


def archive_candidates(row_sid, now=None, cfg=None):
    """Finished sessions (idle, end of turn, no open PR) to archive by id: never the rotated session, never Zero's.
    The app's own archive_session refuses Zero's parent and running sessions (lifecycle/nested.py) on top of this."""
    from ..zero import net
    try:
        zero = net.zero_of(net.load_net()) or ""
    except Exception:  # noqa: BLE001 - an unreadable net keeps every candidate out: fail closed on Zero
        zero = None
    if zero is None:
        return []
    done, _held = rotate.finished(cfg=cfg, now=now)
    out = []
    for r in done:
        sid = r["session"]
        if sid == row_sid or sid[:8] in zero:
            continue
        out.append({"id": sid, "title": (r.get("title") or "")[:60], "idle_minutes": r.get("idle_minutes")})
    return out[:ARCHIVE_MAX]


def prepare_successor(row, transcript, now=None, cfg=None):
    """STOP: write the handover (note, PLAN.md section, brief), write the successor's brief next to it, and list the
    archive candidates. Deterministic: the texts are fixed, the facts come from the transcript. -> the dict."""
    now = time.time() if now is None else now
    job = job_title(row)
    title = numbered_title(job, now)
    done = (f"budget STOP at {round(fraction(row, cfg) * 100)}% (automatic): the work up to the last reply in the "
            "transcript; nothing in the last turn was re-checked by the handover")
    next_ = ("finish the original task in the brief below; before writing more, read the note's Done and Next lines "
             "and the PLAN.md section it wrote")
    r = rotate.handover(transcript, title, next_, done=done,
                        not_verified="everything the session did in its last turn, and every number in this note")
    sid = row["session"]
    brief_file = rotate.state_dir() / "briefs" / f"{sid}.md"
    brief_file.parent.mkdir(parents=True, exist_ok=True)
    brief_file.write_text(r["brief"], encoding="utf-8")
    archive = archive_candidates(sid, now, cfg)
    mark = {"told": "STOP", "stopped_at": now, "title": title, "note": r["note"], "brief": str(brief_file),
            "archive": [a["id"] for a in archive]}
    _set_mark(sid, mark)
    return {"title": title, "note": r["note"], "brief": str(brief_file), "archive": archive,
            "plan_written": r["plan_written"]}


def lines(row, transcript, cfg=None, now=None):
    """-> the budget text for the session's context ("" when nothing is due). Each line is said once per session."""
    cfg = cfg or rotate.config()
    sid = row["session"]
    frac = fraction(row, cfg)
    lv = level(frac)
    m = _mark(sid)
    if lv is None or m.get("told") == "STOP" or (lv == "HALF" and m.get("told") == "HALF"):
        return ""
    pct = round(frac * 100)
    if lv == "HALF":
        _set_mark(sid, {**m, "told": "HALF"})
        return f"BUDGET {pct}%: {HALF_TEXT} ({'; '.join(row.get('why') or []) or 'by the meters'})."
    res = prepare_successor(row, transcript, now, cfg)
    archive = "; ".join(f"archive_session \"{a['id']}\"" for a in res["archive"]) or "none to archive"
    return (f"BUDGET {pct}%: {STOP_TEXT.format(tokens=row['context_last'])}. "
            f"The handover is written: {res['note']} (plan section {'appended' if res['plan_written'] else 'NOT found'}). "
            f"NOW, before any more work: start the successor visibly - Claude desktop start_session, initiation "
            f"\"user_asked\", context \"fresh\", prompt = the file {res['brief']} (its title: {res['title']}). "
            "If the app refuses, say so and run `fieldkit task start <that file>` (headless, no card). Then archive "
            f"this session (set_remote_control off first if on; archive_session \"self\"). Finished sessions to archive "
            f"by id, after the successor is up: {archive}.")


def pending_wakes(sid):
    """-> the CI wake lines for this session (fieldkit/method/ci_wake.py), read once."""
    try:
        from ..method import ci_wake
        return ci_wake.take(sid)
    except Exception:  # noqa: BLE001 - a wake that cannot be read is skipped, the session is not
        return []
