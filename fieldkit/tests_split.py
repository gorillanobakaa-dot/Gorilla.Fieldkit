# Version: 1.0.0 · updated 26-11-02
"""The tests that cover a change, split across every free device and all of them run (HARNESS-PLAN R15).

    fieldkit testcost split [--json]                     where each covering test file would run (nothing is sent)
    python -m fieldkit.tests_split start --state FILE    the pre-commit hook: sends the other shares, prints this
                                                         laptop's share (one file per line) and writes FILE
    python -m fieldkit.tests_split finish --state FILE   the hook, after the local share passed: waits for the shares,
                                                         prints the files that must run here after all (exit codes below)
    python -m fieldkit.tests_split done --state FILE     the hook, when every share passed: records the whole set

WHY (owner, 2026-11-02): a fixed 15-file cap does not scale to 20-50 machines; above it the hook ran NOTHING ("CI runs
them, not this laptop") although a change to cli.py is covered by 59-74 test files. The cap is only this laptop's SHARE.

THE RULES (fixed; no model and no person in the path):
  1. DEVICES: this computer plus every other device that answered `load`, is IDLE and has python, pytest and git
     (orchestrate_load.load_all / idle / capable, any OS).
  2. SHARE: this laptop takes at most `local_test_share` (fieldkit.local.json; default FIELDKIT_LOCAL_TEST_CAP, 15,
     divided by the number of devices, at least 1). The other files go to the other devices, largest first to the least
     loaded one (cost = seconds measured in an earlier JUnit report, else the file's line count / 40).
  3. WINDOWS ONLY: a file marked windows_only (or skipped unless win32) stays on a Windows device; if this laptop is
     the only one, it runs here whatever the share.
  4. TRUST (same as suite_offload): a share passes only on a result signed by a pinned worker key, with its JUnit
     report arrived under the signed sha256, >= 1 pass and 0 failures. A failure refuses the commit and names the tests.
  5. NEVER DROPPED: a share that cannot be sent, does not answer in time, or comes back untrusted runs HERE; so does
     every test file in which the device skipped a test.
  6. NO OTHER DEVICE IDLE and more files than the share: the only case "CI runs them" is said, with the number, and the
     fallback is recorded in the testcost log as a refusal-class row (check split-fallback).
  7. RECORD: each passed share is recorded as `fast:<hash of its files>` so a passed set is never run again.

FIELDKIT_SPLIT_LOCAL=1 in the environment: this laptop alone for that run (as FIELDKIT_SUITE_LOCAL=1 for offload).

Exit codes of `finish`: 0 all shares passed (stdout: files to run here after all); 1 a share failed (commit refused).
"""
import hashlib
import json
import math
import os
import re
import shutil
import sys
import time
from pathlib import Path

REPORT = "fk-split-report.xml"
WAIT_S = 20 * 60
WIN_ONLY = re.compile(r"windows_only|skipif\([^)]*(?:platform\s*!=\s*[\"']win|os\.name\s*!=\s*[\"']nt)")


def cap():
    return int(os.environ.get("FIELDKIT_LOCAL_TEST_CAP", "15"))


def local_share(n_devices, setting=None):
    """This laptop's share: the setting `local_test_share`, else the cap divided by the devices (>= 1)."""
    if setting is None:
        try:
            from .core import settings
            setting = settings.local_settings().get("local_test_share")
        except Exception:  # noqa: BLE001 - no settings file: the default
            setting = None
    if setting:
        return max(1, int(setting))
    return max(1, math.ceil(cap() / max(1, n_devices)))


def windows_only(repo, rel):
    try:
        return bool(WIN_ONLY.search((Path(repo) / rel).read_text(encoding="utf-8", errors="replace")))
    except OSError:
        return False


def durations_path():
    from . import testcost
    return testcost.state_dir() / "file-seconds.json"


def load_durations():
    try:
        return json.loads(durations_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def cost(repo, rel, durations):
    if rel in durations:
        return float(durations[rel])
    try:
        return max(1.0, len((Path(repo) / rel).read_text(encoding="utf-8", errors="replace").splitlines()) / 40)
    except OSError:
        return 1.0


def assign(tests, rows, repo=".", setting=None, durations=None):
    """-> {"shares": {device: [files]}, "me": name, "share": int, "fallback": bool, "ci": [files]}.
    tests: relative paths. rows: orchestrate_load.load_all(). Every test lands in exactly one share, or in "ci" (rule 6)."""
    from . import orchestrate_load as L
    durations = load_durations() if durations is None else durations
    me = next((r for r in rows if r.get("me")), {"name": "here", "os": ""})
    others = [r for r in rows if not r.get("me") and r.get("ok") and L.idle(r) and L.capable(r, me.get("os"), True)]
    share = local_share(1 + len(others), setting)
    out = {"shares": {me["name"]: []}, "me": me["name"], "share": share, "fallback": False, "ci": []}
    if not others:
        if len(tests) <= cap():
            out["shares"][me["name"]] = sorted(tests)
        else:
            out.update(fallback=True, ci=sorted(tests))
        return out
    for r in others:
        out["shares"][r["name"]] = []
    load = dict.fromkeys(out["shares"], 0.0)
    win = {r["name"] for r in others if r.get("os") == "WINDOWS"} | ({me["name"]} if me.get("os") == "WINDOWS" else set())
    order = sorted(tests, key=lambda t: (-cost(repo, t, durations), t))
    for t in order:
        cands = [n for n in out["shares"] if (n in win or not windows_only(repo, t))]
        cands = [n for n in cands if n != me["name"] or len(out["shares"][n]) < share] or \
                ([me["name"]] if windows_only(repo, t) and not (win - {me["name"]}) else cands)
        n = min(cands or [me["name"]], key=lambda c: (load[c], c))
        out["shares"][n].append(t)
        load[n] += cost(repo, t, durations)
    for n in out["shares"]:
        out["shares"][n].sort()
    return out


def check_name(files):
    return "fast:" + hashlib.sha1("\n".join(sorted(files)).encode()).hexdigest()[:12]


def _rel(repo, paths):
    return [os.path.relpath(p, repo).replace("\\", "/") for p in paths]


def start(repo=".", state=None, say=None, rows=None, load_fn=None, send=None, tests=None, project="Gorilla.Fieldkit"):
    """-> {local: [files to run here now], pending: [...], back: [files that could not be sent], ...}; writes state."""
    from . import orchestrate_load as L
    from . import orchestrate_work as W
    from . import testcost
    say = say or (lambda s: print(s, file=sys.stderr, flush=True))
    tests = _rel(repo, testcost.plan(repo, paths=testcost.staged_files(repo))["tests"]) if tests is None else tests
    res = {"local": [], "pending": [], "back": [], "ci": [], "all": sorted(tests), "key": None}
    if not tests:
        return res
    key = testcost.tree_key(repo)
    res["key"] = key
    if testcost.seen(check_name(tests), key):
        say(f"testcost: these {len(tests)} test file(s) passed on this exact tree: not run again")
        res["all"] = []
        return res
    try:
        if os.environ.get("FIELDKIT_SPLIT_LOCAL") == "1":
            raise RuntimeError("FIELDKIT_SPLIT_LOCAL=1")
        rows = (load_fn or L.load_all)() if rows is None else rows
    except Exception as e:  # noqa: BLE001 - no link: this laptop alone
        say(f"split: no load report ({type(e).__name__}): this laptop alone")
        rows = [{"name": "here", "me": True, "ok": True, "os": ""}]
    a = assign(tests, rows, repo)
    me = a["me"]
    if a["fallback"]:
        res["ci"] = a["ci"]
        why = f"{len(tests)} test files cover the staged files (> {cap()}): CI runs them, not this laptop"
        say("split: no other device is idle")
        say(f"testcost: {why}")
        testcost.record("split-fallback", "refused", 0, key, why)
        return res
    res["local"] = a["shares"][me]
    remote = {n: f for n, f in a["shares"].items() if n != me and f}
    names = {r["name"]: r for r in rows}
    say("split: " + ", ".join(f"{n} {len(f)}" for n, f in a["shares"].items()) + f" of {len(tests)} test files "
        f"(this laptop's share {a['share']})")
    tree = None
    try:
        if remote:
            tree, _ = L.export_tree(repo)
        for n, files in remote.items():
            try:
                jid, said, t = (send or W.send)("run", n, str(tree), project,
                                                argv=["admit", "suite", "--report", REPORT, *files],
                                                artefacts=[REPORT], wall=max(5, WAIT_S // 60))
            except Exception as e:  # noqa: BLE001 - fail closed: the share runs here
                jid, said, t = None, f"{type(e).__name__}: {e}", None
            if said != "sent":
                say(f"split: {n} did not take its {len(files)} file(s) ({said}): they run here")
                res["back"] += files
                continue
            say(f"split: {len(files)} file(s) sent to {n} ({names[n].get('os')}), job {jid}")
            res["pending"].append({"device": n, "job": jid, "digest": t["digest"], "files": files,
                                   "t0": time.time(), "os": names[n].get("os")})
    finally:
        if tree is not None:
            shutil.rmtree(Path(tree).parent, ignore_errors=True)
    if state:
        Path(state).write_text(json.dumps(res), encoding="utf-8")
    return res


def finish(res, repo=".", say=None, wait_s=WAIT_S, wait_fn=None, result_fn=None, check_fn=None, record=None,
           project="Gorilla.Fieldkit"):
    """-> {status: pass|fail, failed: [(device, classname, name)], run_here: [files], passed: [device]}."""
    from . import orchestrate_work as W
    from . import suite_offload as S
    from . import testcost
    from .mandate import jobs
    say = say or (lambda s: print(s, file=sys.stderr, flush=True))
    record = record or testcost.record
    out = {"status": "pass", "failed": [], "run_here": list(res.get("back") or []), "passed": []}
    end = time.monotonic() + wait_s
    dur = load_durations()
    for p in res.get("pending") or []:
        n, jid, files = p["device"], p["job"], p["files"]
        left = max(1, end - time.monotonic())
        e = (wait_fn or jobs.wait)(jid, left)
        r = (result_fn or W.verified_result)(jid, project, p["digest"]) if e else None
        why = None
        rep = None
        if r is None:
            why = "no result signed by a pinned worker key in time"
        else:
            arrived = [x for x in (check_fn or W.artefacts_check)(r) if x[0] == REPORT]
            if not arrived or not arrived[0][1]:
                why = "its report did not arrive with the signed sha256"
            else:
                try:
                    rep = S.read_report(arrived[0][2])
                except ValueError as ex:
                    why = str(ex)
        if rep is not None:
            status, said = S.judge(rep)
            if status == "fail":
                say(f"split: FAIL on {n}: {said}")
                out["status"] = "fail"
                out["failed"] += [(n, c, t) for c, t in rep["failures"]]
                continue
            if status == "untrusted":
                why = said
            elif r["status"] != "pass":
                why = f"its report passed but its result says {r['status']}"
        if why:
            say(f"split: {n}: {why}: its {len(files)} file(s) run here")
            out["run_here"] += files
            continue
        again, unmapped = S.local_part(rep, repo)
        if unmapped:
            say(f"split: {n} skipped tests that map to no file here: its {len(files)} file(s) run here")
            out["run_here"] += files
            continue
        out["run_here"] += [f for f in again if f in files]
        out["passed"].append(n)
        _note_durations(rep, dur, repo)
        record(check_name(files), "pass", time.time() - p["t0"], res.get("key"),
               f"{len(files)} file(s) on {n}, job {jid}: {said}")
        say(f"split: PASS on {n}: {said} ({len(files)} file(s); {len(again)} it skipped run here)")
    try:
        durations_path().write_text(json.dumps(dur), encoding="utf-8")
    except OSError:
        pass
    out["run_here"] = sorted(set(out["run_here"]))
    return out


def _note_durations(rep, dur, repo):
    """Per-file seconds for the next split (JUnit testcase time summed per file)."""
    from . import suite_offload as S
    per = {}
    for c, t in rep.get("times") or []:
        f = S.test_file(c, repo)
        if f:
            per[f] = per.get(f, 0.0) + t
    dur.update({f: round(t, 2) for f, t in per.items()})


def hook(argv):
    import argparse
    ap = argparse.ArgumentParser(prog="python -m fieldkit.tests_split")
    ap.add_argument("what", choices=["start", "finish", "done"])
    ap.add_argument("--state", required=True)
    ap.add_argument("--repo", default=".")
    a = ap.parse_args(argv)
    if a.what == "start":
        r = start(a.repo, a.state)
        Path(a.state).write_text(json.dumps(r), encoding="utf-8")
        if r["local"]:
            print("\n".join(r["local"]))
        return 0
    res = json.loads(Path(a.state).read_text(encoding="utf-8"))
    if a.what == "done":
        # every share passed: the whole covering set is recorded, so `start` finds it next time (tests once, R7)
        if res.get("all") and res.get("key"):
            from . import testcost
            testcost.record(check_name(res["all"]), "pass", 0, res["key"], f"{len(res['all'])} file(s), all shares passed")
        return 0
    r = finish(res, a.repo)
    if r["status"] == "fail":
        for d, c, t in r["failed"][:40]:
            print(f"  FAILED on {d}: {c}::{t}", file=sys.stderr)
        return 1
    if r["run_here"]:
        print("\n".join(r["run_here"]))
    return 0


def main(argv, prog="fieldkit testcost split"):
    """fieldkit testcost split [--json] [--repo R]: the assignment, nothing is sent."""
    import argparse
    from . import orchestrate_load as L
    from . import testcost
    ap = argparse.ArgumentParser(prog=prog, description=__doc__.split("\n\n")[0])
    ap.add_argument("--repo", default=".")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    tests = _rel(a.repo, testcost.plan(a.repo, paths=testcost.staged_files(a.repo))["tests"])
    try:
        rows = L.load_all()
    except Exception:  # noqa: BLE001 - no link: this laptop alone
        rows = [{"name": "here", "me": True, "ok": True, "os": ""}]
    r = assign(tests, rows, a.repo)
    if a.json:
        print(json.dumps(r, indent=1))
    else:
        print(f"{len(tests)} covering test file(s); this laptop's share {r['share']}")
        for n, f in r["shares"].items():
            print(f"  {n}: {len(f)} file(s)")
        if r["fallback"]:
            print(f"  no other device idle: CI runs all {len(r['ci'])} (recorded as split-fallback)")
        print("NEXT: " + ("git commit (the hook sends the shares)" if tests else "nothing to test"))
    return 0


if __name__ == "__main__":
    sys.exit(hook(sys.argv[1:]))
