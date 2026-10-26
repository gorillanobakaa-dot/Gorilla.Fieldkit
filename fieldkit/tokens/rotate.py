# Version: 1.0.0 · updated 26-10-26
"""Session rotation: get rid of the monster sessions before they cost more, and start a fresh one. Deterministic.

    fieldkit rotate check [--hours 24] [--session ID] [--json]   every session active in the last N hours, measured,
                                                                 one verdict each: OK / WARN / ROTATE (exit 0 / 1 / 2)
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

Sources: the session-cost adapters (Claude Code CLI + desktop transcripts under $CLAUDE_CONFIG_DIR or ~/.claude,
Windows, Linux and macOS alike; Antigravity's conversation databases). Read-only on transcripts: nothing here ever
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

VERSION = "1.0.0"
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
    d = Path(os.environ.get("FIELDKIT_ROTATE_DIR") or (_root() / "state" / "rotate"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def coordination_dir():
    return Path(os.environ.get("FIELDKIT_COORDINATION_DIR") or (_root() / "state" / "coordination"))


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
    return state_dir() / "meters" / (Path(path).stem + ".json")


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
    path = Path(path)
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


def check(hours=None, session=None, adapters=None, cfg=None, now=None):
    cfg = cfg or config()
    adapters = adapters or sc.adapters_for("all")
    if session:
        a, p = sc.resolve(session, adapters)
        if a is None:
            raise FileNotFoundError(session)
        targets = [(a, p)]
    else:
        targets = sessions(hours or cfg["hours"], adapters, now)
    rows = []
    for a, p in targets:
        try:
            r = judged(measure(a, p), cfg)
        except Exception as e:  # noqa: BLE001 - one unreadable session must not hide the others
            r = {"session": p.stem, "source": a.name, "path": str(p), "level": "OK", "error": str(e),
                 "why": [f"could not be measured: {e}"], "usd": 0.0, "context_last": 0, "calls": 0, "title": ""}
        log(r, "check")
        rows.append(r)
    return sorted(rows, key=lambda r: (-EXIT[r["level"]], -r["usd"]))


# ---------------------------------------------------------------- the next step for the session itself

def next_step(row, transcript=None):
    """The exact instruction for a session over the ROTATE line (what the hook shows it)."""
    tp = transcript or row["path"]
    desktop = (row.get("entrypoint") or "").startswith("claude-desktop")
    start = ("start a fresh session with that brief (Claude desktop: start_session, context \"fresh\", the brief as "
             "its prompt), then archive this one (archive_session \"self\")" if desktop else
             f"start a fresh `claude` in {row.get('cwd') or 'the same folder'} with that brief, then end this one")
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
        if not prompt and now - said.get("t", 0) < cfg["hook_every_s"]:
            return ""
        row = judged(measure(sc.ClaudeCode(), Path(tp)), cfg)
        changed = row["level"] != said.get("level")
        if changed:
            log(row, "hook")
        mark.parent.mkdir(parents=True, exist_ok=True)
        mark.write_text(json.dumps({"level": row["level"], "t": now}), encoding="utf-8")
        if row["level"] == "ROTATE" and (prompt or changed):
            return next_step(row, tp)
        if row["level"] == "WARN" and changed:
            return warn_line(row)
        return ""
    except Exception:  # noqa: BLE001 - a courtesy line must never break the session's hook
        return ""


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
             f"Done: {done or '-'}\nNext: {next_}\nNOT verified: {not_verified or '-'}\n")
    if row.get("task"):
        brief += "\nThe original task (its first message, trimmed):\n" + row["task"] + "\n"
    from ..lifecycle import protocol                  # start side jobs headless; clean up and archive when done
    brief += protocol.brief_tail()
    note.write_text(
        f"# Session rotated: {title}\n\n"
        f"- when: {stamp} (local)\n- session: {row['session']} ({row['source']}, {row.get('entrypoint') or '?'})\n"
        f"- transcript: {row['path']}\n- verdict: {row['level']} - {'; '.join(row['why']) or '-'}\n"
        f"- measured: {row['calls']} calls, ${row['usd']:.2f}, last call sent {round(row['context_last'] / 1000)}k "
        f"tokens (peak {round(row['context_peak'] / 1000)}k)\n- working folder: {where}\n- branch: {branch}\n"
        f"- PRs it mentioned: {', '.join(row['prs']) or 'none'}\n\n"
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
    return {"note": str(note), "plan": str(plan), "plan_written": written, "brief": brief, "row": row}


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
    for a in sc.adapters_for("all"):
        n = len(a.sessions())
        print(f"  [{'ok' if n else '..'}] {a.name:12} {n} session(s) under {a.root}")
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
    c.add_argument("--json", action="store_true")
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
            rows = check(getattr(a, "hours", None), getattr(a, "session", None))
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
    if a.cmd == "doctor":
        return doctor()
    return 0


if __name__ == "__main__":
    sys.exit(main())
