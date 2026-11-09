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
import hashlib
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
from .core.settings import state_root as _state_root

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
#: files whose only change may be the version line: such a diff holds no code
VERSION_FILES = ("pyproject.toml", "fieldkit/__init__.py")
VERSION_LINE = re.compile(r"^\s*(version|__version__)\s*=")
#: trees that hold plans and reports, never code
NON_CODE_TREES = ("state/",)


def state_dir():
    d = Path(os.environ.get("FIELDKIT_TESTCOST_DIR") or _state_root(ROOT) / "state" / "testcost")
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


def _line_diff(repo, base, path):
    """The changed lines of one file (committed since `base`, staged and unstaged), no context, no headers."""
    text = ""
    if base:
        text += _git(repo, "diff", "-U0", f"{base}...HEAD", "--", path)
    text += _git(repo, "diff", "-U0", "HEAD", "--", path)
    return [l for l in text.splitlines() if l[:1] in "+-" and not l.startswith(("+++", "---"))]


def is_version_only(path, lines):
    """True when `path` may carry a version line and every changed line in `lines` is one (at least one line)."""
    path = path.replace("\\", "/")
    return path in VERSION_FILES and bool(lines) and all(VERSION_LINE.match(l[1:]) for l in lines)


def is_no_code(paths, diff_lines=lambda path: []):
    """True when nothing in `paths` is code: documentation no test reads, files under state/, or a version-only diff.
    `diff_lines(path)` -> the changed lines of that file (injectable; the default sees none, so a version file counts
    as code unless the caller reads the diff)."""
    paths = [p.replace("\\", "/") for p in paths if p]
    return bool(paths) and all(
        p.startswith(NON_CODE_TREES) or is_docs_only([p]) or is_version_only(p, diff_lines(p)) for p in paths)


def changed_files(repo=".", base=None):
    """Paths changed against `base` (default: HEAD), staged, unstaged and untracked."""
    out = set()
    if base:
        out |= set(_git(repo, "diff", "--name-only", f"{base}...HEAD").split())
    for line in _git(repo, "status", "--porcelain", "-uall").splitlines():
        p = line[3:].strip().strip('"')
        out.add(p.split(" -> ")[-1])
    return sorted(x for x in out if x)


def merge_base(repo="."):
    """In the middle of a merge: the merge base of HEAD and MERGE_HEAD (the branch side starts there), else None."""
    gitdir = Path(_git(repo, "rev-parse", "--absolute-git-dir").strip())
    if not (gitdir / "MERGE_HEAD").is_file():
        return None
    return _git(repo, "merge-base", "HEAD", "MERGE_HEAD").strip() or None


def staged_files(repo="."):
    """Staged paths. In the middle of a merge only the branch side's own files: what arrives from the base already
    passed the base's CI (TESTS ONCE); testing it again held a slot for an hour (2026-11-03, I-18)."""
    staged = _git(repo, "diff", "--cached", "--name-only").split()
    gitdir = Path(_git(repo, "rev-parse", "--absolute-git-dir").strip())
    if not staged or not (gitdir / "MERGE_HEAD").is_file():
        return staged
    base = _git(repo, "merge-base", "HEAD", "MERGE_HEAD").strip()
    ours = set(_git(repo, "diff", "--name-only", base, "HEAD").split()) if base else set()
    return [p for p in staged if p in ours] if ours else staged


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


TESTMAP = "tests/testmap.yaml"
MUTATION_PLAN = re.compile(r"(^|/)([^/]+_mutations\.yaml|mutations\.yaml|mutations-[^/]+\.yaml)$")


def testmap(repo):
    """[(glob, [test files])] from tests/testmap.yaml: files outside the Python packages (workflows, pins, hooks,
    vendored skills) and the tests that read them. Data, not code (2026-11-02: 17 of 38 PR runs ran the whole suite
    because a data file had no mapped test)."""
    f = Path(repo) / TESTMAP
    if not f.is_file():
        return []
    import yaml
    rows = yaml.safe_load(f.read_text(encoding="utf-8")) or []
    return [(r["glob"], list(r.get("tests") or [])) for r in rows if isinstance(r, dict) and r.get("glob")]


def _glob_match(path, pattern):
    from fnmatch import fnmatchcase
    return fnmatchcase(path, pattern) or (pattern.endswith("/**") and path.startswith(pattern[:-2]))


def _package_tests(q, imports):
    """A file inside a package no test names (fieldkit/emerge/recipes/x.yaml, fieldkit/buildh/x.js, an untested
    module): the tests that import the nearest enclosing package that any test imports."""
    parts = Path(q).parts
    for n in range(len(parts) - 1, 1, -1):
        pkg = ".".join(parts[:n])
        if pkg == "fieldkit":
            break
        hits = {str(t) for t, ms in imports.items() if any(m == pkg or m.startswith(pkg + ".") for m in ms)}
        if hits:
            return hits
    return set()


REGISTRY_TOOLS = "fieldkit/desk/tools.yaml"
REGISTRY_CLI = "fieldkit/cli.py"
# What proves the registry file still loads and dispatches, whatever entry changed (each exists; a missing one is skipped).
REGISTRY_INTEGRITY = {
    REGISTRY_TOOLS: ("tests/test_cards.py", "tests/test_cards_catalogue.py", "tests/test_platform_gate.py",
                     "tests/test_registry_cli.py", "tests/test_tools_find.py"),
    REGISTRY_CLI: ("tests/test_core.py", "tests/test_methods_help.py", "tests/test_registry_cli.py",
                   "tests/test_integration.py", "tests/test_tools_find.py"),
}
_NAME = re.compile(r"^[a-z][a-z0-9_-]{3,}$")


def _show(repo, ref, path):
    import subprocess
    r = subprocess.run(["git", "-C", str(repo), "show", f"{ref}:{path}"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return r.stdout if r.returncode == 0 else None


def _registry_tools_tests(repo, base, test_files, imports, new_ref=None):
    """tools.yaml: the tests of the entries that changed (their `test` command, the tests importing their module,
    the tests naming the id) + the integrity set. None = cannot prove which entries changed -> the caller keeps the
    wide mapping (unparseable file, a removed entry, a change outside `tools:`)."""
    import yaml
    old_text = _show(repo, base or "HEAD", REGISTRY_TOOLS)
    new_path = Path(repo) / REGISTRY_TOOLS
    new_text = _show(repo, new_ref, REGISTRY_TOOLS) if new_ref else (
        new_path.read_text(encoding="utf-8") if new_path.is_file() else None)
    if old_text is None or new_text is None:
        return None
    try:
        old, new = yaml.safe_load(old_text) or {}, yaml.safe_load(new_text) or {}
        if {k: v for k, v in old.items() if k != "tools"} != {k: v for k, v in new.items() if k != "tools"}:
            return None
        olds = {e["id"]: e for e in old.get("tools", [])}
        news = {e["id"]: e for e in new.get("tools", [])}
    except (yaml.YAMLError, KeyError, TypeError, AttributeError):
        return None
    if set(olds) - set(news):
        return None
    changed = [i for i, e in news.items() if olds.get(i) != e]
    if not changed:
        return None
    hits = {str(repo / t) for t in REGISTRY_INTEGRITY[REGISTRY_TOOLS] if (repo / t).is_file()}
    for i in changed:
        e = news[i]
        for tok in map(str, e.get("test") or []):
            m = re.search(r"tests/[\w/.-]+\.py", tok)
            if m and (repo / m.group(0)).is_file():
                hits.add(str(repo / m.group(0)))
        mod = _module_of(re.sub(r"^\$\{FIELDKIT\}/", "", str(e.get("path") or "")))
        if mod and mod != "fieldkit":
            hits |= {str(t) for t, ms in imports.items() if any(m == mod or m.startswith(mod + ".") for m in ms)}
        if len(i) > 3:
            hits |= {str(t) for t in test_files if re.search(r"\b" + re.escape(i) + r"\b", t.read_text(encoding="utf-8", errors="replace"))}
    return hits


def _registry_cli_tests(repo, lines, test_files, imports):
    """cli.py: the command names and modules the changed lines name -> the tests naming/importing them + the integrity
    set. None = no name found in the changed lines (cannot prove what they affect) -> the caller keeps the wide mapping."""
    needles, mods = set(), set()
    for l in lines:
        body = l[1:]
        needles |= {n for n in re.findall(r"[\"']([a-z][a-z0-9_-]+)[\"']", body) if _NAME.match(n)}
        needles |= {n for n in re.findall(r"fieldkit ([a-z][a-z0-9-]{3,})", body)}
        for m in re.finditer(r"from \.([\w.]*) import ([\w, ]+)", body):
            base_mod = m.group(1)
            for name in (x.strip() for x in m.group(2).split(",")):
                mods.add(f"fieldkit.{base_mod}.{name}" if base_mod else f"fieldkit.{name}")
    if not needles and not mods:
        return None
    hits = {str(repo / t) for t in REGISTRY_INTEGRITY[REGISTRY_CLI] if (repo / t).is_file()}
    for t in test_files:
        src = None
        if any(m == x or m.startswith(x + ".") for m in imports[t] for x in mods):
            hits.add(str(t))
            continue
        src = t.read_text(encoding="utf-8", errors="replace")
        if any(re.search(r"\b" + re.escape(n) + r"\b", src) for n in needles):
            hits.add(str(t))
    return hits


def plan(repo=".", base=None, paths=None, diff_lines=None, own_base=None):
    """{"tests": [...], "unmapped": [...], "docs_only": bool, "no_code": bool}: the tests that cover the changed files.

    "no_code": nothing changed but documentation, state/ files or version lines (read from the diff, `diff_lines`
    is injectable): zero tests are selected for those paths.

    A test covers a module when it imports it or a submodule of it (`import fieldkit` alone covers nothing: every test
    has it). A changed test runs itself. A changed module no test imports is matched by name in the test sources (a CLI
    test calls `-m fieldkit lmstudio-log`); still nothing -> "unmapped", which means: the full suite before push."""
    repo = Path(repo)
    paths = changed_files(repo, base) if paths is None else paths
    tests_dir = repo / "tests"
    test_files = sorted(tests_dir.rglob("test_*.py")) if tests_dir.is_dir() else []   # tests/capture/ too
    imports = {t: _imports(t) for t in test_files}
    if own_base and not diff_lines:                    # a merge in progress: only the branch's own change counts, what
        base = own_base                                # arrives from the other side already passed its CI (TESTS ONCE)
        diff_lines = lambda path: [l for l in _git(repo, "diff", "-U0", own_base, "HEAD", "--", path).splitlines()
                                   if l[:1] in "+-" and not l.startswith(("+++", "---"))]
    diff_lines = diff_lines or (lambda path: _line_diff(repo, base, path))
    tmap = testmap(repo)
    chosen, unmapped = set(), []
    for p in paths:
        q = p.replace("\\", "/")
        if q.startswith(NON_CODE_TREES) or is_version_only(q, diff_lines(q) if q in VERSION_FILES else []):
            continue
        if q.startswith("tests/") and Path(q).name.startswith("test_") and q.endswith(".py"):
            chosen.add(str(repo / q))
            continue
        if Path(q).suffix.lower() in DOC_SUFFIXES and not q.startswith(CODE_TREES):
            continue
        rows = [ts for g, ts in tmap if _glob_match(q, g)]
        if rows:                                       # a mapped file; tests: [] = declared read by no test
            chosen |= {str(repo / t) for ts in rows for t in ts}
            continue
        if MUTATION_PLAN.search(q):                    # read only by `fieldkit mutate`: the tests of what it mutates
            own = repo / "tests" / ("test_" + Path(q).name.replace("_mutations.yaml", ".py").replace("mutations-", ""))
            hits = {str(own)} if own.is_file() else (_package_tests(q, imports) if q.startswith("fieldkit/") else set())
            if hits:
                chosen |= hits
            continue
        if q in REGISTRY_INTEGRITY:                    # a shared registry: only the tests of what changed in it
            narrow = (_registry_tools_tests(repo, base, test_files, imports, "HEAD" if own_base else None) if q == REGISTRY_TOOLS else
                      _registry_cli_tests(repo, diff_lines(q), test_files, imports))
            if narrow:
                chosen |= narrow
                continue
        mod = _module_of(q)
        hits = set()
        if mod and mod != "fieldkit":
            hits = {str(t) for t, ms in imports.items() if any(m == mod or m.startswith(mod + ".") for m in ms)}
        if not hits:
            needles = [Path(q).name] + ([mod.rsplit(".", 1)[-1]] if mod else []) +                 ([Path(q).stem] if Path(q).suffix != ".py" and Path(q).stem not in ("mutations", "data", "index") else [])
            needles = [n for n in needles if len(n) > 3 and n not in ("__init__.py", "cli.py", "cli")]
            for t in test_files:
                src = t.read_text(encoding="utf-8", errors="replace")
                if any(re.search(r"\b" + re.escape(n) + r"\b", src) for n in needles):
                    hits.add(str(t))
        if not hits and q.startswith("fieldkit/"):     # no test names it: the tests of its package (the whole
            # suite would add nothing for a file no test imports or names)
            hits = _package_tests(q, imports)
        if hits:
            chosen |= hits
        else:
            unmapped.append(q)
    return {"tests": sorted(chosen), "unmapped": unmapped, "docs_only": is_docs_only(paths),
            "no_code": is_no_code(paths, lambda q: diff_lines(q) if q.replace("\\", "/") in VERSION_FILES else []),
            "changed": paths}


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
    um = sub.add_parser("unmapped", help="tracked files no test covers (each one makes CI run the whole suite)")
    um.add_argument("--repo", default=".")
    um.add_argument("--all", action="store_true", help="every tracked file, not only the changed ones")
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
        tests = plan(a.repo, paths=staged_files(a.repo), own_base=merge_base(a.repo))["tests"]
        cap = int(os.environ.get("FIELDKIT_LOCAL_TEST_CAP", "15"))
        if len(tests) > cap:
            # a hub file (cli.py) fans out to ~60 test files = 30+ min on the laptop. R15: never dropped - this
            # computer keeps its share (the cap), the free devices run the rest (fieldkit/testsplit.py), and what no
            # device takes runs here
            from . import testsplit
            rel = [os.path.relpath(t, a.repo).replace("\\", "/") for t in tests]
            for t in testsplit.start(a.repo, rel, tree_key(a.repo), cap)["local"]:
                print(t)
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
        # the identical test files on the identical tree already passed: a repeat proves nothing (tests once, R7)
        check = "fast:" + hashlib.sha1("\n".join(sorted(pl["tests"])).encode()).hexdigest()[:12]
        key = tree_key(a.repo)
        hit = seen(check, key)
        if hit:
            print(f"testcost: these {len(pl['tests'])} test file(s) passed on this exact tree at {hit['t']}: not run again")
            return 0
        t0 = time.time()
        rc = subprocess.call([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *pl["tests"]], cwd=a.repo)
        record(check, "pass" if rc == 0 else "fail", time.time() - t0, key=key, note=f"{len(pl['tests'])} files")
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
    if a.cmd == "unmapped":
        files = _git(a.repo, "ls-files").split() if a.all else changed_files(a.repo)
        u = plan(a.repo, paths=files, diff_lines=lambda q: [])["unmapped"]
        if getattr(a, "json", False):
            print(json.dumps({"unmapped": u, "checked": len(files)}))
        else:
            for x in u:
                print("  unmapped", x)
            print(f"testcost unmapped: {len(u)} of {len(files)} file(s) covered by no test"
                  + ("" if not u else " - each one runs the whole suite in CI; map it in tests/testmap.yaml or "
                     "give it a test"))
            print("NEXT: " + ("nothing to do" if not u else "map or test the files listed"))
        return 3 if u else 0
    if a.cmd == "docs-only":
        # exit 0 = the hook may skip the suite: the owner's flag is ON and every STAGED path is documentation
        return 0 if flags()["docs_only_privacy_only"] and is_docs_only(staged_files(a.repo)) else 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
