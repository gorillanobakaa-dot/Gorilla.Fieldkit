# Version: 1.1.0 · updated 26-10-26
"""Session rotation: get rid of the monster sessions before they cost more, and start a fresh one. Deterministic.

    fieldkit rotate check [--hours 24] [--session ID] [--json]   every session active in the last N hours, measured,
                                                                 one verdict each: OK / WARN / ROTATE (exit 0 / 1 / 2)
        [--source AGENT] [--cwd DIR] [--newest]                  an agent asking about itself: its newest session
    fieldkit rotate rule [--install FILE]                        the rule for agents with no hook (AGENTS.md, GEMINI.md)
    fieldkit rotate handover --transcript PATH|--session ID --title T --next TEXT [--done TEXT] [--not-verified TEXT]
                                                                 the dated PLAN.md section + a note file + the brief
                                                                 for the fresh session; prints the exact next step
    fieldkit rotate finished [--hours 72] [--gh] [--json]        ended, idle sessions with no open PR: for archive
    fieldkit rotate log [N]                                      the last N verdicts
    fieldkit rotate doctor                                       thresholds, transcript folders, state, PLAN.md, hook
    python -m fieldkit.tokens.rotate --hook                      Claude Code UserPromptSubmit / PostToolUse hook
                                                                 (the room hook calls hook_line() itself)

WHY (owner, 2026-10-26, verbatim): "we need to create a new automated function for this as well in the harness. you
should not have to ask for this. you have a token counter ... you should periodically get rid of the monster sessions
and create new ones that costs less". Every call re-sends the whole conversation: a 64-hour session at ~530k context
per call cost $274 on API credits, about 2.5 times the same work split up (session-cost, 2026-10-21).

THE RULE (defaults below; override under "rotate" in fieldkit.local.json)
    context  tokens sent with the session's last call (its own, not a subagent's): >= 250k WARN, >= 350k ROTATE
    dollars  the session's bill so far (subagents included, priced from prices.json): >= $40 WARN, >= $75 ROTATE
    A call on a model with no published price adds no dollars; the verdict says how many such calls there were.

WHAT HAPPENS ON ROTATE
    Claude Code / the desktop Code tab: the room hook adds the verdict and the exact next step to the session's
    context at the next prompt (and once, mid-turn, when the line is first crossed): finish the current step, run
    `fieldkit rotate handover`, start a fresh session with the brief it prints, archive this one. No session has to
    remember to ask.
    fieldkit autopilot (headless): the job's PreToolUse budget hook denies everything but the checkpoint once the
    job's session is over the ROTATE line; the job ends as "rotate" and restarts from HANDOFF.md in a NEW session
    (never --resume of the monster), up to max_rotations times (job.py).
    Any other agent (Codex, Antigravity, Gorilla OpenCode, anything writing the generic contract): no hook can reach
    it, so the rule block (`rotate rule`, installed in its own instruction file) tells it to run `rotate check
    --source <agent> --newest` itself and hand over on exit 2. The run guard (core/preflight.py) also names any
    session over a line before every guarded build, test run or gate, whichever agent started it.
    `rotate doctor` lists per agent whether it is measured and whether it is triggered.

Sources: the session-cost adapters (Claude Code CLI + desktop transcripts under $CLAUDE_CONFIG_DIR or ~/.claude,
Windows, Linux and macOS alike; Antigravity's conversation databases; OpenAI Codex rollouts; Gorilla OpenCode's
session table; the generic JSONL contract; IBM Bob not measured yet). Read-only on transcripts: nothing here ever
deletes or edits one. Writes only state/rotate/ (meters, verdicts.jsonl, handovers.jsonl) and, on handover,
state/coordination/. No network, except `finished --gh`, which asks the local gh for the state of the PRs a session
mentioned (the owner's own GitHub, where those PRs live).
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from . import session_cost as sc
from ..core.settings import state_root as _state_root

VERSION = "1.1.0"
DEFAULTS = {"ctx_warn": 250_000, "ctx_rotate": 350_000, "usd_warn": 40.0, "usd_rotate": 75.0,
            "hours": 24, "idle_minutes": 60, "finished_hours": 72, "max_rotations": 5, "hook_every_s": 60}
LEVELS = ("OK", "WARN", "ROTATE")
EXIT = {"OK": 0, "WARN": 1, "ROTATE": 2}
PR_URL = re.compile(r"https://github\.com/[\w.-]+/[\w.-]+/pull/\d+")
REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)


# ---------------------------------------------------------------- config and places

def _root():
    from ..core import settings
    return settings.ROOT


def config():
    cfg = dict(DEFAULTS)
    try:
        from ..core import settings
        cfg.update({k: v for k, v in (settings.local_settings().get("rotate") or {}).items() if k in DEFAULTS})
    except Exception:  # noqa: BLE001 - a broken local file must not switch the rule off: the defaults hold
        pass
    return cfg


def state_dir():
    d = Path(os.environ.get("FIELDKIT_ROTATE_DIR") or (_state_root(_root()) / "state" / "rotate"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def coordination_dir():
    return Path(os.environ.get("FIELDKIT_COORDINATION_DIR") or (_state_root(_root()) / "state" / "coordination"))


# ---------------------------------------------------------------- the verdict (pure)

def verdict(context_last, usd, cfg=None):
    """-> (level, [reasons]). The worst of the two rules wins."""
    cfg = cfg or DEFAULTS
    level, why = 0, []
    for value, warn, rot, label in ((context_last, cfg["ctx_warn"], cfg["ctx_rotate"], "context per call"),
                                    (usd, cfg["usd_warn"], cfg["usd_rotate"], "session dollars")):
        if value >= rot:
            level = max(level, 2)
            why.append(f"{label} {_fmt(value, label)} >= ROTATE {_fmt(rot, label)}")
        elif value >= warn:
            level = max(level, 1)
            why.append(f"{label} {_fmt(value, label)} >= WARN {_fmt(warn, label)}")
    return LEVELS[level], why


def _fmt(v, label):
    return f"${v:.2f}" if "dollars" in label else f"{round(v / 1000)}k"


# ---------------------------------------------------------------- measuring (incremental, cached)

def _meter_file(path):
    return state_dir() / "meters" / (_stem(path) + ".json")


def _stem(path):
    """The session id: a Codex rollout's thread id, a database row's id, else the file name without .jsonl."""
    return path.stem if hasattr(path, "stem") else Path(path).stem


def _load_meter(path):
    try:
        m = json.loads(_meter_file(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    m["read"]["seen"] = set(m["read"].get("seen", []))
    return m


def _save_meter(path, m):
    f = _meter_file(path)
    f.parent.mkdir(parents=True, exist_ok=True)
    out = dict(m)
    # one reply is written as several adjacent lines with the same id: the last ids are all dedup needs
    out["read"] = {**m["read"], "seen": sorted(m["read"].get("seen", ()))[-500:]}
    f.write_text(json.dumps(out), encoding="utf-8")


def _fresh_meter():
    return {"read": {}, "facts_off": 0, "usd": 0.0, "calls": 0, "unpriced": 0, "context_last": 0,
            "context_peak": 0, "first_ts": None, "last_ts": None, "cwd": None, "branch": None,
            "entrypoint": None, "stop_reason": None, "prs": [], "task": None, "size": 0}


def _user_text(d):
    c = (d.get("message") or {}).get("content")
    if isinstance(c, list):
        c = "\n".join(x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text")
    return REMINDER.sub("", c).strip() if isinstance(c, str) else ""


def _facts(path, m):
    """Claude Code only: where the session works, how its last reply ended, the PRs it mentioned, its first task."""
    try:
        with open(path, "rb") as fh:
            fh.seek(m["facts_off"])
            chunk = fh.read()
    except OSError:
        return
    end = chunk.rfind(b"\n") + 1
    m["facts_off"] += end
    prs = set(m["prs"])
    for line in chunk[:end].splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if not isinstance(d, dict):
            continue
        prs.update(PR_URL.findall(line.decode("utf-8", "replace")))
        if d.get("isSidechain"):
            continue
        for k, f in (("cwd", "cwd"), ("gitBranch", "branch"), ("entrypoint", "entrypoint")):
            if d.get(k):
                m[f] = d[k]
        if d.get("type") == "assistant":
            m["stop_reason"] = (d.get("message") or {}).get("stop_reason") or m["stop_reason"]
        elif d.get("type") == "user" and m["task"] is None:
            t = _user_text(d)
            if t:
                m["task"] = t[:2000]
        if d.get("timestamp"):
            m["last_ts"] = d["timestamp"]
    m["prs"] = sorted(prs)


def measure(adapter, path, use_cache=True):
    """-> the session's facts: dollars, context of its own last call, where it works. Re-reads only new lines."""
    path = path if hasattr(path, "stat") else Path(path)     # a Codex rollout or a database row keeps its type
    m = _load_meter(path) if use_cache else None
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    if m is None or m.get("source") != adapter.name or (adapter.name == "claude-code" and size < m.get("size", 0)):
        m = _fresh_meter()                            # first look, or the file was rewritten: start over
    m["source"] = adapter.name
    for c in adapter.read(path, m["read"]):
        sc.price_call(c, sc.prices())
        if c.get("aggregate"):                        # the product's own session totals: they replace, not add
            m["calls"], m["usd"], m["unpriced"] = c["n"], c["usd"], c["n_unpriced"]
        else:
            m["calls"] += 1
            m["usd"] += c["usd"]
            m["unpriced"] += 0 if c["priced"] else 1
        if c.get("ts"):
            m["first_ts"] = min(m["first_ts"] or c["ts"], c["ts"])
            m["last_ts"] = max(m["last_ts"] or c["ts"], c["ts"])
        if not c.get("sub"):
            m["context_last"] = c["context"]
            m["context_peak"] = max(m["context_peak"], c["context"])
    if adapter.name == "claude-code":
        _facts(path, m)
    elif hasattr(adapter, "facts"):
        m.update({k: v for k, v in adapter.facts(path, m["read"]).items() if v})
    m["size"] = size
    if use_cache:
        _save_meter(path, m)
    title = adapter.title(path)
    if not title and adapter.name == "claude-code":
        title = _title_from_transcript(path)
    return {"session": path.stem, "source": adapter.name, "path": str(path), "title": title,
            "calls": m["calls"], "usd": round(m["usd"], 4), "unpriced_calls": m["unpriced"],
            "context_last": m["context_last"], "context_peak": m["context_peak"],
            "first": m["first_ts"], "last": m["last_ts"], "cwd": m["cwd"], "branch": m["branch"],
            "entrypoint": m["entrypoint"], "stop_reason": m["stop_reason"], "prs": m["prs"], "task": m["task"]}


def _title_from_transcript(path):
    """The desktop app writes {"type": "custom-title", "customTitle": ...} lines; the last one is the title."""
    try:
        with open(path, "rb") as fh:
            fh.seek(max(0, Path(path).stat().st_size - 2_000_000))
            tail = fh.read()
    except OSError:
        return ""
    for line in reversed(tail.splitlines()):
        if b'"custom-title"' in line:
            try:
                return json.loads(line).get("customTitle") or ""
            except ValueError:
                continue
    return ""


def judged(facts, cfg=None):
    level, why = verdict(facts["context_last"], facts["usd"], cfg or config())
    if facts.get("unpriced_calls"):
        why.append(f"{facts['unpriced_calls']} call(s) on models with no published price add no dollars")
    return {**facts, "level": level, "why": why}


def log(row, by):
    keep = ("session", "source", "title", "level", "why", "context_last", "usd", "calls")
    with open(state_dir() / "verdicts.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"at": time.strftime("%Y-%m-%d %H:%M:%S"), "by": by,
                            **{k: row.get(k) for k in keep}}) + "\n")


def _ago_h(ts, now):
    if not ts:
        return None
    if isinstance(ts, str):
        ts = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    return (now - ts) / 3600


def sessions(hours, adapters=None, now=None):
    """(adapter, path) for every session whose file changed in the last `hours` (all sources)."""
    now = now or time.time()
    out = []
    for a in adapters or sc.adapters_for("all"):
        for p in a.sessions():
            try:
                if now - p.stat().st_mtime <= hours * 3600:
                    out.append((a, p))
            except OSError:
                continue
    return out


def _under(cwd, folder):
    try:
        Path(cwd).resolve().relative_to(Path(folder).resolve())
        return True
    except (ValueError, OSError, TypeError):
        return False


def archived_ids():
    """-> the transcript ids of the Claude desktop sessions the app has archived (lifecycle/desktop.py, read-only)."""
    try:
        from ..lifecycle import desktop
        return {s["cli"] for s in desktop.sessions() if s["archived"] and s["cli"]}
    except Exception:  # noqa: BLE001 - an unreadable app store hides nothing: every session is judged
        return set()


def check(hours=None, session=None, adapters=None, cfg=None, now=None, source="all", cwd=None, newest=False,
          archived=None):
    """Every session active in the last `hours`, judged. source: one adapter's sessions only; cwd: only sessions
    working in that folder (or under it); newest: only the most recently active one (an agent asking about itself).
    A session the desktop app archived (archived: its transcript ids, default archived_ids()) is not judged: it does
    no more calls, and its transcript stays "active" for hours after (C3/I6, 2026-10-28: the guard's preflight named
    the archived Zero 8dc32564 as ROTATE on every job)."""
    cfg = cfg or config()
    adapters = adapters or sc.adapters_for(source)
    if session:
        a, p = sc.resolve(session, adapters)
        if a is None:
            raise FileNotFoundError(session)
        targets = [(a, p)]
    else:
        gone = archived_ids() if archived is None else set(archived)
        targets = [(a, p) for a, p in sessions(hours or cfg["hours"], adapters, now) if p.stem not in gone]
    targets.sort(key=lambda t: t[1].stat().st_mtime, reverse=True)
    rows = []
    for a, p in targets:
        try:
            r = judged(measure(a, p), cfg)
        except Exception as e:  # noqa: BLE001 - one unreadable session must not hide the others
            r = {"session": p.stem, "source": a.name, "path": str(p), "level": "OK", "error": str(e),
                 "why": [f"could not be measured: {e}"], "usd": 0.0, "context_last": 0, "calls": 0, "title": ""}
        if cwd and not _under(r.get("cwd"), cwd):
            continue
        log(r, "check")
        rows.append(r)
        if newest:
            break
    return sorted(rows, key=lambda r: (-EXIT[r["level"]], -r["usd"]))


def preflight_check(ctx=None, hours=2):
    """The run guard's check (fieldkit/core/preflight.py STEPS): sessions of ANY agent active in the last 2 hours
    over a line are named, with the step to take. It informs; it never stops the run (a STOP would end the work
    the handover needs)."""
    rows = [r for r in check(hours) if r["level"] != "OK"]
    if not rows:
        return "ok", "rotate: no session over the WARN line"
    top = rows[0]
    more = f" (+{len(rows) - 1} more)" if len(rows) > 1 else ""
    act = ("hand over: fieldkit rotate handover --session " + top["session"][:8] if top["level"] == "ROTATE"
           else "finish this job, the next one in a fresh session")
    return "warn", (f"rotate: {top['level']} {top['source']} {top['session'][:8]} - {'; '.join(top['why'][:2])}; "
                    f"{act}{more}")


# ---------------------------------------------------------------- the next step for the session itself

#: a turn the owner did not type: another session's message, a background task, a side-session or CI event. On such a
#: turn the desktop app refuses start_session, and a model that falls back to an own_initiative card puts a click in
#: front of the owner (2026-10-26 20:2x: the command post's rotation did exactly that; owner: "we really need to fix
#: these problems once for all")
EVENT_TURN = re.compile(r"^\s*(<cross-session-message|<task-notification|<side-sessions-event|<ci-monitor-event|"
                        r"<system-reminder>|\[SYSTEM NOTIFICATION)")


def owner_turn(call):
    """True when the prompt of this UserPromptSubmit is the owner's own words (not an event)."""
    prompt = str(call.get("prompt") or "")
    return bool(prompt.strip()) and not EVENT_TURN.match(prompt)       # no prompt seen: not the owner's (fail closed)


def next_step(row, transcript=None, owner=False):
    """The exact instruction for a session over the ROTATE line (what the hook shows it). Never a card: on a turn the
    owner did not type, start_session is not called at all; when the app refuses it, the successor is headless."""
    tp = transcript or row["path"]
    desktop = (row.get("entrypoint") or "").startswith("claude-desktop")
    where = row.get("cwd") or "the same folder"
    headless = f"`fieldkit task start <the note it wrote> --cwd \"{where}\"` (a headless successor, no click)"
    if not desktop:
        start = f"start a fresh `claude` in {where} with that brief, then end this one"
    elif owner:
        start = ("start the fresh session: Claude desktop start_session, initiation \"user_asked\", context \"fresh\", "
                 f"the brief as its prompt. If the app refuses it, NEVER propose a card: run {headless} instead. "
                 "Then archive this one (set_remote_control off if it is on, then archive_session \"self\")")
    else:
        start = ("this turn was not started by the owner, and start_session here would become a card the owner must "
                 f"click: do NOT call start_session. Run {headless}, then archive this one (set_remote_control off if "
                 "it is on, then archive_session \"self\"); the command post the owner talks to may instead wait: "
                 "this line comes back at the owner's next message")
    return (f"SESSION ROTATION (fieldkit rotate): this session is ROTATE - {'; '.join(row['why'])}. Every call now "
            f"re-sends {round(row['context_last'] / 1000)}k tokens. Start no new work here. At the next safe point "
            f"(work committed or written down): 1) python -m fieldkit rotate handover --transcript \"{tp}\" --title "
            "\"<job>\" --done \"<what is done>\" --next \"<what is left>\" --not-verified \"<what is not verified>\" "
            f"(writes the dated PLAN.md section and the note, prints the brief); 2) {start}.")


def warn_line(row):
    return (f"session rotation: WARN - {'; '.join(row['why'])}. Finish the current job in this session and hand "
            "the next one to a fresh session (fieldkit rotate check).")


def hook_line(call, event, now=None):
    """-> text for the session's context, or "". ROTATE: at every prompt and once mid-turn when first crossed;
    WARN: once when first crossed. Inside an autopilot job the budget hook enforces it instead. Never raises."""
    try:
        if os.environ.get("FIELDKIT_JOB_DIR") or os.environ.get("FIELDKIT_ROTATE") == "off":
            return ""
        tp = call.get("transcript_path")
        if not tp or not Path(tp).is_file():
            return ""
        now = now or time.time()
        cfg = config()
        mark = state_dir() / "said" / (Path(tp).stem + ".json")
        try:
            said = json.loads(mark.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            said = {"level": "OK", "t": 0}
        prompt = event in ("UserPromptSubmit", "SessionStart")
        # mid-turn the turn's kind is the one its prompt had (remembered); unknown = not the owner's (fail closed)
        owner = owner_turn(call) if event == "UserPromptSubmit" else bool(said.get("owner"))
        if not prompt and now - said.get("t", 0) < cfg["hook_every_s"]:
            return ""
        row = judged(measure(sc.ClaudeCode(), Path(tp)), cfg)
        changed = row["level"] != said.get("level")
        if changed:
            log(row, "hook")
        mark.parent.mkdir(parents=True, exist_ok=True)
        mark.write_text(json.dumps({"level": row["level"], "t": now, "owner": owner}), encoding="utf-8")
        if row["level"] == "ROTATE" and (prompt or changed):
            return next_step(row, tp, owner)
        if row["level"] == "WARN" and changed:
            return warn_line(row)
        return ""
    except Exception:  # noqa: BLE001 - a courtesy line must never break the session's hook
        return ""


from .rotate_stop import HANDOVER_CMD, HANDOVER_FILES, stop_refusal  # noqa: E402,F401 - R1, kept light for the PreToolUse hook


def hook(stdin=None):
    """Standalone hook (for a setup without the room hook): prints additionalContext when there is something."""
    try:
        call = json.loads((stdin or sys.stdin).read() or "{}")
        event = call.get("hook_event_name") or "UserPromptSubmit"
        line = hook_line(call, event)
        if line:
            print(json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": line}}))
    except Exception:  # noqa: BLE001
        pass
    return 0


# ---------------------------------------------------------------- handover

def _cell(s):
    return " ".join(str(s or "-").split()).replace("|", "/")


def handover(transcript, title, next_, done="", not_verified="", now=None, cfg=None):
    """Write the note + the dated PLAN.md section. -> {note, plan, plan_written, brief, row}."""
    now = now or time.time()
    a, p = sc.resolve(str(transcript), sc.adapters_for("all"))
    if a is None:
        raise FileNotFoundError(str(transcript))
    row = judged(measure(a, p), cfg)
    from ..peers import machine                       # the successor runs here: its title starts with this tag
    title = machine.prefix(title)
    stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(now))
    sid = row["session"][:8]
    coord = coordination_dir()
    coord.mkdir(parents=True, exist_ok=True)
    note = coord / f"rotate-{time.strftime('%Y-%m-%d-%H%M', time.localtime(now))}-{sid}.md"
    where = row.get("cwd") or "?"
    branch = row.get("branch") or "?"
    brief = (f"You are continuing \"{title}\" from session {sid}, rotated by fieldkit rotate ({row['level']}: "
             f"{'; '.join(row['why']) or 'by hand'}). Do NOT start over. Read {note} first.\n"
             f"Working folder: {where} (branch {branch}).\n"
             f"Done: {done or '-'}\nNext: {next_}\nNOT verified: {not_verified or '-'}\n"
             f"SESSION TITLE: {title} (title yourself exactly this; it starts with the machine's tag)\n")
    if row.get("task"):
        brief += "\nThe original task (its first message, trimmed):\n" + row["task"] + "\n"
    from ..lifecycle import protocol                  # start side jobs headless; clean up and archive when done
    brief += protocol.brief_tail()
    from ..zero import net as zero_net                # a rotated Zero leaves a pending handover for its relief
    brief += zero_net.on_rotate(row["session"], now)
    honest = _honesty(p, done, not_verified)          # rule HONESTY: the Done line backed by recorded tool output
    if honest:
        brief += ("\nHONESTY: the hand-over's claims were NOT backed (fieldkit honesty check): "
                  + "; ".join(f"{x['kind']} {x['detail']}" for x in honest) + ". Verify them before relying on them.\n")
    from .. import plan                               # where the harness plan stands, so the successor does not forget
    pl = plan.line()
    brief += f"\n{pl}\n" if pl else ""
    note.write_text(
        f"# Session rotated: {title}\n\n"
        f"- when: {stamp} (local)\n- session: {row['session']} ({row['source']}, {row.get('entrypoint') or '?'})\n"
        f"- transcript: {row['path']}\n- verdict: {row['level']} - {'; '.join(row['why']) or '-'}\n"
        f"- measured: {row['calls']} calls, ${row['usd']:.2f}, last call sent {round(row['context_last'] / 1000)}k "
        f"tokens (peak {round(row['context_peak'] / 1000)}k)\n- working folder: {where}\n- branch: {branch}\n"
        f"- PRs it mentioned: {', '.join(row['prs']) or 'none'}\n"
        f"- honesty: {len(honest)} finding(s)"
        f"{': ' + '; '.join(x['kind'] + ' ' + x['detail'] for x in honest) if honest else ''}\n\n"
        f"## Done\n\n{done or '-'}\n\n## Next\n\n{next_}\n\n## NOT verified\n\n{not_verified or '-'}\n\n"
        f"## Brief for the fresh session\n\n```\n{brief}```\n", encoding="utf-8")
    plan = coord / "PLAN.md"
    written = plan.is_file()
    if written:                                       # never creates the plan: a missing PLAN.md is said, not hidden
        with open(plan, "a", encoding="utf-8") as f:
            f.write(f"\n## {stamp}: session rotated: {_cell(title)} ({sid})\n\n"
                    "| Session | Verdict | Done | Next | NOT verified | Note |\n|---|---|---|---|---|---|\n"
                    f"| {sid} ({_cell(where)}, {_cell(branch)}) | {row['level']}: {_cell('; '.join(row['why']))} | "
                    f"{_cell(done)} | {_cell(next_)} | {_cell(not_verified)} | {note.name} |\n")
    with open(state_dir() / "handovers.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"at": stamp, "session": row["session"], "title": title, "level": row["level"],
                            "note": str(note), "plan_written": written}) + "\n")
    return {"note": str(note), "plan": str(plan), "plan_written": written, "brief": brief, "row": row,
            "honesty": honest}


def _honesty(path, done, not_verified):
    """-> fieldkit honesty findings of the hand-over's Done / NOT verified text against the session's tool output.
    Never blocks a rotation (a session at its limit must still hand over): the findings go into the note and brief."""
    try:
        from ..method import honesty
        ev, _kind = honesty.session_evidence(path)
        return honesty.check(f"Done: {done or '-'}\nNOT verified: {not_verified or ''}\n", ev)
    except Exception as e:  # noqa: BLE001 - a checker crash is said, not hidden
        return [{"kind": "ERROR", "line": 0, "text": "", "detail": f"fieldkit honesty could not run: {e}"}]


# ---------------------------------------------------------------- finished side sessions

def pr_state(url, run=subprocess.run):
    """OPEN / MERGED / CLOSED from the local gh, or None when gh cannot say."""
    try:
        r = run(["gh", "pr", "view", url, "--json", "state", "-q", ".state"], capture_output=True, text=True,
                timeout=30)
        return r.stdout.strip() or None if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def finished(hours=None, use_gh=False, cfg=None, now=None, adapters=None, exclude=(), gh=pr_state):
    """Claude Code sessions whose last reply ended its turn, idle for idle_minutes, with no open PR. A session that
    mentioned a PR whose state is unknown is never listed as finished (fail closed). -> (finished, held)."""
    cfg = cfg or config()
    now = now or time.time()
    done, held = [], []
    for a, p in sessions(hours or cfg["finished_hours"], adapters or [sc.ClaudeCode()], now):
        if p.stem in exclude:
            continue
        r = measure(a, p)
        idle_h = _ago_h(r["last"], now)
        r["idle_minutes"] = None if idle_h is None else int(idle_h * 60)
        reasons = []
        if r["stop_reason"] not in ("end_turn", "stop_sequence"):
            reasons.append(f"last reply did not end its turn ({r['stop_reason'] or 'no reply'})")
        if idle_h is None or idle_h * 60 < cfg["idle_minutes"]:
            reasons.append(f"active {r['idle_minutes']} min ago (< {cfg['idle_minutes']})")
        states = {u: (gh(u) if use_gh else None) for u in r["prs"]}
        r["pr_states"] = states
        open_ = [u for u, s in states.items() if s == "OPEN"]
        unknown = [u for u, s in states.items() if s is None]
        if open_:
            reasons.append("open PR " + ", ".join(open_))
        if unknown:
            reasons.append("PR state not checked (add --gh): " + ", ".join(unknown))
        r["held_by"] = reasons
        (held if reasons else done).append(r)
    return done, held


# ---------------------------------------------------------------- the rule for agents without a hook

RULE_BEGIN, RULE_END = "<!-- fieldkit-rotate-rule v1 -->", "<!-- /fieldkit-rotate-rule -->"
#: where each agent reads its own standing instructions for every session (its global file), so the rule reaches it
RULE_FILES = {"antigravity": Path.home() / ".gemini" / "GEMINI.md",
              "codex": Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "AGENTS.md"}
#: how each agent is told: "hook" = the harness adds the line itself; "rule" = the agent runs check itself
TRIGGER = {"claude-code": "room hook (UserPromptSubmit) + the tool router (PreToolUse) + autopilot budget hook",
           "antigravity": "rule in GEMINI.md + autopilot budget hook",
           "codex": "rule in AGENTS.md",
           "gorilla-opencode": "rule in the project's AGENTS.md (it has no global instruction file)",
           "generic": "rule in the agent's own instruction file (AGENTS.md is read by most)",
           "ibm-bob": "none: not measured"}


def rule_text():
    return (f"{RULE_BEGIN}\n"
            "## Session rotation (fieldkit rotate)\n\n"
            "At the start "
            "of each task and after each commit, run `fieldkit rotate check --source <agent> --cwd . --newest` "
            "(agent: codex, antigravity, gorilla-opencode, generic; Antigravity leaves out `--cwd .`); SHORT SESSIONS "
            "says what WARN and ROTATE mean. ROTATE: at the next safe point (work committed or written down) run "
            "`fieldkit rotate handover --session <id from the check> --title \"<job>\" --done \"<done>\" "
            "--next \"<left>\" --not-verified \"<not verified>\"`, start a fresh session with the brief it prints, "
            "and end this one.\n"
            f"{RULE_END}\n")


def rule_present(path):
    try:
        return RULE_BEGIN in Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False


def install_rule(path):
    """Add the rule block to an instruction file, or replace an older copy of it. Keeps the file's line endings.
    -> "added" | "updated" | "same"."""
    path = Path(path)
    raw = path.read_bytes() if path.is_file() else b""
    nl = "\r\n" if b"\r\n" in raw else "\n"
    text = raw.decode("utf-8").replace("\r\n", "\n")
    block = rule_text()
    if RULE_BEGIN in text and RULE_END in text:
        head, rest = text.split(RULE_BEGIN, 1)
        tail = rest.split(RULE_END, 1)[1]
        new = head + block + (tail[1:] if tail.startswith("\n") else tail)
        how = "same" if new == text else "updated"
    else:
        new = text + ("" if not text or text.endswith("\n\n") else "\n" if text.endswith("\n") else "\n\n") + block
        how = "added"
    if how != "same":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(new.replace("\n", nl).encode("utf-8"))
    return how


def agents_status(project_agents_md=None):
    """Per agent: is it measured (an adapter that reads its sessions) and is it triggered (something puts the
    verdict in front of it). -> [{agent, measured, sessions, triggered, how, note}]."""
    out = []
    try:
        from ..autopilot import room
        hook_ok = not room.doctor()["missing"]                # a stale PostToolUse entry still triggers (C6)
    except Exception:  # noqa: BLE001
        hook_ok = False
    project = Path(project_agents_md) if project_agents_md else _root() / "AGENTS.md"
    for name, cls in sc.ADAPTERS.items():
        a = cls()
        verified = getattr(a, "verified", True)
        n = len(a.sessions()) if verified else 0
        if name == "claude-code":
            trig, note = hook_ok, "" if hook_ok else "room hook not wired: fieldkit room doctor"
        elif name in RULE_FILES:
            trig = rule_present(RULE_FILES[name])
            note = "" if trig else f"no rule in {RULE_FILES[name]}: fieldkit rotate rule --install {RULE_FILES[name]}"
        elif name == "ibm-bob":
            trig, note = False, (f"format not verified ({'found' if a.root.is_file() else 'not installed'}: "
                                 f"{a.root}); see session_cost.IBMBob, or write the generic contract")
        else:
            trig = rule_present(project)
            note = "" if trig else f"no rule in {project}: fieldkit rotate rule --install {project}"
        out.append({"agent": name, "measured": verified, "sessions": n, "triggered": trig, "how": TRIGGER[name],
                    "where": str(a.root), "note": note})
    return out


# ---------------------------------------------------------------- doctor and CLI

def doctor():
    cfg = config()
    ok = True
    print(f"rotate {VERSION} doctor")
    print(f"  [ok] thresholds: context WARN {cfg['ctx_warn']:,} / ROTATE {cfg['ctx_rotate']:,} tokens per call; "
          f"dollars WARN ${cfg['usd_warn']:.2f} / ROTATE ${cfg['usd_rotate']:.2f} (\"rotate\" in fieldkit.local.json)")
    if not (cfg["ctx_warn"] <= cfg["ctx_rotate"] and cfg["usd_warn"] <= cfg["usd_rotate"]):
        ok = False
        print("  [--] a WARN line is above its ROTATE line")
    try:
        p = sc.prices()
        dates = ", ".join("%s %s" % (k, v["fetched"]) for k, v in p["providers"].items())
        print(f"  [ok] prices.json: {dates}")
    except (OSError, ValueError, KeyError) as e:
        ok = False
        print(f"  [--] prices.json: {e}")
    print("  per agent: measured (its sessions are read) / triggered (the verdict reaches it):")
    for s in agents_status():
        mark = "ok" if s["measured"] and s["triggered"] else "WARN" if not s["measured"] or s["sessions"] else ".."
        print(f"  [{mark}] {s['agent']:16} measured {'yes' if s['measured'] else 'NO '} ({s['sessions']} session(s), "
              f"{s['where']}); triggered {'yes' if s['triggered'] else 'NO '}: {s['how']}")
        if s["note"]:
            print(f"         {s['note']}")
    print("  every agent: the run guard (fieldkit.core.preflight) names any session over a line before heavy work")
    try:
        d = state_dir()
        (d / ".probe").write_text("x", encoding="utf-8")
        (d / ".probe").unlink()
        print(f"  [ok] state: {d}")
    except OSError as e:
        ok = False
        print(f"  [--] state not writable: {e}")
    plan = coordination_dir() / "PLAN.md"
    print(f"  [{'ok' if plan.is_file() else '..'}] PLAN.md for handovers: {plan}")
    try:
        from ..autopilot import room
        r = room.doctor()
        print(f"  [{'ok' if r['ok'] else '--'}] room hook (carries the rotation line): "
              + ("wired" if r["ok"] else "MISSING " + ", ".join(r["missing"]) + "; fieldkit room doctor prints it"))
        ok = ok and r["ok"]
    except Exception as e:  # noqa: BLE001
        print(f"  [..] room hook not checked: {e}")
    print("  reads transcripts, never edits or deletes one; no network (finished --gh asks the local gh)")
    return 0 if ok else 1


def _table(rows, out=sys.stdout):
    for r in rows:
        out.write(f"{r['level']:7} {r['source'][:6]:6} {r['session'][:8]:8} ${r['usd']:8.2f} "
                  f"{round(r['context_last'] / 1000):5d}k/call {r['calls']:5d} calls  {(r.get('title') or '')[:48]}\n")
        for w in r["why"]:
            out.write(f"          {w}\n")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv[:1] == ["--hook"]:
        return hook()
    p = argparse.ArgumentParser(prog="fieldkit rotate", description="Session rotation: OK / WARN / ROTATE.",
                                formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__.split("WHY")[0])
    sub = p.add_subparsers(dest="cmd")
    c = sub.add_parser("check")
    c.add_argument("--hours", type=float)
    c.add_argument("--session")
    c.add_argument("--source", choices=["all"] + list(sc.ADAPTERS), default="all", help="one agent's sessions")
    c.add_argument("--cwd", help="only sessions working in this folder (or under it)")
    c.add_argument("--newest", action="store_true", help="only the most recently active session (an agent's own)")
    c.add_argument("--json", action="store_true")
    rl = sub.add_parser("rule", help="the rule for agents without a hook; --install adds it to an instruction file")
    rl.add_argument("--install", action="append", default=[], metavar="FILE")
    h = sub.add_parser("handover")
    g = h.add_mutually_exclusive_group(required=True)
    g.add_argument("--transcript")
    g.add_argument("--session")
    h.add_argument("--title", required=True)
    h.add_argument("--next", required=True, dest="next_")
    h.add_argument("--done", default="")
    h.add_argument("--not-verified", default="")
    h.add_argument("--json", action="store_true")
    f = sub.add_parser("finished")
    f.add_argument("--hours", type=float)
    f.add_argument("--gh", action="store_true", help="ask the local gh for each mentioned PR's state")
    f.add_argument("--exclude", action="append", default=[], help="a session id to leave out (your own)")
    f.add_argument("--json", action="store_true")
    lg = sub.add_parser("log")
    lg.add_argument("n", nargs="?", type=int, default=20)
    sub.add_parser("doctor")
    a = p.parse_args(argv)
    if a.cmd in (None, "check"):
        try:
            rows = check(getattr(a, "hours", None), getattr(a, "session", None), source=getattr(a, "source", "all"),
                         cwd=getattr(a, "cwd", None), newest=getattr(a, "newest", False))
        except FileNotFoundError as e:
            print(f"rotate: not found: {e}", file=sys.stderr)
            return 3
        if getattr(a, "json", False):
            print(json.dumps(rows, indent=1))
        else:
            _table(rows)
            n = {lv: sum(r["level"] == lv for r in rows) for lv in LEVELS}
            print(f"{len(rows)} session(s): {n['ROTATE']} ROTATE, {n['WARN']} WARN, {n['OK']} OK")
        return max([EXIT[r["level"]] for r in rows] or [0])
    if a.cmd == "handover":
        try:
            r = handover(a.transcript or a.session, a.title, a.next_, a.done, a.not_verified)
        except FileNotFoundError as e:
            print(f"rotate: not found: {e}", file=sys.stderr)
            return 3
        if a.json:
            print(json.dumps({k: v for k, v in r.items() if k != "row"}, indent=1))
        else:
            print(f"note: {r['note']}")
            print(f"PLAN.md: {'section appended' if r['plan_written'] else 'NOT FOUND, nothing written'}: {r['plan']}")
            for x in r.get("honesty") or []:
                print(f"HONESTY {x['kind']}: {x['detail']}")
            print("NEXT: start a fresh session with the brief below, then archive this one "
                  "(Claude desktop: start_session context fresh + archive_session \"self\").\n")
            print(r["brief"])
        return 0 if r["plan_written"] else 1
    if a.cmd == "finished":
        done, held = finished(a.hours, a.gh, exclude=a.exclude)
        if a.json:
            print(json.dumps({"finished": done, "held": held}, indent=1))
            return 0
        print(f"{len(done)} finished session(s), ready to archive (archive them in the app; this tool deletes nothing):")
        for r in done:
            print(f"  {r['session'][:8]}  idle {r['idle_minutes']} min  ${r['usd']:.2f}  {(r.get('title') or '')[:50]}"
                  f"  {r.get('cwd') or ''}")
        print(f"{len(held)} held (still working, an open PR, or a PR not checked)")
        return 0
    if a.cmd == "log":
        f = state_dir() / "verdicts.jsonl"
        lines = f.read_text(encoding="utf-8").splitlines()[-a.n:] if f.is_file() else []
        print("\n".join(lines))
        return 0
    if a.cmd == "rule":
        if not a.install:
            print(rule_text(), end="")
        for f in a.install:
            print(f"{install_rule(f)}: {f}")
        return 0
    if a.cmd == "doctor":
        return doctor()
    return 0


if __name__ == "__main__":
    sys.exit(main())
