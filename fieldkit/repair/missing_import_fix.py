"""A Python file that uses `shutil.copy`, `re.sub`, ... without importing the module: find it, --apply adds the import.

Kiro's detector was sound but flagged a local variable named `time` or `json` as a missing import, and the fixer put
the import above a docstring or a `from __future__` line. Here a name counts as missing only when nothing in the file
binds it (no import, assignment, parameter, def, class, loop or except name), and the line goes after the docstring
and `__future__` imports, before the first import. The result must parse, or nothing is written.

    fieldkit repair missing-import-fix FILE [--apply] [--json]
Exit: 0 nothing missing (or fixed), 1 error, 3 findings.
"""
import argparse
import ast
from pathlib import Path

from ._common import emit, write_bytes

STDLIB = {"argparse", "ast", "base64", "collections", "copy", "csv", "datetime", "difflib", "fnmatch", "functools",
          "glob", "hashlib", "io", "itertools", "json", "logging", "math", "os", "pathlib", "platform", "random",
          "re", "shlex", "shutil", "signal", "socket", "stat", "string", "struct", "subprocess", "sys", "tempfile",
          "textwrap", "time", "traceback", "uuid", "zipfile"}


def _bound(tree):
    names = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            names.update((a.asname or a.name).split(".")[0] for a in n.names)
        elif isinstance(n, ast.ImportFrom):
            names.update(a.asname or a.name for a in n.names)
        elif isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            names.add(n.id)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(n.name)
        elif isinstance(n, ast.arg):
            names.add(n.arg)
        elif isinstance(n, ast.ExceptHandler) and n.name:
            names.add(n.name)
        elif isinstance(n, (ast.Global, ast.Nonlocal)):
            names.update(n.names)
    return names


def find_missing(source):
    tree = ast.parse(source)
    bound = _bound(tree)
    used = {n.value.id for n in ast.walk(tree) if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
            and n.value.id in STDLIB}
    return sorted(used - bound)


def _insert_index(tree):
    """0-based line for the new imports: before the first import after the docstring/__future__ block, else after it."""
    body, last, i = tree.body, 0, 0
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
        last, i = body[0].end_lineno, 1
    while i < len(body) and isinstance(body[i], ast.ImportFrom) and body[i].module == "__future__":
        last, i = body[i].end_lineno, i + 1
    for node in body[i:]:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            return node.lineno - 1
        break
    return last


def fix_source(source):
    """-> (new_source, missing)."""
    missing = find_missing(source)
    if not missing:
        return source, []
    tree = ast.parse(source)
    nl = "\r\n" if "\r\n" in source else "\n"
    lines = source.splitlines(keepends=True)
    at = _insert_index(tree)
    if at and not lines[at - 1].endswith(("\n", "\r")):
        lines[at - 1] += nl
    lines.insert(at, "".join(f"import {m}{nl}" for m in missing))
    new = "".join(lines)
    ast.parse(new)
    return new, missing


def main(argv=None):
    ap = argparse.ArgumentParser(prog="fieldkit repair missing-import-fix", description=__doc__.split("\n")[0])
    ap.add_argument("file")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    p = Path(a.file)
    if not p.is_file():
        emit(a.json, "FAIL", f"file not found: {p}", "pass an existing Python file")
        return 1
    try:
        new, missing = fix_source(p.read_bytes().decode("utf-8"))
    except (SyntaxError, UnicodeDecodeError) as e:
        emit(a.json, "FAIL", f"{p.name} cannot be read as Python: {e}", "fix the file first")
        return 1
    if not missing:
        emit(a.json, "PASS", f"no missing stdlib import in {p.name}", "none", {"missing": []})
        return 0
    if a.apply:
        write_bytes(p, new.encode("utf-8"))
        emit(a.json, "PASS", f"added import {', '.join(missing)} to {p.name}", "re-run the test", {"missing": missing})
        return 0
    emit(a.json, "FAIL", f"{p.name} uses {', '.join(missing)} without importing it", f"fieldkit repair missing-import-fix {p} --apply",
         {"missing": missing})
    return 3
