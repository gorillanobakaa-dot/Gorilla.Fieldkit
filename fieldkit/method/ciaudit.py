# Version: 1.0.0 · updated 26-11-02-03-10
"""What CI really ran: per pull-request run of the tests workflow, the selection line ci_tests.py printed (the whole
suite, N test files, or no code), its wall time, and the files that sent it to the whole suite.

    fieldkit ciaudit [--repo OWNER/NAME] [--since YYYY-MM-DD] [--limit N] [--max-whole-share 0.1] [--json]
    fieldkit ciaudit replay [--covmap covmap.json] [--runs 20] [--repo OWNER/NAME] [--json]   (C4.4)

Born 2026-11-02: 17 of 38 PR runs since 2026-10-26 ran the whole suite (421 min against 49 min targeted) because a
data file had no mapped test; nobody had counted. Exit 3 when the share of whole-suite runs is above the budget.
Reads GitHub through `gh` (run list + each run's log); writes nothing.
"""
import argparse
import json
import os
import re
from fieldkit import proc as fkproc
import sys
from collections import Counter
from datetime import datetime

LINE = re.compile(r"ci-tests: (no test covers (?P<files>.+?): the whole suite|the whole suite|"
                  r"(?P<n>\d+) test file\(s\) cover the change|no code changed - no tests|"
                  r"the merged tree equals the pull request's tree.*)")


def _gh(args, run=fkproc.run):
    r = run(["gh", *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.returncode, r.stdout or "", r.stderr or ""


def classify(log):
    """-> (kind, files): kind whole | targeted | none | unknown; files that sent it to the whole suite."""
    m = LINE.search(log or "")
    if not m:
        return "unknown", []
    if m.group("files"):
        return "whole", [f.strip() for f in m.group("files").split(",")]
    if m.group(0).startswith("ci-tests: the whole suite"):
        return "whole", []
    if m.group("n"):
        return "targeted", []
    return "none", []


def _secs(a, b):
    f = "%Y-%m-%dT%H:%M:%SZ"
    try:
        return (datetime.strptime(b, f) - datetime.strptime(a, f)).total_seconds()
    except (TypeError, ValueError):
        return 0.0


def audit(repo, since=None, limit=100, workflow="tests.yml", gh=None):
    gh = gh or _gh
    args = ["run", "list", "--workflow", workflow, "--limit", str(limit), "--json",
            "databaseId,event,createdAt,startedAt,updatedAt,attempt,headBranch,conclusion"]
    if repo:
        args += ["--repo", repo]
    if since:
        args += ["--created", f">={since}"]
    rc, out, err = gh(args)
    if rc:
        raise RuntimeError(f"gh run list: {err.strip()[:300]}")
    runs = [r for r in json.loads(out or "[]") if r.get("event") == "pull_request" and r.get("conclusion")]
    rows = []
    for r in runs:
        rc, log, _ = gh(["run", "view", str(r["databaseId"]), "--log", *(["--repo", repo] if repo else [])])
        kind, files = classify(log if rc == 0 else "")
        rows.append({"run": r["databaseId"], "branch": r.get("headBranch"), "kind": kind, "files": files,
                     "minutes": round(_secs(r.get("startedAt"), r.get("updatedAt")) / 60, 1),
                     "attempt": r.get("attempt"),
                     "lifetime_minutes": round(_secs(r.get("createdAt"), r.get("updatedAt")) / 60, 1),
                     "conclusion": r.get("conclusion")})
    return rows


def timeline(repo, run_id, gh=None):
    """Read the timestamps of every attempt of one run, including time between attempts."""
    gh = gh or _gh

    def read(endpoint):
        rc, out, err = gh(["api", endpoint])
        if rc:
            raise RuntimeError(f"gh api {endpoint}: {err.strip()[:300]}")
        data = json.loads(out)
        if not isinstance(data, dict):
            raise ValueError(f"expected an object from {endpoint}")
        return data

    endpoint = f"repos/{repo}/actions/runs/{run_id}"
    run = read(endpoint)
    count = run.get("run_attempt")
    if type(count) is not int or count < 1:
        raise ValueError(f"run {run_id} has no usable attempt count")
    rows = []
    previous_end = None
    for attempt in range(1, count + 1):
        data = read(f"{endpoint}/attempts/{attempt}")
        created, start = data.get("created_at"), data.get("run_started_at")
        end = data.get("updated_at") if data.get("status") == "completed" else None
        # Some reruns report creation seconds after start. There is no defensible queue interval for those.
        queue = _secs(created, start) if created and start else None
        elapsed = _secs(start, end) if start and end else None
        # A per-attempt queue belongs to that attempt, so do not count it again as an inter-attempt gap.
        boundary = min(created, start) if created and start else created or start
        gap = _secs(previous_end, boundary) if previous_end and boundary else None
        rows.append({"attempt": attempt, "created_at": created, "started_at": start, "ended_at": end,
                     "queue_s": queue if queue is not None and queue >= 0 else None,
                     "active_s": elapsed if elapsed is not None and elapsed >= 0 else None,
                     "gap_before_s": max(0, gap) if gap is not None else None,
                     "conclusion": data.get("conclusion")})
        previous_end = end
    return {"run": run_id, "attempts": rows}


def summary(rows):
    by = {k: [r for r in rows if r["kind"] == k] for k in ("whole", "targeted", "none", "unknown")}
    judged = len(by["whole"]) + len(by["targeted"]) + len(by["none"])
    return {"runs": len(rows), "judged": judged,
            **{f"{k}_runs": len(v) for k, v in by.items()},
            **{f"{k}_minutes": round(sum(r["minutes"] for r in v), 1) for k, v in by.items()},
            "whole_share": round(len(by["whole"]) / judged, 3) if judged else 0.0,
            "triggers": Counter(f for r in by["whole"] for f in r["files"]).most_common()}


FAILED = re.compile(r"(?:FAILED|ERROR) (tests/[\w/.\-]+?\.py)")


def red_tests(log):
    """-> the test files a pytest log reports FAILED or ERROR (`FAILED tests/test_x.py::test_y - ...`)."""
    return sorted(set(FAILED.findall(log or "")))


def _git_hunks(root):
    """-> hunks_for(sha) reading the local repository: {path: (hunks, old_source)} of `git diff -U0 merge-base sha`;
    None when the commit is not in the clone."""
    from . import covsel

    def git(*args):
        r = fkproc.run(["git", "-C", root, *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode:
            raise RuntimeError(r.stderr.strip()[:200])
        return r.stdout

    def hunks_for(sha):
        try:
            mb = git("merge-base", "origin/main", sha).strip()
            names = git("diff", "--name-only", mb, sha).split()
        except RuntimeError:
            return None
        out = {}
        for q in names:
            try:
                old = git("show", f"{mb}:{q}")
            except RuntimeError:
                old = None                                            # a new file
            try:
                hunks = covsel.parse_hunks(git("diff", "-U0", mb, sha, "--", q))
            except RuntimeError:
                hunks = None
            out[q] = (hunks, old)
        return out
    return hunks_for


def replay(cmap, runs=20, repo="", gh=None, hunks_for=None, fallback=None, root="."):
    """C4.4: replay the last `runs` red pull-request runs of tests.yml (read-only `gh`): would the coverage selection
    have included every test file that went red? A red test file that is not selected is a MISS.
    `fallback(files)` is the import selection for files the map cannot judge (default: the import rule over the current
    tree: an approximation, the tests of that day may differ). -> rows, one per red run."""
    from . import covsel

    gh = gh or _gh
    hunks_for = hunks_for or _git_hunks(root)
    if fallback is None:
        def fallback(files):
            from fieldkit import testcost
            r = testcost.plan(root, None, paths=files)
            return {"tests": [os.path.relpath(t, root).replace("\\", "/") for t in r["tests"]], "unmapped": r["unmapped"]}
    args = ["run", "list", "--workflow", "tests.yml", "--event", "pull_request", "--status", "failure",
            "--limit", str(runs), "--json", "databaseId,headSha,headBranch"]
    if repo:
        args += ["--repo", repo]
    rc, out, err = gh(args)
    if rc:
        raise RuntimeError(f"gh run list: {err.strip()[:300]}")
    rows = []
    for r in json.loads(out or "[]"):
        rc, log, _ = gh(["run", "view", str(r["databaseId"]), "--log-failed", *(["--repo", repo] if repo else [])])
        red = red_tests(log if rc == 0 else "")
        files = hunks_for(r["headSha"])
        row = {"run": r["databaseId"], "sha": r["headSha"][:10], "branch": r.get("headBranch"), "red": red,
               "selected": [], "missed": [], "unmapped": [], "status": "replayed"}
        if not red:
            row["status"] = "no red test file in the log"
        elif files is None:
            row["status"] = "commit not in the local clone"
        else:
            cov = covsel.coverage_plan(
                sorted(files), cmap, lambda q: files[q][0], lambda q: files[q][1], fallback)
            row["selected"], row["unmapped"] = cov["tests"], cov["unmapped"]
            row["missed"] = [t for t in red if t not in cov["tests"]]
        rows.append(row)
    return rows


def replay_summary(rows):
    done = [r for r in rows if r["status"] == "replayed"]
    missed = [r for r in done if r["missed"]]
    return {"red_runs": len(rows), "replayed": len(done), "skipped": len(rows) - len(done),
            "red_tests": sum(len(r["red"]) for r in done), "misses": sum(len(r["missed"]) for r in done),
            "runs_with_misses": len(missed), "gate": "union" if missed else "import"}


def replay_main(argv):
    ap = argparse.ArgumentParser(prog="fieldkit ciaudit replay", description="would the coverage selection have caught "
                                 "every test that went red in the last N red pull-request runs?")
    ap.add_argument("--covmap", default=os.environ.get("FIELDKIT_COVMAP") or "covmap.json")
    ap.add_argument("--runs", type=int, default=20)
    ap.add_argument("--repo", default="")
    ap.add_argument("--root", default=".")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        cmap = json.loads(open(a.covmap, encoding="utf-8").read())
        if cmap.get("schema") != 1:
            raise ValueError(f"{a.covmap}: not a covmap (schema 1)")
        rows = replay(cmap, a.runs, a.repo, root=a.root)
    except (OSError, RuntimeError, ValueError) as e:
        print(f"ciaudit replay: {e}")
        return 1
    s = replay_summary(rows)
    if a.json:
        print(json.dumps({"summary": s, "runs": rows}, ensure_ascii=False))
    else:
        for r in rows:
            print(f"  {r['run']} {r['sha']} {r['status']:<10} red {len(r['red'])} missed {len(r['missed'])}  {r['branch']}"
                  + "".join(f"\n      MISS {t}" for t in r["missed"]))
        print(f"ciaudit replay: {s['replayed']} of {s['red_runs']} red run(s) replayed, {s['red_tests']} red test file(s), "
              f"{s['misses']} miss(es)")
        print("NEXT: " + ("selection_gate stays import: the gate for the coverage selection is union (never flipped here)"
                          if s["misses"] else "0 misses: the owner may review flipping selection_gate; it stays import until then"))
    return 3 if s["misses"] else 0


def main(argv=None):
    if argv and argv[0] == "replay":
        return replay_main(argv[1:])
    ap = argparse.ArgumentParser(prog="fieldkit ciaudit", description=__doc__.split("\n\n")[0])
    ap.add_argument("--repo", default="", help="OWNER/NAME (default: the current checkout's GitHub repository)")
    ap.add_argument("--since", default="", help="YYYY-MM-DD: runs created on or after")
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--workflow", default="tests.yml")
    ap.add_argument("--max-whole-share", type=float, default=0.1, help="budget: whole-suite runs / judged runs")
    ap.add_argument("--run", type=int, help="show queue, active time and gaps for each attempt of one run")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        if a.run is not None:
            if a.run < 1:
                ap.error("--run must be a positive run ID")
            repo = a.repo
            if not repo:
                rc, repo, err = _gh(["repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"])
                if rc:
                    raise RuntimeError(f"gh repo view: {err.strip()[:300]}")
            report = timeline(repo.strip(), a.run)
            if a.json:
                print(json.dumps(report))
            else:
                def seconds(value):
                    return f"{value:,.0f} s" if value is not None else "unknown"

                for row in report["attempts"]:
                    print(f"attempt {row['attempt']}: queue {seconds(row['queue_s'])}, "
                          f"active {seconds(row['active_s'])}, "
                          f"prior gap {seconds(row['gap_before_s'])}, {row['conclusion'] or 'pending'}")
                print("NEXT: nothing to do")
            return 0
        rows = audit(a.repo, a.since, a.limit, a.workflow)
    except (RuntimeError, ValueError) as e:
        print(f"ciaudit: {e}")
        return 1
    s = summary(rows)
    over = s["whole_share"] > a.max_whole_share
    if a.json:
        print(json.dumps({"summary": s, "runs": rows, "over_budget": over}, ensure_ascii=False))
    else:
        for r in rows:
            print(f"  {r['run']}  {r['kind']:<8} {r['minutes']:>6} min  {r['branch']}"
                  + (f"  <- {', '.join(r['files'])}" if r["files"] else ""))
        print(f"ciaudit: {s['runs']} PR run(s); whole suite {s['whole_runs']} ({s['whole_minutes']} min), targeted "
              f"{s['targeted_runs']} ({s['targeted_minutes']} min), no code {s['none_runs']}, unreadable "
              f"{s['unknown_runs']}; whole-suite share {s['whole_share']:.0%} (budget {a.max_whole_share:.0%})")
        for f, n in s["triggers"]:
            print(f"  trigger {n}x {f}")
        print("NEXT: " + ("map each trigger in tests/testmap.yaml (fieldkit testcost unmapped --all)" if over
                          else "nothing to do"))
    return 3 if over else 0


if __name__ == "__main__":
    sys.exit(main())
