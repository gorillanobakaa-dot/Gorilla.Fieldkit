# Version: 1.0.0 · updated 26-11-02-13-00
"""fieldkit refusals: a refusal repeated by any harness tool becomes a defect row, never an inbox item (plan R14, defect 8:
the train said "head moved" five times and nobody triaged it).

    fieldkit refusals list [--json]      every reason key, how often, and whether its defect row is open
    fieldkit refusals record TOOL TEXT   (tools call record() themselves; this is for a script)

Train, ship, guard, goal and task write each refusal to state/refusals.jsonl: tool, reason key (heads, ids and numbers
stripped, so "head moved (abc -> def)" is one reason), full text, time, session. The same key twice appends ONE row to
the table of state/coordination/ZERO-OPEN-PROBLEMS.md (next number, the problem, OPEN, "repeated refusal: KEY (N times)").
record() never raises: a ledger that cannot be written must not turn one refusal into two failures.
"""
import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

from .core.settings import state_root

ROOT = Path(__file__).resolve().parents[1]
LEDGER = "refusals.jsonl"
PROBLEMS = Path("coordination") / "ZERO-OPEN-PROBLEMS.md"
REPEAT = 2                                               # the same key this many times opens the row
MARK = "repeated refusal: "
_HEADER = ("# Open problems - the one list\n\nStatus: OPEN / PR / DONE.\n\n"
           "| # | Problem | Status | Proof needed / where |\n|---|---|---|---|\n")


def state(root=None):
    return state_root(root or ROOT) / "state"


def key_of(tool, text):
    """The reason key: the text with commit heads, ids, numbers and quoted names stripped, so the same refusal on
    another head or PR is the same key."""
    t = (text or "").lower()
    t = re.sub(r"\b[0-9a-f]{7,40}\b", "H", t)
    t = re.sub(r"\bt-\d{8}-\d{6}-[0-9a-f]+\b", "ID", t)
    t = re.sub(r"\d+(\.\d+)?", "N", t)
    t = re.sub(r"[\"'`][^\"'`]*[\"'`]", "Q", t)
    t = re.sub(r"\s+", " ", t).strip()
    return f"{tool}: {t[:100]}"


def _rows(root):
    p = state(root) / LEDGER
    out = []
    try:
        for ln in p.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(ln))
            except ValueError:
                continue
    except OSError:
        pass
    return out


def _open_row(root, key, n, text, tool):
    """Append the one row for `key` to the open-problems table unless it is already there. -> the row number or 0."""
    p = state(root) / PROBLEMS
    body = p.read_text(encoding="utf-8") if p.is_file() else _HEADER
    if f"{MARK}{key} (" in body:
        return 0
    lines = body.split("\n")
    nums = [int(m.group(1)) for x in lines for m in [re.match(r"\|\s*(\d+)\s*\|", x)] if m]
    num = max(nums, default=0) + 1
    last = max((i for i, x in enumerate(lines) if x.startswith("|")), default=len(lines) - 1)
    why = " ".join((text or "").split())[:160].replace("|", "/")
    lines.insert(last + 1, f"| {num} | A repeated refusal by {tool}: {why} | OPEN | {MARK}{key} ({n} times) |")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(lines), encoding="utf-8")
    return num


def record(tool, text, session=None, root=None):
    """Write one refusal; open the defect row when its key has now been seen REPEAT times. -> the row number opened
    (0 when none). Never raises."""
    try:
        key = key_of(tool, text)
        row = {"tool": tool, "key": key, "text": " ".join((text or "").split())[:600], "t": time.strftime("%Y-%m-%dT%H:%M:%S"),
               "session": session or os.environ.get("CLAUDE_SESSION_ID") or str(os.getpid())}
        d = state(root)
        d.mkdir(parents=True, exist_ok=True)
        with open(d / LEDGER, "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
        n = sum(1 for r in _rows(root) if r.get("key") == key)
        if n >= REPEAT:
            return _open_row(root, key, n, text, tool)
    except Exception:
        pass
    return 0


def summary(root=None):
    """-> [{key, tool, count, last, row_open}] newest first; row_open reads the defect table."""
    by = {}
    for r in _rows(root):
        e = by.setdefault(r.get("key"), {"key": r.get("key"), "tool": r.get("tool"), "count": 0, "last": ""})
        e["count"] += 1
        e["last"] = r.get("text", "")
    p = state(root) / PROBLEMS
    body = p.read_text(encoding="utf-8").split("\n") if p.is_file() else []
    for e in by.values():
        e["row_open"] = any(f"{MARK}{e['key']} (" in x and "| OPEN |" in x for x in body)
    return sorted(by.values(), key=lambda e: -e["count"])


def open_count(root=None):
    return sum(1 for e in summary(root) if e["row_open"])


def hook_text(root=None):
    """The room hook's line; "" when none is open. Never raises."""
    try:
        n = open_count(root)
    except Exception:
        return ""
    return f"{n} repeated refusals open - fieldkit refusals list" if n else ""


def main(argv=None):
    ap = argparse.ArgumentParser(prog="fieldkit refusals", description=__doc__.split("\n")[0])
    ap.add_argument("--json", action="store_true", help="list: JSON")
    sub = ap.add_subparsers(dest="cmd", required=True)
    lp = sub.add_parser("list")
    lp.add_argument("--json", action="store_true", dest="json_list")
    rp = sub.add_parser("record")
    rp.add_argument("tool")
    rp.add_argument("text")
    a = ap.parse_args(argv)
    if a.cmd == "record":
        num = record(a.tool, a.text)
        print(f"recorded; defect row {num} opened" if num else "recorded")
        return 0
    s = summary()
    if a.json or a.json_list:
        print(json.dumps(s, indent=1))
        return 0
    for e in s:
        print(f"{e['count']:>3}x {'OPEN ' if e['row_open'] else '     '}{e['key']}")
    if not s:
        print("no refusals recorded")
    print("NEXT: " + ("triage each OPEN row in ZERO-OPEN-PROBLEMS.md (a repeated reason is a defect)" if open_count() else "nothing open"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
