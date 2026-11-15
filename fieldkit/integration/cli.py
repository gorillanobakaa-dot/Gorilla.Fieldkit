"""fieldkit integration check|self-test: is a capability built in? (docs/INTEGRATION.md)

    fieldkit integration check --repo PATH [--changed BASE] [--kind opencode|fieldkit] [--json]
    fieldkit integration self-test --kind opencode|fieldkit [--json]

A capability (a helper, a tool, a command) counts as built in only when six surfaces hold: 1 carried, 2 known to the
AI, 3 visible, 4 on the release page, 5 on every system, 6 tested. The rules per surface are data in
fieldkit/integration/surfaces.yaml. `check` prints one line per missing surface with the file to fix; --changed BASE
looks only at what `git diff BASE` adds or changes. `self-test` builds a fixture repository, seeds one omission per
surface and exits 0 only when every one is caught and the clean fixture passes.
Exit: 0 built in, 2 bad usage, 3 gaps.
"""
import argparse
import json
import sys
from pathlib import Path

from . import core, selftest


def _out(data, as_json, lines):
    if as_json:
        print(json.dumps(data, indent=1, ensure_ascii=False))
    else:
        for l in lines:
            print(l)
        print(f"NEXT: {data['next']}")


def cmd_check(a):
    root = Path(a.repo)
    if not root.is_dir():
        print(f"REFUSED: {a.repo} is not a folder", file=sys.stderr)
        return 2
    r = core.run_check(root, a.changed, a.kind)
    if r.get("error"):
        print(f"REFUSED: {r['error']}: {a.repo}", file=sys.stderr)
        return 2
    gaps = r["gaps"]
    lines = [f"GAP {g['kind']}:{g['capability']} [{g['surface']}] {g['what']} -> fix {g['fix']}" for g in gaps]
    scope = f"changed since {a.changed}" if a.changed else "all"
    lines.append(f"integration {r['kind']} ({scope}): {len(r['checked'])} capabilities checked, {len(gaps)} gaps"
                 if gaps else f"integration {r['kind']} ({scope}): {len(r['checked'])} capabilities checked, all built in")
    r["next"] = ("fix the files named above, then run `fieldkit integration check --repo %s` again" % a.repo
                 if gaps else "nothing to do")
    _out(r, a.json, lines)
    return 3 if gaps else 0


def cmd_selftest(a):
    ok, lines = selftest.run(a.kind)
    lines.append(f"self-test {a.kind}: " + ("every seeded omission was caught" if ok else "NOT every omission was caught"))
    data = {"ok": ok, "kind": a.kind, "lines": lines,
            "next": "nothing to do" if ok else "fix the rule or the reader named MISSED above (surfaces.yaml, core.py)"}
    _out(data, a.json, lines)
    return 0 if ok else 3


def main(argv=None):
    ap = argparse.ArgumentParser(prog="fieldkit integration", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="action", required=True)
    c = sub.add_parser("check", help="hold every capability of a repository to every surface")
    c.add_argument("--repo", required=True, help="the Gorilla.Opencode or Fieldkit checkout")
    c.add_argument("--changed", metavar="BASE", help="only capabilities `git diff BASE` adds or changes")
    c.add_argument("--kind", choices=["opencode", "fieldkit"], help="default: found from the repository")
    c.add_argument("--json", action="store_true")
    s = sub.add_parser("self-test", help="seed one omission per surface in a fixture; every one must be caught")
    s.add_argument("--kind", required=True, choices=["opencode", "fieldkit"])
    s.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    return cmd_check(a) if a.action == "check" else cmd_selftest(a)


if __name__ == "__main__":
    sys.exit(main())
