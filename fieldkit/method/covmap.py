# Version: 1.0.0 · updated 26-11-22-18-30
"""Coverage map: which test ran which lines (CI-SPEED-PLAN C4.1, the base of C5 test-level receipts and C4.2 selection).

    fieldkit ci covmap build DB OUT [--root DIR]    coverage database (dynamic_context = test_function) -> map JSON
    fieldkit ci covmap merge A B ... OUT            union of maps (Linux + Windows, or shards)
    fieldkit ci covmap show MAP [--test NODE]       size, or the files one test covered
    fieldkit ci covmap key MAP NODE [--root DIR]    the receipt key of a test: sha256 of node + the git blob of each covered file

Map: {"schema": 1, "tests": {"tests/test_x.py::test_y": {"fieldkit/a.py": [[first, last], ...]}}}. Only files inside
the repository are recorded (paths relative to --root, forward slashes). A test whose covered files are all unchanged
cannot change its result, so its key is stable exactly until one of those files changes (C5).
"""
import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

SCHEMA = 1


def parse_context(ctx):
    """'tests.test_x.Class.test_y' (coverage dynamic context) -> 'tests/test_x.py::Class::test_y'; '' = not a test."""
    if not ctx:
        return ""
    parts = ctx.split(".")
    for i, part in enumerate(parts):
        if part.startswith("test_"):
            return "/".join(parts[:i + 1]) + ".py" + (f"::{'::'.join(parts[i + 1:])}" if parts[i + 1:] else "")
    return ""


def ranges(lines):
    """Sorted line numbers -> [[first, last], ...]."""
    out = []
    for n in sorted(set(lines)):
        if out and n == out[-1][1] + 1:
            out[-1][1] = n
        else:
            out.append([n, n])
    return out


def relative(path, root):
    try:
        return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        return ""


def from_lines(by_file, root="."):
    """{abs file: {line: [context, ...]}} -> map. Files outside root and lines run outside a test are dropped."""
    tests = {}
    for path, by_line in by_file.items():
        rel = relative(path, root)
        if not rel:
            continue
        mine = {}
        for line, contexts in by_line.items():
            for ctx in contexts:
                node = parse_context(ctx)
                if node:
                    mine.setdefault(node, set()).add(line)
        for node, lines in mine.items():
            tests.setdefault(node, {})[rel] = ranges(lines)
    return {"schema": SCHEMA, "tests": {k: tests[k] for k in sorted(tests)}}


def build(db, root="."):
    from coverage import CoverageData

    data = CoverageData(basename=str(db))
    data.read()
    return from_lines({f: data.contexts_by_lineno(f) for f in data.measured_files()}, root)


def merge(maps):
    tests = {}
    for m in maps:
        if m.get("schema") != SCHEMA:
            raise ValueError(f"covmap schema {m.get('schema')!r}, expected {SCHEMA}")
        for node, files in m["tests"].items():
            for f, rs in files.items():
                have = tests.setdefault(node, {}).get(f, [])
                lines = {n for a, b in have + rs for n in range(a, b + 1)}
                tests[node][f] = ranges(lines)
    return {"schema": SCHEMA, "tests": {k: tests[k] for k in sorted(tests)}}


def key(cmap, node, blob):
    """-> receipt key of a test, or None when the map does not know it (no receipt may then be reused).
    `blob(path)` returns the content id of a file ('' = missing); one missing covered file also gives None."""
    files = (cmap.get("tests") or {}).get(node)
    if not files:
        return None
    parts = []
    for f in sorted(files):
        b = blob(f)
        if not b:
            return None
        parts.append((f, b))
    return hashlib.sha256(json.dumps([node, parts]).encode()).hexdigest()


def git_blob(root="."):
    cache = {}

    def look(path):
        if path not in cache:
            r = subprocess.run(["git", "rev-parse", f"HEAD:{path}"], cwd=root, capture_output=True, text=True)
            cache[path] = r.stdout.strip() if r.returncode == 0 else ""
        return cache[path]
    return look


def _load(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def _save(p, m):
    Path(p).write_text(json.dumps(m, separators=(",", ":"), sort_keys=True), encoding="utf-8")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="fieldkit ci covmap", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("db")
    b.add_argument("out")
    b.add_argument("--root", default=".")
    m = sub.add_parser("merge")
    m.add_argument("paths", nargs="+")
    s = sub.add_parser("show")
    s.add_argument("map")
    s.add_argument("--test")
    k = sub.add_parser("key")
    k.add_argument("map")
    k.add_argument("node")
    k.add_argument("--root", default=".")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "build":
            cm = build(a.db, a.root)
            _save(a.out, cm)
            print(f"covmap: {len(cm['tests'])} tests -> {a.out}")
        elif a.cmd == "merge":
            if len(a.paths) < 3:
                print("covmap: merge needs at least two maps and an output")
                return 2
            cm = merge(_load(p) for p in a.paths[:-1])
            _save(a.paths[-1], cm)
            print(f"covmap: merged {len(a.paths) - 1} maps, {len(cm['tests'])} tests -> {a.paths[-1]}")
        elif a.cmd == "show":
            cm = _load(a.map)
            if a.test:
                files = cm["tests"].get(a.test)
                if not files:
                    print(f"covmap: {a.test} not in the map")
                    return 3
                for f in sorted(files):
                    print(f"{f}: {len(sum((list(range(x, y + 1)) for x, y in files[f]), []))} lines")
            else:
                print(f"covmap: {len(cm['tests'])} tests, {len({f for t in cm['tests'].values() for f in t})} files")
        else:
            kk = key(_load(a.map), a.node, git_blob(a.root))
            if kk is None:
                print(f"covmap: no key for {a.node} (not in the map, or a covered file is missing)")
                return 3
            print(kk)
    except (OSError, ValueError, KeyError, ImportError) as exc:
        print(f"covmap: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
