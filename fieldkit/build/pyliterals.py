"""Python string literals with stray backslashes: find them, and make them raw only when that is provably safe.

Two failures seen again and again in captured agent sessions (P18 distill; triage set agent-shell):

- invalid-escape: "\\d+" in a normal string. Python 3.12 warns (SyntaxWarning), a later Python refuses it.
  Fixed only when the raw string has exactly the same value as the original.
- unicode-escape: "C:\\Users\\..." in a normal string, so \\U starts a unicode escape and the file does not
  parse at all. Fixed only when the raw string is a Windows drive path; anything else is reported, not changed.

Reads tokens, never imports or runs the file. --apply rewrites only the reported literals, then checks the
whole file parses; if it does not, nothing is written.

    python -m fieldkit.build.pyliterals FILE... [--apply] [--json]

Exit: 0 nothing to fix (or fixed), 1 error, 3 findings (without --apply).
"""
import argparse
import ast
import io
import json
import re
import sys
import tokenize
import warnings
from pathlib import Path

MAX_BYTES = 4 * 1024 * 1024
DRIVE_PATH = re.compile(r"^[A-Za-z]:\\")


def _prefix(tok):
    i = 0
    while i < len(tok) and tok[i] not in "'\"":
        i += 1
    return tok[:i].lower(), tok[i:]


def _eval(literal):
    """(ok, value, warned): evaluate one literal with warnings recorded."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            value = ast.literal_eval(literal)
        except (SyntaxError, ValueError):
            return False, None, False
    warned = any(issubclass(w.category, (SyntaxWarning, DeprecationWarning)) and "escape" in str(w.message)
                 for w in caught)
    return True, value, warned


def classify(literal):
    """One STRING token -> (kind, fixed_literal or None) or None when it needs nothing."""
    prefix, rest = _prefix(literal)
    if "\\" not in rest or "r" in prefix or "f" in prefix or "u" in prefix:
        return None
    raw = "r" + literal
    ok_raw, raw_value, raw_warned = _eval(raw)
    ok, value, warned = _eval(literal)
    if ok:
        if not warned:
            return None
        if ok_raw and not raw_warned and raw_value == value:
            return "invalid-escape", raw
        return "invalid-escape", None
    if ok_raw and isinstance(raw_value, str) and DRIVE_PATH.match(raw_value):
        return "unicode-escape", raw
    return "unicode-escape", None


def scan_text(text):
    """-> list of findings {line, col, kind, before, after, fixable, start, end} (offsets into text)."""
    starts, pos = [0], 0
    for line in text.splitlines(keepends=True):
        pos += len(line)
        starts.append(pos)
    out = []
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError) as e:
        raise ValueError(f"cannot tokenize: {e}") from None
    for t in tokens:
        if t.type != tokenize.STRING:
            continue
        got = classify(t.string)
        if not got:
            continue
        kind, fixed = got
        start = starts[t.start[0] - 1] + t.start[1]
        end = starts[t.end[0] - 1] + t.end[1]
        out.append({"line": t.start[0], "col": t.start[1] + 1, "kind": kind, "before": t.string[:120],
                    "after": (fixed or "")[:120], "fixable": fixed is not None, "start": start, "end": end,
                    "_fixed": fixed})
    return out


def apply_text(text, findings):
    """Rewrite the fixable literals; -> new text, or raise ValueError when the result does not parse."""
    new = text
    for f in sorted((f for f in findings if f["fixable"]), key=lambda f: f["start"], reverse=True):
        new = new[:f["start"]] + f["_fixed"] + new[f["end"]:]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            ast.parse(new)
        except SyntaxError as e:
            raise ValueError(f"after the fix the file still does not parse (line {e.lineno}): nothing written") from None
    return new


def check_file(path, apply=False):
    p = Path(path)
    if not p.is_file():
        return {"file": str(p), "error": "not a file", "findings": []}
    if p.stat().st_size > MAX_BYTES:
        return {"file": str(p), "error": f"larger than {MAX_BYTES} bytes", "findings": []}
    raw = p.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return {"file": str(p), "error": "not UTF-8", "findings": []}
    newline = "\r\n" if b"\r\n" in raw else "\n"
    text = text.replace("\r\n", "\n")
    try:
        findings = scan_text(text)
    except ValueError as e:
        return {"file": str(p), "error": str(e), "findings": []}
    report = {"file": str(p), "error": None, "findings": findings, "applied": 0}
    if apply and any(f["fixable"] for f in findings):
        try:
            new = apply_text(text, findings)
        except ValueError as e:
            report["error"] = str(e)
            return report
        p.write_bytes(new.replace("\n", newline).encode("utf-8"))
        report["applied"] = sum(f["fixable"] for f in findings)
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m fieldkit.build.pyliterals", description=__doc__.split("\n\n")[0])
    ap.add_argument("files", nargs="+")
    ap.add_argument("--apply", action="store_true", help="rewrite the fixable literals (value-preserving)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    reports = [check_file(f, a.apply) for f in a.files]
    for r in reports:
        for f in r["findings"]:
            f.pop("_fixed", None)
    if a.json:
        print(json.dumps(reports, indent=1))
    else:
        for r in reports:
            if r["error"]:
                print(f"FAIL: {r['file']}: {r['error']}")
            for f in r["findings"]:
                what = f"-> {f['after']}" if f["fixable"] else "-> not changed: read and fix by hand"
                print(f"{r['file']}:{f['line']}:{f['col']}: {f['kind']}: {f['before']} {what}")
    errors = any(r["error"] for r in reports)
    left = sum(len(r["findings"]) - r.get("applied", 0) for r in reports)
    done = sum(r.get("applied", 0) for r in reports)
    if not a.json:
        if errors:
            print("NEXT: fix the reported error, then run again")
        elif left:
            print(f"FINDINGS: {left} literal(s)" + (f", {done} fixed" if done else "")
                  + "\nNEXT: " + ("rerun with --apply" if not a.apply else "fix the rest by hand"))
        else:
            print(f"PASS: {done} literal(s) fixed" if done else "PASS: no stray backslashes")
            print("NEXT: run the covering tests (fieldkit testcost staged)")
    return 1 if errors else 3 if left else 0


if __name__ == "__main__":
    sys.exit(main())
