# Version: 1.0.0 · updated 26-10-28-18-35
"""Owner actions per job, counted (HARNESS-PLAN I9, target 0): `fieldkit friction` lists what COULD wait on a person;
this counts what DID, job by job.

    fieldkit friction jobs [--since YYYY-MM-DD] [--json]   one line per job with its count and each action's cause;
                                                            WARN when more than 0; exit 3 when any job has one

A job is a link job (~/.fieldkit/link/jobs/*, a `fieldkit task start --on` that landed here), an autopilot job with a
CHECKPOINT.json (state/jobs, state/goals/*/jobs: `fieldkit goal`, runbooks, `fieldkit task start` here) or an emerge
run (state/emerge/*/journal.jsonl, build-start to build-done/stop). What counts, from the job's own Claude Code
transcripts (the same JSONL under ~/.claude on Linux and %USERPROFILE%\\.claude on Windows; the session is the
checkpoint's session_id, or any transcript whose first prompt carries "Harness (job ID)"):

    permission   a tool call the owner rejected ("doesn't want to proceed") or one that stopped for a grant ("haven't
                 granted it yet"). A prompt the owner APPROVED leaves no record in the transcript: not counted
    trust        a workspace trust prompt in the job's session.log
    ask          AskUserQuestion or ExitPlanMode: the session waited on the owner
    question     a turn that ended on a question mark (the owner rule: never end a turn on a question)
    inbox        the job ran `fieldkit inbox add` (an emerge run: an inbox item of kind build for its recipe in its window)
    needs-you    the job sent a [NEEDS YOU] note over the cable (`peers link send --needs-you` or the tag in the text)

Read-only, offline, no model. This computer's jobs only: a job that ran on the other computer is counted there.
"""
import json
import re
import shlex
import time
from pathlib import Path

from . import desktop

JOB_MARK = re.compile(r"Harness \(job (?P<id>[\w.-]+)\)")
TASK_MARK = re.compile(r"fieldkit-task (?P<id>T-\d{8}-\d{6}-\w+)")
REJECTED_AT = "The user doesn't want to proceed with this tool use"
NOT_GRANTED = re.compile(r"Claude requested permissions to use (?P<tool>[\w.-]+), but you haven't granted it yet")
TRUST = re.compile(r"trust (this|the files in this) folder|Do you trust", re.I)
NEEDS_YOU_TAG = re.compile(r"^\s*\[\s*NEEDS YOU\s*\]", re.I)
ASK_TOOLS = desktop.WAITING_TOOLS
HEAD_BYTES = 200_000


def _since(date):
    """'YYYY-MM-DD' -> epoch at local midnight; None -> today's midnight."""
    return time.mktime(time.strptime(date or time.strftime("%Y-%m-%d"), "%Y-%m-%d"))


def _read(p):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _stamp(s):
    try:
        return time.mktime(time.strptime(str(s), "%Y-%m-%d %H:%M:%S"))
    except ValueError:
        return None


def _state():
    from ..core import settings
    return settings.state_root() / "state"


def _link_jobs_dir():
    try:
        from ..peers import link
        return link.home() / "jobs"
    except Exception:  # noqa: BLE001 - no link on this computer: no link jobs
        return None


def _job(d, kind):
    cp = _read(d / "CHECKPOINT.json")
    b = _read(d / "budget.json")
    start = b.get("started") or (d.stat().st_mtime if d.exists() else 0)
    times = [_stamp(a.get("at")) for a in cp.get("attempts") or []]
    end = cp.get("ended_at") or max([t for t in times if t] or [0]) or None
    return {"id": cp.get("job") or d.name, "kind": kind, "dir": str(d), "start": start, "end": end,
            "status": cp.get("status") or ("running" if not cp else "?"), "sessions": [cp["session_id"]]
            if cp.get("session_id") else [], "task": ""}


def checkpoint_jobs(link_dir=None, state=None, since=0):
    """-> [job] for every link job and every autopilot job dir that started at or after `since`."""
    out = []
    link_dir = _link_jobs_dir() if link_dir is None else Path(link_dir)
    if link_dir and link_dir.is_dir():
        out += [_job(d, "link") for d in sorted(link_dir.iterdir()) if d.is_dir()]
    state = _state() if state is None else Path(state)
    for pat, kind in (("jobs/**/CHECKPOINT.json", "autopilot"), ("goals/*/jobs/*/CHECKPOINT.json", "goal"),
                      ("lifecycle/tasks/**/CHECKPOINT.json", "task")):
        out += [_job(p.parent, kind) for p in sorted(state.glob(pat))]
    return [j for j in out if (j["start"] or 0) >= since]


def emerge_runs(state=None, since=0):
    """-> [job] per emerge run: build-start to the next build-done or build-stop of the same recipe."""
    state = _state() if state is None else Path(state)
    out = []
    for jf in sorted(state.glob("emerge/*/journal.jsonl")):
        cur = None
        for line in jf.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            t = e.get("t")
            t = t if isinstance(t, (int, float)) else _stamp(t) if t else None
            if e.get("event") == "build-start":
                cur = {"id": f"emerge:{e.get('recipe') or jf.parent.name}@{time.strftime('%Y%m%d-%H%M%S', time.localtime(t or 0))}",
                       "kind": "emerge", "recipe": e.get("recipe") or jf.parent.name, "dir": str(jf.parent),
                       "start": t or 0, "end": None, "status": "running", "sessions": [], "task": ""}
                out.append(cur)
            elif cur and e.get("event") in ("build-done", "build-stop"):
                cur.update(end=t, status=e.get("event")[6:])
                cur = None
    return [j for j in out if j["start"] >= since]


def find_transcripts(job_ids, since, env=None):
    """-> {job id: [transcript paths]}: transcripts touched since `since` whose first prompt names "Harness (job ID)"."""
    want, out = set(job_ids), {}
    for c in desktop.config_dirs(env):
        for p in c.glob("projects/*/*.jsonl"):
            try:
                if p.stat().st_mtime < since:
                    continue
                with open(p, encoding="utf-8", errors="replace") as fh:
                    head = fh.read(HEAD_BYTES)
            except OSError:
                continue
            m = JOB_MARK.search(head)
            if m and m["id"] in want:
                out.setdefault(m["id"], []).append(p)
    return out


def _texts(content):
    if isinstance(content, str):
        return [content]
    out = []
    for b in content if isinstance(content, list) else []:
        if isinstance(b, dict):
            if b.get("type") == "text":
                out.append(str(b.get("text") or ""))
            elif b.get("type") == "tool_result":
                out += _texts(b.get("content"))
    return out


def _argv_after(cmd, *words):
    """-> the shell words after `fieldkit ... WORDS` in a Bash command (each `fieldkit` call in it), parsed as the shell
    would: a note that only MENTIONS --needs-you or "inbox add" in its text is not a call."""
    try:
        toks = shlex.split(cmd, posix=True)
    except ValueError:
        return []
    n, out = len(words), []
    for i, t in enumerate(toks):
        if t == "fieldkit" or t.endswith("/fieldkit") or t.endswith("\\fieldkit.exe"):
            if toks[i + 1:i + 1 + n] == list(words):
                rest = toks[i + 1 + n:]
                for j, r in enumerate(rest):          # this call ends at the next shell operator
                    if r in ("&&", "||", ";", "|"):
                        rest = rest[:j]
                        break
                out.append(rest)
    return out


def inbox_adds(cmd):
    """-> [title] for each `fieldkit inbox add TITLE ...` call in a Bash command (`--help` is not an item)."""
    return [next((r for r in rest if not r.startswith("-")), "")
            for rest in _argv_after(cmd, "inbox", "add") if not {"-h", "--help"} & set(rest)]


def needs_you_sends(cmd):
    """-> [text] for each `fieldkit peers link send` call that sets --needs-you or starts its note with the tag."""
    out = []
    for rest in _argv_after(cmd, "peers", "link", "send"):
        text = next((r for r in rest if not r.startswith("-")), "")
        if "--needs-you" in rest or NEEDS_YOU_TAG.search(text):
            out.append(text)
    return out


def _short(s, n=90):
    return " ".join(str(s).split())[:n]


def scan(path):
    """One pass over a transcript -> {"actions": [{cause, detail, at}], "task": the fieldkit task id it names}."""
    acts, task, msgs = [], "", {}
    try:
        fh = open(path, encoding="utf-8", errors="replace")
    except OSError:
        return {"actions": [], "task": ""}
    with fh:
        for line in fh:
            if '"assistant"' not in line and '"user"' not in line:
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get("isSidechain") or d.get("type") not in ("user", "assistant"):
                continue
            msg, at = d.get("message") or {}, desktop._ts(d)
            content = msg.get("content")
            if d["type"] == "user":
                if not task:
                    m = TASK_MARK.search(" ".join(_texts(content)))
                    task = m["id"] if m else ""
                for b in content if isinstance(content, list) else []:
                    if isinstance(b, dict) and b.get("type") == "tool_result":
                        text = " ".join(_texts(b.get("content"))).lstrip()[:300]
                        g = NOT_GRANTED.match(text)        # the result IS the refusal, not output that quotes it
                        if text.startswith(REJECTED_AT):
                            acts.append({"cause": "permission", "detail": "a tool call rejected at a prompt", "at": at})
                        elif g:
                            acts.append({"cause": "permission", "detail": f"{g['tool']} stopped for a grant", "at": at})
                continue
            mid = msg.get("id") or d.get("uuid")
            m = msgs.setdefault(mid, {"text": "", "stop": None, "at": at})
            m["stop"] = msg.get("stop_reason") or m["stop"]
            for b in content if isinstance(content, list) else []:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "text":
                    m["text"] += str(b.get("text") or "")
                elif b.get("type") == "tool_use":
                    name, inp = str(b.get("name") or "").rsplit("__", 1)[-1], b.get("input") or {}
                    if name in ASK_TOOLS:
                        acts.append({"cause": "ask", "detail": name, "at": at})
                    cmd = str(inp.get("command") or "") if name == "Bash" else ""
                    acts += [{"cause": "inbox", "detail": _short(t), "at": at} for t in inbox_adds(cmd)]
                    acts += [{"cause": "needs-you", "detail": _short(t), "at": at} for t in needs_you_sends(cmd)]
    for m in msgs.values():
        if m["stop"] == "end_turn" and m["text"].rstrip().endswith("?"):
            last = m["text"].rstrip().rsplit("\n", 1)[-1]
            acts.append({"cause": "question", "detail": _short(last), "at": m["at"]})
    return {"actions": sorted(acts, key=lambda a: a["at"] or 0), "task": task}


def trust_actions(job):
    try:
        text = (Path(job["dir"]) / "session.log").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return [{"cause": "trust", "detail": _short(m.group(0)), "at": None} for m in TRUST.finditer(text)]


def emerge_inbox(job, items):
    """Inbox items an emerge run added: kind build, its recipe in the command, added inside the run's window."""
    out = []
    for i in items:
        at = _stamp(i.get("added"))
        if (i.get("kind") == "build" and f"emerge run {job.get('recipe')}" in str(i.get("command") or "")
                and at is not None and at >= job["start"] and (job["end"] is None or at <= job["end"])):
            out.append({"cause": "inbox", "detail": f"#{i.get('n')} {_short(i.get('title'))}", "at": at})
    return out


def count(since=None, link_dir=None, state=None, env=None, inbox_items=None):
    """-> [job with "actions" and "count"], oldest first. `since`: 'YYYY-MM-DD' (default today)."""
    t0 = _since(since)
    jobs = checkpoint_jobs(link_dir, state, t0) + emerge_runs(state, t0)
    found = find_transcripts([j["id"] for j in jobs if j["kind"] != "emerge"], t0, env)
    if inbox_items is None:
        try:
            from ..autopilot import inbox
            inbox_items = inbox.items(None)
        except Exception:  # noqa: BLE001 - no inbox here: nothing to count from it
            inbox_items = []
    for j in jobs:
        acts = []
        if j["kind"] == "emerge":
            acts = emerge_inbox(j, inbox_items)
        else:
            paths = {str(p) for p in found.get(j["id"], [])}
            paths |= {str(p) for s in j["sessions"] for p in [desktop.transcript(s, env)] if p}
            for p in sorted(paths):
                r = scan(p)
                acts += r["actions"]
                j["task"] = j["task"] or r["task"]
            j["transcripts"] = sorted(paths)
            acts += trust_actions(j)
        j["actions"], j["count"] = acts, len(acts)
    return sorted(jobs, key=lambda j: j["start"] or 0)


def by_task(jobs):
    """-> {fieldkit task id: job} for the jobs whose prompt named a task."""
    return {j["task"]: j for j in jobs if j.get("task")}


def label(n):
    return f"owner actions {n}" + (" WARN" if n else "")


def lines(jobs):
    L = []
    for j in jobs:
        when = time.strftime("%m-%d %H:%M", time.localtime(j["start"])) if j["start"] else "-"
        seen = "" if j["kind"] == "emerge" or j.get("transcripts") else "  (no transcript here)"
        L.append(f"{'WARN' if j['count'] else 'OK':<5} {j['count']:>2}  {when}  {j['kind']:<9} {j['id']:<28} "
                 f"{j.get('task') or '-':<26} {j['status']}{seen}")
        L += [f"        {a['cause']:<10} {a['detail']}" for a in j["actions"]]
    n = sum(j["count"] for j in jobs)
    bad = sum(1 for j in jobs if j["count"])
    L.append(f"VERDICT  {n} owner action(s) in {bad} of {len(jobs)} job(s) (target 0)" if n else
             f"VERDICT  0 owner actions in {len(jobs)} job(s)")
    L.append("NEXT: " + ("each cause above is a path to close (a rule, the mandate, a script), then count again"
                         if n else "nothing: keep it at 0"))
    return L


def main(argv):
    import argparse
    ap = argparse.ArgumentParser(prog="fieldkit friction jobs", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", help="YYYY-MM-DD (default: today)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        jobs = count(a.since)
    except ValueError as e:
        print(f"friction jobs: {e}")
        return 2
    print(json.dumps(jobs, indent=1, ensure_ascii=False, default=str) if a.json else "\n".join(lines(jobs)))
    return 3 if any(j["count"] for j in jobs) else 0


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv[1:]))
