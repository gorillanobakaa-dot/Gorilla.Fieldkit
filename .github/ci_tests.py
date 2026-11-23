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
import os
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

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


def present(tests, root="."):
    """-> the selected test files that still exist: a pull request that deletes a test file must not make pytest
    fail on "file not found" (PR 552). A deleted file has nothing left to run."""
    return [t for t in tests if (Path(root) / t).exists()]


def checkout_head():
    return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()


def read_selection(path, head):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("schema") != 1 or data.get("head") != head:
        raise ValueError("selection does not match the checked out commit")
    tests = data["tests"]
    if tests is not None and (not isinstance(tests, list) or any(
            not isinstance(t, str) or not t.startswith("tests/") or "\\" in t or ".." in PurePosixPath(t).parts
            for t in tests)):
        raise ValueError("invalid test paths in selection")
    return tests, data["why"]


def read_durations_block(path):
    """-> the durations block of a selection file (None when it has none)."""
    return json.loads(Path(path).read_text(encoding="utf-8")).get("durations")


def durations_block(path, tests, base):
    """-> (block, error): the weights every shard splits by, or why the selection must fail closed (ciplan.block)."""
    from fieldkit.method import ciplan

    selected = tests if tests is not None else sorted(p.as_posix() for p in Path("tests").rglob("test_*.py"))
    added = []
    if base and base != "full" and set(base) != {"0"}:
        r = subprocess.run(["git", "diff", "--diff-filter=A", "--name-only", f"{base}...HEAD"], capture_output=True, text=True)
        added = [x.replace("\\", "/") for x in r.stdout.split()] if r.returncode == 0 else []
    return ciplan.block(ciplan.read(path), selected, added)


def option(argv, name):
    if name not in argv:
        return None
    index = argv.index(name)
    argv.pop(index)
    return argv.pop(index)


def shard_log(shard):
    return Path(os.environ["RUNNER_TEMP"]) / f"fieldkit-pytest-{(shard or '1').split('/')[0]}.log"


def tee(cmd, log):
    """Run cmd, echo its output live and keep it in `log` (C6 reads the log to find a hung test)."""
    env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
    with log.open("wb") as keep, subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env) as proc:
        for raw in proc.stdout:
            keep.write(raw)
            sys.stdout.write(raw.decode("utf-8", "replace"))
            sys.stdout.flush()
    return proc.returncode


def run_checked(cmd, tests, shard):
    """C6: a hung test (pytest-timeout) gets one automatic re-run of the shard; hanging twice is quarantine."""
    rc = run_tests(cmd, tests, shard)
    if rc == 0 or os.getenv("GITHUB_ACTIONS") != "true":
        return rc
    from fieldkit.method import cihang

    log = shard_log(shard)
    text = log.read_text(encoding="utf-8", errors="replace") if log.is_file() else ""
    node = cihang.hung_node(text)
    if not node or cihang.other_failures(text):
        return rc
    print(f"ci-tests: HANG 1/2 {node}: re-running this shard once", flush=True)
    log.unlink(missing_ok=True)
    rc2 = run_tests(cmd, tests, shard)
    text2 = log.read_text(encoding="utf-8", errors="replace") if log.is_file() else ""
    if rc2 == 0:
        print(f"ci-tests: HANG-FLAKE {node} hung once and passed on the re-run", flush=True)
        print(f"::warning::HANG-FLAKE {node} hung once and passed on the re-run", flush=True)
        return 0
    if cihang.hung_node(text2) == node:
        print(f"ci-tests: HANG-QUARANTINE {node} hung twice. Add to .github/quarantine.yaml under tests:", flush=True)
        print(cihang.quarantine_line(node, os.getenv("GITHUB_RUN_ID", "")), flush=True)
    return rc2


def run_tests(cmd, tests, shard):
    """Bound PR shards, stream their progress, and stop only the shard's process tree."""
    if os.getenv("GITHUB_ACTIONS") == "true" and os.getenv("RUNNER_TEMP") and (
            not shard or os.getenv("GITHUB_EVENT_NAME") != "pull_request"):
        return tee(cmd, shard_log(shard))
    if not shard or os.getenv("GITHUB_ACTIONS") != "true" or os.getenv("GITHUB_EVENT_NAME") != "pull_request":
        return subprocess.call(cmd)

    import codecs
    import time

    from fieldkit.core import contain, liveness
    from fieldkit.method import shard as durations

    budget = durations.load()
    planned = sum(budget.get(name, 30) for name in tests)
    deadline = liveness.deadline_for([planned], floor=600, cap=1680, factor=4)
    log = shard_log(shard)
    start = next_heartbeat = time.monotonic()
    offset = 0
    decoder = codecs.getincrementaldecoder("utf-8")("replace")

    def progress(final=False):
        nonlocal offset, next_heartbeat
        with log.open("rb") as output:
            output.seek(offset)
            chunk = output.read()
            offset = output.tell()
        if text := decoder.decode(chunk, final=final):
            print(text, end="", flush=True)
        now = time.monotonic()
        if not final and now >= next_heartbeat:
            print(f"ci-tests: shard {shard} active {now - start:.0f}s / {deadline:.0f}s; log {offset} bytes",
                  flush=True)
            next_heartbeat = now + 60

    env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
    label = f"CI {os.environ.get('RUNNER_OS', '')} shard {shard}"
    with liveness.beacon("ci-tests", f"shard-{shard.split('/')[0]}", label) as beacon:
        result = contain.run(cmd, label=label, deadline_s=deadline, beacon=beacon, idle_s=360,
                             env=env, stdout_path=log, merge=True, on_tick=progress)
        if log.is_file():
            progress(final=True)
    print(f"ci-tests: {label} {result.status} in {result.seconds:.1f}s"
          + (f": {result.why}" if result.why else ""), flush=True)
    if result.err:
        print(result.err, file=sys.stderr)
    return result.rc


def summary(reused, ran, single=0, proof=None):
    """C5: one line the release and the PR can quote: how many tests were reused and how many ran."""
    line = f"ci-tests: reused {reused} / ran {ran} tests ({single} single tests skipped by receipt)"
    print(line, flush=True)
    for path in filter(None, (os.getenv("GITHUB_STEP_SUMMARY"),)):
        try:
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(line + chr(10))
        except OSError:
            pass
    if proof:
        try:
            (Path(proof).parent / "reuse.json").write_text(json.dumps({"reused": reused, "ran": ran}), encoding="utf-8")
        except OSError:
            pass


def main(argv):
    merged = "--merged" in argv                      # a push to main: skip a tree the pull request already tested
    argv = [x for x in argv if x != "--merged"]
    try:
        shard = option(argv, "--shard")
        incoming = option(argv, "--read-selection")
        outgoing = option(argv, "--write-selection")
        durations_file = option(argv, "--durations")
        if incoming and outgoing:
            raise ValueError("cannot read and write a test selection in the same step")
        tests, why = (read_selection(incoming, checkout_head()) if incoming
                      else selection(argv[1] if len(argv) > 1 else "full", merged))
        block = read_durations_block(incoming) if incoming else None
        if outgoing and tests is not REFUSED:
            record = {"schema": 1, "head": checkout_head(), "tests": tests, "why": why}
            if durations_file:
                from fieldkit.method import ciplan

                found, err = durations_block(durations_file, tests, argv[1] if len(argv) > 1 else "full")
                if err:
                    print(f"ci-tests: REFUSED - {err}")
                    return 1
                record["durations"] = found
                print("ci-tests:", ciplan.explain(found))
            Path(outgoing).write_text(json.dumps(record), encoding="utf-8")
            print("ci-tests:", why)
            return 0
    except (IndexError, KeyError, OSError, ValueError, AttributeError, subprocess.SubprocessError) as exc:
        print(f"ci-tests: REFUSED - selection unavailable: {exc}")
        return 1
    print("ci-tests:", why)
    hang_weights = {}
    if tests is not REFUSED and tests:
        tests = present(tests)
    if shard and tests is not REFUSED and tests != []:   # the whole suite (tests None) is every test file, split
        from fieldkit.method import shard as sh
        every = tests if tests is not None else sorted(p.as_posix() for p in Path("tests").rglob("test_*.py"))
        from fieldkit.method import ciplan

        weights = ((block or {}).get("weights") or {}).get(ciplan.os_key()) or sh.load()
        hang_weights = weights
        tests = sh.pick(every, weights, shard)
        print(f"ci-tests: shard {shard}: {len(tests)} file(s)")
        if not tests:
            return 0
    if tests is REFUSED:
        print("ci-tests: REFUSED - no test was run", flush=True)
        return 1
    if tests == []:
        return 0
    skip_nodes, reused_files = set(), 0
    evidence_token = os.environ.pop("FIELDKIT_CI_READ_TOKEN", "")
    if os.getenv("FIELDKIT_REUSE_CI") == "1":
        import ci_file_proof

        selected = tests if tests is not None else sorted({path.as_posix() for root in (Path("tests"), Path("local/tests"))
                                                          for pattern in ("test_*.py", "*_test.py")
                                                          for path in root.rglob(pattern)})
        if selected:
            if hasattr(ci_file_proof, "reuse_plan"):
                pending, skip_nodes, reused_files = ci_file_proof.reuse_plan(
                    selected, token=evidence_token, repair=os.getenv("GITHUB_EVENT_NAME") == "pull_request")
            else:                                     # an older module: file level only
                pending, skip_nodes, reused_files = ci_file_proof.reuse(selected, token=evidence_token), set(), 0
            if tests is not None or pending != selected:
                tests = pending
            if tests == []:
                summary(len(selected), 0)
                return 0
    proof = os.getenv("FIELDKIT_TEST_EVIDENCE")
    nodes_out = None
    if skip_nodes:
        skip_file = Path(os.environ.get("RUNNER_TEMP", ".")) / "fieldkit-skip-nodes.json"
        skip_file.write_text(json.dumps(sorted(skip_nodes)), encoding="utf-8")
        os.environ["FIELDKIT_SKIP_NODES"] = str(skip_file)
    if proof:
        nodes_out = Path(os.environ.get("RUNNER_TEMP", ".")) / "fieldkit-nodes.json"
        os.environ["FIELDKIT_NODES_OUT"] = str(nodes_out)
    cmd = [sys.executable, "-m", "pytest", "-q", "-rsf"]
    if os.getenv("GITHUB_ACTIONS") == "true":
        from fieldkit.method import cihang, ciplan

        hang = ciplan.load_config().get("hang")
        limit = cihang.timeout_for(hang_weights, hang)
        # thread method: the only one that works on Windows; it ends the process, and run_checked re-runs the shard once
        cmd.extend(["-o", "faulthandler_timeout=300", "-vv", f"--timeout={limit}", "--timeout-method=thread"])
        # C7: the runner has several cores; `serial` tests share one xdist group (tests/conftest.py)
        from fieldkit.method import cirunner

        fp = cirunner.fingerprint()
        workers = os.getenv("FIELDKIT_XDIST") or str(cirunner.workers(fp, ciplan.load_config().get("runner")))
        print(cirunner.describe(fp, workers), flush=True)
        cmd.extend(["-n", workers, "--dist", "loadgroup"])
        if os.getenv("GITHUB_EVENT_NAME") == "pull_request":
            cmd.append("-x")
        print(f"ci-tests: per-test timeout {limit}s (hang config {hang})", flush=True)
        cmd.extend(cihang.deselect_args(cihang.load_quarantine(), tests))
    cmd.extend(tests or [])
    if proof and tests:
        from ci_file_proof import context, write_receipt

        identity = context()
        if identity:
            report = Path(os.environ["RUNNER_TEMP"]) / "fieldkit-test-report.xml"
            cmd.extend(["--junitxml", str(report), "-o", "junit_family=xunit1"])
            print("ci-tests:", " ".join(cmd), flush=True)
            status = run_checked(cmd, tests, shard)
            if report.is_file():                      # C1: every shard hands its junit to the durations job
                import shutil
                Path(proof).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(report, Path(proof).parent / "junit.xml")
            passed_nodes, ran = [], 0
            try:
                record = json.loads(nodes_out.read_text(encoding="utf-8"))
                passed_nodes = record.get("passed", [])
                ran = len(passed_nodes) + len(record.get("failed", []))
            except (OSError, ValueError, AttributeError):
                pass
            if status == 0 or passed_nodes:           # C5: a red run still records what passed
                write_receipt(Path(proof), report, tests, identity, nodes=passed_nodes)
            summary(reused_files + len(skip_nodes), ran, len(skip_nodes), proof)
            return status
    print("ci-tests:", " ".join(cmd), flush=True)
    return run_checked(cmd, tests, shard)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
