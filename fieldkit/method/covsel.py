# Version: 1.0.0 · updated 26-11-23-04-00
"""Coverage selection (CI-SPEED-PLAN C4.2-C4.4): the tests that RAN the changed lines, not every test that imports the file.

    covsel.coverage_plan(repo, base, paths, cmap, fallback)   -> the coverage selection of a change
    covsel.combine(gate, imp, cov)                            -> the selection the gate uses

A change inside a function of a file the map knows selects the tests whose covered ranges intersect the changed
lines (hunks of `git diff -U0 BASE...HEAD`, old-side numbers: the map describes the base). Per file it falls back to the
import selection (`fallback(files)`) when the file is not in the map, is new, deleted, not Python, is dirty in the work
tree, or a hunk sits outside every function (module top level, class body). When the file is in the map, every hunk is
inside a function and no test ran those lines, the file is UNMAPPED: the caller refuses (fail closed, as today).
The gate (ci.yaml `selection_gate`) stays `import` until `fieldkit ciaudit replay` shows 0 misses; `union` after misses.
"""
import ast
import re
from pathlib import Path

GATES = ("import", "coverage", "union")
HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def parse_hunks(diff_text):
    """`git diff -U0` text -> [(old_first, old_last)]. A pure insertion (count 0) touches the line before and after."""
    out = []
    for line in diff_text.splitlines():
        m = HUNK.match(line)
        if not m:
            continue
        start, count = int(m.group(1)), int(m.group(2)) if m.group(2) is not None else 1
        out.append((start, start + count - 1) if count else (start, start + 1))
    return out


def function_spans(source):
    """-> [(first, last)] line spans of every function and method (decorators included); None if it does not parse."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return None
    spans = []
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            first = min([n.lineno] + [d.lineno for d in n.decorator_list])
            spans.append((first, n.end_lineno))
    return spans


def inside_function(hunk, spans):
    a, b = hunk
    return any(s <= a and b <= e for s, e in spans)


def covering(cmap, path, hunks):
    """-> the test files whose recorded ranges of `path` intersect any hunk."""
    hit = set()
    for node, files in (cmap.get("tests") or {}).items():
        for a, b in files.get(path) or []:
            if any(a <= hb and ha <= b for ha, hb in hunks):
                hit.add(node.split("::", 1)[0])
                break
    return hit


def mapped_files(cmap):
    return {f for files in (cmap.get("tests") or {}).values() for f in files}


def coverage_plan(paths, cmap, hunks_of, old_source_of, fallback):
    """The coverage selection. `hunks_of(path)` -> [(a, b)] or None (cannot read: new, deleted, dirty);
    `old_source_of(path)` -> the file at the base (None = new); `fallback(files)` -> {"tests": [...], "unmapped": [...]}.
    -> {"tests", "unmapped", "fallback": {file: reason}, "covered": {file: [test files]}}"""
    known = mapped_files(cmap)
    covered, fell, unmapped = {}, {}, []
    for p in paths:
        q = p.replace("\\", "/")
        if not q.endswith(".py"):
            fell[q] = "not python"
        elif q.startswith("tests/") and Path(q).name.startswith("test_"):
            fell[q] = "a changed test runs itself"
        elif q not in known:
            fell[q] = "not in the map"
        else:
            src, hunks = old_source_of(q), hunks_of(q)
            spans = function_spans(src) if src is not None else None
            if src is None:
                fell[q] = "new file"
            elif hunks is None:
                fell[q] = "diff unreadable (deleted or dirty)"
            elif spans is None:
                fell[q] = "base source does not parse"
            elif not hunks or not all(inside_function(h, spans) for h in hunks):
                fell[q] = "change outside any function"
            else:
                hit = covering(cmap, q, hunks)
                if hit:
                    covered[q] = sorted(hit)
                else:
                    unmapped.append(q)
    fb = fallback(sorted(fell)) if fell else {"tests": [], "unmapped": []}
    tests = sorted({t for ts in covered.values() for t in ts} | set(fb["tests"]))
    return {"tests": tests, "unmapped": sorted(set(unmapped) | set(fb["unmapped"])), "fallback": fell,
            "covered": covered}


def combine(gate, imp, cov):
    """-> (tests, unmapped) the gate uses. import: today's rule. coverage: the map's. union: both (never fewer tests
    than the import rule), refused only when neither selection can cover a file."""
    if gate not in GATES:
        raise ValueError(f"selection_gate {gate!r}: expected one of {', '.join(GATES)}")
    if cov is None or gate == "import":
        return list(imp["tests"]), list(imp["unmapped"])
    if gate == "coverage":
        return list(cov["tests"]), list(cov["unmapped"])
    return (sorted(set(imp["tests"]) | set(cov["tests"])),
            sorted(set(imp["unmapped"]) & set(cov["unmapped"])))
