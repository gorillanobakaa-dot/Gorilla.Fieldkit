"""Which tests CI runs (R7 in docs/HARNESS-PLAN.md; owner, 2026-10-28: the full suite only at the end).

    python .github/ci_tests.py BASE     the tests covering BASE...HEAD (fieldkit testcost plan)
    python .github/ci_tests.py BASE --merged   same, but nothing when a merge commit's tree equals its PR head's tree
    A diff of only documentation, state/ files or version lines selects zero tests (testcost plan: no_code).
    python .github/ci_tests.py full     the whole suite (a finished project, a release)

Fail safe: when the changed files cannot be read, or a changed file is covered by no test ("unmapped"), the whole
suite runs, as testcost's own rule says. Mutation plans (tests/*_mutations.yaml) are read only by `fieldkit mutate`
and never pull in the whole suite.
"""
import json
import re
import subprocess
import sys

MUTATION_PLAN = re.compile(r"^tests/[^/]+_mutations\.yaml$")


def merged_tree_already_tested(head="HEAD"):
    """True when HEAD is a merge commit whose tree equals its second parent's: the pull request already ran green on
    exactly these files, so the push to main proves nothing new. Any difference (main moved) returns False."""
    def tree(rev):
        r = subprocess.run(["git", "rev-parse", rev], capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 else None
    a, b = tree(f"{head}^{{tree}}"), tree(f"{head}^2^{{tree}}")
    return bool(a) and a == b


def selection(base, merged=False):
    """-> (tests, why); tests None = the whole suite, [] = nothing to run."""
    if merged and merged_tree_already_tested():
        return [], "the merged tree equals the pull request's tree, already green: nothing new to test"
    if not base or base == "full" or set(base) == {"0"}:          # all zeros: a new branch has no "before"
        return None, "the whole suite"
    r = subprocess.run([sys.executable, "-m", "fieldkit", "testcost", "plan", "--base", base, "--json"],
                       capture_output=True, text=True)
    try:
        plan = json.loads(r.stdout)
    except ValueError:
        return None, f"testcost plan gave no answer (exit {r.returncode}): the whole suite"
    if plan.get("no_code") or plan.get("docs_only"):
        return [], "no code changed - no tests"
    unmapped = [p for p in plan.get("unmapped", []) if not MUTATION_PLAN.match(p.replace("\\", "/"))]
    if unmapped:
        return None, "no test covers " + ", ".join(unmapped) + ": the whole suite"
    tests = sorted({t.replace("\\", "/") for t in plan.get("tests", [])})
    return tests, f"{len(tests)} test file(s) cover the change"


def main(argv):
    merged = "--merged" in argv                      # a push to main: skip a tree the pull request already tested
    argv = [x for x in argv if x != "--merged"]
    tests, why = selection(argv[1] if len(argv) > 1 else "full", merged)
    print("ci-tests:", why)
    if tests == []:
        return 0
    cmd = [sys.executable, "-m", "pytest", "-q", "-rsf", *(tests or [])]
    print("ci-tests:", " ".join(cmd), flush=True)
    return subprocess.call(cmd)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
