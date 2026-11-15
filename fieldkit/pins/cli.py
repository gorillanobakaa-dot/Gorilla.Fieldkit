"""fieldkit pins check|self-test: helper sources must be immutable and sha256-pinned.

    fieldkit pins check --repo PATH [--json]
    fieldkit pins self-test [--json]

`check` reads third_party/PINS.md, internal/toolbox/SHA256SUMS, scripts/build-toolbox.ps1, scripts/build-toolbox.sh and
scripts/fetch-toolbox-extras.sh of a Gorilla OpenCode tree, finds every source URL and refuses (exit 3) each one that is
rolling (latest, nightly, rolling, autobuild, master/main/HEAD archives, refs/heads, unversioned paths) or has no
sha256 pin. `self-test` seeds each break kind into a temp copy of a fixture and proves check exits 3 for each.
Exit: 0 all pinned and immutable, 2 bad usage, 3 findings.
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
    findings, seen = core.scan(root)
    lines = [f"{f['kind'].upper()} {f['url']} ({f['file']}:{f['line']}) {f['why']}".replace("  ", " ")
             for f in findings]
    lines.append(f"pins: {len(set(seen))} source URLs read, {len(findings)} refused" if findings
                 else f"pins: {len(set(seen))} source URLs read, all immutable and sha256-pinned")
    data = {"ok": not findings, "repo": str(root), "urls": sorted(set(seen)), "findings": findings,
            "next": (f"pin each URL above to a fixed version and its sha256, then run `fieldkit pins check --repo {a.repo}` again"
                     if findings else "nothing to do")}
    _out(data, a.json, lines)
    return 3 if findings else 0


def cmd_selftest(a):
    ok, lines = selftest.run()
    lines.append("self-test: " + ("every break kind was refused and the clean tree passed" if ok
                                  else "NOT every break kind was refused"))
    _out({"ok": ok, "lines": lines, "next": "nothing to do" if ok else "fix the rule named MISSED above (pins/core.py)"},
         a.json, lines)
    return 0 if ok else 3


def main(argv=None):
    ap = argparse.ArgumentParser(prog="fieldkit pins", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="action", required=True)
    c = sub.add_parser("check", help="refuse rolling or unpinned helper source URLs")
    c.add_argument("--repo", required=True, help="the Gorilla OpenCode checkout")
    c.add_argument("--json", action="store_true")
    s = sub.add_parser("self-test", help="seed each break kind in a fixture; check must exit 3 for each")
    s.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    return cmd_check(a) if a.action == "check" else cmd_selftest(a)


if __name__ == "__main__":
    sys.exit(main())
