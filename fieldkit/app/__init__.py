"""Find and start any program by name, on Windows, Linux and macOS.

    fieldkit app find NAME [--json]            every match: path, version, how found, running now
    fieldkit app start NAME [-- ARGS]          start it detached the way the OS does; new PID, verified after 3 s

NAME is matched without case, spaces or dashes ("Gorilla OpenCode" = gorilla-opencode). Several different programs
match: they are listed and nothing starts. Exit 0 fine, 1 not started, 3 not found or ambiguous.
"""
from __future__ import annotations

import argparse
import json
import sys

from . import find as F


def main(argv=None, prog="fieldkit app"):
    argv = list(argv if argv is not None else sys.argv[1:])
    args = []
    if "--" in argv:
        i = argv.index("--")
        argv, args = argv[:i], argv[i + 1:]
    ap = argparse.ArgumentParser(prog=prog, description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    for n in ("find", "start"):
        p = sub.add_parser(n)
        p.add_argument("name", nargs="+")
        p.add_argument("--json", action="store_true")
    ns = ap.parse_args(argv)
    if not ns.cmd:
        ap.print_help()
        return 2
    query = " ".join(ns.name)
    matches = F.find(query)
    if ns.cmd == "find":
        if ns.json:
            print(json.dumps(matches, indent=1))
        else:
            print(f"{len(matches)} match(es) for {query!r}")
            print("\n".join(F.render(matches)))
            print("NEXT: " + ("fieldkit app start " + repr(matches[0]["name"]) if matches else "check the spelling, or install it"))
        return 0 if matches else 3
    from . import start as S
    m, why = F.pick(matches)
    if m is None:
        print(f"NOT STARTED: {query!r}: {why}")
        print("\n".join(F.render(matches)))
        print("NEXT: name one of them exactly" if matches else "NEXT: fieldkit app find with another spelling")
        return 3
    r = S.start(m, args)
    if ns.json:
        print(json.dumps(r, indent=1))
    else:
        print(f"{'STARTED' if r['ok'] else 'NOT STARTED'} {r['name']} via {r['method']}: {r['why']}")
        print(f"  {r['path']}")
        print("NEXT: " + (f"stop it by PID {r['pid']} when done" if r["ok"] else "read the reason above"))
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
