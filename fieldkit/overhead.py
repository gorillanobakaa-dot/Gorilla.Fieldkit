"""How much longer than the bare build script a job took, and where the extra time went (fieldkit overhead).

Owner, 2026-10-26: the bar is Gentoo Portage and his own pre-AI build script - a 90-minute Firefox or kernel compile
and package, unattended, no clicks. AI tooling turned it into days ("This is not progress, is regress"). The test cost
audit the same day measured Firefox 158: 88 minutes of migration took 61 hours. Nothing showed that while it happened;
this meter shows it for every job.

    fieldkit overhead [--state DIR] [--json]          one line per known job (build-harness tasks, goals)
    fieldkit overhead JOB [--session ID]... [--since T] [--until T] [--bare MIN] [--json]
                                                      one job: a build-harness task name, or goal:<id>
    fieldkit overhead baselines [--json]              the bare times the stage logs give

Every second of a job's wall clock goes to exactly ONE bucket; where sources overlap the first in this list wins:
    bare     the one run of each build stage the bare script does (migrate, compile, package): the longest that
             succeeded. The sum of these is the baseline.
    rework   every other run of a stage: a second compile, the leak gate again, post-install again, a re-run of a
             check on a tree that already passed it, a goal job attempt after a failed one
    tests    tests and gates: package checks, post-install, leak gate, replay/check-change, testcost rows,
             pytest / mutate / telemetry scan calls in the transcript
    tools    the agent's other tool calls (reads, edits, git, ...)
    llm      the model generating: a Claude Code transcript's request -> response
    owner    waiting for the owner: a turn ended until the owner's next message, open inbox items, the owner's
             approve of a plan or of pre-approvals, approval prompts (a file tool that waited over 60 s, a question
             to the owner), denied tool calls
    idle     none of the above (sleep / poll loops count here)

A journal that falls silent for 24 hours ends a job; what follows is a follow-up job (`firefox-158.0 #2`).
Budget: "overhead": {"max_ratio": 1.5} in fieldkit.local.json; a job over it is WARN (goal status, the goal report and
board_line() for the Board). Without a stage log the baseline is unknown (NOBASE) unless "overhead": {"baselines":
{"JOB": MINUTES}} or --bare gives it; NOBASE never warns. Transcripts come from --session or "overhead": {"sessions":
{"JOB": ["ID", ...]}}; never guessed (other sessions run at the same time). Claude Code transcripts only.

Read-only, no network, zero model tokens, no prompts. Exit 0 OK, 1 a job over budget (WARN), 2 bad arguments.
"""
import argparse
import json
import os
import re
import statistics
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from .core.settings import state_root as _state_root

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / "fieldkit.local.json"

BUCKETS = ("bare", "rework", "tests", "tools", "llm", "owner", "idle")        # priority order, first wins
LABEL = {"bare": "bare work", "rework": "rework", "tests": "tests+gates", "tools": "agent tools", "llm": "LLM",
         "owner": "waiting-for-owner", "idle": "idle"}
BARE_STAGES = ("migrate", "compile", "package")
MIGRATE_EVENTS = {"migrate-init", "migrate-work", "migrate-check", "migrate-action", "script-start",
                  "script-done", "script-failed", "auto-done", "auto-miss", "obsolete", "dropped", "packet", "lesson",
                  "submit", "superseded", "done", "hand-edit", "unblock", "revert"}
MAX_RATIO = 1.5
SILENCE_S = 24 * 3600          # a journal silent this long ends a job
BURST_GAP_S = 30 * 60          # migration events closer than this are one migration run
LLM_CAP_S = 15 * 60            # one model response never counts longer (a stalled stream)
PROMPT_S = 60                  # a file tool that took longer waited on an approval prompt
POST_INSTALL_CAP_S = 30 * 60   # post_install has no start event: from the install, at most 30 min (audit rule)
VERIFY_CAP_S = 90 * 60
FILE_TOOLS = {"Read", "Edit", "Write", "MultiEdit", "NotebookEdit", "Glob", "Grep"}
DENY_MARKS = ("Permission for this action was denied", "doesn't want to proceed", "The user rejected",
              "denied this tool", "tool use was rejected")


# ---- time ---------------------------------------------------------------------------------------------------------
def parse_t(s):
    """Epoch seconds. ISO with Z/offset = that; a naive 'YYYY-MM-DD HH:MM:SS' (journals, events.log, inbox) = local
    time, or FIELDKIT_OVERHEAD_UTC_OFFSET minutes when set (tests pin it); 'YYYYMMDD-HHMMSS' (run folders) too."""
    s = str(s).strip()
    if re.fullmatch(r"\d{8}-\d{6}", s):
        s = f"{s[:4]}-{s[4:6]}-{s[6:8]} {s[9:11]}:{s[11:13]}:{s[13:15]}"
    if re.search(r"(Z|[+-]\d\d:?\d\d)$", s) and len(s) > 19:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    dt = datetime.strptime(s.replace("T", " ")[:19], "%Y-%m-%d %H:%M:%S")
    off = os.environ.get("FIELDKIT_OVERHEAD_UTC_OFFSET")
    if off not in (None, ""):
        return dt.replace(tzinfo=timezone(timedelta(minutes=int(off)))).timestamp()
    return dt.astimezone().timestamp()


def show_t(t):
    off = os.environ.get("FIELDKIT_OVERHEAD_UTC_OFFSET")
    if off not in (None, ""):
        return datetime.fromtimestamp(t, timezone(timedelta(minutes=int(off)))).strftime("%Y-%m-%d %H:%M")
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(t))


def fmt(s):
    m = int(round((s or 0) / 60))
    if m < 60:
        return f"{m} min"
    h, m = divmod(m, 60)
    return f"{h} h {m:02d} min"


# ---- config -------------------------------------------------------------------------------------------------------
def config(path=None):
    p = Path(path or LOCAL)
    try:
        cfg = json.loads(p.read_text(encoding="utf-8")).get("overhead") or {}
    except (OSError, ValueError):
        cfg = {}
    try:
        ratio = float(cfg.get("max_ratio", MAX_RATIO))
    except (TypeError, ValueError):
        ratio = MAX_RATIO
    return {"max_ratio": ratio if ratio > 0 else MAX_RATIO, "baselines": cfg.get("baselines") or {},
            "sessions": cfg.get("sessions") or {}}


def _jsonl(path):
    rows = []
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return rows
    for line in text.splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if isinstance(d, dict):
            rows.append(d)
    return rows


def _rj(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# ---- stage runs from a build-harness journal ----------------------------------------------------------------------
def journal_runs(events):
    """[{stage, start, end, ok}] from journal events (already parsed, in order)."""
    runs, build, pkg_end, install, mig, started = [], None, None, None, None, None
    for e in events:
        t, k = e["_t"], e.get("event")
        if k == "start":
            started = t
        if k == "approve" and started is not None and "owner" in str(e.get("who", "")):
            runs.append({"stage": "owner-approve", "start": started, "end": t, "ok": True})
        if k in MIGRATE_EVENTS:
            if mig and t - mig["end"] <= BURST_GAP_S:
                mig["end"] = t
            else:
                mig = {"stage": "migrate", "start": t, "end": t, "ok": True}
                runs.append(mig)
            continue
        if k == "build-start":
            build = t
        elif k == "build-stage-done" and build is not None:
            stage = "compile" if e.get("stage") == "build" else "package"
            runs.append({"stage": stage, "start": build, "end": t, "ok": True})
            build = t if stage == "compile" else None
            if stage == "package":
                pkg_end = t
        elif k == "build-stop" and build is not None:
            stage = "package" if e.get("stage") == "package" else "compile"
            runs.append({"stage": stage, "start": build, "end": t, "ok": False})
            build = None
        elif k == "build-verified" and pkg_end is not None:
            if t - pkg_end < VERIFY_CAP_S:
                runs.append({"stage": "package-checks", "start": pkg_end, "end": t, "ok": e.get("ok") is True})
            pkg_end = None
        elif k in ("install", "backup"):
            install = t
        elif k == "post_install":
            start = max(install if install is not None else t - 600, t - POST_INSTALL_CAP_S)
            runs.append({"stage": "post-install", "start": start, "end": t, "ok": e.get("ok") is True})
            install = t
        elif k in ("check-change", "replay-proof", "installer-test") and e.get("seconds"):
            runs.append({"stage": k, "start": t - float(e["seconds"]), "end": t, "ok": e.get("ok") is True})
    return runs


def leakgate_runs(folder, events):
    """Leak gate runs from their folders: gate-plan.json `started` -> the journal's next leakgate event before the
    next run starts, else the results file's time, else the newest file."""
    folder = Path(folder)
    plans = []
    for d in sorted(folder.glob("2*")) if folder.is_dir() else []:
        g = _rj(d / "gate-plan.json")
        if not g or not g.get("started"):
            continue
        try:
            plans.append((parse_t(g["started"]), d))
        except ValueError:
            continue
    marks = [(e["_t"], e) for e in events if e.get("event") == "leakgate"]
    runs = []
    for i, (start, d) in enumerate(plans):
        nxt = plans[i + 1][0] if i + 1 < len(plans) else float("inf")
        hit = next(((t, e) for t, e in marks if start < t <= nxt), None)
        if hit:
            end, ok = hit[0], hit[1].get("final") == "PASS"
        else:
            ok = False
            res = [d / f for f in ("test-results.original.json", "test-results.json") if (d / f).is_file()]
            files = res or [f for f in d.iterdir() if f.is_file() and f.name not in ("REVIEW.md", "gate-plan.json")]
            end = min(f.stat().st_mtime for f in res) if res else max((f.stat().st_mtime for f in files), default=start)
            end = min(end, nxt)            # a later re-judge touches files; a run never outlasts the next one's start
        runs.append({"stage": "leak-gate", "start": start, "end": max(start, end), "ok": ok, "run": d.name})
    return runs


def classify_runs(runs):
    """-> (intervals, kept). Migrate: the first run is bare (later bursts are fixes after a build); compile and
    package: the longest successful run (none succeeded: the longest); a gate stage: the last successful run is tests
    (none: the longest); every other run of a stage is rework."""
    out, keep = [], {}
    for stage in {r["stage"] for r in runs}:
        rs = [r for r in runs if r["stage"] == stage]
        ok = [r for r in rs if r["ok"]]
        if stage == "migrate":
            keep[stage] = rs[0]
        elif stage in BARE_STAGES:
            keep[stage] = max(ok or rs, key=lambda r: r["end"] - r["start"])
        elif ok:
            keep[stage] = ok[-1]
        else:
            keep[stage] = max(rs, key=lambda r: r["end"] - r["start"])
    for r in runs:
        if r["stage"] == "owner-approve":
            b = "owner"
        elif keep.get(r["stage"]) is r:
            b = "bare" if r["stage"] in BARE_STAGES else "tests"
        else:
            b = "rework"
        out.append((r["start"], r["end"], b, r["stage"]))
    return out, {s: r for s, r in keep.items() if s in BARE_STAGES}


# ---- transcripts --------------------------------------------------------------------------------------------------
def claude_projects():
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "projects"


def transcript_files(ids, root=None):
    """Main transcript + subagents for every session id (a prefix is enough)."""
    root = Path(root or claude_projects())
    out = []
    for sid in ids:
        for f in sorted(root.glob(f"*/{sid}*.jsonl")):
            out.append(f)
            out += sorted((f.with_suffix("") / "subagents").glob("*.jsonl"))
    return out


def bash_kind(cmd):
    c = (cmd or "").lower()
    if re.search(r"git commit|pytest|\bmutate\b|leakgate|leak-gate|check-change|replay|go test|go vet|cargo test|"
                 r"telemetry scan|privacy scan|public-check|post-install|post_install|about-pages|testcost run|"
                 r"\bverify\b|e2e|scenario", c):
        return "tests"
    if re.search(r"build-harness\b.*\bbuild|mach build|go build|cargo build|pipeline run|fieldkit build|nfpm", c):
        return "compile"
    if re.search(r"^\s*(sleep|start-sleep)\b|[;&|]\s*(sleep|start-sleep) \d|fieldkit wait|wait-for|gh run watch|"
                 r"gh pr checks .*--watch", c):
        return "poll"
    return "other"


def _human(d, cont):
    if d.get("isMeta") or d.get("isCompactSummary"):
        return False
    if isinstance(cont, str):
        return not cont.lstrip().startswith("<")
    if isinstance(cont, list) and cont:
        first = cont[0]
        if first.get("type") == "image":
            return True
        if first.get("type") == "text":
            return not str(first.get("text", "")).lstrip().startswith("<")
    return False


def _text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(str(b.get("text", "")) for b in content if isinstance(b, dict))
    return ""


def transcript_intervals(paths, journal=False):
    """-> (intervals, counts). llm = previous entry -> assistant entry (capped); tool = tool_use -> tool_result;
    owner = previous entry -> a message the owner typed, approval prompts, denied calls."""
    out, counts = [], Counter()
    for f in paths:
        prev, pend = None, {}
        try:
            fh = open(f, encoding="utf-8", errors="replace")
        except OSError:
            continue
        with fh:
            for line in fh:
                if '"timestamp"' not in line:
                    continue
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                ty, ts = d.get("type"), d.get("timestamp")
                if ty not in ("user", "assistant") or not ts:
                    continue
                try:
                    t = parse_t(ts)
                except ValueError:
                    continue
                msg = d.get("message") or {}
                cont = msg.get("content")
                if ty == "assistant":
                    if prev is not None and t > prev:
                        out.append((prev, min(t, prev + LLM_CAP_S), "llm", "model"))
                    for c in cont if isinstance(cont, list) else []:
                        if isinstance(c, dict) and c.get("type") == "tool_use":
                            pend[c.get("id")] = (t, c.get("name", ""), c.get("input") or {})
                    prev = t
                    continue
                results = [b for b in cont if isinstance(b, dict) and b.get("type") == "tool_result"] \
                    if isinstance(cont, list) else []
                if results:
                    for b in results:
                        if b.get("tool_use_id") not in pend:
                            continue
                        start, name, inp = pend.pop(b["tool_use_id"])
                        bucket, kind = _tool_bucket(name, inp, t - start, journal)
                        if any(m in _text(b.get("content")) for m in DENY_MARKS):
                            bucket, kind = "owner", "denied"
                            counts["denied"] += 1
                        if kind == "prompt":
                            counts["approval prompts"] += 1
                        out.append((start, t, bucket, kind))
                elif _human(d, cont):
                    if prev is not None and t > prev:
                        out.append((prev, t, "owner", "turn ended"))
                    counts["owner messages"] += 1
                prev = t
    return out, counts


def _tool_bucket(name, inp, secs, journal):
    if name == "AskUserQuestion":
        return "owner", "prompt"
    if name in ("Bash", "PowerShell"):
        if inp.get("run_in_background"):
            return "tools", "background start"
        k = bash_kind(inp.get("command", ""))
        if k == "tests":
            return "tests", "test call"
        if k == "compile":
            return ("tools" if journal else "bare"), "compile call"
        if k == "poll":
            return "idle", "poll"
        return "tools", name
    if name in FILE_TOOLS and secs > PROMPT_S:
        return "owner", "prompt"
    return "tools", name


# ---- other sources ------------------------------------------------------------------------------------------------
def testcost_intervals(path):
    """testcost runs.jsonl rows -> tests; a re-run of a check on a tree that already passed it -> rework."""
    out, passed = [], set()
    for r in _jsonl(path):
        try:
            t, s = parse_t(r["t"]), float(r.get("seconds") or 0)
        except (KeyError, ValueError):
            continue
        key = (r.get("check"), r.get("key"))
        if r.get("status") == "skipped" or s <= 0:
            continue
        redo = bool(r.get("key")) and key in passed
        out.append((t - s, t, "rework" if redo else "tests", f"testcost {r.get('check')}"))
        if r.get("status") == "pass" and r.get("key"):
            passed.add(key)
    return out


def inbox_intervals(folder, match, now=None):
    """Open time of inbox items whose title / why / command names the job."""
    out = []
    pat = [m.lower() for m in match if m]
    for p in sorted(Path(folder).glob("*.json")) if folder and Path(folder).is_dir() else []:
        it = _rj(p) or {}
        hay = " ".join(str(it.get(k, "")) for k in ("title", "why", "command")).lower()
        if not pat or not any(m in hay for m in pat) or not it.get("added"):
            continue
        try:
            a = parse_t(it["added"])
            b = parse_t(it["done"]) if it.get("done") else (now or time.time())
        except ValueError:
            continue
        out.append((a, max(a, b), "owner", f"inbox #{it.get('n')}"))
    return out


# ---- the sweep ----------------------------------------------------------------------------------------------------
def sweep(intervals, t0, t1):
    """Seconds per bucket: every second of [t0, t1] in exactly one bucket, the first in BUCKETS that covers it."""
    rank = {b: i for i, b in enumerate(BUCKETS)}
    pts = []
    for a, b, bucket, _ in intervals:
        a, b = max(a, t0), min(b, t1)
        if b > a:
            pts.append((a, 1, rank[bucket]))
            pts.append((b, -1, rank[bucket]))
    pts.sort()
    secs = dict.fromkeys(BUCKETS, 0.0)
    active = [0] * len(BUCKETS)
    cur = t0
    for t, delta, r in pts:
        if t > cur:
            top = next((i for i, n in enumerate(active) if n > 0), rank["idle"])
            secs[BUCKETS[top]] += t - cur
            cur = t
        active[r] += delta
    if t1 > cur:
        secs["idle"] += t1 - cur
    return secs


# ---- jobs ---------------------------------------------------------------------------------------------------------
def state_dir(state=None):
    return Path(state or os.environ.get("FIELDKIT_OVERHEAD_STATE") or _state_root(ROOT) / "state")


def _segments(events):
    segs, cur = [], []
    for e in events:
        if cur and e["_t"] - cur[-1]["_t"] > SILENCE_S:
            segs.append(cur)
            cur = []
        cur.append(e)
    return segs + ([cur] if cur else [])


def build_jobs(state=None):
    """{name: {"kind": "build", "journal": path, "events": [...]}} one per journal segment."""
    jobs = {}
    root = state_dir(state) / "build-harness"
    for j in sorted(root.glob("*/journal.jsonl")) if root.is_dir() else []:
        evs = []
        for e in _jsonl(j):
            try:
                e["_t"] = parse_t(e["t"])
            except (KeyError, ValueError):
                continue
            evs.append(e)
        for i, seg in enumerate(_segments(evs)):
            name = j.parent.name + (f" #{i + 1}" if i else "")
            jobs[name] = {"kind": "build", "task": j.parent.name, "journal": j, "events": seg, "all": evs}
    return jobs


def goal_jobs(state=None, goals=None):
    jobs = {}
    root = Path(goals) if goals else state_dir(state) / "goals" if state or not os.environ.get("FIELDKIT_GOALS") \
        else Path(os.environ["FIELDKIT_GOALS"])
    for d in sorted(root.iterdir()) if root.is_dir() else []:
        if (d / "goal.json").is_file():
            jobs[f"goal:{d.name}"] = {"kind": "goal", "dir": d, "gid": d.name}
    return jobs


def _goal_parts(job, now):
    d = job["dir"]
    st = _rj(d / "state.json") or {}
    goal = _rj(d / "goal.json") or {}
    lines = []
    for raw in (d / "events.log").read_text(encoding="utf-8", errors="replace").splitlines() \
            if (d / "events.log").is_file() else []:
        try:
            lines.append((parse_t(raw[:19]), raw[19:].strip()))
        except ValueError:
            continue
    if not lines:
        return None
    t0 = lines[0][0]
    done = st.get("status") not in (None, "active", "paused")
    t1 = parse_t(st["finished"]) if done and st.get("finished") else (lines[-1][0] if done else now)
    iv, sessions = [], []
    created = next((t for t, x in lines if x.startswith("goal created")), None)
    approved = next((t for t, x in lines if "pre-approvals approved" in x), None)
    if created is not None and approved is not None and approved > created:
        iv.append((created, approved, "owner", "pre-approvals"))
    for cp in sorted(d.glob("jobs/*/CHECKPOINT.json")):
        c = _rj(cp) or {}
        if c.get("session_id"):
            sessions.append(c["session_id"])
        prev_end = ""
        for a in c.get("attempts") or []:
            try:
                end, secs = parse_t(a["at"]), float(a.get("seconds") or 0)
            except (KeyError, ValueError):
                continue
            if str(prev_end).startswith("failed"):
                iv.append((end - secs, end, "rework", f"job {cp.parent.name} retry"))
            for sid in [a.get("session_id")] if a.get("session_id") else []:
                sessions.append(sid)
            prev_end = a.get("end", "")
    for v in _jsonl(d / "verdicts.jsonl"):
        try:
            end, secs = parse_t(v["at"]), float(v.get("seconds") or 0)
        except (KeyError, ValueError):
            continue
        if secs > 0:
            iv.append((end - secs, end, "tests", f"verify {v.get('id')}"))
    bare = goal.get("bare_minutes")
    return {"t0": t0, "t1": max(t0, t1), "intervals": iv, "sessions": list(dict.fromkeys(sessions)),
            "bare": float(bare) * 60 if bare else None, "bare_parts": {"goal.json bare_minutes": float(bare) * 60}
            if bare else {}, "runs": Counter()}


def measure(name, job, sessions=(), since=None, until=None, bare_min=None, cfg=None, state=None, now=None,
            transcripts=True, projects=None, inbox_dir=None, testcost_log=None):
    """One job -> a result dict (see report_line). Read-only."""
    cfg = cfg or config()
    now = now or time.time()
    notes = []
    if job["kind"] == "build":
        evs = job["events"]
        t0, t1 = evs[0]["_t"], evs[-1]["_t"]
        lg = job["journal"].parent.parent / "leakgate" / job["task"]
        runs = journal_runs(evs) + [r for r in leakgate_runs(lg, job["all"]) if t0 <= r["start"] <= t1]
        t0, t1 = min([t0] + [r["start"] for r in runs]), max([t1] + [r["end"] for r in runs])
        runs.sort(key=lambda r: r["start"])
        iv, kept = classify_runs(runs)
        bare_parts = {s: kept[s]["end"] - kept[s]["start"] for s in BARE_STAGES if s in kept}
        bare = sum(bare_parts.values()) or None
        run_counts = Counter(r["stage"] for r in runs if r["stage"] != "owner-approve")
        match = [job["task"]]
        journal = True
    else:
        g = _goal_parts(job, now)
        if g is None:
            return None
        t0, t1, iv, bare, bare_parts, run_counts = g["t0"], g["t1"], g["intervals"], g["bare"], g["bare_parts"], g["runs"]
        sessions = list(sessions) + g["sessions"]
        match = [job["gid"]]
        journal = False
    if since:
        t0 = max(t0, parse_t(since))
    if until:
        t1 = min(t1, parse_t(until))
    base_cfg = cfg["baselines"].get(name) or cfg["baselines"].get(job.get("task") or "")
    if bare_min is not None:
        bare, bare_parts = float(bare_min) * 60, {"--bare": float(bare_min) * 60}
    elif base_cfg and not bare:
        bare, bare_parts = float(base_cfg) * 60, {"overhead.baselines": float(base_cfg) * 60}
    sessions = list(dict.fromkeys(list(sessions) + list(cfg["sessions"].get(name) or cfg["sessions"].get(
        job.get("task") or "") or [])))
    counts = Counter()
    if transcripts and sessions:
        files = transcript_files(sessions, projects)
        if not files:
            notes.append(f"no transcript found for session(s) {', '.join(sessions)}")
        tv, counts = transcript_intervals(files, journal=journal)
        iv += tv
    elif not sessions:
        notes.append("no session transcript given: model and agent time are not split out (they count as idle)")
    st = state_dir(state)
    iv += testcost_intervals(testcost_log or st / "testcost" / "runs.jsonl")
    if inbox_dir is None:
        from .autopilot import hardlines
        inbox_dir = hardlines.STATE / "inbox"
    if job.get("kind") != "goal":
        # 2026-10-27: a goal never waits on an inbox item (it waits only on its own questions and the pre-approvals,
        # both read from its events); an open item that merely names it said "waiting-for-owner 12 h" for a goal that
        # never paused once (the 9 h were a hung go test)
        iv += inbox_intervals(inbox_dir, match, now)
    secs = sweep(iv, t0, t1)
    wall = max(0.0, t1 - t0)
    ratio = wall / bare if bare else None
    level = "NOBASE" if ratio is None else "WARN" if ratio > cfg["max_ratio"] else "OK"
    rework = Counter()
    for a, b, bucket, stage in iv:
        if bucket == "rework":
            rework[stage] += max(0.0, min(b, t1) - max(a, t0))
    return {"job": name, "kind": job["kind"], "start": show_t(t0), "end": show_t(t1), "wall_s": round(wall),
            "bare_s": round(bare) if bare else None, "bare_parts": {k: round(v) for k, v in bare_parts.items()},
            "ratio": round(ratio, 2) if ratio else None, "max_ratio": cfg["max_ratio"], "level": level,
            "buckets": {k: round(v) for k, v in secs.items()}, "rework": {k: round(v) for k, v in rework.items()},
            "runs": dict(run_counts), "counts": dict(counts), "sessions": sessions, "notes": notes}


def report_line(r):
    """`JOB: bare 1 h 30 min (...), took 6 h 10 min = 4.1x WARN (max 1.5x); waiting-for-owner 2 h 40 min, ...`"""
    if r["bare_s"]:
        parts = ", ".join(f"{k} {fmt(v)}" for k, v in r["bare_parts"].items())
        head = (f"{r['job']}: bare {fmt(r['bare_s'])} ({parts}), took {fmt(r['wall_s'])} = {r['ratio']:.1f}x "
                f"{r['level']}" + (f" (max {r['max_ratio']:g}x)" if r["level"] == "WARN" else ""))
    else:
        head = f"{r['job']}: bare unknown (no stage log), took {fmt(r['wall_s'])} NOBASE"
    split = [f"{LABEL[b]} {fmt(s)}" for b, s in sorted(r["buckets"].items(), key=lambda kv: -kv[1])
             if b != "bare" and s >= 30]
    return head + ("; " + ", ".join(split) if split else "")


def detail_lines(r):
    out = [report_line(r), f"  window {r['start']} -> {r['end']}"]
    if r["runs"]:
        out.append("  stage runs: " + ", ".join(f"{k} x{v}" for k, v in sorted(r["runs"].items())))
    if r["rework"]:
        out.append("  rework: " + ", ".join(f"{k} {fmt(v)}" for k, v in sorted(r["rework"].items(), key=lambda kv: -kv[1])))
    if r["counts"]:
        out.append("  counted: " + ", ".join(f"{k} {v}" for k, v in sorted(r["counts"].items())))
    if r["sessions"]:
        out.append("  sessions: " + ", ".join(s[:8] for s in r["sessions"]))
    out += [f"  note: {n}" for n in r["notes"]]
    return out


def all_jobs(state=None, goals=None):
    return {**build_jobs(state), **goal_jobs(state, goals)}


def measure_all(state=None, cfg=None, transcripts=False, goals=None, **kw):
    """Every known job; transcripts off by default (the ratio and the WARN do not depend on them)."""
    out = []
    for name, job in all_jobs(state, goals).items():
        r = measure(name, job, cfg=cfg, state=state, transcripts=transcripts, **kw)
        if r:
            out.append(r)
    return out


def goal_line(gid, goals=None, state=None, cfg=None):
    """One line for goal status / the goal report; never raises (a broken meter never stops a goal)."""
    try:
        jobs = goal_jobs(state, goals)
        r = measure(f"goal:{gid}", jobs[f"goal:{gid}"], cfg=cfg, state=state) if f"goal:{gid}" in jobs else None
        return ("OVERHEAD " + report_line(r)) if r else ""
    except Exception as e:  # noqa: BLE001
        return f"OVERHEAD: not measured ({type(e).__name__}: {e})"


def board_line(state=None, cfg=None):
    """For the zero-net Board: {"level": OK|WARN, "line": ..., "jobs": [...]}; WARN when any job is over budget."""
    rs = measure_all(state=state, cfg=cfg)
    over = sorted((r for r in rs if r["level"] == "WARN"), key=lambda r: -r["ratio"])
    cfg = cfg or config()
    if over:
        line = (f"OVERHEAD WARN: {len(over)} of {len(rs)} job(s) over {cfg['max_ratio']:g}x the bare build; worst "
                f"{over[0]['job']} {over[0]['ratio']:.1f}x ({fmt(over[0]['wall_s'])} for {fmt(over[0]['bare_s'])})")
    else:
        line = f"OVERHEAD OK: {len(rs)} job(s), none over {cfg['max_ratio']:g}x the bare build"
    return {"level": "WARN" if over else "OK", "line": line,
            "jobs": [{"job": r["job"], "level": r["level"], "ratio": r["ratio"]} for r in rs]}


def baselines(state=None):
    """[(what, seconds, source)] the bare times the stage logs give."""
    st = state_dir(state)
    out = []
    for name, job in build_jobs(state).items():
        if "#" in name:
            continue
        r = measure(name, job, cfg={"max_ratio": MAX_RATIO, "baselines": {}, "sessions": {}}, state=state,
                    transcripts=False, inbox_dir="")
        if r and r["bare_s"]:
            out.append((f"{name} ({', '.join(f'{k} {fmt(v)}' for k, v in r['bare_parts'].items())})", r["bare_s"],
                        str(job["journal"])))
    rep = _rj(st / "gorilla-opencode" / "last-report.json")
    if rep and rep.get("stages"):
        s = sum(float(x.get("seconds") or 0) for x in rep["stages"])
        out.append((f"gorilla-opencode release pipeline ({rep.get('started', '?')})", s,
                    str(st / "gorilla-opencode" / "last-report.json")))
    hist = {}
    for r in _jsonl(st / "eta" / "history.jsonl"):
        if r.get("step") and r.get("seconds"):
            hist.setdefault(r["step"], []).append(float(r["seconds"]))
    for step, xs in sorted(hist.items()):
        if re.search(r"build|package|compile|kernel", step):
            out.append((f"{step} (median of {len(xs)})", statistics.median(xs), str(st / "eta" / "history.jsonl")))
    if not any("kernel" in w for w, _, _ in out):
        out.append(("kernel compile + package", None, "not measured on this machine (the Linux box keeps its logs)"))
    return out


def main(argv=None, prog="fieldkit overhead"):
    ap = argparse.ArgumentParser(prog=prog, description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("job", nargs="?", help="a build-harness task (firefox-158.0, 'firefox-158.0 #2'), goal:<id>, "
                                           "or 'baselines'")
    ap.add_argument("--session", action="append", default=[], help="a Claude Code session id (prefix) of this job")
    ap.add_argument("--since")
    ap.add_argument("--until")
    ap.add_argument("--bare", type=float, help="the bare minutes, when no stage log gives them")
    ap.add_argument("--state", help="Fieldkit state folder (default: this checkout's state/)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    cfg = config()
    if a.job == "baselines":
        bs = baselines(a.state)
        if a.json:
            print(json.dumps([{"what": w, "seconds": s, "source": src} for w, s, src in bs], indent=1))
        else:
            for w, s, src in bs:
                print(f"  {fmt(s) if s is not None else 'unknown':>12}  {w}  [{src}]")
        return 0
    if a.job:
        jobs = all_jobs(a.state)
        if a.job not in jobs:
            print(f"overhead: no job {a.job!r}; known: {', '.join(jobs) or 'none'}", file=sys.stderr)
            return 2
        try:
            r = measure(a.job, jobs[a.job], a.session, a.since, a.until, a.bare, cfg, a.state)
        except ValueError as e:
            print(f"overhead: {e}", file=sys.stderr)
            return 2
        if r is None:
            print(f"overhead: {a.job} has no events yet", file=sys.stderr)
            return 2
        print(json.dumps(r, indent=1) if a.json else "\n".join(detail_lines(r)))
        return 1 if r["level"] == "WARN" else 0
    rs = measure_all(a.state, cfg)
    if a.json:
        print(json.dumps(rs, indent=1))
    else:
        print("\n".join(report_line(r) for r in rs) or "overhead: no jobs found")
    return 1 if any(r["level"] == "WARN" for r in rs) else 0


if __name__ == "__main__":
    sys.exit(main())
