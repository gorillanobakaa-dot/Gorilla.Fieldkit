# Version: 1.0.0 · updated 26-10-22-14-00
"""Per-job budgets and what ended a headless Claude run (long horizon step 1; DESIGN.md sections 2 and 0b).

Owner, 2026-10-22: "A turn cap can end an unfinished task, so the caller needs a controlled failure or handoff path
... set it high to 10000". The night run's job 2 ran out of its 40 turns after 31 min, mid-mutate, nothing committed;
a hidden 7200 s timeout sat behind it. Here:

  budget      {turns, wall_minutes, cost_usd} per attempt; defaults turns 10000, wall 12 h, no dollar cap
  meter       the job's own counter, kept by the hook (claude_hook.py) in <job>/meter.json: tool calls (the turn
              proxy), wall time since the attempt started, dollars from the transcript (fieldkit/tokens/session_cost.py)
  thresholds  50 / 80 / 95 %: the job is told where it stands; at 80 % it must checkpoint (HANDOFF.md) before any
              further non-read call; at 100 % only a checkpoint is allowed
  classify    what ended a run: success | turns | wall | budget | usage_limit | rate_limit | api_error | crash
  reset_at    when a "usage limit reached / resets at" answer says the window reopens (epoch, or a clock time)
"""
import json
import os
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

DEFAULT = {"turns": 10000, "wall_minutes": 720, "cost_usd": None}
LEVELS = (50, 80, 95)
EXIT_PAUSED = 75                     # sysexits EX_TEMPFAIL: budget exhausted, resumable from the checkpoint
COST_EVERY = 10                      # the hook re-reads the transcript's cost every N tool calls
MTIME_SLACK_S = 1.0                  # file times vs time.time(): see checkpointed_since


def merge(*budgets):
    """Later budgets override earlier ones key by key; None/absent keeps the earlier value."""
    out = dict(DEFAULT)
    for b in budgets:
        for k, v in (b or {}).items():
            if k in DEFAULT and v is not None:
                out[k] = v
    out["turns"] = int(out["turns"])
    out["wall_minutes"] = float(out["wall_minutes"])
    return out


# ---- the meter (written by the hook, read by the runner) --------------------------------------------------------
def _read(p):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def start(job_dir, budget, now=None):
    """A new attempt: its budget and a fresh meter."""
    d = Path(job_dir)
    d.mkdir(parents=True, exist_ok=True)
    now = now or time.time()
    (d / "budget.json").write_text(json.dumps({**budget, "started": now}, indent=1), encoding="utf-8")
    (d / "meter.json").write_text(json.dumps({"calls": 0, "usd": None, "said": [], "mark80": None,
                                              "exhausted": None}, indent=1), encoding="utf-8")


def shares(budget, meter, now=None):
    """-> {turns, wall, cost}: the used share (0..) of each budget; cost None when unknown or uncapped."""
    now = now or time.time()
    out = {"turns": meter.get("calls", 0) / max(1, budget["turns"]),
           "wall": (now - budget.get("started", now)) / 60 / max(1e-9, budget["wall_minutes"]), "cost": None}
    if budget.get("cost_usd") and meter.get("usd") is not None:
        out["cost"] = meter["usd"] / budget["cost_usd"]
    return out


def used(budget, meter, now=None):
    """-> (share, which): the budget nearest its end."""
    s = shares(budget, meter, now)
    which = max((k for k in s if s[k] is not None), key=lambda k: s[k])
    return s[which], which


def describe(budget, meter, now=None):
    now = now or time.time()
    mins = (now - budget.get("started", now)) / 60
    cost = f"${meter['usd']:.2f}" if meter.get("usd") is not None else "$ unknown"
    cap = f" of ${budget['cost_usd']:.2f}" if budget.get("cost_usd") else ""
    return (f"turns {meter.get('calls', 0)} of {budget['turns']}, wall {int(mins)} of {int(budget['wall_minutes'])} "
            f"min, cost {cost}{cap}")


def transcript_summary(transcript_path):
    """The session's own transcript measured by session-cost (fieldkit/tokens/session_cost.py): {usd, context_last,
    ...}. None when it cannot be read (the cost is then known only at the end, from total_cost_usd)."""
    if not transcript_path or not Path(transcript_path).is_file():
        return None
    try:
        from ..tokens import session_cost as sc
        adapter, path = sc.resolve(str(transcript_path), sc.adapters_for("claude-code"))
        return sc.measure(adapter, path)[1] if adapter else None
    except Exception:  # noqa: BLE001 - a meter must never break the run it measures
        return None


def rotate_verdict(transcript_path):
    """-> the session rotation row ({level, why, ...}) for the job's own transcript, or None (fieldkit rotate)."""
    if not transcript_path or not Path(transcript_path).is_file():
        return None
    try:
        from ..tokens import rotate, session_cost as sc
        return rotate.judged(rotate.measure(sc.ClaudeCode(), Path(transcript_path)))
    except Exception:  # noqa: BLE001 - a meter must never break the run it measures
        return None


def transcript_usd(transcript_path):
    s = transcript_summary(transcript_path)
    return s["usd"] if s else None


def checkpointed_since(job_dir, t):
    h = Path(job_dir) / "HANDOFF.md"
    # Windows stamps files from the coarse system tick (~16 ms) while time.time() is precise: a HANDOFF.md written
    # right after t can carry an mtime a few ms before it (test_long_horizon failed 2 runs in 4, 2026-10-26)
    return t is not None and h.is_file() and h.stat().st_mtime >= t - MTIME_SLACK_S


def is_checkpoint_call(tool, args):
    """Writing HANDOFF.md or committing: the only work allowed once a checkpoint is due."""
    name = (tool or "").lower()
    text = json.dumps(args or {}).replace("\\\\", "/").lower()
    if "handoff.md" in text and (name in ("write", "edit", "multiedit") or "command" in (args or {})):
        return True
    cmd = (args or {}).get("command") or ""
    return bool(re.search(r"\bgit\b[^\n]*\b(add|commit|status|diff|log)\b", cmd)) and "push" not in cmd


def hook_pre(job_dir, tool, args, transcript=None, now=None, read_tool=False):
    """The budget part of the PreToolUse hook. -> None (allow) or the deny reason. Counts the call."""
    d = Path(job_dir)
    b, m = _read(d / "budget.json"), _read(d / "meter.json")
    if not b:
        return None
    now = now or time.time()
    m["calls"] = m.get("calls", 0) + 1
    if transcript and (m["calls"] % COST_EVERY == 1 or m.get("usd") is None) and b.get("cost_usd"):
        usd = transcript_usd(transcript)
        m["usd"] = usd if usd is not None else m.get("usd")
    if transcript and m.get("exhausted") != "rotate" and m["calls"] % COST_EVERY == 1:
        row = rotate_verdict(transcript)
        if row and row["level"] == "ROTATE":
            m["exhausted"] = "rotate"
            m["rotate_why"] = "; ".join(row["why"])
    share, which = used(b, m, now)
    why = None
    if share >= 0.8 and m.get("mark80") is None:
        m["mark80"] = now
    if not read_tool and not is_checkpoint_call(tool, args):
        if m.get("exhausted") == "rotate":
            why = (f"SESSION ROTATION: this session is over the ROTATE line ({m.get('rotate_why')}). Write "
                   f"{d / 'HANDOFF.md'} now (done / next / blockers / commands that worked / evidence), commit your "
                   "work in progress on your branch, and end your turn; the harness restarts the job from it in a "
                   "fresh session.")
        elif share >= 1.0 and which != "turns":
            m["exhausted"] = m.get("exhausted") or which
            why = (f"BUDGET EXHAUSTED ({which}: {describe(b, m, now)}). Write {d / 'HANDOFF.md'} now (done / next / "
                   "blockers / commands that worked / evidence) and end your turn; the harness resumes you from it.")
        elif share >= 0.8 and not checkpointed_since(d, m.get("mark80")):
            why = (f"CHECKPOINT REQUIRED at {int(share * 100)} % of the {which} budget ({describe(b, m, now)}). "
                   f"First write {d / 'HANDOFF.md'} (done / next / blockers / commands that worked / evidence) and "
                   "commit your work in progress on your branch; then carry on.")
    (d / "meter.json").write_text(json.dumps(m, indent=1), encoding="utf-8")
    return why


def hook_post(job_dir, now=None):
    """The PostToolUse part: a one-time message as each of 50 / 80 / 95 % is crossed. -> text or None."""
    d = Path(job_dir)
    b, m = _read(d / "budget.json"), _read(d / "meter.json")
    if not b:
        return None
    share, which = used(b, m, now)
    due = [lv for lv in LEVELS if share * 100 >= lv and lv not in m.get("said", [])]
    if not due:
        return None
    lv = max(due)
    m["said"] = sorted(set(m.get("said", [])) | set(due))
    (d / "meter.json").write_text(json.dumps(m, indent=1), encoding="utf-8")
    msg = f"BUDGET {lv} %: {describe(b, m, now)} ({which} is nearest its end)."
    if lv >= 80:
        msg += f" Checkpoint now: write {d / 'HANDOFF.md'} and commit your work in progress on your branch."
    return msg


# ---- what ended a run -------------------------------------------------------------------------------------------
USAGE = re.compile(r"usage limit reached|hit your (?:usage )?limit|(?:5-hour|weekly|session|opus) limit reached|"
                   r"out of (?:extra )?usage|limit will reset", re.I)
EPOCH = re.compile(r"limit reached\|(\d{10})")
RESETS = re.compile(r"reset[s]?\s+(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?(?:\s*\(([^)]+)\))?", re.I)
RATE = re.compile(r"\b(429|529)\b|rate[_ ]?limit|overloaded", re.I)
RETRY_AFTER = re.compile(r"retry[-_ ]after\D{0,6}(\d+)", re.I)
API_4XX = re.compile(r"API Error:\s*4(?!29)\d\d", re.I)


def _text(r):
    res = r.get("result") if isinstance(r.get("result"), dict) else {}
    return "\n".join(str(x) for x in (res.get("result", ""), r.get("stdout", ""), r.get("stderr", "")) if x)


def classify(r, meter=None):
    """-> (kind, detail) for one claude() result (run.claude's dict)."""
    res = r.get("result") if isinstance(r.get("result"), dict) else None
    text = _text(r)
    if r.get("timed_out"):
        return "wall", f"killed after {r.get('seconds')} s (the wall-time budget)"
    if res and res.get("subtype") == "error_max_turns":
        return "turns", f"turn budget reached after {res.get('num_turns')} turns"
    if USAGE.search(text):
        return "usage_limit", text.strip()[:300]
    if RATE.search(text) and (not res or res.get("is_error") or r.get("rc")):
        return "rate_limit", text.strip()[:300]
    if API_4XX.search(text) and (not res or res.get("is_error")):
        return "api_error", text.strip()[:300]
    if (meter or {}).get("exhausted") == "rotate":
        return "rotate", f"session over the ROTATE line ({meter.get('rotate_why')}); restart from the checkpoint"
    if (meter or {}).get("exhausted"):
        return "budget", f"{meter['exhausted']} budget exhausted (the hook allowed only a checkpoint)"
    if res and not res.get("is_error") and r.get("rc") == 0:
        return "success", str(res.get("result", ""))[:300]
    if res:
        return "crash", f"{res.get('subtype')}: {str(res.get('result', ''))[:300]}"
    return "crash", f"exit {r.get('rc')} with no JSON result: {text.strip()[-300:]}"


def retry_after(text):
    m = RETRY_AFTER.search(text or "")
    return int(m.group(1)) if m else None


def reset_at(text, now=None):
    """-> (epoch seconds, how it was known). Unknown -> 30 minutes from now, said so (the next attempt probes)."""
    now = now or time.time()
    m = EPOCH.search(text or "")
    if m:
        return int(m.group(1)), "the epoch in the message"
    m = RESETS.search(text or "")
    if m:
        h, mi, ap, tz = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").lower(), m.group(4)
        if ap == "pm" and h < 12:
            h += 12
        if ap == "am" and h == 12:
            h = 0
        base = datetime.fromtimestamp(now)
        zone = None
        if tz:
            try:
                from zoneinfo import ZoneInfo
                zone = ZoneInfo(tz.strip())
                base = datetime.fromtimestamp(now, zone)
            except Exception:  # noqa: BLE001 - no tz database: read it as local time and say so
                zone = None
        t = base.replace(hour=h % 24, minute=mi, second=0, microsecond=0)
        if t.timestamp() <= now:
            t += timedelta(days=1)
        return int(t.timestamp()), f"'resets {m.group(0).split(None, 1)[-1]}' read as " + (tz if zone else "local time")
    return int(now + 1800), "no reset time in the message: probe again in 30 min"
