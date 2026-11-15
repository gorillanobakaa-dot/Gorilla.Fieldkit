"""Windows cp1252 UnicodeEncodeError in a Python script: find the text-mode open()/read_text()/write_text() calls
with no encoding and the missing stdout UTF-8 setting; --apply adds exactly those arguments.

Kiro's version rewrote `open(...)` with a regex (broke on nested parentheses, mode keywords and binary modes). This
one reads the syntax tree: a call is changed only when it is plainly text mode (mode absent, or a literal without
"b") and has no encoding and no *args/**kwargs that could carry one. The only edit is `, encoding="utf-8"` after the
last argument, plus one stdout line after the docstring and `from __future__` imports. The result must parse
again, or nothing is written.

    fieldkit repair cp1252-stdout-fix FILE [--apply] [--json]
Exit: 0 nothing to fix (or fixed), 1 error, 3 findings.
"""
import argparse
import ast
from pathlib import Path

from ._common import emit, write_bytes

STDOUT_LINE = 'import sys; getattr(sys.stdout, "reconfigure", lambda **_: None)(encoding="utf-8", errors="replace")'
PATH_METHODS = {"read_text": 1, "write_text": 2}     # name -> number of positional args that already reach `encoding`


def _text_mode(node):
    """True when this open() is text mode (so it needs an encoding)."""
    mode = node.args[1] if len(node.args) >= 2 else None
    for kw in node.keywords:
        if kw.arg == "mode":
            mode = kw.value
    if mode is None:
        return True
    if isinstance(mode, ast.Constant) and isinstance(mode.value, str):
        return "b" not in mode.value
    return False


def _sites(tree):
    """-> [call nodes] that need an encoding argument."""
    out = []
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        if any(isinstance(a, ast.Starred) for a in n.args) or any(k.arg is None for k in n.keywords):
            continue
        if any(k.arg == "encoding" for k in n.keywords):
            continue
        f = n.func
        if isinstance(f, ast.Name) and f.id == "open":
            if len(n.args) < 4 and (n.args or n.keywords) and _text_mode(n):
                out.append(n)
        elif isinstance(f, ast.Attribute) and f.attr in PATH_METHODS:
            if len(n.args) < PATH_METHODS[f.attr]:
                out.append(n)
        elif isinstance(f, ast.Attribute) and f.attr == "open" and isinstance(f.value, ast.Call):
            # Path(...).open("w"): the receiver is a call, so not the io/os module functions
            if len(n.args) < 4 and _text_mode(n):
                out.append(n)
    return out


def _has_print(tree):
    return any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "print" for n in ast.walk(tree))


def _insert_line_index(tree):
    """0-based line to insert at: after the docstring and the `from __future__` imports."""
    last = 0
    body = tree.body
    i = 0
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
        last = body[0].end_lineno
        i = 1
    while i < len(body) and isinstance(body[i], ast.ImportFrom) and body[i].module == "__future__":
        last = body[i].end_lineno
        i += 1
    return last


def fix_source(src):
    """-> (new_source, findings, error). findings: [{line, rule}]."""
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return src, [], f"does not parse: {e.msg} (line {e.lineno})"
    sites = _sites(tree)
    findings = [{"line": n.lineno, "rule": "no-encoding"} for n in sites]
    need_stdout = _has_print(tree) and "reconfigure" not in src
    if need_stdout:
        findings.append({"line": 1, "rule": "no-stdout-reconfigure"})
    findings.sort(key=lambda f: f["line"])
    if not findings:
        return src, [], None
    lines = src.splitlines(keepends=True)
    edits = set()
    for n in sites:
        last = None
        for a in list(n.args) + [k.value for k in n.keywords]:
            if last is None or (a.end_lineno, a.end_col_offset) > (last.end_lineno, last.end_col_offset):
                last = a
        if last is not None:
            edits.add((last.end_lineno - 1, last.end_col_offset))
        elif isinstance(n.func, ast.Attribute):       # read_text() with no arguments: insert before the closing paren
            edits.add((n.end_lineno - 1, n.end_col_offset - 1, "bare"))
    for e in sorted(edits, key=lambda e: (e[0], e[1]), reverse=True):
        ln, col = e[0], e[1]
        text = 'encoding="utf-8"' if len(e) == 3 else ', encoding="utf-8"'
        raw = lines[ln].encode("utf-8")
        lines[ln] = (raw[:col] + text.encode() + raw[col:]).decode("utf-8")
    if need_stdout:
        nl = "\r\n" if "\r\n" in src else "\n"
        at = _insert_line_index(tree)
        if at and not lines[at - 1].endswith(("\n", "\r")):
            lines[at - 1] += nl
        lines.insert(at, STDOUT_LINE + nl)
    new = "".join(lines)
    try:
        ast.parse(new)
    except SyntaxError as e:
        return src, findings, f"the fix would not parse ({e.msg}, line {e.lineno}); nothing written"
    return new, findings, None


def main(argv=None):
    ap = argparse.ArgumentParser(prog="fieldkit repair cp1252-stdout-fix", description=__doc__.split("\n")[0])
    ap.add_argument("file")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    p = Path(a.file)
    if not p.is_file():
        emit(a.json, "FAIL", f"file not found: {p}", "pass an existing Python file")
        return 1
    try:
        src = p.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        emit(a.json, "FAIL", f"{p.name} is not UTF-8; not touched", "convert it to UTF-8 first")
        return 1
    new, findings, err = fix_source(src)
    if err:
        emit(a.json, "FAIL", f"{p.name} {err}", "fix the syntax error first", {"findings": findings})
        return 1
    if not findings:
        emit(a.json, "PASS", f"{p.name} already names UTF-8 everywhere it matters", "none", {"findings": []})
        return 0
    if a.apply:
        write_bytes(p, new.encode("utf-8"))
        emit(a.json, "PASS", f"applied {len(findings)} UTF-8 fix(es) to {p.name}", "re-run the script", {"findings": findings})
        return 0
    lines = sorted({f["line"] for f in findings})[:10]
    emit(a.json, "FAIL", f"{len(findings)} site(s) in {p.name} use the Windows code page (lines {lines})",
         f"fieldkit repair cp1252-stdout-fix {p} --apply", {"findings": findings})
    return 3
