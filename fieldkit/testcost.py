"""What the checks cost, and the cheapest set that is enough (fieldkit testcost).

Owner, 2026-10-26: "Test cost audit ... gets incorporated into the harness in the source code and turned into a
deterministic tool with deterministic checks and part of the preflight. We can't afford to waste literally thousands
of dollars for the tests in Api credits." Measured the same night (PLAN.md "2026-10-26: Test cost audit"): an 88-minute
Firefox migration took 61 hours of wall clock; the Fieldkit pre-commit hook runs ~3,300 tests (about 10 minutes) on
EVERY commit, also when the tree it tests has already passed.

    fieldkit testcost key [--repo R]             the working tree's content hash (what the tests actually see)
    fieldkit testcost seen CHECK [--key K]       exit 0 when CHECK already PASSED on exactly this tree (prints when)
    fieldkit testcost record CHECK STATUS SECONDS [--key K] [--note N]   a result: pass | fail | refused | skipped
    fieldkit testcost plan [--base REF]          changed files -> the tests that cover them (the FAST set)
    fieldkit testcost run --fast [--base REF]    run only that set (while iterating; the full suite runs before push)
    fieldkit testcost budget KIND                the expected wall time of KIND from measured history
    fieldkit testcost report [--last N]          test time, re-runs avoided, minutes saved, refusals and what they caught
    fieldkit testcost flags                      the gate tiers the owner decides (all OFF unless the owner said yes)

Deterministic: a key is a git tree hash, a plan is an import scan, a budget is the median of logged runs. Nothing is
guessed and nothing is skipped unless it is provably redundant (the identical tree already passed the identical check).
The gate tiers that WEAKEN a check (docs-only commits without the suite, ...) are flags in fieldkit.local.json under
"testcost"; every one is False until the owner says yes.
"""
import argparse
import ast
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: the tiers the owner decides (2026-10-26 morning list); False = today's behavior
FLAGS = {
    "docs_only_privacy_only": "a commit that changes only documentation runs the privacy scan, not the full suite",
    "full_suite_once_per_tree": "the full suite runs once per tree before push, not on every local commit",
    "leak_gate_once_per_release": "the leak gate runs once on the release build; --only/--quick while fixing",
    "post_install_once": "package checks + post-install run once, on the installed build",
}
DOC_SUFFIXES = {".md", ".rst", ".txt", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}
#: a documentation file inside these trees can still be read by a test (skills, cards): never "docs only"
CODE_TREES = ("fieldkit/", "tests/", "skills/", "hooks/", "kits/")


def state_dir():
    d = Path(os.environ.get("FIELDKIT_TESTCOST_DIR") or ROOT / "state" / "testcost")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _git(repo, *args, env=None):
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env)
    if r.returncode:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()[:200]}")
    return r.stdout


def tree_key(repo="."):
    """The hash of the tree the tests see: tracked + untracked files, .gitignore respected, nothing committed.
    A copy of the index takes `git add -A` so the real index is never touched."""
    git_dir = Path(_git(repo, "rev-parse", "--absolute-git-dir").strip())
    with tempfile.TemporaryDirectory() as td:
        idx = Path(td) / "index"
        if (git_dir / "index").exists():
            shutil.copyfile(git_dir / "index", idx)
        env = dict(os.environ, GIT_INDEX_FILE=str(idx))
        _git(repo, "add", "-A", env=env)
        return _git(repo, "write-tree", env=env).strip()


def _jsonl(name):
    return state_dir() / name


def _append(name, row):
    with open(_jsonl(name), "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _read(name):
    p = _jsonl(name)
    if not p.exists():
        return []
    rows = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def record(check, status, seconds, key=None, note=""):
    row = {"t": time.strftime("%Y-%m-%d %H:%M:%S"), "check": check, "status": status, "seconds": round(float(seconds), 1),
           "key": key or "", "note": note}
    if os.environ.get("FIELDKIT_PARITY_MANIFEST"):      # what it ran on (fieldkit/parity.py, set by the run guard)
        row["manifest"] = os.environ["FIELDKIT_PARITY_MANIFEST"]
    if os.environ.get("FIELDKIT_PARITY_DEFENDER"):     # Defender state on every result, baseline or not
        try:
            row["defender"] = json.loads(os.environ["FIELDKIT_PARITY_DEFENDER"])
        except ValueError:
            pass
    if os.environ.get("FIELDKIT_PARITY_COMPARABLE") == "0":      # machine state off the baseline (Defender)
        row["comparable"] = False
    _append("runs.jsonl", row)
    return row


def seen(check, key):
    """The last PASS of `check` on exactly `key`, or None. Only a pass counts: a failure never lets anything skip."""
    if not key:
        return None
    hit = None
    for r in _read("runs.jsonl"):
        if r.get("check") == check and r.get("key") == key and r.get("status") == "pass":
            hit = r
    return hit


def budget(check, last=20, minimum=3):
    """Median wall time of the last `last` passing runs of `check`, or None with fewer than `minimum` of them."""
    xs = [r["seconds"] for r in _read("runs.jsonl") if r.get("check") == check and r.get("status") == "pass"][-last:]
    return statistics.median(xs) if len(xs) >= minimum else None


def flags():
    """The owner's tier decisions from fieldkit.local.json ("testcost": {...}); every unknown or absent flag is False."""
    out = {k: False for k in FLAGS}
    p = ROOT / "fieldkit.local.json"
    if p.exists():
        try:
            cfg = json.loads(p.read_text(encoding="utf-8")).get("testcost") or {}
        except ValueError:
            cfg = {}
        for k in FLAGS:
            out[k] = cfg.get(k) is True
    return out


def is_docs_only(paths):
    """True when every path is documentation that no test reads: a .md/.txt/picture outside the code trees."""
    paths = [p.replace("\\", "/") for p in paths if p]
    return bool(paths) and all(Path(p).suffix.lower() in DOC_SUFFIXES and not p.startswith(CODE_TREES) for p in paths)


def changed_files(repo=".", base=None):
    """Paths changed against `base` (default: HEAD), staged, unstaged and untracked."""
    out = set()
    if base:
        out |= set(_git(repo, "diff", "--name-only", f"{base}...HEAD").split())
    for line in _git(repo, "status", "--porcelain", "-uall").splitlines():
        p = line[3:].strip().strip('"')
        out.add(p.split(" -> ")[-1])
    return sorted(x for x in out if x)


def staged_files(repo="."):
    return _git(repo, "diff", "--cached", "--name-only").split()


def _module_of(path):
    """fieldkit/a/b.py -> fieldkit.a.b ; fieldkit/a/__init__.py -> fieldkit.a ; anything else -> None."""
    p = Path(path.replace("\\", "/"))
    if p.suffix != ".py" or not p.parts or p.parts[0] != "fieldkit":
        return None
    parts = list(p.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _imports(test_file):
    """Every fieldkit module a test file names in an import (from fieldkit.a import b -> fieldkit.a and fieldkit.a.b)."""
    try:
        tree = ast.parse(Path(test_file).read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return set()
    mods = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            mods |= {a.name for a in n.names if a.name.startswith("fieldkit")}
        elif isinstance(n, ast.ImportFrom) and n.module and n.module.startswith("fieldkit") and not n.level:
            mods.add(n.module)
            mods |= {f"{n.module}.{a.name}" for a in n.names}
    return mods


def plan(repo=".", base=None, paths=None):
    """{"tests": [...], "unmapped": [...], "docs_only": bool}: the tests that cover the changed files.

    A test covers a module when it imports it or a submodule of it (`import fieldkit` alone covers nothing: every test
    has it). A changed test runs itself. A changed module no test imports is matched by name in the test sources (a CLI
    test calls `-m fieldkit lmstudio-log`); still nothing -> "unmapped", which means: the full suite before push."""
    repo = Path(repo)
    paths = changed_files(repo, base) if paths is None else paths
    tests_dir = repo / "tests"
    test_files = sorted(tests_dir.glob("test_*.py")) if tests_dir.is_dir() else []
    imports = {t: _imports(t) for t in test_files}
    chosen, unmapped = set(), []
    for p in paths:
        q = p.replace("\\", "/")
        if q.startswith("tests/") and Path(q).name.startswith("test_") and q.endswith(".py"):
            chosen.add(str(repo / q))
            continue
        if Path(q).suffix.lower() in DOC_SUFFIXES and not q.startswith(CODE_TREES):
            continue
        mod = _module_of(q)
        hits = set()
        if mod and mod != "fieldkit":
            hits = {str(t) for t, ms in imports.items() if any(m == mod or m.startswith(mod + ".") for m in ms)}
        if not hits:
            needles = [Path(q).name] + ([mod.rsplit(".", 1)[-1]] if mod else [])
            needles = [n for n in needles if len(n) > 3 and n not in ("__init__.py", "cli.py", "cli")]
            for t in test_files:
                src = t.read_text(encoding="utf-8", errors="replace")
                if any(re.search(r"\b" + re.escape(n) + r"\b", src) for n in needles):
                    hits.add(str(t))
        if hits:
            chosen |= hits
        else:
            unmapped.append(q)
    return {"tests": sorted(chosen), "unmapped": unmapped, "docs_only": is_docs_only(paths), "changed": paths}


def report(last=200):
    rows = _read("runs.jsonl")[-last:]
    if not rows:
        return "testcost: nothing logged yet"
    by = {}
    for r in rows:
        b = by.setdefault(r.get("check", "?"), {"runs": 0, "seconds": 0.0, "skipped": 0, "saved": 0.0, "refused": 0})
        if r.get("status") == "skipped":
            b["skipped"] += 1
            try:
                b["saved"] += float(r.get("note", "").split("saved=")[1].split()[0])
            except (IndexError, ValueError):
                pass
        else:
            b["runs"] += 1
            b["seconds"] += r.get("seconds", 0)
        if r.get("status") in ("refused", "fail"):
            b["refused"] += 1
    lines = [f"testcost over the last {len(rows)} logged runs:"]
    for k, b in sorted(by.items(), key=lambda kv: -kv[1]["seconds"]):
        lines.append(f"  {k}: {b['runs']} run(s) {b['seconds'] / 60:.1f} min, {b['refused']} refused/failed, "
                     f"{b['skipped']} re-run(s) avoided (~{b['saved'] / 60:.1f} min saved)")
    return "\n".join(lines)


def preflight_check(ctx):
    """The run guard's check (fieldkit/core/preflight.py STEPS): the measured budget of this run, one line, never a stop.
    ctx = {"label", "kind"}; a label with fewer than three logged passes says nothing."""
    b = budget(ctx.get("label", ""))
    if b is None:
        return "ok", ""
    return "ok", f"testcost: '{ctx['label']}' usually takes {b / 60:.1f} min (median of logged passes)"


def main(argv=None, prog="fieldkit testcost"):
    ap = argparse.ArgumentParser(prog=prog, description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    k = sub.add_parser("key")
    k.add_argument("--repo", default=".")
    s = sub.add_parser("seen")
    s.add_argument("check")
    s.add_argument("--key")
    s.add_argument("--repo", default=".")
    r = sub.add_parser("record")
    r.add_argument("check")
    r.add_argument("status", choices=["pass", "fail", "refused", "skipped"])
    r.add_argument("seconds", type=float)
    r.add_argument("--key")
    r.add_argument("--note", default="")
    p = sub.add_parser("plan")
    p.add_argument("--base")
    p.add_argument("--repo", default=".")
    p.add_argument("--json", action="store_true")
    ru = sub.add_parser("run")
    ru.add_argument("--fast", action="store_true", required=True)
    ru.add_argument("--base")
    ru.add_argument("--repo", default=".")
    bu = sub.add_parser("budget")
    bu.add_argument("check")
    rp = sub.add_parser("report")
    rp.add_argument("--last", type=int, default=200)
    sub.add_parser("flags")
    st = sub.add_parser("staged", help="the test files that cover the staged files, one per line (the pre-commit hook)")
    st.add_argument("--repo", default=".")
    d = sub.add_parser("docs-only")
    d.add_argument("--repo", default=".")
    a = ap.parse_args(argv)

    if a.cmd == "key":
        print(tree_key(a.repo))
        return 0
    if a.cmd == "seen":
        hit = seen(a.check, a.key or tree_key(a.repo))
        if hit:
            print(f"{a.check}: passed on this exact tree at {hit['t']} ({hit['seconds'] / 60:.1f} min): not run again")
            return 0
        return 1
    if a.cmd == "record":
        record(a.check, a.status, a.seconds, a.key, a.note)
        return 0
    if a.cmd == "staged":
        # owner 2026-10-27 (verbatim): "Da: pe laptop doar testele fișierelor atinse, suita completă doar în CI."
        tests = plan(a.repo, paths=staged_files(a.repo))["tests"]
        cap = int(os.environ.get("FIELDKIT_LOCAL_TEST_CAP", "15"))
        if len(tests) > cap:
            # a hub file (cli.py) fans out to ~60 test files = 30+ min on the laptop: that is the suite, so CI runs it
            print(f"testcost: {len(tests)} test files cover the staged files (> {cap}): CI runs them, not this laptop",
                  file=sys.stderr)
            return 0
        for t in tests:
            print(os.path.relpath(t, a.repo).replace("\\", "/"))
        return 0
    if a.cmd in ("plan", "run"):
        pl = plan(a.repo, a.base)
        if a.cmd == "plan":
            if getattr(a, "json", False):
                print(json.dumps(pl, indent=1))
            else:
                print(f"{len(pl['changed'])} changed file(s); {len(pl['tests'])} test file(s) cover them"
                      + ("; documentation only" if pl["docs_only"] else ""))
                for t in pl["tests"]:
                    print("  " + os.path.relpath(t, a.repo))
                for u in pl["unmapped"]:
                    print(f"  UNMAPPED {u}: no test names it, so the full suite runs before push")
            return 0
        if not pl["tests"]:
            print("testcost: no test covers the changed files" + (" (documentation only)" if pl["docs_only"] else ""))
            return 0
        t0 = time.time()
        rc = subprocess.call([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *pl["tests"]], cwd=a.repo)
        record("fast", "pass" if rc == 0 else "fail", time.time() - t0, note=f"{len(pl['tests'])} files")
        return rc
    if a.cmd == "budget":
        b = budget(a.check)
        print(f"{a.check}: " + (f"{b / 60:.1f} min (median)" if b is not None else "not enough logged runs yet"))
        return 0
    if a.cmd == "report":
        print(report(a.last))
        return 0
    if a.cmd == "flags":
        f = flags()
        for name, why in FLAGS.items():
            print(f"  {'ON ' if f[name] else 'off'}  {name}: {why}")
        print('  (set in fieldkit.local.json "testcost": {...} only on the owner\'s yes)')
        return 0
    if a.cmd == "docs-only":
        # exit 0 = the hook may skip the suite: the owner's flag is ON and every STAGED path is documentation
        return 0 if flags()["docs_only_privacy_only"] and is_docs_only(staged_files(a.repo)) else 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
