"""Compare test failures by name, before and after a change (docs/METHODS.md #1).

    fieldkit regress record before -- python test_harness.py         (run, keep the names that passed and failed)
    ... make the change ...
    fieldkit regress record after  -- python test_harness.py
    fieldkit regress compare before after

Totals hide things: 665 passed / 11 failed before and after can still be a test fixed and another broken. This
compares failures BY NAME: NEW failures (the change broke them), FIXED (the change fixed them), STILL failing
(were broken already - not this change's fault), and tests that APPEARED or VANISHED (a test that stopped
running is not a pass).

Test names are read from the output of: pytest (-rA or -v), go test -v, unittest -v, and "[PASS] / [FAIL] name"
or "PASS: / FAIL: name" lines. Records live in state/regress/<label>.json.
Exit 0 when there is nothing new failing, 3 when there is, 2 for bad input.
"""
import argparse
import json
import re
import subprocess
import time

from ..core import settings
from . import emit

STORE = settings.ROOT / "state" / "regress"

PATTERNS = [
    # pytest -rA summary and -v lines
    (re.compile(r"^(PASSED|FAILED|ERROR)\s+(\S+::\S+)"), {"PASSED": "pass", "FAILED": "fail", "ERROR": "fail"}),
    (re.compile(r"^(\S+::\S+)\s+(PASSED|FAILED|ERROR)\b"), None),
    # go test -v
    (re.compile(r"^\s*--- (PASS|FAIL): (\S+)"), {"PASS": "pass", "FAIL": "fail"}),
    # unittest -v:  test_x (module.Class) ... ok / FAIL / ERROR
    (re.compile(r"^(\w+ \([\w.]+\)) \.\.\. (ok|FAIL|ERROR)\b"), None),
    # bracketed and colon styles
    (re.compile(r"^\s*\[(PASS|FAIL|OK|FAILED)\]\s+(.+?)\s*$"), {"PASS": "pass", "OK": "pass", "FAIL": "fail",
                                                                "FAILED": "fail"}),
    (re.compile(r"^\s*(PASS|FAIL):\s+(.+?)\s*$"), {"PASS": "pass", "FAIL": "fail"}),
]


def parse(output):
    """-> {test name: pass|fail}. The last word on a test wins (a rerun that passed counts as passed)."""
    res = {}
    for line in output.splitlines():
        for rx, words in PATTERNS:
            m = rx.match(line)
            if not m:
                continue
            a, b = m.group(1), m.group(2)
            if words is None:                                  # name first, verdict second
                name, verdict = a, {"PASSED": "pass", "ok": "pass"}.get(b, "fail")
            else:
                verdict, name = words[a], b
            res[name] = verdict
            break
    return res


def record(label, cmd, cwd=None, timeout=7200):
    t0 = time.time()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd, timeout=timeout)
        out, code = r.stdout + r.stderr, r.returncode
    except subprocess.TimeoutExpired:
        out, code = "", None
    tests = parse(out)
    rec = {"label": label, "command": cmd, "exit": code, "seconds": round(time.time() - t0, 1),
           "when": time.strftime("%Y-%m-%d %H:%M:%S"), "tests": tests,
           "passed": sum(v == "pass" for v in tests.values()), "failed": sum(v == "fail" for v in tests.values())}
    STORE.mkdir(parents=True, exist_ok=True)
    (STORE / f"{label}.json").write_text(json.dumps(rec, indent=1), encoding="utf-8")
    return rec


def load(label):
    p = STORE / f"{label}.json"
    if not p.is_file():
        raise FileNotFoundError(f"no record {label!r}: fieldkit regress record {label} -- COMMAND")
    return json.loads(p.read_text(encoding="utf-8"))


def compare(before, after):
    b, a = before["tests"], after["tests"]
    new = sorted(n for n, v in a.items() if v == "fail" and b.get(n) == "pass")
    fixed = sorted(n for n, v in a.items() if v == "pass" and b.get(n) == "fail")
    still = sorted(n for n, v in a.items() if v == "fail" and b.get(n) == "fail")
    appeared = sorted(n for n in a if n not in b)
    vanished = sorted(n for n in b if n not in a)
    new_failing_appeared = [n for n in appeared if a[n] == "fail"]
    ok = not new and not new_failing_appeared and bool(a)
    return {"ok": ok, "before": f"{before['passed']} passed / {before['failed']} failed",
            "after": f"{after['passed']} passed / {after['failed']} failed", "new_failures": new, "fixed": fixed,
            "still_failing": still, "appeared": appeared, "vanished": vanished,
            "next": ("no test names found in the output: is it a supported format?" if not a else
                     "fix the NEW failures: the change broke them" if new or new_failing_appeared else
                     "check why tests VANISHED: a test that stopped running is not a pass" if vanished else "")}


def _lines(d):
    out = [f"before: {d['before']}", f"after:  {d['after']}"]
    for key, tag in (("new_failures", "NEW FAIL"), ("fixed", "FIXED   "), ("still_failing", "STILL   "),
                     ("vanished", "VANISHED"), ("appeared", "APPEARED")):
        for n in d[key][:40]:
            out.append(f"  {tag} {n}")
    out.append("OK: nothing new failing" if d["ok"] else "the change made things fail")
    return out


def main(argv=None, prog="fieldkit regress"):
    argv = list(argv or [])
    test = []
    if "--" in argv:
        i = argv.index("--")
        argv, test = argv[:i], argv[i + 1:]
    ap = argparse.ArgumentParser(prog=prog, description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["record", "compare", "show"])
    ap.add_argument("labels", nargs="+")
    ap.add_argument("--cwd")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        if a.action == "record":
            if not test or len(a.labels) != 1:
                print("REFUSED: fieldkit regress record LABEL -- COMMAND")
                return 2
            r = record(a.labels[0], test, a.cwd)
            emit(r, a.json, lambda d: [f"{d['label']}: {d['passed']} passed, {d['failed']} failed "
                                       f"({len(d['tests'])} test names read; exit {d['exit']})"])
            return 0
        if a.action == "show":
            r = load(a.labels[0])
            emit(r, a.json, lambda d: [f"{n}: {v}" for n, v in sorted(d["tests"].items())])
            return 0
        if len(a.labels) != 2:
            print("REFUSED: fieldkit regress compare BEFORE AFTER")
            return 2
        r = compare(load(a.labels[0]), load(a.labels[1]))
        emit(r, a.json, _lines)
        return 0 if r["ok"] else 3
    except FileNotFoundError as e:
        print(f"REFUSED: {e}")
        return 2
