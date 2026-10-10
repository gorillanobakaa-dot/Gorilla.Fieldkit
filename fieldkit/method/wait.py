"""Wait on a condition, never on a clock (docs/METHODS.md #10).

    fieldkit wait --until "git log -1 --format=%s" --matches "^Add" [--timeout 600] [--every 5]
    fieldkit wait --until "test -f build/done"                       (exit code 0 is the condition)
    fieldkit wait --until-file PATH [--timeout 600]

A fixed sleep is either too short (the next step runs too early and fails in a way that looks real) or too long
(time wasted on every run). This polls the thing itself until it is true, with a deadline, and says how long it
took and what the last check printed. Exit 0 when the condition held, 3 at the deadline, 2 for bad input.
"""
import argparse
import re
import subprocess
import time
from pathlib import Path

from . import emit, split_command


def check(argv, matches=None, expect_exit=0, timeout=60):
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)
    out = (r.stdout + r.stderr).strip()
    ok = (r.returncode == expect_exit) if matches is None else bool(re.search(matches, out, re.M))
    return ok, out


def wait(argv=None, path=None, matches=None, expect_exit=0, timeout=600.0, every=5.0, clock=time.monotonic,
         sleep=time.sleep):
    start = clock()
    tries, last = 0, ""
    while True:
        tries += 1
        if path is not None:
            ok, last = Path(path).exists(), f"{path} {'exists' if Path(path).exists() else 'does not exist'}"
        else:
            ok, last = check(argv, matches, expect_exit)
        waited = clock() - start
        if ok:
            return {"ok": True, "tries": tries, "seconds": round(waited, 1), "last": last[-400:]}
        if waited + every > timeout:
            return {"ok": False, "tries": tries, "seconds": round(waited, 1), "last": last[-400:],
                    "next": "the condition never held: read the last output; raise --timeout only if it is "
                            "still making progress"}
        sleep(every)


def main(argv=None, prog="fieldkit wait"):
    ap = argparse.ArgumentParser(prog=prog, description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--until", help="the command to check, as one string")
    g.add_argument("--until-file", help="wait until this path exists")
    ap.add_argument("--matches", help="regex the command's output must match (instead of its exit code)")
    ap.add_argument("--exit", type=int, default=0, dest="expect_exit", help="the exit code that means done (0)")
    ap.add_argument("--timeout", type=float, default=600)
    ap.add_argument("--every", type=float, default=5)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.every <= 0 or a.timeout <= 0:
        print("REFUSED: --every and --timeout must be above 0")
        return 2
    r = wait(split_command(a.until) if a.until else None, a.until_file, a.matches, a.expect_exit, a.timeout, a.every)
    emit(r, a.json, lambda d: [("DONE" if d["ok"] else "TIMEOUT") + f" after {d['seconds']}s, {d['tries']} check(s)",
                               f"last: {d['last'][-200:]}"])
    return 0 if r["ok"] else 3
