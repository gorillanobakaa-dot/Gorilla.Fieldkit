"""Prove a test can fail (docs/METHODS.md #2): break a rule on purpose, run the test, put the file back.

    fieldkit mutate FILE --swap "!ok || u" "ok && u" -- go test ./internal/llm/agent/ -run Local
    fieldkit mutate --plan mutations.yaml -- python -m pytest -q tests/test_x.py

A test that passes whatever the code does proves nothing. For each mutation:

  1. the test command must PASS on the untouched file first (otherwise a failure proves nothing either);
  2. the text to swap must occur exactly once (or --count times): an ambiguous swap is refused, never guessed;
  3. the file is changed, the test runs, and the file is put back byte for byte - also on Ctrl+C or a crash;
  4. verdict: CAUGHT (the test failed, as it should) or SURVIVED (the test passed with the rule broken: the test
     does not hold that rule).

A plan file holds several mutations: [{file: PATH, swap: [OLD, NEW], why: "what rule this breaks"}].
Exit 0 when every mutation was caught, 3 when one survived, 2 for bad input or a test that fails untouched.
"""
import argparse
import hashlib
import signal
import subprocess
from pathlib import Path

import yaml

from . import emit


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def run_test(test, cwd=None, timeout=1800):
    try:
        r = subprocess.run(test, capture_output=True, text=True, timeout=timeout, cwd=cwd)
    except subprocess.TimeoutExpired:
        return None, "the test timed out"
    except OSError as e:
        return None, str(e)
    return r.returncode == 0, (r.stdout + r.stderr)[-1500:]


def mutate_once(file, old, new, test, count=1, cwd=None, timeout=1800):
    """-> {verdict: caught|survived|refused, ...}. The file is always restored."""
    p = Path(file)
    original = p.read_bytes()
    text = original.decode("utf-8")
    n = text.count(old)
    if n != count:
        return {"verdict": "refused", "file": str(p), "found": n,
                "detail": f"{old!r} occurs {n} time(s) in {p.name}, expected {count}: make the swap unambiguous"}
    mutated = text.replace(old, new).encode("utf-8")

    def restore(*_):
        p.write_bytes(original)

    def interrupted(*_):
        restore()
        raise KeyboardInterrupt

    prev = {}
    for s in (signal.SIGINT, signal.SIGTERM):
        try:
            prev[s] = signal.signal(s, interrupted)       # put the file back on Ctrl+C too
        except (ValueError, OSError):                       # not the main thread: the finally still restores
            pass
    try:
        p.write_bytes(mutated)
        passed, out = run_test(test, cwd, timeout)
    finally:
        restore()
        for s, h in prev.items():
            signal.signal(s, h)
    assert _sha(p.read_bytes()) == _sha(original), f"{p} was not restored"
    if passed is None:
        return {"verdict": "refused", "file": str(p), "detail": out}
    return {"verdict": "survived" if passed else "caught", "file": str(p), "swap": [old, new],
            "test_output": "" if not passed else out[-600:]}


def mutate(plan, test, cwd=None, timeout=1800):
    ok, out = run_test(test, cwd, timeout)
    if not ok:
        return {"ok": False, "baseline": "failed", "detail": out[-800:], "results": [],
                "next": "make the test pass on the untouched code first: a failure proves nothing yet"}
    results = []
    for m in plan:
        old, new = m["swap"]
        r = mutate_once(m["file"], old, new, test, m.get("count", 1), cwd, timeout)
        r["why"] = m.get("why", "")
        results.append(r)
    survived = [r for r in results if r["verdict"] == "survived"]
    refused = [r for r in results if r["verdict"] == "refused"]
    return {"ok": not survived and not refused, "baseline": "passed", "results": results,
            "caught": sum(r["verdict"] == "caught" for r in results), "survived": len(survived),
            "refused": len(refused),
            "next": ("write a test that fails when this rule breaks: " + "; ".join(
                r.get("why") or f"{Path(r['file']).name}: {r['swap'][0]!r}" for r in survived)) if survived else
            ("fix the refused swaps" if refused else "")}


def _lines(d):
    if d.get("baseline") == "failed":
        return ["REFUSED: the test fails on the untouched code", d["detail"][-400:]]
    out = []
    for r in d["results"]:
        tag = {"caught": "CAUGHT  ", "survived": "SURVIVED", "refused": "REFUSED "}[r["verdict"]]
        what = r.get("why") or (f"{r['swap'][0]!r} -> {r['swap'][1]!r}" if r.get("swap") else r.get("detail", ""))
        out.append(f"{tag} {Path(r['file']).name}: {what}")
    out.append(f"{d['caught']} caught, {d['survived']} survived, {d['refused']} refused")
    return out


def main(argv=None, prog="fieldkit mutate"):
    argv = list(argv or [])
    if "--" not in argv:
        print("REFUSED: give the test command after --, e.g. fieldkit mutate F --swap A B -- pytest -q")
        return 2
    i = argv.index("--")
    own, test = argv[:i], argv[i + 1:]
    ap = argparse.ArgumentParser(prog=prog, description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file", nargs="?")
    ap.add_argument("--swap", nargs=2, metavar=("OLD", "NEW"))
    ap.add_argument("--count", type=int, default=1)
    ap.add_argument("--plan", help="YAML list of {file, swap: [OLD, NEW], why}")
    ap.add_argument("--cwd")
    ap.add_argument("--timeout", type=float, default=1800)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(own)
    if not test:
        print("REFUSED: no test command after --")
        return 2
    if a.plan:
        plan = yaml.safe_load(Path(a.plan).read_text(encoding="utf-8")) or []
    elif a.file and a.swap:
        plan = [{"file": a.file, "swap": a.swap, "count": a.count}]
    else:
        print("REFUSED: give FILE --swap OLD NEW, or --plan FILE")
        return 2
    for m in plan:
        if not Path(m.get("file", "")).is_file() or len(m.get("swap") or []) != 2:
            print(f"REFUSED: bad mutation {m!r}")
            return 2
    r = mutate(plan, test, a.cwd, a.timeout)
    emit(r, a.json, _lines)
    return 0 if r["ok"] else (2 if r.get("baseline") == "failed" else 3)
