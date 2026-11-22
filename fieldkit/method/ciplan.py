# Version: 1.0.0 · updated 26-11-22-12-00
"""CI durations: measure every test on every main run, then balance on real numbers (CI-SPEED-PLAN C1).

    fieldkit ci durations merge --os OS [--prev F] --out F JUNIT.xml...   per-test seconds, merged into F
    fieldkit ci durations fetch --out F                                   newest `ci-durations` artifact of main (gh)
    fieldkit ci plan --explain --selection F      which durations the split uses (the block ci_tests.py --durations writes)

The file is `{"schema": 1, "run": ID, "updated": ISO, "complete": bool, "tests": {OS: {node id: seconds}}}`.
It is an artifact (`ci-durations`, 90 days), never committed: main moves only by pull request.
`complete` means one run of the whole suite fed it (the weekly full run). Until then the split is degraded, loudly:
balanced by file count. After that a file older than 7 days or covering under 90% of the selected test files fails
the selection closed. A test with no record weighs the p90, so a new test never sits unnoticed in a light shard.
"""
import argparse
import datetime
import json
import math
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

SCHEMA = 1
MAX_AGE_DAYS = 7
MIN_COVERAGE = 0.90
ARTIFACT = "ci-durations"
OSES = ("windows", "linux")


def os_key(platform=None):
    p = platform or sys.platform
    return "windows" if p.startswith("win") else "linux"


def node_id(case):
    """pytest node id of a junit testcase (xunit1: `file`, classname = dotted module + class)."""
    f = (case.get("file") or "").replace("\\", "/")
    if not f:
        return ""
    module = f[:-3].replace("/", ".") if f.endswith(".py") else f
    cls = case.get("classname", "")
    rest = cls[len(module) + 1:] if cls.startswith(module + ".") else ""
    return f + "::" + (rest.replace(".", "::") + "::" if rest else "") + case.get("name", "")


def from_junit(path):
    """-> {node id: seconds}; a skipped test has no duration worth keeping."""
    out = {}
    for case in ET.parse(path).getroot().iter("testcase"):
        nid = node_id(case)
        if not nid or case.find("skipped") is not None:
            continue
        try:
            t = float(case.get("time") or 0)
        except ValueError:
            continue
        if math.isfinite(t) and t >= 0:
            out[nid] = round(out.get(nid, 0.0) + t, 3)
    return out


def empty():
    return {"schema": SCHEMA, "run": None, "updated": None, "complete": False, "tests": {o: {} for o in OSES}}


def read(path):
    """-> the durations dict, or None when the file is missing. A present but unreadable file raises ValueError."""
    p = Path(path) if path else None
    if not p or not p.is_file():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"durations file unreadable: {exc}")
    if not isinstance(d, dict) or d.get("schema") != SCHEMA or not isinstance(d.get("tests"), dict):
        raise ValueError("durations file has the wrong shape")
    for o, tests in d["tests"].items():
        if not isinstance(tests, dict) or any(not isinstance(v, (int, float)) for v in tests.values()):
            raise ValueError(f"durations for {o} are not numbers")
    return d


def merge(prev, os_name, new, run, complete, now=None):
    """-> prev with `new` ({node id: s}) laid over this OS. A whole-suite run replaces the OS's tests (gone tests drop)."""
    d = prev or empty()
    d["tests"].setdefault(os_name, {})
    d["tests"][os_name] = dict(new) if complete else {**d["tests"][os_name], **new}
    d["run"] = run
    d["updated"] = (now or datetime.datetime.now(datetime.timezone.utc)).isoformat(timespec="seconds")
    d["complete"] = bool(d.get("complete") or complete)
    return d


def per_file(tests):
    out = {}
    for nid, s in tests.items():
        f = nid.split("::", 1)[0]
        out[f] = out.get(f, 0.0) + s
    return out


def p90(values):
    v = sorted(values)
    return v[min(len(v) - 1, math.ceil(0.9 * len(v)) - 1)] if v else 1.0


def age_days(d, now=None):
    if not d.get("updated"):
        return None
    then = datetime.datetime.fromisoformat(d["updated"])
    return ((now or datetime.datetime.now(datetime.timezone.utc)) - then).total_seconds() / 86400


def block(d, selected, new_files=(), now=None):
    """-> (block, error). `selected` = the selected test files (None = the whole suite, passed as a list by the caller).
    The block rides in the selection JSON so every shard splits the same way. error is a string: fail closed."""
    if d is None:
        return {"run": None, "degraded": "no durations artifact yet: shards balanced by file count", "weights": {}}, None
    if not d.get("complete"):
        return {"run": d.get("run"), "degraded": "durations artifact has no whole-suite run yet: balanced by file count",
                "weights": {}}, None
    age = age_days(d, now)
    if age is None or age > MAX_AGE_DAYS:
        return None, (f"durations are {'undated' if age is None else f'{age:.1f} days old'} (limit {MAX_AGE_DAYS}): "
                      "run tests.yml on main (Run workflow) to refresh them")
    rated = [f for f in selected if f not in set(new_files)]
    weights, coverage = {}, {}
    for o in OSES:
        files = per_file(d["tests"].get(o, {}))
        guess = p90(files.values())
        weights[o] = {f: round(files.get(f, guess), 3) for f in selected}
        coverage[o] = round(sum(1 for f in rated if f in files) / len(rated), 4) if rated else 1.0
        if coverage[o] < MIN_COVERAGE:
            missing = [f for f in rated if f not in files][:5]
            return None, (f"durations for {o} cover {coverage[o]:.0%} of the selected test files (limit "
                          f"{MIN_COVERAGE:.0%}); no record: {', '.join(missing)}")
    return {"run": d.get("run"), "age_days": round(age, 2), "coverage": coverage, "weights": weights}, None


def explain(b):
    if not b:
        return "durations: none recorded in this selection"
    if b.get("degraded"):
        return f"durations: DEGRADED - {b['degraded']}"
    cov = min(b["coverage"].values()) if b.get("coverage") else 0
    return f"durations: run {b['run']}, coverage {cov:.0%}"


def fetch(out, repo=None):
    """Download the newest `ci-durations` artifact of main with gh; writes OUT. False when there is none yet."""
    base = ["gh", "api", f"repos/{repo or '{owner}/{repo}'}/actions/artifacts?name={ARTIFACT}&per_page=20"]
    items = json.loads(subprocess.check_output(base, text=True)).get("artifacts", [])
    for item in items:
        run = item.get("workflow_run") or {}
        if item.get("expired") or run.get("head_branch") != "main":
            continue
        with tempfile.TemporaryDirectory(prefix="fieldkit-durations-") as tmp:
            subprocess.check_call(["gh", "run", "download", str(run["id"]), "-n", ARTIFACT, "-D", tmp])
            src = next(Path(tmp).rglob("durations.json"), None)
            if src:
                Path(out).write_text(src.read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
                return True
    return False


def main(argv=None):
    ap = argparse.ArgumentParser(prog="fieldkit ci", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    dur = sub.add_parser("durations")
    dur.add_argument("verb", choices=["merge", "fetch"])
    dur.add_argument("junit", nargs="*")
    dur.add_argument("--os", default=os_key())
    dur.add_argument("--prev")
    dur.add_argument("--out")
    dur.add_argument("--run", default=None)
    dur.add_argument("--complete", action="store_true")
    dur.add_argument("--repo", default=None)
    plan = sub.add_parser("plan")
    plan.add_argument("--explain", action="store_true")
    plan.add_argument("--selection")
    a = ap.parse_args(argv)
    if a.cmd == "plan":
        if not a.selection:
            print("ci plan --explain needs --selection FILE (the selection JSON CI wrote)")
            return 2
        data = json.loads(Path(a.selection).read_text(encoding="utf-8"))
        print("ci:", explain(data.get("durations")))
        return 0
    if a.verb == "fetch":
        got = fetch(a.out, a.repo)
        print(f"ci: {'fetched ' + ARTIFACT if got else 'no ' + ARTIFACT + ' artifact on main yet'}")
        return 0
    if a.verb == "merge":
        tests = {}
        for path in a.junit:
            for k, v in from_junit(path).items():
                tests[k] = round(tests.get(k, 0.0) + v, 3) if k in tests else v
        d = merge(read(a.prev), a.os, tests, a.run, a.complete)
        Path(a.out).write_text(json.dumps(d, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        print(f"ci: {a.out}: {sum(len(t) for t in d['tests'].values())} tests, run {d['run']}, complete={d['complete']}")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
