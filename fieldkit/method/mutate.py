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
import json
import os
import re
import shutil
import signal
import subprocess
import time
from pathlib import Path

import yaml

from . import emit


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _msys_roots():
    """Where Git for Windows lives, as Git Bash writes it into a rewritten argument (C:/Program Files/Git)."""
    roots = set()
    for exe in ("git", "bash"):
        w = shutil.which(exe)
        if w:
            p = Path(w).resolve()
            for up in (p.parent, p.parent.parent, p.parent.parent.parent):
                if (up / "usr" / "bin").is_dir():
                    roots.add(str(up).replace("\\", "/"))
    if os.name == "nt":
        roots.add("C:/Program Files/Git")
    return sorted(roots, key=len, reverse=True)


def msys_rewritten(arg, text, roots=None, guess=True):
    """Git Bash rewrites an argument that starts with / into a Windows path before Python sees it:
    '"/usr/share/x.sh", 0o755' arrives as '"C:/Program Files/Git/usr/share/x.sh", 0o755' (2026-10-25: a swap
    refused as 'occurs 0 times', fixed by MSYS_NO_PATHCONV=1). -> the argument as it was typed, when it holds a Git
    root the file does not (or, with guess, when cutting a drive path's first folders gives text the file holds);
    else None."""
    if arg in text:
        return None
    for root in (roots if roots is not None else _msys_roots()):
        for r in {root, root.replace("/", "\\")}:
            if r in arg and r not in text:
                return arg.replace(r, "")
    if guess:                                             # a Git installed somewhere else: try each folder cut
        for m in re.finditer(r"[A-Za-z]:/", arg):
            rest = arg[m.start():]
            for k, c in enumerate(rest):
                if c == "/" and k > 2 and (arg[:m.start()] + rest[k:]) in text:
                    return arg[:m.start()] + rest[k:]
    return None


def msys_refusal(m, text, roots=None):
    """-> a refused result when Git Bash rewrote the OLD or the NEW text of a swap, else None."""
    for which, arg, guess in (("OLD", m["swap"][0], True), ("NEW", m["swap"][1], False)):
        typed = msys_rewritten(arg, text, roots, guess)
        if typed is not None:
            return {"verdict": "refused", "file": str(m["file"]), "msys": True,
                    "detail": f"Git Bash rewrote the {which} text: typed {typed!r}, arrived as {arg!r}. Run it "
                              "again with MSYS_NO_PATHCONV=1 in front (MSYS_NO_PATHCONV=1 fieldkit mutate ...), "
                              "from PowerShell, or with the swaps in a --plan file"}
    return None


def _decode(data):
    """Child output as UTF-8, never the locale: text=True used cp1252 on Windows and a byte it leaves undefined
    (0x81 in a Go test's UTF-8) crashed the whole plan (2026-10-26). Bad bytes become U+FFFD, never an error."""
    return (data or b"").decode("utf-8", errors="replace").replace("\r\n", "\n")


def run_test(test, cwd=None, timeout=1800):
    try:
        r = subprocess.run(test, capture_output=True, timeout=timeout, cwd=cwd)
    except subprocess.TimeoutExpired:
        return None, "the test timed out"
    except OSError as e:
        return None, str(e)
    return r.returncode == 0, (_decode(r.stdout) + _decode(r.stderr))[-1500:]


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


def mutate(plan, test, cwd=None, timeout=1800, roots=None):
    # a swap Git Bash rewrote is refused before anything runs: the test result would be about text nobody typed.
    # Only swaps from the command line: a --plan file is read by Python, never rewritten.
    rewritten = [r for r in (msys_refusal(m, Path(m["file"]).read_text(encoding="utf-8"), roots)
                             for m in plan if m.get("from_argv")) if r]
    if rewritten:
        return {"ok": False, "baseline": "not run", "results": rewritten, "caught": 0, "survived": 0,
                "refused": len(rewritten), "next": "MSYS_NO_PATHCONV=1 in front of the command"}
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


def proof_dir():
    """Where mutate proofs live: the checkout's shared state/ (a model may not write there: hard line 3)."""
    from ..core.settings import state_root
    return Path(os.environ.get("FIELDKIT_MUTATE_PROOFS") or (state_root(Path.cwd()) / "state" / "mutate-proofs"))


def write_proof(r, plan, test):
    """2026-10-28 (owner: a headless run may change a hook or guard for an approved plan item "with tests that prove
    the check still fails"): the record a commit of a guard file is checked against. Per file: the sha256 of its
    content (restored, so the content that is committed) and whether every mutation of it was caught. -> the path."""
    files = {}
    for m, res in zip(plan, r.get("results") or []):
        f = Path(m["file"]).resolve()                 # as the plan is read: from this process's folder
        e = files.setdefault(str(f), {"sha256": _sha(f.read_bytes()), "caught": 0, "other": 0})
        e["caught" if res.get("verdict") == "caught" else "other"] += 1
    if not files:
        return None
    d = proof_dir()
    d.mkdir(parents=True, exist_ok=True)
    rec = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "test": " ".join(test), "baseline": r.get("baseline"),
           "files": files}
    out = d / f"{_sha(repr(sorted((k, v['sha256']) for k, v in files.items())).encode())[:16]}.json"
    out.write_text(json.dumps(rec, indent=1), encoding="utf-8")
    return out


def proven(path, data):
    """-> True when a mutate proof covers `path` with this exact content: at least one mutation, every one caught."""
    key, sha = str(Path(path).resolve()), _sha(data)
    for f in proof_dir().glob("*.json") if proof_dir().is_dir() else []:
        try:
            e = (json.loads(f.read_text(encoding="utf-8")).get("files") or {}).get(key)
        except (OSError, ValueError):
            continue
        if e and e.get("sha256") == sha and e.get("caught", 0) > 0 and not e.get("other"):
            return True
    return False


def _lines(d):
    if d.get("baseline") == "failed":
        return ["REFUSED: the test fails on the untouched code", d["detail"][-400:]]
    out = []
    for r in d["results"]:
        tag = {"caught": "CAUGHT  ", "survived": "SURVIVED", "refused": "REFUSED "}[r["verdict"]]
        what = r["detail"] if r.get("msys") else r.get("why") or (
            f"{r['swap'][0]!r} -> {r['swap'][1]!r}" if r.get("swap") else r.get("detail", ""))
        out.append(f"{tag} {Path(r['file']).name}: {what}")
    out.append(f"{d['caught']} caught, {d['survived']} survived, {d['refused']} refused")
    return out


def main(argv=None, prog="fieldkit mutate"):
    argv = list(argv or [])
    # --help needs no command after --: `fieldkit cards check` asks every tool for its help (2026-10-18: refused)
    if "--" not in argv and any(x in ("-h", "--help") for x in argv):
        argv = argv + ["--"]
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
        plan = [{"file": a.file, "swap": a.swap, "count": a.count, "from_argv": True}]
    else:
        print("REFUSED: give FILE --swap OLD NEW, or --plan FILE")
        return 2
    for m in plan:
        if not Path(m.get("file", "")).is_file() or len(m.get("swap") or []) != 2:
            print(f"REFUSED: bad mutation {m!r}")
            return 2
    from ..core import preflight               # the one run guard (2026-10-26); a STOP refuses with exit 2
    from .. import admission                    # a heavy job: it waits for a slot of the machine-wide gate (stage O4)
    with admission.admitted("mutate", f"mutate {Path(plan[0]['file']).name}"):
        r = preflight.call("fieldkit mutate", "test", mutate, plan, test, a.cwd, a.timeout,
                           refused={"ok": False, "baseline": "not run", "results": [], "caught": 0, "survived": 0,
                                    "refused": 0})
    if r.get("baseline") == "passed":
        r["proof"] = str(write_proof(r, plan, test) or "")
    emit(r, a.json, _lines)
    return 0 if r["ok"] else (2 if r.get("baseline") in ("failed", "not run") else 3)
