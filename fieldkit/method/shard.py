# Version: 1.0.0 · updated 26-11-12-06-30
"""CI shards (plan P9 item 7; owner, 2026-11-12: "we can use their huge power of processing").

The selected test files are split into N parallel jobs per OS, balanced by recorded durations (longest first, each
file to the lightest shard: deterministic, the same split on every run). A file with no record counts the median.

    python -m fieldkit.method.shard split I/N FILE...        the files shard I (1-based) of N runs, one per line
    python -m fieldkit.method.shard record JUNIT.xml [--out F]   per-file seconds from a pytest --junitxml report
                                                                 (merged into F, .github/test_durations.json)

N is `batch.shards.default` in fieldkit/method/batch.yaml (4); the workflow's shard list must match it
(tests/test_shard.py compares them). A shard with no file runs nothing and is green; the per-OS status `test (OS)`
is green only when every shard of that OS is green (the aggregator job in .github/workflows/tests.yml).
"""
import argparse
import json
import statistics
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

DURATIONS = ".github/test_durations.json"


def load(path=DURATIONS):
    p = Path(path)
    try:
        d = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
    except ValueError:
        d = {}
    return {str(k).replace("\\", "/"): float(v) for k, v in d.items() if isinstance(v, (int, float))}


def balance(tests, durations, n):
    """-> n lists, the files split by longest-processing-time-first. Deterministic: ties break by name."""
    n = max(1, int(n))
    tests = sorted({t.replace("\\", "/") for t in tests})
    known = [durations[t] for t in tests if t in durations]
    guess = statistics.median(known) if known else 1.0
    weight = {t: durations.get(t, guess) for t in tests}
    shards, load = [[] for _ in range(n)], [0.0] * n
    for t in sorted(tests, key=lambda t: (-weight[t], t)):
        i = min(range(n), key=lambda k: (load[k], k))
        shards[i].append(t)
        load[i] += weight[t]
    return [sorted(s) for s in shards]


def pick(tests, durations, spec):
    """spec "I/N" -> the files shard I of N runs."""
    i, n = (int(x) for x in str(spec).split("/"))
    if not 1 <= i <= n:
        raise ValueError(f"shard {spec}: I must be 1..N")
    return balance(tests, durations, n)[i - 1]


def spread(shards, durations):
    """-> (heaviest, lightest) shard seconds: what the balance achieved."""
    guess = statistics.median(durations.values()) if durations else 1.0
    w = [sum(durations.get(t, guess) for t in s) for s in shards]
    return max(w), min(w)


def from_junit(xml_path):
    """-> {test file: seconds} from a pytest junit report (a testcase's classname maps back to its file)."""
    out = {}
    for case in ET.parse(xml_path).getroot().iter("testcase"):
        f = case.get("file")
        if not f:                                       # classname = module path, then maybe a class: stop at the test_*.py
            parts = case.get("classname", "").split(".")
            i = next((k for k, x in enumerate(parts) if x.startswith("test_") or x == "tests"), len(parts) - 1)
            i = next((k for k, x in enumerate(parts) if k >= i and x.startswith("test_")), i)
            f = "/".join(parts[:i + 1]) + ".py"
        out[f] = out.get(f, 0.0) + float(case.get("time") or 0)
    return {k: round(v, 3) for k, v in out.items()}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m fieldkit.method.shard", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("verb", choices=["split", "record"])
    ap.add_argument("spec", help="split: I/N; record: the junit xml")
    ap.add_argument("files", nargs="*")
    ap.add_argument("--durations", default=DURATIONS)
    ap.add_argument("--out", default=DURATIONS)
    a = ap.parse_args(argv)
    if a.verb == "split":
        print("\n".join(pick(a.files, load(a.durations), a.spec)))
        return 0
    merged = load(a.out)
    merged.update(from_junit(a.spec))
    Path(a.out).write_text(json.dumps(dict(sorted(merged.items())), indent=1) + "\n", encoding="utf-8", newline="\n")
    print(f"{a.out}: {len(merged)} test files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
