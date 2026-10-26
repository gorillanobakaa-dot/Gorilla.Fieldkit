"""Finished and idle agent sessions with what they hold, for the command post to archive on every watchdog wake.

    finished   no open PR (merged, closed or none) and idle >= idle_minutes: ARCHIVE
    idle       an open PR (waiting on CI / the merge) and idle >= idle_minutes: reported, kept until the PR closes
    held       working now (CPU over the busy line), the parent of a live session, exempt, or named with --keep

Measured, not guessed: RAM is the resident set of the session's claude CLI and every child it has (test runners,
hooks, servers it started), CPU is over one interval, from the same processes (hog's data, psutil). Evidence 2026-10-26
18:4x: the laptop ran 20 claude.exe and 15 python.exe at 94 % CPU, many from idle sessions.

Archiving is the desktop app's own verb (archive_session); this module only lists. A session with Remote Control on
cannot be archived until it is turned off (2026-10-26: the owner archived one by hand), so its fix says so.
"""
import os
import time

from . import desktop

DEFAULTS = {"idle_minutes": 30, "busy_cpu_pct": 5.0, "hog_warn_gb": 1.0}


def config():
    cfg = dict(DEFAULTS)
    try:
        from ..core import settings
        cfg.update({k: v for k, v in (settings.local_settings().get("lifecycle") or {}).items() if k in DEFAULTS})
    except Exception:  # noqa: BLE001 - a broken local file must not switch the sweep off: the defaults hold
        pass
    return cfg


def command_post(net_file=None):
    """-> the ids Zero and its 2IC go by in NET.yaml (fieldkit zero): host session ids and 8-character session
    prefixes. The command post is never listed for archive, however long it waits."""
    import re
    from pathlib import Path
    try:
        if net_file is None:
            from ..tokens.rotate import coordination_dir
            net_file = coordination_dir() / "NET.yaml"
        import yaml
        net = yaml.safe_load(Path(net_file).read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001 - no net: nothing to hold
        return set()
    if net.get("net") != "open":
        return set()
    out = set()
    for role in ("zero", "2ic"):
        r = net.get(role) or {}
        out |= {str(r[k]) for k in ("host_session", "session") if r.get(k)}
        m = re.match(r"claude:(\w{8})@", str(r.get("station") or ""))
        if m:
            out.add(m[1])
    return out


def classify(sessions, procs, now=None, cfg=None, me=None, keep=(), zero=None):
    """-> rows, one per live (not archived) session but `me`: {id, title, state, why, idle_min, rss, cpu_pct, prs,
    remote, fix}. state: finished | idle | held | active."""
    cfg = cfg or config()
    now = now or time.time()
    zero = command_post() if zero is None else zero
    live = [s for s in sessions if not s["archived"]]
    parents = {s["parent"] for s in live if s["parent"]}
    rows = []
    for s in live:
        if s["id"] == me:
            continue
        p = procs.get(s["id"]) or {}
        idle = int((now - s["last"]) / 60) if s["last"] else None
        open_ = [x["url"] for x in s["prs"] if x["state"] == "OPEN"]
        busy = (p.get("cpu_pct") or 0) >= cfg["busy_cpu_pct"]
        quiet = idle is not None and idle >= cfg["idle_minutes"]
        if s["id"] in keep or s["exempt"]:
            state, why = "held", "kept by --keep" if s["id"] in keep else "exempt from auto-archive (the owner's mark)"
        elif s["id"] in zero or s["cli"] in zero or (s["cli"] and s["cli"][:8] in zero):
            state, why = "held", "the command post (Zero or its 2IC in NET.yaml)"
        elif s["id"] in parents:
            state, why = "held", "started sessions that are still live"
        elif busy:
            state, why = "held", f"working now ({p['cpu_pct']:.0f}% CPU)"
        elif not quiet:
            state, why = "active", f"active {idle if idle is not None else '?'} min ago"
        elif open_:
            state, why = "idle", f"idle {idle} min, open PR {', '.join(open_)}"
        else:
            closed = [f"{x['state'].lower()} {x['url']}" for x in s["prs"] if x["state"]]
            state, why = "finished", f"idle {idle} min, " + (", ".join(closed) if closed else "no PR")
        fix = ""
        if state == "finished":
            fix = (f"set_remote_control off, then archive_session {s['id']}" if s["remote"]
                   else f"archive_session {s['id']}")
        elif state == "idle":
            fix = "keep until its PR merges (auto-archive on PR close), or ask it why it is idle"
        rows.append({"id": s["id"], "title": s["title"], "state": state, "why": why, "idle_min": idle,
                     "rss": p.get("rss", 0), "cpu_pct": p.get("cpu_pct"), "pids": p.get("pids", []),
                     "prs": s["prs"], "remote": s["remote"], "fix": fix})
    order = {"finished": 0, "idle": 1, "held": 2, "active": 3}
    return sorted(rows, key=lambda r: (order[r["state"]], -r["rss"]))


def sweep(interval=1.0, now=None, cfg=None, me=None, keep=(), dirs=None, procs=None, zero=None):
    me = me if me is not None else os.environ.get(desktop.HOST_ENV, "")
    procs = desktop.processes(interval) if procs is None else procs
    return classify(desktop.sessions(dirs), procs, now, cfg, me, keep, zero)


def totals(rows):
    fin = [r for r in rows if r["state"] == "finished"]
    idle = [r for r in rows if r["state"] == "idle"]
    return {"finished": len(fin), "idle": len(idle), "rss": sum(r["rss"] for r in fin + idle),
            "rss_finished": sum(r["rss"] for r in fin)}


def summary(rows):
    t = totals(rows)
    if not (t["finished"] or t["idle"]):
        return f"lifecycle: no finished or idle agent sessions ({len(rows)} live)"
    return (f"lifecycle: idle agent sessions holding {t['rss'] / 2**30:.1f} GB "
            f"({t['finished']} finished: archive; {t['idle']} idle with an open PR)")


def hog_findings(cfg=None, rows=None):
    """The hog's row (fieldkit hog check calls this on its live pass): no CPU interval, so it costs no wait."""
    from ..hog import rules
    cfg = cfg or config()
    rows = sweep(interval=0.0, cfg=cfg) if rows is None else rows
    t = totals(rows)
    if not (t["finished"] or t["idle"]):
        return []
    sev = "warn" if t["finished"] and t["rss"] >= cfg["hog_warn_gb"] * 2**30 else "ok"
    return [rules._f("sessions", sev, summary(rows) + ": fieldkit sessions sweep",
                     {"gb": round(t["rss"] / 2**30, 2), "finished": t["finished"], "idle": t["idle"]})]
