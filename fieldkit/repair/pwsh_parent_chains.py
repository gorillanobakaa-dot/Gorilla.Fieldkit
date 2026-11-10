"""Who opened all those consoles: the parent chain of every PowerShell, pwsh and conhost process, and the parents
that own the most of them (`fieldkit hog` reports a hoarding parent; this shows the whole chain up to the root).

Live process table by default (psutil, Windows/Linux/macOS); `--proc-json FILE` reads a saved list [{pid, ppid, name}].
Cycles and PID reuse cannot loop it: a chain stops at a repeated PID and at 25 links.

    fieldkit repair pwsh-parent-chains [--proc-json FILE] [--top N] [--json]
Exit: 0 traced, 1 error.
"""
import argparse
import json
import re
from collections import Counter
from pathlib import Path

from ._common import emit

TARGETS = ("powershell.exe", "pwsh.exe", "conhost.exe", "powershell", "pwsh", "conhost")
MAX_LINKS = 25


def build_chains(procs, targets=TARGETS):
    """-> [[(name, pid), ...]] one chain per target process, from the process up to its oldest known ancestor."""
    by_pid = {int(p["pid"]): p for p in procs}
    chains = []
    for p in procs:
        if (p.get("name") or "").lower() not in targets:
            continue
        chain, cur, seen = [], p, set()
        while cur is not None and int(cur["pid"]) not in seen and len(chain) < MAX_LINKS:
            pid = int(cur["pid"])
            seen.add(pid)
            chain.append((cur.get("name") or "?", pid))
            ppid = int(cur.get("ppid") or 0)
            cur = by_pid.get(ppid) if ppid and ppid != pid else None
        chains.append(chain)
    return chains


def owners(chains, targets=TARGETS):
    """-> Counter of 'name(pid)' for the first ancestor of each chain that is not itself a console/shell target."""
    c = Counter()
    for ch in chains:
        for name, pid in ch[1:]:
            if name.lower() not in targets:
                c[f"{name}({pid})"] += 1
                break
    return c


def live_table():
    import psutil
    out = []
    for p in psutil.process_iter(["name", "ppid"]):
        out.append({"pid": p.pid, "ppid": p.info.get("ppid"), "name": p.info.get("name")})
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(prog="fieldkit repair pwsh-parent-chains", description=__doc__.split("\n")[0])
    ap.add_argument("--proc-json", help="a saved process list instead of the live one")
    ap.add_argument("--top", type=int, default=5, help="how many owning parents to list")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.proc_json:
        try:
            procs = json.loads(Path(a.proc_json).read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            emit(a.json, "FAIL", f"cannot read the process list: {e}", "pass [{pid, ppid, name}]")
            return 1
    else:
        procs = live_table()
    chains = build_chains(procs)
    text = [" <- ".join(f"{n}({p})" for n, p in ch) for ch in chains]
    top = owners(chains).most_common(a.top)
    if not a.json:
        for t in text:
            print(t)
        for who, n in top:
            print(f"owner {who}: {n} console/shell process(es)")
    emit(a.json, "PASS", f"traced {len(chains)} console/shell chain(s)", "fieldkit hog check" if top and top[0][1] >= 10 else "none",
         {"chains": text, "owners": [{"owner": w, "count": n} for w, n in top]})
    return 0
