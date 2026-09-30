"""The tool registry: what exists, where, whether it is safe to probe, what is tested.

`check()` never runs a tool. It confirms the file exists, that it parses, and
decides whether it is safe to call with --help, from the code itself:

    safe to probe = has an `if __name__ == "__main__"` guard, uses argparse,
                    and has no statements at module level that do work.

That rule exists because on 2026-09-29 `organize.py --help` was run to see
its options; it has no argument handling and ran its whole job instead. Only
tests listed in tools.yaml are ever executed, and only with run_tests=True.
"""
import ast
import subprocess
import sys
from pathlib import Path

from ..core import settings
from ..core.host import platform_ok

REGISTRY = Path(__file__).resolve().parent / "tools.yaml"
# The owner's own tools (private folders, personal projects) live beside the
# checkout in local/, which git ignores: same format, merged at load time.
LOCAL_REGISTRY = settings.ROOT / "local" / "tools.yaml"


# Module-level calls that set things up rather than doing the tool's job.
_SETUP_CALLS = ("sys.path.insert", "sys.path.append", "reconfigure", "warnings.filterwarnings",
                "warnings.simplefilter", "logging.basicConfig", "logging.getLogger", "os.environ.setdefault",
                "sys.setrecursionlimit", "faulthandler.enable", "atexit.register", "register_adapter")


def _harmless(n):
    """True if a module-level statement only sets up (imports, definitions, path/encoding setup)."""
    if isinstance(n, (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
                      ast.Assign, ast.AnnAssign, ast.Pass)):
        return True
    if isinstance(n, ast.Expr):
        if isinstance(n.value, ast.Constant):             # docstring
            return True
        if isinstance(n.value, ast.Call):
            name = ast.unparse(n.value.func)
            return any(name == c or name.endswith("." + c) for c in _SETUP_CALLS)
        return False
    if isinstance(n, ast.If):
        return all(_harmless(b) for b in n.body + n.orelse)
    if isinstance(n, ast.Try):
        stmts = n.body + n.orelse + n.finalbody + [b for h in n.handlers for b in h.body]
        return all(_harmless(b) for b in stmts)
    return False


def load(strict=False):
    raw = list(settings.read_file(REGISTRY)["tools"])
    if LOCAL_REGISTRY.is_file():
        raw += settings.read_file(LOCAL_REGISTRY).get("tools") or []
    tools = settings.expand(raw, strict=strict)
    ids = [t["id"] for t in tools]
    dup = {i for i in ids if ids.count(i) > 1}
    if dup:
        raise ValueError(f"duplicate tool ids in tools.yaml / local/tools.yaml: {sorted(dup)}")
    return tools


def probe_safety(path):
    """-> (safe: bool, reason: str) from static analysis of a .py file."""
    try:
        src = Path(path).read_text(encoding="utf-8-sig")   # tolerate a BOM
        tree = ast.parse(src)
    except (OSError, SyntaxError, UnicodeDecodeError) as e:
        return False, f"does not parse: {type(e).__name__}"
    guard = any(isinstance(n, ast.If) and "__main__" in ast.unparse(n.test) for n in tree.body)
    uses_argparse = "argparse" in src
    work = []
    for n in tree.body:
        if isinstance(n, ast.If) and "__main__" in ast.unparse(n.test):
            continue
        if not _harmless(n):
            kind = "call" if isinstance(n, ast.Expr) else type(n).__name__
            work.append(f"line {n.lineno}: {kind} at module level")
    if not guard and not work:
        return False, "library module: import it, do not run it"
    if work:
        return False, "RUNS ON LOAD - " + "; ".join(work[:3])
    if not guard:
        return False, "no __main__ guard"
    if not uses_argparse:
        return False, "no argparse: --help is not understood"
    return True, "main guard + argparse, no module-level work"


def check(run_tests=False, only=None, timeout=300):
    """Report per tool. Runs nothing except the listed tests, and only if asked."""
    out = []
    for t in load():
        if only and t["id"] not in only:
            continue
        row = {"id": t["id"], "harness": t.get("harness"), "platforms": t.get("platforms"),
               "changes": t.get("changes", True), "has_test": bool(t.get("test")), "retired": t.get("retired", False)}
        path = t.get("path")
        if not path:
            row["status"] = "remote" if t.get("repo") else "no-path"
            row["repo"] = t.get("repo")
            out.append(row)
            continue
        p = Path(path)
        row["exists"] = p.exists()
        if not p.exists():
            row["status"] = "missing"
            out.append(row)
            continue
        entry = t.get("entry") or []
        if entry[1:3] == ["-m", "fieldkit"]:
            row["probe_safe"], row["probe_reason"] = True, "reached through the fieldkit CLI (argparse)"
        elif p.suffix == ".py":
            row["probe_safe"], row["probe_reason"] = probe_safety(p)
        row["status"] = "present"
        if run_tests and t.get("test") and platform_ok(t.get("platforms")):
            cmd = [sys.executable if c in ("python", "python3") else c for c in t["test"]]
            r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                               timeout=timeout, cwd=p.parent if p.is_file() else p)
            row["test_ok"] = r.returncode == 0
            row["test_tail"] = (r.stdout + r.stderr).strip().splitlines()[-5:]
            from .cards import record_test          # trust level "tested" needs a pass on THIS file
            record_test(t["id"], t.get("path") if p.is_file() else None, row["test_ok"])
        out.append(row)
    return out
