"""mozbuild's own rules that Python syntax does not check, found the hard way, each with a detector and a fixer.

empty-assignment   `DIRS = []` (or `+= []`, or a list holding only comments) is a Python statement mozbuild refuses:
                   "Variable DIRS assigned an empty value" (2026-10-02, build 11: excising "pingsender" left
                   `DIRS = [ # comment ]` in toolkit/components/telemetry/moz.build). Fix: the statement goes, a
                   comment keeps the record.

Used three times: by the verifier (a problem before any build), by `repair` (fixed automatically at the gate), and
by build-run's STOPS table (fixed and retried if one ever reaches mach).
"""
import ast
import re
from pathlib import Path

EMPTY_LOG = re.compile(r"Variable (\w+) assigned an empty value")
FILE_LOG = re.compile(r"The error occurred while processing the following file:\s*\n\s*\n\s*(\S+moz\.build)")


def empty_assignments(text):
    """-> [(line, variable)] for UPPER_CASE names assigned or extended with an empty list."""
    out = []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return out
    for node in ast.walk(tree):
        targets, value = [], None
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AugAssign):
            targets, value = [node.target], node.value
        if isinstance(value, ast.List) and not value.elts:
            for t in targets:
                if isinstance(t, ast.Name) and t.id.isupper():
                    out.append((node.lineno, t.id))
    return out


def _empty_nodes(tree):
    """(start_line, end_line, variable) of each empty UPPER_CASE list statement, 1-based."""
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AugAssign):
            target, value = node.target, node.value
        else:
            continue
        if isinstance(target, ast.Name) and target.id.isupper() and isinstance(value, ast.List) and not value.elts:
            out.append((node.lineno, node.end_lineno, target.id))
    return out


def fix_empty_assignments(path):
    """Remove every empty UPPER_CASE list statement from a moz.build, keeping its comments. -> [variables fixed]."""
    p = Path(path)
    raw = p.read_bytes()
    nl = "\r\n" if b"\r\n" in raw else "\n"
    text = raw.decode("utf-8").replace("\r\n", "\n")
    try:
        spans = _empty_nodes(ast.parse(text))
    except SyntaxError:
        return []
    lines = text.split("\n")
    for start, end, var in sorted(spans, reverse=True):
        kept = [l.strip() for l in lines[start - 1:end] if l.strip().startswith("#")]
        lines[start - 1:end] = kept + [f"# GORILLA: empty {var} assignment removed (mozbuild refuses an empty value)"]
    if spans:
        p.write_bytes(nl.join(lines).encode("utf-8"))
    return [v for _, _, v in sorted(spans)]


def from_log(lines):
    """-> (moz.build path, variable) from mach's error text, or (None, None)."""
    text = "\n".join(lines)
    m, f = EMPTY_LOG.search(text), FILE_LOG.search(text)
    return (f.group(1) if f else None), (m.group(1) if m else None)


# --- sorted lists (2026-10-03, build 17: "UnsortedError ... expected AIWindowStub.sys.mjs but got GorillaLinkMode") ---
# mozbuild keeps these variables as StrictOrderingOnAppendList: a literal list appended to them must be sorted,
# case-insensitively. A hand edit that inserts a file out of order stops the build at configure.
import re as _re

SORTED_VARS = ("EXTRA_JS_MODULES", "EXTRA_PP_JS_MODULES", "EXTRA_COMPONENTS", "EXTRA_PP_COMPONENTS", "MOZ_SRC_FILES",
               "TESTING_JS_MODULES", "XPIDL_SOURCES", "UNIFIED_SOURCES", "SOURCES", "EXPORTS", "JAR_MANIFESTS")
_LIST = _re.compile(r"^(?P<var>[A-Za-z_][\w.\[\]\"]*)\s*\+?=\s*\[\s*$")
_ITEM = _re.compile(r'^(?P<ind>\s*)"(?P<val>[^"]+)",\s*$')


def _blocks(lines):
    """-> [(var, start, end, [(value, [line indexes incl. its leading comments])])] for one-item-per-line lists."""
    out, i = [], 0
    while i < len(lines):
        m = _LIST.match(lines[i])
        if m and "[" not in m.group("var") and m.group("var").split(".")[0] in SORTED_VARS:   # not SOURCES["x"].flags
            items, pending, j, ok = [], [], i + 1, True
            while j < len(lines) and lines[j].strip() != "]":
                t = lines[j].strip()
                im = _ITEM.match(lines[j])
                if im:
                    items.append((im.group("val"), pending + [j]))
                    pending = []
                elif t.startswith("#") or not t:
                    pending.append(j)
                else:
                    ok = False                      # computed or multi-value lines: not ours to judge
                    break
                j += 1
            if ok and j < len(lines) and not pending:
                out.append((m.group("var"), i, j, items))
            i = j
        i += 1
    return out


def unsorted_lists(text):
    """-> [(line number, var, the first out-of-order value)] for sorted-only mozbuild lists."""
    lines = text.split("\n")
    bad = []
    for var, start, end, items in _blocks(lines):
        vals = [v for v, _ in items]
        for a, b in zip(vals, vals[1:]):
            if a.lower() > b.lower():
                bad.append((start + 1, var, b if vals.index(b) else a))
                break
    return bad


def fix_unsorted_lists(path):
    """Sort every sorted-only list in `path` case-insensitively, each value keeping its own leading comments.
    -> [var] fixed."""
    p = Path(path)
    raw = p.read_bytes().decode("utf-8")
    nl = "\r\n" if "\r\n" in raw else "\n"
    lines = raw.replace("\r\n", "\n").split("\n")
    fixed = []
    for var, start, end, items in reversed(_blocks(lines)):
        vals = [v for v, _ in items]
        if vals == sorted(vals, key=str.lower):
            continue
        body = []
        for v, idx in sorted(items, key=lambda it: it[0].lower()):
            body += [lines[k] for k in idx]
        lines[start + 1:end] = body
        fixed.append(var)
    if fixed:
        p.write_bytes(nl.join(lines).encode("utf-8"))
    return fixed


def unsorted_from_log(lines):
    """The moz.build path from mach's UnsortedError text, or None."""
    for l in lines:
        m = _re.search(r"\['([^']+moz\.build)'\]", l)
        if m and "Unsorted" in " ".join(lines):
            return m.group(1)
    return None
