# Version: 1.0.0 · updated 26-11-13
"""`fieldkit compileonce check`: no Fieldkit tool or hook starts `python -c`, `go run` or a bytecode-less Python per call.

    fieldkit compileonce check [--root DIR] [--json]     exit 0 clean, 3 findings (file:line, rule, fix)

The rules, the scan roots and the allowed one-offs are data (rules.yaml). A hit not in `allow` is a finding; an `allow`
row that matches nothing any more is reported as STALE (data kept honest, exit 3 too). Linux, Windows, macOS: it
only reads text."""
import argparse
import json
import re
import sys
from pathlib import Path

import yaml

RULES = Path(__file__).with_name("rules.yaml")


def load(path=None):
    d = yaml.safe_load(Path(path or RULES).read_text(encoding="utf-8"))
    for r in d["rules"]:
        r["_re"] = re.compile("|".join(r["patterns"]))
    return d


def _files(root, scan):
    skip = set(scan["skip_dirs"])
    suf = set(scan["suffixes"])
    for top in scan["roots"]:
        base = Path(root) / top
        if not base.exists():
            continue
        for p in sorted(base.rglob("*")):
            if p.is_file() and p.suffix in suf and not (skip & set(p.relative_to(root).parts)):
                yield p


def scan_text(text, rules):
    """-> [(line_no, rule_id)] for one file's text."""
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        for r in rules:
            if r["_re"].search(line):
                out.append((n, r["id"]))
    return out


def check(root=".", data=None):
    d = data or load()
    root = Path(root)
    allow = {(a["file"], a["rule"]) for a in d["allow"]}
    used, findings = set(), []
    fixes = {r["id"]: r["fix"] for r in d["rules"]}
    for p in _files(root, d["scan"]):
        rel = p.relative_to(root).as_posix()
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for n, rid in scan_text(text, d["rules"]):
            if (rel, rid) in allow:
                used.add((rel, rid))
            else:
                findings.append({"file": rel, "line": n, "rule": rid, "fix": fixes[rid]})
    stale = sorted(a for a in allow if a not in used and (root / a[0]).exists())
    return {"findings": findings, "stale_allow": [{"file": f, "rule": r} for f, r in stale]}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="fieldkit compileonce")
    ap.add_argument("verb", choices=["check"])
    ap.add_argument("--root", default=None, help="the repo (default: this checkout)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    root = a.root or Path(__file__).resolve().parents[2]
    res = check(root)
    bad = bool(res["findings"] or res["stale_allow"])
    if a.json:
        print(json.dumps(res, indent=1))
    else:
        for f in res["findings"]:
            print(f"FINDING {f['file']}:{f['line']} [{f['rule']}] {f['fix']}")
        for s in res["stale_allow"]:
            print(f"STALE   allow row {s['file']} [{s['rule']}] matches nothing: remove it")
        print("compileonce: " + ("findings above" if bad else "OK, no per-call compile"))
        print("NEXT: " + ("fix the lines, or add an allow row with a reason for a true one-off" if bad else "carry on"))
    return 3 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
