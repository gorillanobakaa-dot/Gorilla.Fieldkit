"""The temperature journal: the CPU temperature on disk every 30 s, kept 7 days, so a crash has a record.

Crash 2026-10-27 ~04:14: the laptop lost power under load and NO temperature record existed for 03:30-04:15 (the hog's
samples carried none, the guard printed to its own output only). Owner, verbatim: "jurnal de temperatura".

    fieldkit thermal journal            loop forever: one line every 30 s to state/thermal/journal-YYYY-MM-DD.csv

A line: ISO time, CPU C, the fan control's peak C, load %, fan level, rpm, mode (Gorilla TPFanControl's status file).
Each line is flushed and fsynced, so the last line before a power cut is on disk. Day files older than 7 days are
removed (the journal's own files only, by their name pattern).
"""
import os
import re
import time
from pathlib import Path

INTERVAL_S = 30
KEEP_DAYS = 7
NAME = re.compile(r"^journal-(\d{4}-\d{2}-\d{2})\.csv$")
HEADER = "time,cpu_c,max_c,load_pct,fan,rpm,mode\n"


def folder():
    from .. import settings
    return Path(settings.ROOT) / "state" / "thermal"


def line(s, now=None):
    """status dict (buildguard.read_status) -> one CSV line; a missing status is a line that says so."""
    t = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now))
    if not s:
        return f"{t},,,,,,no-status\n"
    return f"{t},{s.get('t', s.get('cpu', ''))},{s.get('max', '')},{s.get('load', '')},{s.get('fan', '')}," \
           f"{s.get('rpm', '')},{s.get('mode', '')}\n"


def write(d, text, now=None):
    p = Path(d) / f"journal-{time.strftime('%Y-%m-%d', time.localtime(now))}.csv"
    p.parent.mkdir(parents=True, exist_ok=True)
    new = not p.exists()
    with open(p, "a", encoding="ascii", newline="") as f:
        if new:
            f.write(HEADER)
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    return p


def prune(d, now=None, keep_days=KEEP_DAYS):
    """Remove the journal's own day files older than keep_days. -> the names removed."""
    cut = time.strftime("%Y-%m-%d", time.localtime((now or time.time()) - keep_days * 86400))
    gone = []
    for p in Path(d).glob("journal-*.csv"):
        m = NAME.match(p.name)
        if m and m.group(1) < cut:
            p.unlink()
            gone.append(p.name)
    return gone


def run(d=None, interval=INTERVAL_S, read=None, sleep=time.sleep, ticks=None):
    from .buildguard import read_status
    d, read = d or folder(), read or read_status
    n = 0
    while ticks is None or n < ticks:
        write(d, line(read()))
        if n % 120 == 0:
            prune(d)
        n += 1
        if ticks is None or n < ticks:
            sleep(interval)
    return n
