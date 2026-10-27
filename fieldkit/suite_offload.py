# Version: 1.0.0 · updated 26-10-27
"""The pre-commit suite on the other computer: the OS-neutral part runs on an idle Linux device over the cable, this
computer runs only what that device could not vouch for.

    fieldkit orchestrate offload [--tree REPO] [--json]      once, by hand: where the suite would go and the verdict
    python -m fieldkit.suite_offload hook --key KEY          the pre-commit hook's door (exit codes below)

WHY (owner, 2026-10-26 21:4x: "every MB counts ... delegate more jobs to the linux box"): a full suite on the Windows
laptop took 58.4 min, 23.8 of 63.7 GB RAM and 80 C (measured by the command post); on the VAIO it takes 12.1-12.5 min.

THE RULES (fixed; no model and no person in the path):
  1. WANTED: setting `suite_offload` in fieldkit.local.json: "auto" (the default: on Windows only), "on" or "off".
     FIELDKIT_SUITE_LOCAL=1 in the environment is "off" for that one run.
  2. WHERE: `orchestrate_load.choose(load_all(), os, any_os=True)`: another device that answered `load`, is IDLE
     (a free slot, CPU under 50 %, below its hot line) and has python, pytest and git. None: the suite runs here.
     A device that is down or BUSY is never waited for: the commit does not queue behind the cable.
  3. THE JOB: the exact testcost tree (`export_tree`), sent with `orchestrate job run` as `admit suite --report
     REPORT`, so the worker's own gate admits it and pytest writes a JUnit XML report. The report is the job's one
     declared artefact; it comes back with the sha256 the worker signed.
  4. TRUST: a pass counts only when the result is signed by a pinned worker key, the report arrived with the signed
     sha256, and the report itself says at least one test passed and none failed or errored. Anything less (no
     result in time, no report, a report with 0 tests, an older worker that cannot write one) is NOT a pass: the
     full suite runs here. A report that names failed tests refuses the commit (exit 1) and names them.
  5. WHAT STAYS HERE: every test file in which the other device SKIPPED a test (windows_only, a tool it lacks, any
     skip) runs here, plus the hook's own privacy scan. The skip list comes from the report, not from a guess.
  6. RECORD: testcost gets `full-suite@<OS>` for the tree from the device's result; the hook records `full-suite`
     only after the local part passed too.

Exit codes of `hook`: 0 the device passed (stdout: the test files to run here, one per line, maybe none); 1 the
device's report names failures (refuse the commit); 10 run the full suite here (with the reason on stderr).
"""
import json
import os
import shutil
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

REPORT = "fk-suite-report.xml"
LOCAL = 10
WALL_MIN = 40                                         # the VAIO's suite is 12.1-12.5 min; a queue there is allowed
WAIT_S = 45 * 60


def wanted(os_tag, setting=None, env=None):
    """-> (yes, why). Rule 1."""
    env = os.environ if env is None else env
    if env.get("FIELDKIT_SUITE_LOCAL") == "1":
        return False, "FIELDKIT_SUITE_LOCAL=1: the suite runs here"
    s = str(setting or "auto").lower()
    if s == "off":
        return False, "suite_offload is off in fieldkit.local.json"
    if s == "on" or os_tag == "WINDOWS":
        return True, ""
    return False, f"suite_offload auto: only Windows sends its suite away (this is {os_tag})"


def setting():
    from .core import settings
    try:
        return settings.local_settings().get("suite_offload")
    except Exception:  # noqa: BLE001 - no settings file: the default
        return None


def read_report(path):
    """-> {tests, passed, failed, errors, skipped: [(classname, name)], failures: [(classname, name)]} from a JUnit
    XML report (pytest --junitxml). Raises ValueError for a file that is not one."""
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError) as e:
        raise ValueError(f"not a JUnit report: {e}") from e
    cases = list(root.iter("testcase"))
    if root.tag not in ("testsuites", "testsuite"):
        raise ValueError(f"not a JUnit report: root <{root.tag}>")
    out = {"tests": len(cases), "passed": 0, "failed": 0, "errors": 0, "skipped": [], "failures": []}
    for c in cases:
        who = (c.get("classname") or "", c.get("name") or "")
        if c.find("failure") is not None:
            out["failed"] += 1
            out["failures"].append(who)
        elif c.find("error") is not None:
            out["errors"] += 1
            out["failures"].append(who)
        elif c.find("skipped") is not None:
            out["skipped"].append(who)
        else:
            out["passed"] += 1
    return out


def test_file(classname, repo):
    """'tests.test_x.TestY' -> 'tests/test_x.py' when that file exists in repo, else None (the longest prefix wins)."""
    parts = classname.split(".")
    for n in range(len(parts), 0, -1):
        rel = "/".join(parts[:n]) + ".py"
        if (Path(repo) / rel).is_file():
            return rel
    return None


def local_part(report, repo):
    """Rule 5. -> (the test files in which the other device skipped a test, sorted, each once; the skipped tests
    that map to no file here). A module skipped while it was collected (pytest.skip(allow_module_level=True),
    importorskip) has no classname: its name is the module."""
    files, unmapped = set(), []
    for c, n in report["skipped"]:
        f = test_file(c or n, repo)
        if f:
            files.add(f)
        else:
            unmapped.append(f"{c}::{n}" if c else n)
    return sorted(files), unmapped


def judge(report):
    """Rule 4 on the report alone. -> (status pass|fail|untrusted, why)."""
    if report["failed"] or report["errors"]:
        return "fail", f"{report['failed']} failed, {report['errors']} error(s) of {report['tests']}"
    if report["passed"] < 1:
        return "untrusted", f"the report holds {report['tests']} test(s) and none passed: nothing was tested"
    return "pass", f"{report['passed']} passed, {len(report['skipped'])} skipped of {report['tests']}"


def offload(repo=".", project="Gorilla.Fieldkit", say=None, os_tag=None, rows=None, load_fn=None, send=None,
            wait_fn=None, result_fn=None, check_fn=None, record=None, wait_s=WAIT_S):
    """-> {status: pass|fail|local, why, where, local: [test files], report}. The rules are in the docstring."""
    from . import orchestrate_load as L
    from . import orchestrate_work as W
    from . import testcost
    from .mandate import jobs
    from .peers import link
    say = say or (lambda s: print(s, file=sys.stderr, flush=True))
    me = os_tag or link.os_tag()
    out = {"status": "local", "why": "", "where": "here", "local": [], "report": None}
    yes, why = wanted(me, setting())
    if not yes:
        return dict(out, why=why)
    try:
        rows = (load_fn or L.load_all)() if rows is None else rows
    except Exception as e:  # noqa: BLE001 - no link at all: the suite runs here
        return dict(out, why=f"no load report: {type(e).__name__}: {e}")
    pick = L.choose(rows, me, any_os=True)
    if pick is None:
        others = [r for r in rows if not r.get("me")]
        said = "; ".join(f"{r['name']} {'down: ' + str(r.get('error'))[:60] if not r.get('ok') else 'BUSY' if not L.idle(r) else 'lacks pytest or git'}"
                         for r in others) or "no other device in the household"
        return dict(out, why=f"no idle capable device ({said})")
    name = pick["name"]
    tree = None
    try:
        tree, key = L.export_tree(repo)
        t0 = time.time()
        jid, said, t = (send or W.send)("run", name, str(tree), project, argv=["admit", "suite", "--report", REPORT],
                                         artefacts=[REPORT], wall=WALL_MIN)
    except Exception as e:  # noqa: BLE001 - fail closed: the suite runs here
        return dict(out, why=f"sending to {name} failed: {type(e).__name__}: {e}")
    finally:
        if tree is not None:
            shutil.rmtree(tree.parent, ignore_errors=True)
    if said != "sent":
        return dict(out, why=f"{name} did not take it: {said}")
    say(f"OFFLOAD  the suite went to {name} ({pick['os']}), job {jid}; waiting up to {wait_s / 60:.0f} min")
    e = (wait_fn or jobs.wait)(jid, wait_s)
    r = (result_fn or W.verified_result)(jid, project, t["digest"]) if e else None
    if r is None:
        return dict(out, why=f"{name}: no result signed by a pinned worker key within {wait_s / 60:.0f} min")
    arrived = [(n, ok, where) for n, ok, where in (check_fn or W.artefacts_check)(r) if n == REPORT]
    if not arrived or not arrived[0][1]:
        return dict(out, why=f"{name} said {r['status']} but its report did not arrive with the signed sha256 "
                             f"({r['evidence'].splitlines()[0][:100] if r.get('evidence') else 'no evidence'})")
    try:
        rep = read_report(arrived[0][2])
    except ValueError as e:
        return dict(out, why=f"{name}'s report: {e}")
    status, said = judge(rep)
    if status == "untrusted":
        return dict(out, why=f"{name}: {said}")
    if status == "pass" and r["status"] != "pass":
        return dict(out, why=f"{name}'s report passed but its result says {r['status']}: "
                             f"{r['evidence'].splitlines()[0][:100]}")
    here, unmapped = local_part(rep, repo) if status == "pass" else ([], [])
    if unmapped:
        return dict(out, why=f"{name} skipped {len(unmapped)} test(s) that map to no test file here "
                             f"(first: {unmapped[0][:80]}): the full suite runs here")
    (record or testcost.record)(f"full-suite@{r.get('os') or pick['os']}", status, time.time() - t0, key,
                                f"offloaded from {me} to {name}, job {jid}: {said}")
    return {"status": status, "why": said, "where": name, "local": here,
            "report": {k: v for k, v in rep.items() if k != "skipped"} | {"skipped": len(rep["skipped"])},
            "job": jid, "key": key}


def hook(argv):
    import argparse
    ap = argparse.ArgumentParser(prog="python -m fieldkit.suite_offload hook")
    ap.add_argument("--tree", default=".")
    ap.add_argument("--project", default="Gorilla.Fieldkit")
    a = ap.parse_args(argv)
    r = offload(a.tree, a.project)
    if r["status"] == "pass":
        print(f"OFFLOAD  PASS on {r['where']}: {r['why']}; {len(r['local'])} test file(s) it skipped run here",
              file=sys.stderr)
        print("\n".join(r["local"]))
        return 0
    if r["status"] == "fail":
        print(f"OFFLOAD  FAIL on {r['where']}: {r['why']}", file=sys.stderr)
        for c, n in r["report"]["failures"][:40]:
            print(f"  FAILED {c}::{n}", file=sys.stderr)
        return 1
    print(f"OFFLOAD  the full suite runs here: {r['why']}", file=sys.stderr)
    return LOCAL


def main(argv):
    """fieldkit orchestrate offload [--tree REPO] [--project P] [--json] (from orchestrate.main)."""
    import argparse
    ap = argparse.ArgumentParser(prog="fieldkit orchestrate offload", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tree", default=".", help="the git checkout whose tree is tested")
    ap.add_argument("--project", default="Gorilla.Fieldkit", help="the repository the mandate names")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    r = offload(a.tree, a.project, say=print)
    if a.json:
        print(json.dumps(r, indent=1, ensure_ascii=False))
    else:
        for f in r["local"]:
            print(f"  RUN HERE {f}")
        print(f"VERDICT  {r['status'].upper()} on {r['where']}: {r['why']}")
        print("NEXT: " + ("python -m pytest " + " ".join(r["local"]) if r["status"] == "pass" and r["local"] else
                          "fix the failed tests" if r["status"] == "fail" else "python -m pytest (the full suite here)"
                          if r["status"] == "local" else "nothing"))
    return {"pass": 0, "fail": 3}.get(r["status"], LOCAL)


if __name__ == "__main__":
    if sys.argv[1:2] == ["hook"]:
        sys.exit(hook(sys.argv[2:]))
    sys.exit(main(sys.argv[1:]))
