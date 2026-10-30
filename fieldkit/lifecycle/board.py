"""Every finished session leaves its Board row in state/coordination/PLAN.md (plan R3: 58 of 75 sessions, $703, had
no row with their id on 2026-10-28). A check, not a hope; plain code, no model.

    fieldkit board check [--date YYYY-MM-DD] [--json]   sessions with a row / all, for the day (default today);
                                                        each one without a row; exit 3 when any
    fieldkit board add SESSION [SESSION ...]            the row, from the session's own transcript (title, id,
    fieldkit board add --missing [--date ...]           branch, PRs, cost); every session of the day without one

A session HAS a row when a table line of PLAN.md names its id's first 8 characters, or the autopilot job id or task id
its first message carries ("job 20261028-191309-claude", "T-20261028-191309-77ff9e"): `fieldkit task report` and the
listener write those. Finished: a Claude Code transcript (this computer) with at least one call, quiet for
idle_minutes (lifecycle config, default 30). The other computer: `fieldkit friction` asks it over the cable (its
`board` rows), `fieldkit sessions sweep` names the finished desktop sessions here without one.

The row added says it came from the transcript: the session's own report (what was done, what was NOT verified) is
not invented. A missing PLAN.md is an error, never a new file.
"""
import argparse
import json
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path

KEY_RES = (re.compile(r"\bT-\d{8}-\d{6}-[0-9a-f]{6}\b"), re.compile(r"\bjob (\d{8}-\d{6}-[a-z]+)\b"))


def plan_file():
    from ..tokens.rotate import coordination_dir
    return coordination_dir() / "PLAN.md"


def _cell(s, n=None):
    s = " ".join(str(s or "-").split()).replace("|", "/")
    return s if n is None or len(s) <= n else s[: n - 3].rstrip() + "..."


def first_message(path):
    """The whole first message a person or a brief typed (rotate.measure keeps 2,000 characters; the job and task
    ids sit at a brief's end)."""
    from ..tokens.rotate import _user_text
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if isinstance(d, dict) and d.get("type") == "user" and not d.get("isMeta") \
                        and not d.get("isSidechain"):
                    t = _user_text(d)
                    if t:
                        return t
    except OSError:
        pass
    return ""


def keys(facts):
    """-> the strings a Board row may name this session by: its id's first 8 characters, its job and task ids."""
    out = [facts["session"][:8]]
    if "first_message" not in facts:
        facts["first_message"] = first_message(facts["path"]) if facts.get("path") else ""
    text = facts.get("first_message") or facts.get("task") or ""
    for rx in KEY_RES:
        for m in rx.finditer(text):
            out.append(m.group(m.lastindex or 0))
    return list(dict.fromkeys(out))


def rows_of(plan):
    """-> every table line of PLAN.md (a missing plan raises: it is never made)."""
    return [ln for ln in Path(plan).read_text(encoding="utf-8", errors="replace").splitlines() if ln.startswith("|")]


def has_row(facts, table):
    ks = keys(facts)
    return any(k in ln for ln in table for k in ks)


def _ts(v):
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def finished(since, until=None, now=None, idle_minutes=None, adapters=None, measure=None):
    """-> facts (rotate.measure) of every Claude Code session here with a call in [since, until) and quiet for
    idle_minutes. since/until: epoch seconds."""
    from ..tokens import rotate, session_cost as sc
    from . import sweep
    now = now or time.time()
    idle = sweep.config()["idle_minutes"] if idle_minutes is None else idle_minutes
    measure = measure or rotate.measure
    out = []
    for a in adapters if adapters is not None else [sc.ClaudeCode()]:
        for p in a.sessions():
            try:
                if p.stat().st_mtime < since:
                    continue
            except OSError:
                continue
            f = measure(a, p)
            last = _ts(f.get("last"))
            if not f.get("calls") or last is None or last < since or (until and last >= until):
                continue
            if now - last < idle * 60:
                continue
            out.append(f)
    return out


def day_window(day=None):
    d = day or date.today()
    start = datetime.combine(d, datetime.min.time()).timestamp()
    return start, start + 86400


def check(since, until=None, plan=None, **kw):
    """-> {"with": n, "all": n, "missing": [facts], "line": "..."}."""
    plan = plan or plan_file()
    table = rows_of(plan)
    done = finished(since, until, **kw)
    missing = [f for f in done if not has_row(f, table)]
    n, m = len(done), len(done) - len(missing)
    return {"with": m, "all": n, "missing": missing, "plan": str(plan),
            "line": f"BOARD {m}/{n} finished session(s) have a row in PLAN.md" + (f"; {len(missing)} without" if
                                                                                  missing else "")}


def row(facts, tag=None):
    """The Board row, from the transcript only (6 cells, the shape of the handover tables)."""
    from ..peers import machine
    title = machine.prefix(_title(facts) or "untitled", tag)
    prs = facts.get("prs") or []
    where = f"branch {facts.get('branch') or '?'}" + (f", PR {' '.join(prs[-3:])}" if prs else ", no PR named")
    return (f"| {_cell(title, 110)} (session {facts['session'][:8]}) | ENDED, no report of its own: row by "
            f"`fieldkit board add` from the transcript | {_cell(where)} | - | everything: this row is not the "
            f"session's report | ${facts.get('usd', 0):.2f}, {facts.get('calls', 0)} calls, last {_cell(facts.get('last'))}"
            f", {_cell(facts.get('cwd'))} |")


def _first_line(text):
    """A brief's "# " heading (its title), else its first line that is not a banner."""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    for ln in lines:
        if ln.startswith("# "):
            return ln[2:].strip()
    return next((ln for ln in lines if not ln.startswith("---")), "")


def _title(f):
    return f.get("title") or _first_line(f.get("first_message") or f.get("task"))


def add(facts_list, plan=None, tag=None):
    """Append a row for each session the Board lacks. -> the session ids written. A missing plan raises."""
    plan = Path(plan or plan_file())
    table = rows_of(plan)
    new = [f for f in facts_list if not has_row(f, table)]
    if new:
        text = plan.read_text(encoding="utf-8")
        with open(plan, "a", encoding="utf-8") as fh:
            fh.write(("" if text.endswith("\n") else "\n") + "".join(row(f, tag) + "\n" for f in new))
    return [f["session"] for f in new]


def ensure(session, plan=None):
    """The handovers' call: the session's row, when it has none. -> "written" | "present" | "no plan" | "unknown"."""
    from ..tokens import rotate, session_cost as sc
    a, p = sc.resolve(str(session), [sc.ClaudeCode()])
    if a is None:
        return "unknown"
    try:
        return "written" if add([rotate.measure(a, p)], plan) else "present"
    except OSError:
        return "no plan"


def friction_rows(now=None, hours=24, **kw):
    """One friction row per session that ended in the last `hours` with no Board row (fieldkit friction, both
    computers: the link verb `friction` answers with these too)."""
    now = now or time.time()
    try:
        r = check(now - hours * 3600, now=now, **kw)
    except OSError:
        return []                                     # no PLAN.md on this computer: no Board to keep
    return [{"kind": "board", "key": f"board:{f['session']}",
             "line": f"session \"{_cell(_title(f), 60)}\" ({f['session'][:8]}) "
                     f"ended with no Board row in PLAN.md",
             "fix": f"fieldkit board add {f['session']}"} for f in r["missing"]]


def main(argv=None):
    ap = argparse.ArgumentParser(prog="fieldkit board", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["check", "add"])
    ap.add_argument("sessions", nargs="*", help="add: session ids (or their first characters) or transcript paths")
    ap.add_argument("--missing", action="store_true", help="add: every finished session of the day without a row")
    ap.add_argument("--date", help="the day, YYYY-MM-DD (default today)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        since, until = day_window(date.fromisoformat(a.date) if a.date else None)
    except ValueError:
        print(f"bad --date {a.date}: YYYY-MM-DD")
        return 2
    plan = plan_file()
    if not plan.is_file():
        print(f"no Board: {plan} does not exist (it is never made here)\nNEXT: run in the Fieldkit checkout that "
              "holds state/coordination/PLAN.md")
        return 1
    if a.action == "check":
        r = check(since, until, plan)
        if a.json:
            print(json.dumps({**r, "missing": [{k: f.get(k) for k in ("session", "title", "usd", "calls", "last",
                                                                        "branch", "path")} for f in r["missing"]]},
                             indent=1, ensure_ascii=False))
        else:
            for f in r["missing"]:
                print(f"NO ROW  {f['session'][:8]}  ${f.get('usd', 0):>7.2f}  "
                      f"{_cell(_title(f), 70)}")
            print(r["line"])
            print("NEXT: " + ("fieldkit board add --missing" + (f" --date {a.date}" if a.date else "")
                              if r["missing"] else "nothing: every finished session has its row"))
        return 3 if r["missing"] else 0
    if a.missing:
        todo = check(since, until, plan)["missing"]
    elif a.sessions:
        from ..tokens import rotate, session_cost as sc
        todo = []
        for s in a.sessions:
            ad, p = sc.resolve(s, [sc.ClaudeCode()])
            if ad is None:
                print(f"no transcript for {s}")
                return 2
            todo.append(rotate.measure(ad, p))
    else:
        print("board add SESSION [SESSION ...] | --missing")
        return 2
    written = add(todo, plan)
    print(json.dumps({"written": written}) if a.json else
          f"BOARD rows written: {len(written)} ({' '.join(s[:8] for s in written) or 'none needed'}) in {plan}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
