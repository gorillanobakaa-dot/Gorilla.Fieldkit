# Version: 1.0.0 · updated 26-11-02-03-10
"""What CI really ran: per pull-request run of the tests workflow, the selection line ci_tests.py printed (the whole
suite, N test files, or no code), its wall time, and the files that sent it to the whole suite.

    fieldkit ciaudit [--repo OWNER/NAME] [--since YYYY-MM-DD] [--limit N] [--max-whole-share 0.1] [--json]

Born 2026-11-02: 17 of 38 PR runs since 2026-10-26 ran the whole suite (421 min against 49 min targeted) because a
data file had no mapped test; nobody had counted. Exit 3 when the share of whole-suite runs is above the budget.
Reads GitHub through `gh` (run list + each run's log); writes nothing.
"""
import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime

LINE = re.compile(r"ci-tests: (no test covers (?P<files>.+?): the whole suite|the whole suite|"
                  r"(?P<n>\d+) test file\(s\) cover the change|no code changed - no tests|"
                  r"the merged tree equals the pull request's tree.*)")


def _gh(args, run=subprocess.run):
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
            "databaseId,event,createdAt,updatedAt,headBranch,conclusion"]
    if repo:
        args += ["--repo", repo]
    if since:
        args += ["--created", f">={since}"]
    rc, out, err = gh(args)
    if rc:
        raise RuntimeError(f"gh run list: {err.strip()[:300]}")
    runs = [r for r in json.loads(out or "[]") if r.get("event") == "pull_request"]
    rows = []
    for r in runs:
        rc, log, _ = gh(["run", "view", str(r["databaseId"]), "--log", *(["--repo", repo] if repo else [])])
        kind, files = classify(log if rc == 0 else "")
        rows.append({"run": r["databaseId"], "branch": r.get("headBranch"), "kind": kind, "files": files,
                     "minutes": round(_secs(r.get("createdAt"), r.get("updatedAt")) / 60, 1),
                     "conclusion": r.get("conclusion")})
    return rows


def summary(rows):
    by = {k: [r for r in rows if r["kind"] == k] for k in ("whole", "targeted", "none", "unknown")}
    judged = len(by["whole"]) + len(by["targeted"]) + len(by["none"])
    return {"runs": len(rows), "judged": judged,
            **{f"{k}_runs": len(v) for k, v in by.items()},
            **{f"{k}_minutes": round(sum(r["minutes"] for r in v), 1) for k, v in by.items()},
            "whole_share": round(len(by["whole"]) / judged, 3) if judged else 0.0,
            "triggers": Counter(f for r in by["whole"] for f in r["files"]).most_common()}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="fieldkit ciaudit", description=__doc__.split("\n\n")[0])
    ap.add_argument("--repo", default="", help="OWNER/NAME (default: the current checkout's GitHub repository)")
    ap.add_argument("--since", default="", help="YYYY-MM-DD: runs created on or after")
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--workflow", default="tests.yml")
    ap.add_argument("--max-whole-share", type=float, default=0.1, help="budget: whole-suite runs / judged runs")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
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
