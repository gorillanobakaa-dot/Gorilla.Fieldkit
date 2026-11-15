"""Git conflict blocks in an append-only file (a log, a Board table, a list where both sides added their own lines):
keep ours, then theirs. With --apply the markers go and both sides stay.

Fails closed: a file with an unclosed or stray marker is refused and nothing is written; the generated registries
(tools.yaml, platform_index.json, groups.yaml) are refused too, `fieldkit merge-driver` merges those entry by
entry. diff3 base sections (`|||||||`) are dropped. Each line keeps its own line ending (CRLF or LF).

    fieldkit repair keep-both FILE... [--apply] [--json]
Exit: 0 resolved (or none and --apply), 1 error, 3 conflict blocks found (without --apply) or none found.
"""
import argparse
import re
from pathlib import Path

from ._common import emit, write_bytes

BLOCK = re.compile(
    rb"^<<<<<<< [^\n]*\n(.*?)(?:^\|\|\|\|\|\|\| [^\n]*\n.*?)?^=======\r?\n(.*?)^>>>>>>> [^\n]*(?:\n|\Z)",
    re.S | re.M)
STRAY = re.compile(rb"^(?:<<<<<<< |=======\r?$|>>>>>>> )", re.M)
GENERATED = {"tools.yaml", "platform_index.json", "groups.yaml"}


def resolve_bytes(raw):
    """-> (bytes, blocks). Raises ValueError when a marker is left over after the blocks are resolved."""
    def keep(m):
        a, b = m.group(1), m.group(2)
        if a and not a.endswith(b"\n"):
            a += b"\r\n" if a.endswith(b"\r") else b"\n"
        return a + b
    out, n = BLOCK.subn(keep, raw)
    if STRAY.search(out):
        raise ValueError("a conflict marker is left that is not part of a complete block")
    return out, n


def main(argv=None):
    ap = argparse.ArgumentParser(prog="fieldkit repair keep-both", description=__doc__.split("\n")[0])
    ap.add_argument("files", nargs="+")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    plan, total = [], 0
    for f in a.files:
        p = Path(f)
        if not p.is_file():
            emit(a.json, "FAIL", f"file not found: {p}", "pass existing conflicted files")
            return 1
        if p.name in GENERATED:
            emit(a.json, "FAIL", f"{p.name} is a generated registry: both sides cannot simply stay", "fieldkit merge-driver")
            return 1
        try:
            out, n = resolve_bytes(p.read_bytes())
        except ValueError as e:
            emit(a.json, "FAIL", f"{p.name}: {e}; nothing written", "resolve it by hand")
            return 1
        plan.append((p, out, n))
        total += n
    if total == 0:
        emit(a.json, "FAIL", f"0 conflict blocks in {len(plan)} file(s)", "git status --porcelain lists the unmerged paths")
        return 3
    if not a.apply:
        emit(a.json, "FAIL", f"{total} conflict block(s) in {len(plan)} file(s) would keep both sides",
             "fieldkit repair keep-both FILE... --apply", {"blocks": total})
        return 3
    for p, out, n in plan:
        if n:
            write_bytes(p, out)
    emit(a.json, "PASS", f"kept both sides of {total} block(s) in {len(plan)} file(s)", "git add the resolved files", {"blocks": total})
    return 0
