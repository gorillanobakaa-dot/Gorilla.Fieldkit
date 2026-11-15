"""Insert one row (a Board row, a Waiting row) into a markdown file before an anchor line.

Kiro's version searched the anchor anywhere in the file and cut the file in the middle of a line when it matched
mid-line. Here the anchor must be the start of exactly one line (leading spaces ignored); zero or several matches
are refused. The row goes in with the file's own line ending; a row already in the file is not added twice (that
is success, the job is done). `fieldkit board add` writes a session's own Board row; this puts any row anywhere.

    fieldkit repair plan-row-insert FILE --anchor TEXT_OR_FILE --row TEXT_OR_FILE [--apply] [--json]
Exit: 0 inserted or already there, 1 error, 3 anchor missing/ambiguous, or the row is ready (without --apply).
"""
import argparse
import os
from pathlib import Path

from ._common import emit, write_bytes


def insert_row_bytes(doc, anchor, row):
    """-> (new_doc, changed). Raises ValueError: anchor missing, or matching more than one line."""
    anchor, row = anchor.strip(), row.strip()
    if not anchor or not row:
        raise ValueError("anchor and row must not be empty")
    nl = b"\r\n" if b"\r\n" in doc[:4000] else b"\n"
    if row in doc:
        return doc, False
    starts, pos = [], 0
    for line in doc.split(b"\n"):
        if line.lstrip().startswith(anchor):
            starts.append(pos)
        pos += len(line) + 1
    if not starts:
        raise ValueError("anchor not found at the start of any line")
    if len(starts) > 1:
        raise ValueError(f"anchor starts {len(starts)} lines; give a longer anchor")
    at = starts[0]
    return doc[:at] + row + nl + doc[at:], True


def _arg(v):
    p = Path(v)
    return p.read_bytes() if len(v) < 260 and p.is_file() else v.encode("utf-8")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="fieldkit repair plan-row-insert", description=__doc__.split("\n")[0])
    ap.add_argument("target")
    ap.add_argument("--anchor", required=True, help="anchor text, or a file holding it")
    ap.add_argument("--row", required=True, help="row text, or a file holding it")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if not os.path.isfile(a.target):
        emit(a.json, "FAIL", f"target not found: {a.target}", "pass an existing markdown file")
        return 1
    doc = Path(a.target).read_bytes()
    try:
        out, changed = insert_row_bytes(doc, _arg(a.anchor), _arg(a.row))
    except ValueError as e:
        emit(a.json, "FAIL", str(e), "look at the anchor line in the target file")
        return 3
    if not changed:
        emit(a.json, "PASS", "the row is already in the file", "none", {"changed": False})
        return 0
    if not a.apply:
        emit(a.json, "FAIL", "the row would be inserted", f"fieldkit repair plan-row-insert {a.target} --anchor ... --row ... --apply", {"changed": False})
        return 3
    write_bytes(a.target, out)
    emit(a.json, "PASS", "row inserted", "check the table with git diff", {"changed": True})
    return 0
