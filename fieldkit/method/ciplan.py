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
import os
import subprocess
from fieldkit import proc as fkproc
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


DEFAULTS = {"target_seconds": 180, "setup_seconds": {"windows": 40, "linux": 20}, "budget": {"windows": 16, "linux": 16},
            "degraded_file_seconds": 10, "max_spread": 1.25, "keep_together": [],
            "hang": {"factor": 10, "floor": 120, "cap": 900, "default": 300},
            "slots": {"account_limit": 40, "reserve": 4,
                      "projects": {"fieldkit": {"windows": 16, "linux": 16}, "rest": {"windows": 2, "linux": 2}}}}


def load_config(path=".github/ci.yaml"):
    """-> the plan config: .github/ci.yaml over DEFAULTS (a missing file is the defaults; a bad value fails loud)."""
    cfg = {k: (dict(v) if isinstance(v, dict) else v) for k, v in DEFAULTS.items()}
    p = Path(path)
    if p.exists():
        import yaml

        got = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        for k, v in got.items():
            if k not in DEFAULTS:
                raise ValueError(f"{path}: unknown key {k!r}")
            cfg[k] = {**cfg[k], **v} if isinstance(cfg[k], dict) else v
    return cfg


def slot_problems(cfg, project="fieldkit"):
    """C3: the account runs at most `account_limit` jobs at once across every repository, so the per-project budgets
    (both OSes) plus the reserve for small jobs must fit, and this project's runner budget must fit its own slots."""
    s = cfg.get("slots")
    if not s:
        return []
    problems = []
    total = int(s["reserve"]) + sum(int(n) for p in s["projects"].values() for n in p.values())
    if total > int(s["account_limit"]):
        problems.append(f"slots: budgets {total} (incl. reserve {s['reserve']}) exceed the account limit {s['account_limit']}")
    mine = s["projects"].get(project)
    if mine is None:
        problems.append(f"slots: no budget for project {project!r}")
    else:
        for o in ("windows", "linux"):
            if int(cfg["budget"][o]) > int(mine.get(o, 0)):
                problems.append(f"slots: runner budget {o} {cfg['budget'][o]} is over this project's {mine.get(o, 0)} slots")
    return problems


def every_test_file(root="."):
    return sorted(p.relative_to(root).as_posix() for p in Path(root, "tests").rglob("test_*.py"))


def plan(files, blk, cfg, floor=1, root="."):
    """-> {os: {"n", "work", "heaviest", "lightest", "shards", "single", "granule"}}: N follows the work, the split is the one
    `ci_tests.py --shard I/N` makes (same weights, same balance), so the plan is what the shards will run.
    N = clamp(ceil(work / max(target - setup, 30)), floor, min(budget, files)); no files = 0. Floor 1 keeps shard 1,
    which carries the PR-level gates, alive on every run that has files."""
    from fieldkit.method import shard as sh

    files = sorted(set(files))
    out = {}
    for o in OSES:
        w = ((blk or {}).get("weights") or {}).get(o) or sh.load()
        unit = float(cfg["degraded_file_seconds"])
        work = sum(float(w.get(f, unit)) for f in files)
        per = max(float(cfg["target_seconds"]) - float(cfg["setup_seconds"][o]), 30.0)
        n = math.ceil(work / per) if files else 0
        n = min(max(n, floor), max(int(cfg["budget"][o]), floor), max(len(files), floor)) if files else 0
        shards = sh.balance(files, w, n) if n else []
        loads = [sum(float(w.get(f, unit)) for f in s) for s in shards]
        top = loads.index(max(loads)) if loads else 0
        out[o] = {"n": n, "work": round(work, 1), "heaviest": round(max(loads), 1) if loads else 0.0,
                  "lightest": round(min(loads), 1) if loads else 0.0, "shards": shards,
                  "single": bool(shards) and len(shards[top]) == 1,
                  "granule": round(max((float(w.get(f, unit)) for f in files), default=0.0), 1)}
    return out


def check(files, pl, cfg):
    """-> list of problems: a dropped or duplicated file, or a spread over max_spread (once N > 1; a heaviest shard
    that is one indivisible file, or a gap within one file's weight, is exempt: nothing finer can be done at file level)."""
    problems = slot_problems(cfg)
    want = sorted(set(files))
    for o, p in pl.items():
        got = [f for s in p["shards"] for f in s]
        if len(got) != len(set(got)):
            problems.append(f"{o}: a file is in two shards: {sorted({f for f in got if got.count(f) > 1})[:3]}")
        if sorted(set(got)) != want:
            problems.append(f"{o}: shards do not cover the selection (missing {sorted(set(want) - set(got))[:3]}, "
                            f"extra {sorted(set(got) - set(want))[:3]})")
        # moving the smallest file of the heaviest shard cannot help once the gap is within one file's weight
        if p["n"] > 1 and p["lightest"] > 0 and not p["single"] and p["heaviest"] - p["lightest"] > p.get("granule", 0):
            ratio = p["heaviest"] / p["lightest"]
            if ratio > float(cfg["max_spread"]):
                problems.append(f"{o}: heaviest shard is {ratio:.2f}x the lightest (limit {cfg['max_spread']})")
    return problems


def describe(pl):
    return "\n".join(f"ci: {o}: N={p['n']} work={p['work']}s heaviest={p['heaviest']}s lightest={p['lightest']}s"
                     for o, p in pl.items())



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
    items = json.loads(fkproc.check_output(base, text=True)).get("artifacts", [])
    for item in items:
        run = item.get("workflow_run") or {}
        if item.get("expired") or run.get("head_branch") != "main":
            continue
        with tempfile.TemporaryDirectory(prefix="fieldkit-durations-") as tmp:
            fkproc.check_call(["gh", "run", "download", str(run["id"]), "-n", ARTIFACT, "-D", tmp])
            src = next(Path(tmp).rglob("durations.json"), None)
            if src:
                Path(out).write_text(src.read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
                return True
    return False


def _adapter_plan(a):
    """C8: the plan and the checks of ciplan, over an adapter's units."""
    from fieldkit.method import ciadapters

    ad = ciadapters.ADAPTERS[a.adapter]
    read_text = lambda f: Path(f).read_text(encoding="utf-8") if f else None
    if not a.collect and a.adapter != "kernel":
        print("ci plan --adapter needs --collect FILE (the collect command's output)")
        return 2
    units = ad.collect(read_text(a.collect) or "")
    cfg = load_config(a.config)
    pl, bad = ciadapters.plan(ad, units, read_text(a.raw_durations), cfg, floor=a.floor)
    print(f"ci: adapter {ad.name}: {len(units)} units")
    print(describe(pl))
    if a.slice_spec:
        i, n = (int(x) for x in a.slice_spec.split("/"))
        shards = pl[a.plan_os]["shards"]
        if n != len(shards) or not 1 <= i <= n:
            print(f"ci: --slice {a.slice_spec} does not match the plan (N={len(shards)})")
            return 2
        for cmd in ad.slice(shards[i - 1]):
            print(__import__("shlex").join(cmd))
    if a.check:
        for line in bad:
            print("ci: PLAN CHECK FAILED:", line)
        if bad:
            return 3
        print("ci: plan check ok: every unit is in exactly one shard, spread within limit")
    return 0


def main(argv=None):
    if argv and argv[0] == "quarantine":              # C6: .github/quarantine.yaml, see cihang.py
        from . import cihang

        return cihang.main(argv[1:])
    if argv and argv[0] == "covmap":                  # C4.1: which test ran which lines, see covmap.py
        from . import covmap

        return covmap.main(argv[1:])
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
    pp = sub.add_parser("plan")
    pp.add_argument("--explain", action="store_true")
    pp.add_argument("--selection")
    pp.add_argument("--config", default=".github/ci.yaml")
    pp.add_argument("--check", action="store_true")
    pp.add_argument("--min", type=int, default=1, dest="floor")
    pp.add_argument("--github-output", action="store_true")
    pp.add_argument("--adapter", choices=["pytest", "go", "firefox", "kernel"],
                    help="C8: plan another runner's units (see ciadapters.py); omitted = today's pytest plan")
    pp.add_argument("--collect", help="adapter: the collect command's output")
    pp.add_argument("--raw-durations", help="adapter: the runner's own timing report")
    pp.add_argument("--slice", dest="slice_spec", help="adapter: print the commands of shard I/N (needs --os)")
    pp.add_argument("--os", default=os_key(), dest="plan_os")
    a = ap.parse_args(argv)
    if a.cmd == "plan" and a.adapter:
        return _adapter_plan(a)
    if a.cmd == "plan":
        if not a.selection:
            print("ci plan needs --selection FILE (the selection JSON CI wrote)")
            return 2
        data = json.loads(Path(a.selection).read_text(encoding="utf-8"))
        if a.explain:
            print("ci:", explain(data.get("durations")))
        files = data.get("tests")
        if files is None:
            files = every_test_file()
        files = [f for f in files if Path(f).exists()]
        cfg = load_config(a.config)
        pl = plan(files, data.get("durations"), cfg, floor=a.floor)
        print(describe(pl))
        if a.github_output:
            lines = []
            for o, key in (("windows", "windows"), ("linux", "ubuntu")):
                lines.append(f"n_{key}={pl[o]['n']}")
                lines.append(f"matrix_{key}={json.dumps(list(range(1, max(pl[o]['n'], 1) + 1)))}")   # [1] at N=0: the job is skipped, a matrix may not be empty
            with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8", newline="\n") as fh:
                fh.write("\n".join(lines) + "\n")
        if a.check:
            bad = check(files, pl, cfg)
            for line in bad:
                print("ci: PLAN CHECK FAILED:", line)
            if bad:
                return 3
            print("ci: plan check ok: every selected file is in exactly one shard, spread within limit")
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
