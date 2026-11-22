# Version: 1.0.0 · updated 26-11-22-18-00
"""CI hangs: a test that hangs costs one bounded timeout, one automatic re-run, then quarantine (CI-SPEED-PLAN C6).

    fieldkit ci quarantine [--check] [--file F]     list the quarantined tests; --check exits 3 while any is listed

A hung test used to hold a runner until the job timeout (30 minutes). Now:
  1. pytest-timeout kills a test after `timeout_for` seconds: factor x the p90 of the recorded file times, between
     `floor` and `cap` (.github/ci.yaml `hang:`); no durations yet = `default`.
  2. ci_tests.py finds the hung test in the shard log (`hung_node`) and re-runs the shard once. A pass is green and the
     hang is printed as `HANG-FLAKE`.
  3. The same test hanging twice is `HANG-QUARANTINE`: the run is red and names the line to add to
     .github/quarantine.yaml. A listed test is deselected, so main stays unblocked, and `ci quarantine --check` (a
     release gate) fails while the list is not empty: nothing ships on a skipped hang (owner decision O-CI3).
"""
import argparse
import math
import re
import sys
from pathlib import Path

QUARANTINE = ".github/quarantine.yaml"
DEFAULT_HANG = {"factor": 10, "floor": 120, "cap": 900, "default": 300}
_NODE = re.compile(r"^(?P<node>[\w./\\-]+\.py::\S+)(?P<rest>.*)$")
_RESULT = re.compile(r"\b(PASSED|FAILED|SKIPPED|XFAIL|XPASS|ERROR)\b")
_MARK = re.compile(r"\+{5,} Timeout \+{5,}|Failed: Timeout >|Timeout \(>\d")


def timeout_for(weights, hang=None):
    """-> seconds one test may run. `weights` = {file: seconds} (the shard's durations); empty = the default."""
    h = {**DEFAULT_HANG, **(hang or {})}
    vals = sorted(float(v) for v in (weights or {}).values() if v and v > 0)
    if not vals:
        return int(h["default"])
    p90 = vals[min(len(vals) - 1, math.ceil(0.9 * len(vals)) - 1)]
    return int(min(max(h["factor"] * p90, h["floor"]), h["cap"]))


_CRASH = re.compile(r"worker 'gw\d+' crashed while running '(?P<node>[^']+)'")


def hung_node(log):
    """-> the node id of the test pytest-timeout killed, from a -v log, or None when the log shows no timeout.
    The hung test is the last one that started and never printed a result."""
    if not _MARK.search(log):
        return None
    crashed = _CRASH.findall(log)            # xdist: the thread timeout ends the worker, xdist names its test
    if crashed:
        return crashed[-1].replace("\\", "/")
    last = None
    for line in log.splitlines():
        m = _NODE.match(line.strip())
        if not m:
            continue
        if _RESULT.search(m.group("rest")):
            last = None
        else:
            last = m.group("node").replace("\\", "/")
    return last


def other_failures(log):
    """True when the log has a failure that is not a timeout (a real red must not be hidden by a re-run)."""
    for line in log.splitlines():
        if re.search(r"\bFAILED\b|\bERROR\b", line) and "Timeout" not in line and _NODE.match(line.strip()):
            return True
    return False


def load_quarantine(path=QUARANTINE):
    """-> {node id: why}. A missing file is an empty list; a malformed one fails loud (never silently skips tests)."""
    p = Path(path)
    if not p.exists():
        return {}
    import yaml

    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    tests = data.get("tests") or {}
    if not isinstance(tests, dict) or any(not isinstance(v, str) or not v.strip() for v in tests.values()):
        raise ValueError(f"{path}: `tests:` must map node id -> why it hangs")
    return {str(k): v for k, v in tests.items()}


def deselect_args(quarantined, selected):
    """-> pytest `--deselect` arguments for the quarantined tests that belong to a selected file."""
    files = {s.replace("\\", "/") for s in selected} if selected else None
    args = []
    for nid in sorted(quarantined):
        if files is None or nid.split("::", 1)[0] in files:
            args += ["--deselect", nid]
    return args


def quarantine_line(node, run=""):
    return f'  "{node}": "hung twice in CI{f" (run {run})" if run else ""}: find the cause, then remove this line"'


def main(argv=None):
    ap = argparse.ArgumentParser(prog="fieldkit ci quarantine", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--file", default=QUARANTINE)
    a = ap.parse_args(argv)
    try:
        q = load_quarantine(a.file)
    except (ValueError, OSError) as exc:
        print(f"ci: QUARANTINE UNREADABLE: {exc}")
        return 3
    for nid, why in sorted(q.items()):
        print(f"ci: quarantined {nid}: {why}")
    if a.check and q:
        print(f"ci: {len(q)} test(s) quarantined: a release is blocked until each cause is fixed and its line removed")
        return 3
    print(f"ci: quarantine {'empty' if not q else str(len(q)) + ' test(s)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
