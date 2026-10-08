"""What a task's journal says happened, counted: builds, attempts, stops, hand edits, automation, installs, heat.

    fieldkit build-harness stats <task> [--since 2026-10-02] [--until 2026-10-04] [--json]

Born 2026-10-03: the 157 release notes and story needed the numbers of the port (how many builds, how many compile
attempts, why each one stopped, how much was ported by hand, how often an install failed its proof rows). A throwaway
script with the task, the path and the dates typed in counted them; every number in a public document has to come
from somewhere a reader can re-run, so this is that script, for any task. It only reads journal.jsonl.

A build run is everything from one `build-start` to the next. Its attempts: one per stop (a fix and a retry follow
each), plus one when it compiled or was verified; at least one. `--since` / `--until` take a date (2026-10-02) or a
time (2026-10-02 08:00); `--until` with a date includes that whole day.
"""
import collections
import json

from . import task


def journal(task_id):
    p = task.STATE / task_id / "journal.jsonl"
    if not p.is_file():
        raise task.Refused(f"no journal for task {task_id!r} ({p})")
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def window(events, since=None, until=None):
    """Events whose time `t` ("YYYY-mm-dd HH:MM:SS") is inside [since, until]; a bare date as `until` is its whole day."""
    return [e for e in events if (not since or e.get("t", "") >= since) and (not until or e.get("t", "")[:len(until)] <= until)]


def build_runs(events):
    """-> [{t, stops: [(signature, attempt, stage)], fixes, compiled, verified, interrupted, attempts}]"""
    runs, cur = [], None
    for e in events:
        ev = e["event"]
        if ev == "build-start":
            cur = {"t": e.get("t"), "stops": [], "fixes": [], "compiled": False, "verified": False, "interrupted": False}
            runs.append(cur)
        elif cur is None:
            continue
        elif ev == "build-stop":
            cur["stops"].append((e.get("signature"), e.get("attempt"), e.get("stage")))
        elif ev == "build-fix":
            cur["fixes"].append((e.get("signature"), e.get("what")))
        elif ev == "build-stage-done" and e.get("stage") == "build":
            cur["compiled"] = True
        elif ev == "build-verified":
            cur["verified"] = True
        elif ev == "interrupted":
            cur["interrupted"] = True
    for r in runs:
        r["attempts"] = max(1, len(r["stops"]) + (1 if r["compiled"] or r["verified"] else 0))
    return runs


def _failed_row(row):
    """A post-install result row: [name, rc] (rc 0 = pass) or {"name", "status"|"rc"}."""
    if isinstance(row, dict):
        return row.get("status", "ok") not in ("ok", "pass", "PASS") or row.get("rc") not in (0, None)
    return len(row) > 1 and row[1] not in (0, None)


def _number(v):
    try:
        float(v)
        return v is not None and not isinstance(v, bool)
    except (TypeError, ValueError):
        return False


def summarise(events):
    ev = collections.Counter(e["event"] for e in events)
    runs = build_runs(events)
    of = lambda name: [e for e in events if e["event"] == name]          # noqa: E731
    hand = of("hand-edit")
    pi = of("post_install")
    rows = [r for e in pi for r in e.get("results") or []]
    scripts = collections.Counter(e.get("step") for e in of("script-start"))
    failed = collections.Counter(e.get("step") for e in of("script-failed"))
    peaks = sorted((float(e["peak"]) for e in of("thermal") if _number(e.get("peak"))), reverse=True)
    return {
        "events": len(events), "first": events[0]["t"] if events else None, "last": events[-1]["t"] if events else None,
        "by_event": dict(ev.most_common()),
        "builds": {"runs": len(runs), "attempts": sum(r["attempts"] for r in runs),
                   "compiled": sum(r["compiled"] for r in runs), "verified": sum(r["verified"] for r in runs),
                   "interrupted": sum(r["interrupted"] for r in runs), "refused": ev["build-refused"],
                   "stops_by_signature": dict(collections.Counter(s[0] for r in runs for s in r["stops"]).most_common()),
                   "fixes": sum(len(r["fixes"]) for r in runs),
                   "list": [{"t": r["t"], "attempts": r["attempts"], "compiled": r["compiled"], "verified": r["verified"],
                             "interrupted": r["interrupted"], "stops": [s[0] for s in r["stops"]]} for r in runs]},
        "hand": {"edits": len(hand), "steps": sum(len(e.get("steps") or []) for e in hand),
                 "files": len({f for e in hand for f in e.get("files") or []}),
                 "kinds": dict(collections.Counter(e.get("kind") or "(none)" for e in hand).most_common()),
                 "owner_steps": ev["owner-step"], "needs_person": ev["needs-person"]},
        "automation": {"auto_done": ev["auto-done"], "auto_miss": ev["auto-miss"], "agent_runs": ev["agent-run"],
                       "submits": ev["submit"], "submits_ok": sum(1 for e in of("submit") if e.get("ok")),
                       "reverts": ev["revert"], "deferred": ev["deferred"], "obsolete": ev["obsolete"],
                       "relocated": ev["relocated"], "reopened": ev["reopened"], "regressions": ev["regression"]},
        "installs": {"installs": ev["install"], "ok": sum(1 for e in of("install") if e.get("ok")),
                     "post_install_runs": len(pi), "post_install_ok": sum(1 for e in pi if e.get("ok")),
                     "rows": len(rows), "row_failures": sum(1 for r in rows if _failed_row(r))},
        "thermal": {"events": len(of("thermal")), "peak": peaks[0] if peaks else None, "top5": peaks[:5]},
        "scripts": {s: {"runs": n, "failed": failed.get(s, 0)} for s, n in scripts.most_common()},
        "other": {"truthbound": [e.get("changed") for e in of("truthbound")], "netbench": ev["netbench"],
                  "leakgate": dict(collections.Counter(("release " if e.get("release") else "dev ") + str(e.get("final"))
                                                       for e in of("leakgate"))),
                  "migrate_parks": ev["migrate-park"]},
    }


def run(task_id, since=None, until=None):
    events = window(journal(task_id), since, until)
    return {"task": task_id, "since": since, "until": until, **summarise(events)}


def lines(s):
    b, h, a, i, th = s["builds"], s["hand"], s["automation"], s["installs"], s["thermal"]
    once = sum(1 for v in s["scripts"].values() if v["runs"] == 1 and not v["failed"])
    span = (f" from {s['since']}" if s["since"] else "") + (f" until {s['until']}" if s["until"] else "")
    out = [f"STATS {s['task']}{span}: {s['events']} journal events, {s['first']} .. {s['last']}",
           f"builds: {b['runs']} run(s), {b['attempts']} compile attempt(s), {b['compiled']} compiled, {b['verified']} verified, "
           f"{b['interrupted']} interrupted, {b['refused']} refused before starting",
           f"  stops by signature: {b['stops_by_signature'] or 'none'}"]
    for n, r in enumerate(b["list"], 1):
        out.append(f"  {n:3}. {r['t']}  attempts {r['attempts']}" + ("  compiled" if r["compiled"] else "")
                   + ("  verified" if r["verified"] else "") + ("  INTERRUPTED" if r["interrupted"] else "")
                   + (f"  stops {r['stops']}" if r["stops"] else ""))
    out += [f"hand edits: {h['edits']} event(s), {h['steps']} step(s), {h['files']} distinct file(s); kinds {h['kinds']}; "
            f"owner steps {h['owner_steps']}, needs a person {h['needs_person']}",
            f"automation: auto-done {a['auto_done']}, auto-miss {a['auto_miss']}, agent runs {a['agent_runs']}, submits "
            f"{a['submits']} ({a['submits_ok']} ok), reverts {a['reverts']}, deferred {a['deferred']}, obsolete {a['obsolete']}, "
            f"relocated {a['relocated']}, reopened {a['reopened']}, regressions {a['regressions']}",
            f"installs: {i['installs']} ({i['ok']} ok); post-install {i['post_install_runs']} run(s) ({i['post_install_ok']} ok), "
            f"{i['rows']} row(s), {i['row_failures']} row failure(s)",
            f"thermal: {th['events']} event(s), peak {th['peak']} C, top {th['top5']}",
            "scripts: " + (", ".join(f"{k} {v['runs']}" + (f" ({v['failed']} failed)" if v["failed"] else "")
                                     for k, v in s["scripts"].items() if v["runs"] > 1 or v["failed"]) or "none run twice")
            + (f"; {once} other step(s) ran once, none failed" if once else ""),
            f"other: truthbound {s['other']['truthbound']}, netbench {s['other']['netbench']}, leak gate "
            f"{s['other']['leakgate'] or 'none'}, migration parks {s['other']['migrate_parks']}"]
    return out
