# Version: 1.1.0 · updated 26-10-18 (reachable as `fieldkit flaky`)
"""fieldkit flaky: a test that fails sometimes is measured, explained and fixed, never only logged.

2026-10-16: tests/test_peers.py was written to state/flaky-tests.log at 10:58 and again at 12:01 and nothing read the
log; by evening it refused three commits. The cause took a hand-made loop (run the file 10 times), a pytest warning
(the real error was an exception in a thread, shown only as PytestUnhandledThreadExceptionWarning) and reading the
code: on Windows every test in one pytest process named its pipe after the same pid. Each of those steps is here.

    fieldkit flaky repeat TEST... [-n 10]   failure rate over n fresh runs -> REAL (every run) | FLAKY k/n | STABLE;
                                            the first failure and any swallowed thread exception, and the scan's
                                            findings for the test's file
    fieldkit flaky log [-n 5]               every test in state/flaky-tests.log, measured again (exit 3 while any is
                                            still flaky: a logged flake is a defect with a test, not a note)
    fieldkit flaky scan [PATH...]           names tests can collide on: fixed named pipes, fixed TCP ports, Global\\
                                            mutexes, names built from the pid (os.getpid() in a pipe/socket/lock name)
"""
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOG = ROOT / "state" / "flaky-tests.log"

_THREAD = re.compile(r"PytestUnhandledThreadExceptionWarning: Exception in thread [^\n]*\n(.*?)(?:\n\n|\Z)", re.S)
_ERR = re.compile(r"^E\s+.*$", re.M)

# (kind, regex over source): names that live in the whole operating system, not in a test's own folder
GLOBAL_NAMES = (
    ("named pipe", re.compile(r"""\\\\\\\\\.\\\\pipe\\\\|r?["']\\\\\\\\\.\\\\pipe""")),
    ("named pipe", re.compile(r"\\\\\.\\pipe\\")),
    ("fixed TCP port", re.compile(r"""\.bind\(\s*\(\s*["'][\w.:]*["']\s*,\s*([1-9]\d{2,4})\s*\)""")),
    ("fixed TCP port", re.compile(r"""\b(?:port|PORT)\s*=\s*([1-9]\d{3,4})\b""")),
    ("global mutex/event", re.compile(r"""["']Global\\\\""")),
    ("pid-derived name", re.compile(r"""(?:pipe|sock|lock|mutex|endpoint)\w*\s*[=(+].{0,40}os\.getpid\(\)""", re.I)),
)


def run_once(test, cwd=ROOT, timeout=900):
    bt = tempfile.mkdtemp(prefix="fkflaky-")
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--basetemp", bt, "-rw",
                        test], cwd=str(cwd), capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=timeout)
    out = r.stdout + r.stderr
    return {"ok": r.returncode == 0, "rc": r.returncode, "errors": _ERR.findall(out)[:6],
            "thread": [m.strip()[-600:] for m in _THREAD.findall(out)][:2]}


def verdict(fails, n):
    return "STABLE" if not fails else ("REAL" if fails == n else f"FLAKY {fails}/{n}")


def scan(paths=None):
    """-> [{file, line, kind, text}] OS-wide names in the code under test and in the tests."""
    paths = [Path(p) for p in (paths or [ROOT / "fieldkit", ROOT / "tests"])]
    out = []
    for base in paths:
        files = [base] if base.is_file() else sorted(base.rglob("*.py"))
        for f in files:
            try:
                lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for n, line in enumerate(lines, 1):
                if line.lstrip().startswith("#"):
                    continue
                for kind, rx in GLOBAL_NAMES:
                    if rx.search(line):
                        out.append({"file": str(f.relative_to(ROOT) if f.is_relative_to(ROOT) else f), "line": n,
                                    "kind": kind, "text": line.strip()[:120]})
                        break
    return out


def _module_of(test):
    """tests/test_x.py::name -> the fieldkit modules that test file imports (to scan the code it exercises)."""
    f = ROOT / test.split("::")[0]
    mods = set()
    try:
        for m in re.finditer(r"^\s*from fieldkit(?:\.([\w.]+))? import ([\w, ]+)", f.read_text(encoding="utf-8"), re.M):
            pkg = (m.group(1) or "").replace(".", "/")
            for name in m.group(2).split(","):
                p = ROOT / "fieldkit" / pkg / f"{name.strip()}.py"
                if p.is_file():
                    mods.add(p)
                d = ROOT / "fieldkit" / pkg / name.strip()
                if d.is_dir():
                    mods.update(d.glob("*.py"))
    except OSError:
        pass
    return sorted(mods)


def repeat(tests, n=10, run=run_once):
    rows = []
    for t in tests:
        runs = [run(t) for _ in range(n)]
        fails = [r for r in runs if not r["ok"]]
        first = fails[0] if fails else None
        hints = scan([ROOT / t.split("::")[0], *_module_of(t)]) if fails else []
        rows.append({"test": t, "runs": n, "failed": len(fails), "verdict": verdict(len(fails), n),
                     "errors": first["errors"] if first else [], "thread": first["thread"] if first else [],
                     "collision_hints": hints[:8]})
    return rows


def logged(log=LOG):
    """Unique tests named in the flaky log, newest last."""
    if not Path(log).is_file():
        return []
    seen = {}
    for line in Path(log).read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.search(r"passed when run again: (\S+)", line)
        if m:
            seen.pop(m.group(1), None)
            seen[m.group(1)] = line[:19]
    return list(seen)


def lines(rows):
    out = []
    for r in rows:
        out.append(f"  {r['verdict']:<12} {r['test']}  ({r['failed']} of {r['runs']} runs failed)")
        out += [f"      {e}" for e in r["errors"][:3]]
        for t in r["thread"]:
            out.append("      exception in a thread (pytest shows it only as a warning):")
            out += [f"        {l}" for l in t.splitlines()[-4:]]
        for h in r["collision_hints"]:
            out.append(f"      shared OS name? {h['kind']} at {h['file']}:{h['line']}: {h['text']}")
    bad = [r for r in rows if r["verdict"] != "STABLE"]
    out.append("FLAKY: " + ("none - every test passed every run" if not bad else
                            f"{len(bad)} test(s) not stable; fix the cause (a shared OS name, a timing assumption), then "
                            f"repeat until STABLE"))
    return out


def main(argv=None, prog="fieldkit flaky"):
    import argparse
    ap = argparse.ArgumentParser(prog=prog)
    ap.add_argument("action", choices=["repeat", "log", "scan"])
    ap.add_argument("targets", nargs="*")
    ap.add_argument("-n", type=int, default=None)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.action == "scan":
        found = scan(a.targets or None)
        print(json.dumps(found, indent=1) if a.json else
              "\n".join([f"  {f['kind']:<20} {f['file']}:{f['line']}: {f['text']}" for f in found] +
                        [f"SCAN: {len(found)} OS-wide name(s); a test that uses one needs its own (see tests/test_peers.py reg)"]))
        return 0
    tests = a.targets if a.action == "repeat" else logged()
    if not tests:
        print("FLAKY: nothing to measure" + (" (the log is empty)" if a.action == "log" else ""))
        return 0
    rows = repeat(tests, n=a.n or (10 if a.action == "repeat" else 5))
    print(json.dumps(rows, indent=1) if a.json else "\n".join(lines(rows)))
    return 3 if any(r["verdict"] != "STABLE" for r in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
