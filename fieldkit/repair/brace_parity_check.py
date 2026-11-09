"""C and C++ delimiter balance ({} () []) in seconds, before a two-hour compile. Lexical only, not a parser.

Comments, string and character literals (also C++ raw strings and 1'000 digit separators) and preprocessor lines
(also their backslash continuations) are blanked first; only the first branch of each #if/#else is checked. Reports
unmatched, mismatched and unclosed delimiters with line numbers, never source text. Bounded: 100 files, 2 MiB each.

    fieldkit repair brace-parity-check FILE... [--json]
Exit: 0 balanced, 1 unreadable input, 3 findings.
"""
import argparse
import re
from pathlib import Path

from ._common import emit

MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_FILES = 100
OPENERS = {"{": "}", "(": ")", "[": "]"}
CLOSERS = {c: o for o, c in OPENERS.items()}
IF_RE = re.compile(r"^\s*#\s*(if|ifdef|ifndef)\b")
ELSE_RE = re.compile(r"^\s*#\s*(else|elif)\b")
ENDIF_RE = re.compile(r"^\s*#\s*endif\b")
RAW_RE = re.compile(r'R"([^()\\ \t\n]{0,16})\(')
SUPPORTED = {".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx"}


def blank_literals(text):
    """Blank comments and literal contents, keeping every newline (so line numbers stay right)."""
    out, i, n = [], 0, len(text)
    while i < n:
        c, d = text[i], text[i + 1] if i + 1 < n else ""
        if c == "/" and d == "/":
            j = i
            while j < n and text[j] != "\n":
                if text[j] == "\\" and j + 1 < n and text[j + 1] == "\n":
                    out.append(" \n")
                    j += 2
                    continue
                out.append(" ")
                j += 1
            i = j
        elif c == "/" and d == "*":
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append("".join("\n" if ch == "\n" else " " for ch in text[i:j]))
            i = j
        elif c == "R" and d == '"' and RAW_RE.match(text, i) and (i == 0 or not (text[i - 1].isalnum() or text[i - 1] == "_")):
            m = RAW_RE.match(text, i)
            end = text.find(")" + m.group(1) + '"', m.end())
            j = n if end < 0 else end + len(m.group(1)) + 2
            out.append('R""' + "".join("\n" if ch == "\n" else " " for ch in text[i + 3:j]))
            i = j
        elif c == '"' or (c == "'" and not (i and (text[i - 1].isalnum()) and d.isalnum())):
            q, j = c, i + 1
            out.append(q)
            while j < n and text[j] != q and text[j] != "\n":
                if text[j] == "\\" and j + 1 < n:
                    out.append("  " if text[j + 1] != "\n" else " \n")
                    j += 2
                    continue
                out.append(" ")
                j += 1
            if j < n and text[j] == q:
                out.append(q)
                j += 1
            i = j
        else:
            out.append(c)
            i += 1
    return "".join(out)


def strip_preprocessor(text):
    """Blank directives (and their continuation lines); inside #if/#else keep only the first branch."""
    out, skip, stack, cont = [], 0, [], False
    for line in text.split("\n"):
        was_cont, cont = cont, line.rstrip().endswith("\\")
        if was_cont:
            out.append("")
        elif IF_RE.match(line):
            stack.append(True)
            skip += 1 if skip else 0
            out.append("")
        elif ELSE_RE.match(line):
            if stack and stack[-1] and not skip:
                stack[-1], skip = False, 1
            out.append("")
        elif ENDIF_RE.match(line):
            if skip:
                skip -= 1
            if stack:
                stack.pop()
            out.append("")
        elif line.lstrip().startswith("#"):
            out.append("")
        else:
            out.append("" if skip else line)
    return "\n".join(out)


def check_text(text):
    clean = strip_preprocessor(blank_literals(text))
    stack, findings = [], []
    for ln, line in enumerate(clean.split("\n"), start=1):
        for ch in line:
            if ch in OPENERS:
                stack.append((ch, ln))
            elif ch in CLOSERS:
                if not stack:
                    findings.append({"line": ln, "rule": "unmatched-close", "bracket": ch})
                elif stack[-1][0] != CLOSERS[ch]:
                    op, opl = stack.pop()
                    findings.append({"line": ln, "rule": "mismatched-close", "bracket": ch, "opened": op, "opened_line": opl})
                else:
                    stack.pop()
    findings += [{"line": ln, "rule": "unclosed", "bracket": ch} for ch, ln in stack]
    return findings


def scan_file(path):
    path = Path(path)
    if path.suffix.lower() not in SUPPORTED:
        raise ValueError(f"unsupported source extension: {path.name}")
    try:
        data = path.read_bytes()
    except OSError as e:
        raise ValueError(f"cannot read {path.name}: {e}") from e
    if len(data) > MAX_FILE_BYTES:
        raise ValueError(f"file exceeds the {MAX_FILE_BYTES}-byte limit: {path.name}")
    if b"\x00" in data:
        raise ValueError(f"binary-looking input is not supported: {path.name}")
    return {"file": path.name, "findings": check_text(data.decode("utf-8", errors="replace"))}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="fieldkit repair brace-parity-check", description=__doc__.split("\n")[0])
    ap.add_argument("files", nargs="+")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if len(a.files) > MAX_FILES:
        emit(a.json, "FAIL", f"at most {MAX_FILES} files per call", "split the list")
        return 1
    try:
        results = [scan_file(f) for f in a.files]
    except ValueError as e:
        emit(a.json, "FAIL", str(e), "pass C or C++ source files (.c .cc .cpp .cxx .h .hh .hpp .hxx)")
        return 1
    count = sum(len(r["findings"]) for r in results)
    data = {"files_scanned": len(results), "finding_count": count, "results": results,
            "limitations": ["lexical check, not a compiler", "only the first branch of each preprocessor conditional"]}
    if count:
        if not a.json:
            for r in results:
                for f in r["findings"]:
                    print(f"{r['file']}:{f['line']}: {f['rule']} '{f['bracket']}'" +
                          (f" (opened '{f['opened']}' line {f['opened_line']})" if "opened" in f else ""))
        emit(a.json, "FAIL", f"{count} delimiter finding(s) in {len(results)} file(s)", "fix them before the compile", data)
        return 3
    emit(a.json, "PASS", f"{len(results)} file(s) balanced", "none", data)
    return 0
