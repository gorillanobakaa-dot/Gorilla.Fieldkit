# Version: 1.0.0 · updated 26-11-12
"""IDLE REPORT (plan P9): the gaps where work was open and nothing acted.

    fieldkit idle report [--since 24h] [--min 30] [--json]

2026-11-12, 05:47 to 11:17: Zero ended its turn with jobs started; a PR went BEHIND, a job died, a job blocked, a PR
went red, and nobody acted for about 5.5 hours. This reads the same three clocks the owner would have to read by hand:

    work open   a task from its start to its report; a PR of a mandate branch from open to merged/closed (gh)
    acted       a room line of a station that is not a job's own worktree (Zero, the owner's session, the keeper)
    gap         a stretch of open work with no acting line for at least --min minutes

A job's own lines (`@wt-job-...`) are progress, not acting: they are what makes work to be decided. Read-only, no model.
"""
import json
import re
import sys
import time
from pathlib import Path

WORKER = re.compile(r"@wt-job-")


def _t(s):
    try:
        return time.mktime(time.strptime(str(s)[:19], "%Y-%m-%d %H:%M:%S"))
    except (TypeError, ValueError):
        return None


def parse_since(s, now):
    m = re.fullmatch(r"(\d+)([hdm])", (s or "24h").strip())
    if not m:
        raise ValueError(f"--since {s!r}: use 24h, 3d or 90m")
    return now - int(m.group(1)) * {"h": 3600, "d": 86400, "m": 60}[m.group(2)]


def acting_times(room_lines):
    """-> sorted epoch seconds of the room lines an acting station wrote (not a job's own worktree)."""
    out = []
    for ln in room_lines:
        f = ln.split("\t")
        if len(f) >= 4 and _t(f[0]) is not None and not WORKER.search(f[1]):
            out.append(_t(f[0]))
    return sorted(out)


def open_spans(tasks, prs, now):
    """-> [(start, end)] merged: every task and PR the way it was open."""
    spans = []
    for t in tasks:
        a = _t(t.get("at"))
        if a is not None:
            spans.append((a, _t(t.get("reported")) or now))
    for pr in prs:
        a = _t(pr.get("created") or pr.get("createdAt", "").replace("T", " ").rstrip("Z"))
        e = _t((pr.get("closed") or pr.get("mergedAt") or pr.get("closedAt") or "").replace("T", " ").rstrip("Z"))
        if a is not None:
            spans.append((a, e or now))
    spans.sort()
    merged = []
    for a, b in spans:
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return merged


def gaps(spans, acts, since, now, min_s=1800):
    """-> [{start, end, minutes}]: inside open work, between two acting lines (or the span's edge), >= min_s long."""
    out = []
    for a, b in spans:
        a, b = max(a, since), min(b, now)
        if b <= a:
            continue
        marks = [a] + [x for x in acts if a < x < b] + [b]
        for lo, hi in zip(marks, marks[1:]):
            if hi - lo >= min_s:
                out.append({"start": lo, "end": hi, "minutes": int((hi - lo) / 60)})
    return out


def lines(report):
    if not report["gaps"]:
        return [f"idle: no gap of {report['min']} min or more in the last {report['since']} with work open and nothing acting"]
    f = lambda e: time.strftime("%Y-%m-%d %H:%M", time.localtime(e))  # noqa: E731
    return [f"IDLE  {f(g['start'])} to {f(g['end'])}  {g['minutes'] // 60} h {g['minutes'] % 60:02d} min  work open, nothing acted"
            for g in report["gaps"]]


def report(room_lines, tasks, prs, since="24h", min_minutes=30, now=None):
    now = time.time() if now is None else now
    sec = parse_since(since, now)
    g = gaps(open_spans(tasks, prs, now), acting_times(room_lines), sec, now, min_minutes * 60)
    return {"since": since, "min": min_minutes, "gaps": g}


def _room_lines():
    from ..autopilot import room
    try:
        return Path(room._log()).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="fieldkit idle", description=__doc__.split("\n\n")[0])
    ap.add_argument("action", choices=["report"])
    ap.add_argument("--since", default="24h")
    ap.add_argument("--min", type=int, default=30, help="shortest gap to report, minutes")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(sys.argv[1:] if argv is None else argv)
    from . import tasks
    prs = []
    try:
        from ..method import train
        from ..core import settings
        g = train.GH(settings.state_root())
        prs = [x for x in g._json("pr", "list", "-R", g.slug, "--state", "all", "--limit", "60", "--json",
                                  "headRefName,createdAt,mergedAt,closedAt") if x["headRefName"].startswith(("feat/", "fix/", "release-", "night/"))]
    except Exception:  # noqa: BLE001 - without gh the tasks and the room still say most of it
        pass
    r = report(_room_lines(), list(tasks.ledger().values()), prs, a.since, a.min)
    print(json.dumps(r, indent=1) if a.json else "\n".join(lines(r)) + "\nNEXT: fieldkit keeper status")
    return 3 if r["gaps"] else 0


if __name__ == "__main__":
    sys.exit(main())
