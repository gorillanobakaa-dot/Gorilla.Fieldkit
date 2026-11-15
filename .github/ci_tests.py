"""Which tests CI runs (R7 in docs/HARNESS-PLAN.md; owner, 2026-10-28: the full suite only at the end).

    python .github/ci_tests.py BASE     the tests covering BASE...HEAD (fieldkit testcost plan)
    python .github/ci_tests.py BASE --merged   same, but nothing when a merge commit's tree equals its PR head's tree
    A diff of only documentation, state/ files or version lines selects zero tests (testcost plan: no_code).
    python .github/ci_tests.py full     the whole suite (a finished project, a release)
    --shard I/N                         run only shard I of N of the selected files (fieldkit.method.shard; the
                                        workflow runs N shards per OS in parallel, plan P9 item 7)
    A batch pull request (head branch night/batch-*, fieldkit ship batch) carries its selection in its body
    (<!-- fieldkit-batch {"tests": [...]} -->): what no member's own green head proved plus what two members both
    cover. Without that marker, or with "tests": null, the normal selection above applies.

Never the whole suite for a change (owner, 2026-11-02: "I don't want 40,000 tests for a small change because some
file has no card"). When the changed files cannot be read, there is no base to compare, or a changed file is covered
by no test ("unmapped"), CI FAILS at once with what to fix and runs no test. Before: each of those ran the whole
suite (29 of 66 PR runs, 720 min, `fieldkit ciaudit`). The whole suite runs only when asked: `full`. Mutation plans
(tests/*_mutations.yaml) are read only by `fieldkit mutate` and select nothing.
"""
import json
import re
import subprocess
import sys

REFUSED = False                       # the selection refuses: CI fails in seconds, no test runs
MUTATION_PLAN = re.compile(r"^tests/[^/]+_mutations\.yaml$")


def merged_tree_already_tested(head="HEAD"):
    """True when HEAD is a merge commit whose tree equals its second parent's: the pull request already ran green on
    exactly these files, so the push to main proves nothing new. Any difference (main moved) returns False."""
    def tree(rev):
        r = subprocess.run(["git", "rev-parse", rev], capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 else None
    a, b = tree(f"{head}^{{tree}}"), tree(f"{head}^2^{{tree}}")
    return bool(a) and a == b


BATCH_MARK = re.compile(r"<!-- fieldkit-batch (\{.*?\}) -->", re.S)


def batch_selection(event_path=None, head_ref=None):
    """-> the test files a batch PR's body names, or None (not a batch, no marker, or the tool could not select)."""
    import os
    head_ref = os.environ.get("GITHUB_HEAD_REF", "") if head_ref is None else head_ref
    event_path = os.environ.get("GITHUB_EVENT_PATH", "") if event_path is None else event_path
    if not head_ref.startswith("night/batch-") or not event_path:
        return None
    try:
        body = (json.load(open(event_path, encoding="utf-8")).get("pull_request") or {}).get("body") or ""
        m = BATCH_MARK.search(body)
        tests = json.loads(m.group(1)).get("tests") if m else None
    except (OSError, ValueError):
        return None
    return sorted({t.replace("\\", "/") for t in tests}) if isinstance(tests, list) else None


def selection(base, merged=False):
    """-> (tests, why); tests None = the whole suite (only `full`), [] = nothing to run, REFUSED = fail, run nothing."""
    picked = batch_selection() if base != "full" else None
    if picked is not None:
        return picked, f"batch PR: {len(picked)} test file(s) not already proven by a member's own green head"
    if merged and merged_tree_already_tested():
        return [], "the merged tree equals the pull request's tree, already green: nothing new to test"
    if base == "full":
        return None, "the whole suite (asked for)"
    if not base or set(base) == {"0"}:                              # all zeros: a new branch has no "before"
        return REFUSED, "no base commit to compare with: nothing is selected and the whole suite is never the default"
    r = subprocess.run([sys.executable, "-m", "fieldkit", "testcost", "plan", "--base", base, "--json"],
                       capture_output=True, text=True)
    try:
        plan = json.loads(r.stdout)
    except ValueError:
        return REFUSED, (f"testcost plan gave no answer (exit {r.returncode}): fix testcost; "
                         f"{(getattr(r, 'stderr', '') or '').strip()[-300:]}")
    if plan.get("no_code") or plan.get("docs_only"):
        return [], "no code changed - no tests"
    unmapped = [p for p in plan.get("unmapped", []) if not MUTATION_PLAN.match(p.replace("\\", "/"))]
    if unmapped:
        return REFUSED, ("no test covers " + ", ".join(unmapped) + ": map each in tests/testmap.yaml or give it a "
                         "test (`fieldkit testcost unmapped --all`)")
    tests = sorted({t.replace("\\", "/") for t in plan.get("tests", [])})
    return tests, f"{len(tests)} test file(s) cover the change"


def main(argv):
    merged = "--merged" in argv                      # a push to main: skip a tree the pull request already tested
    argv = [x for x in argv if x != "--merged"]
    shard = None
    if "--shard" in argv:
        k = argv.index("--shard")
        shard = argv[k + 1]
        argv = argv[:k] + argv[k + 2:]
    tests, why = selection(argv[1] if len(argv) > 1 else "full", merged)
    print("ci-tests:", why)
    if shard and tests is not REFUSED and tests != []:   # the whole suite (tests None) is every test file, split
        from pathlib import Path
        from fieldkit.method import shard as sh
        every = tests if tests is not None else sorted(p.as_posix() for p in Path("tests").rglob("test_*.py"))
        tests = sh.pick(every, sh.load(), shard)
        print(f"ci-tests: shard {shard}: {len(tests)} file(s)")
        if not tests:
            return 0
    if tests is REFUSED:
        print("ci-tests: REFUSED - no test was run", flush=True)
        return 1
    if tests == []:
        return 0
    cmd = [sys.executable, "-m", "pytest", "-q", "-rsf", *(tests or [])]
    print("ci-tests:", " ".join(cmd), flush=True)
    return subprocess.call(cmd)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
