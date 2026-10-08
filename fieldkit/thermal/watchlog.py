"""The thermal watcher's log (Gorilla.firefox `working scripts/watch_thermals.py` -> state/thermal_watch.log), read
back: how hot did the CPU run during a build or a leak-gate run?

Each line is `HH:MM:SS   57.9 C   peak  57.9   load 100%   10 compiler(s)`: a time of day and no date, and the file
holds every day the watcher ever ran. The 2026-10-06 hourly sitrep of a release leak gate kept "the latest stretch
where the clock never goes backwards", which drops everything before midnight in a run that crosses it. Here the
dates are put back from the end: the last line was written on the day of the file's modification time, each step
back in time-of-day order across a line boundary is a midnight, and a gap longer than `max_gap` (the watcher was
not running, for an unknown number of days) ends what can be dated; lines before it are not used.
"""
import datetime as dt
import re
import statistics
from pathlib import Path

LINE = re.compile(r"^(\d\d):(\d\d):(\d\d)\s+([\d.]+) C\s+peak\s+[\d.]+\s+load\s+(\d+)%")


def read(path, max_gap=dt.timedelta(hours=2), end=None):
    """-> [(datetime, celsius, load %)] oldest first, only the stretch that can be dated (see the module docstring).
    `end`: the date-time of the last line (default: the file's modification time)."""
    p = Path(path)
    if not p.is_file():
        return []
    rows = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        m = LINE.match(line)
        if m:
            rows.append((dt.time(int(m.group(1)), int(m.group(2)), int(m.group(3))), float(m.group(4)), int(m.group(5))))
    if not rows:
        return []
    end = end or dt.datetime.fromtimestamp(p.stat().st_mtime)
    day = end.date()
    if dt.datetime.combine(day, rows[-1][0]) > end + dt.timedelta(minutes=5):
        day -= dt.timedelta(days=1)                   # written just before midnight, file touched after it
    out = []
    later = None
    for t, c, load in reversed(rows):
        when = dt.datetime.combine(day, t)
        if later is not None and when > later:        # earlier in the file but later in the day: a midnight between
            day -= dt.timedelta(days=1)
            when = dt.datetime.combine(day, t)
        if later is not None and later - when > max_gap:
            break
        out.append((when, c, load))
        later = when
    return out[::-1]


def summary(rows, since=None, hot=70.0, recent=dt.timedelta(minutes=30), until=None):
    """-> {readings, first, last, min, mean, max, max_at, above, hot, recent: {mean, max, load}, now} or None.
    `recent` is the last stretch of the window; `now` its last reading."""
    rows = [r for r in rows if (since is None or r[0] >= since) and (until is None or r[0] <= until)]
    if not rows:
        return None
    temps = [r[1] for r in rows]
    peak = max(rows, key=lambda r: r[1])
    last = [r for r in rows if r[0] >= rows[-1][0] - recent]
    return {"readings": len(rows), "first": rows[0][0].isoformat(sep=" "), "last": rows[-1][0].isoformat(sep=" "),
            "min": min(temps), "mean": round(statistics.mean(temps), 1), "max": peak[1],
            "max_at": peak[0].isoformat(sep=" "), "hot": hot, "above": sum(1 for x in temps if x > hot),
            "recent": {"minutes": int(recent.total_seconds() // 60), "mean": round(statistics.mean(r[1] for r in last), 1),
                       "max": max(r[1] for r in last), "load": round(statistics.mean(r[2] for r in last))},
            "now": {"at": rows[-1][0].isoformat(sep=" "), "celsius": rows[-1][1], "load": rows[-1][2]}}


def lines(s, label="CPU"):
    if not s:
        return [f"{label}: no thermal watcher readings in that time (is watch_thermals.py running?)"]
    return [f"{label} {s['first']} .. {s['last']}: {s['readings']} readings, min {s['min']:.0f} C, mean {s['mean']} C, "
            f"max {s['max']:.0f} C (at {s['max_at'][11:]}); readings above {s['hot']:.0f} C: {s['above']}",
            f"{label} last ~{s['recent']['minutes']} min: mean {s['recent']['mean']} C, max {s['recent']['max']:.0f} C, "
            f"mean load {s['recent']['load']}%; now {s['now']['celsius']:.0f} C at {s['now']['at'][11:]}"]
