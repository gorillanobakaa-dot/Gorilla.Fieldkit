# Version: 1.0.0 · updated 26-10-19
"""Prove a CHECK can fail (docs/METHODS.md #18), the sibling of `fieldkit mutate`, which proves a TEST can fail.

    fieldkit canary list                  every registered canary and witness
    fieldkit canary run [ID ...]          each check on a planted input it MUST flag, and on a clean control it must pass
    fieldkit canary witness [ID ...]      a check's reading of the source against an independent reading of the artifact

Born 2026-10-18: `fieldkit telemetry scan` passed Gorilla OpenCode with 0 findings while the shipped program carried a
tracing library 1,036 times. The scan was not wrong about what it read; it read only the source, and its pattern wanted
the library's exporter by name. Two questions caught it: does the check fail on a file planted to fail it (a canary), and
does an independent reading of the shipped artifact agree (a second witness). Both are now tools.

A canary (fieldkit/method/canaries.yaml, plus the owner's local/canaries.yaml for machine paths) is data:

    - id: telemetry-scan
      check: telemetry-scan                       # the card id the check runs under (for the gate and the report)
      why: "a planted Sentry call is found"
      canary: {files: {main.rs: [<parts joined at run time>]}}
      control: {files: {main.rs: ["fn main() {}"]}}
      run: [python, -m, fieldkit, telemetry, scan, "{dir}"]
      flagged: {exit: [3]}                        # how the check says "found": exit codes and/or a stdout regex
      passed: {exit: [0]}                         # how it says "clean"

File contents are lists of parts joined at run time, so the registry itself never trips the privacy or telemetry
scanners that it exists to test. {dir} is a fresh temporary folder holding the files; {python} is this interpreter.

Verdicts per canary: OK (flags the canary, passes the control), BLIND (passes the canary: the check cannot see what it
exists to see), FALSE-ALARM (flags the clean control: a check that fails everything proves nothing either),
UNAVAILABLE (the check did not run: missing command or tool), ERROR (an answer that is neither flagged nor passed).
Exit 0 only when every canary run is OK; 3 otherwise; 2 for bad input.

A witness runs two commands that must agree on clean / not clean: the source reading and the artifact reading.
DISAGREE is a finding (exit 3), never averaged away.

Every check runs OFFLINE (fieldkit/core/offline.py): no name is looked up on the real network, and each name a check
tried is listed in its row as `lookups_refused`. 2026-10-19: before this, the leak-gate canaries made a real DNS query
for Mozilla's telemetry host on every run, and the gate's DNS witness saw it.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

from ..core import offline, settings

HERE = Path(__file__).resolve().parent
REGISTRY = HERE / "canaries.yaml"


def local_registry():
    return settings.ROOT / "local" / "canaries.yaml"


def load(files=None):
    """-> {"canaries": [...], "witnesses": [...]} from the public registry and the owner's local one (merged, local
    wins on an id). Duplicate ids within one file are refused."""
    out = {"canaries": {}, "witnesses": {}}
    for f in files or [REGISTRY, local_registry()]:
        f = Path(f)
        if not f.is_file():
            continue
        data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        for kind in ("canaries", "witnesses"):
            seen = set()
            for item in data.get(kind) or []:
                if item["id"] in seen:
                    raise ValueError(f"{f}: duplicate {kind[:-1]} id {item['id']!r}")
                seen.add(item["id"])
                out[kind][item["id"]] = dict(item, registry=str(f))   # not "source": a witness has a source side
    return {k: list(v.values()) for k, v in out.items()}


def _content(parts):
    return "".join(parts) if isinstance(parts, list) else str(parts)


def _plant(spec, root):
    for rel, parts in (spec.get("files") or {}).items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(_content(parts).encode("utf-8"))


def _argv(run, folder):
    sub = {"{dir}": str(folder), "{python}": sys.executable}
    return [sub.get(a, a.replace("{dir}", str(folder))) for a in run]


def _matches(rule, rc, out):
    if not rule:
        return False
    if "exit" in rule and rc not in rule["exit"]:
        return False
    if "stdout" in rule and not re.search(rule["stdout"], out, re.M):
        return False
    return True


LAST_REFUSED = []   # the names the last _run's check tried to look up (refused by the offline guard)


def _run(argv, cwd=None, timeout=600):
    """Run a check offline. -> (exit code or None, output); the names it tried to look up land in LAST_REFUSED."""
    LAST_REFUSED.clear()
    with tempfile.TemporaryDirectory(prefix="fkoffline-") as d:
        log = Path(d) / "refused.txt"
        try:
            r = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=cwd,
                               timeout=timeout, env=offline.child_env(log))
        except FileNotFoundError as e:
            return None, f"not found: {e}"
        except subprocess.TimeoutExpired:
            return None, "timed out"
        finally:
            if log.is_file():
                LAST_REFUSED.extend(sorted(set(log.read_text(encoding="utf-8").split())))
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def judge(c, side, rc, out):
    """-> "flagged" | "passed" | "unavailable" | "error" for one side's answer."""
    if rc is None:
        return "unavailable"
    if _matches(c.get("flagged"), rc, out):
        return "flagged"
    if _matches(c.get("passed"), rc, out):
        return "passed"
    if c.get("unavailable") and _matches(c["unavailable"], rc, out):
        return "unavailable"
    return "error"


def run_one(c, timeout=600):
    """Plant the canary and the control in separate temporary folders, run the check on each, judge."""
    row = {"id": c["id"], "check": c.get("check", c["id"]), "why": c.get("why", "")}
    sides = {}
    for side in ("canary", "control"):
        with tempfile.TemporaryDirectory(prefix=f"fkcanary-{side}-") as d:
            folder = Path(d)
            spec = c.get(side) or {}
            _plant(spec, folder)
            # a side may carry its own command line (a check that judges a command, not files: the tool router)
            rc, out = _run(_argv(spec.get("run", c["run"]), folder), timeout=timeout)
            sides[side] = {"exit": rc, "answer": judge(c, side, rc, out), "tail": out[-400:]}
            if LAST_REFUSED:
                sides[side]["lookups_refused"] = list(LAST_REFUSED)
    row.update(sides)
    a, b = sides["canary"]["answer"], sides["control"]["answer"]
    if "unavailable" in (a, b):
        row["verdict"] = "UNAVAILABLE"
    elif a == "flagged" and b == "passed":
        row["verdict"] = "OK"
    elif a == "passed":
        row["verdict"] = "BLIND"
    elif b == "flagged":
        row["verdict"] = "FALSE-ALARM"
    else:
        row["verdict"] = "ERROR"
    return row


def witness_one(w, timeout=1800):
    """Two independent readings that must agree on clean / not clean."""
    row = {"id": w["id"], "check": w.get("check", w["id"]), "why": w.get("why", "")}
    for side in ("source", "artifact"):
        spec = w[side]
        missing = [p for p in spec.get("requires", []) if not Path(p).exists()]
        if missing:
            row[side] = {"answer": "unavailable", "why": f"missing: {', '.join(missing)}"}
            continue
        with tempfile.TemporaryDirectory(prefix="fkwitness-") as empty:   # {empty}: e.g. no source beside an artifact
            rc, out = _run([a.replace("{empty}", empty).replace("{python}", sys.executable) for a in spec["run"]],
                           timeout=timeout)
        row[side] = {"exit": rc, "answer": judge(spec, side, rc, out), "tail": out[-400:]}
        if LAST_REFUSED:
            row[side]["lookups_refused"] = list(LAST_REFUSED)
    a, b = row["source"]["answer"], row["artifact"]["answer"]
    if "unavailable" in (a, b) or "error" in (a, b):
        row["verdict"] = "UNAVAILABLE" if "unavailable" in (a, b) else "ERROR"
    else:
        row["verdict"] = "AGREE" if a == b else "DISAGREE"
    return row


def select(items, ids):
    if not ids:
        return items
    known = {i["id"]: i for i in items}
    unknown = [i for i in ids if i not in known]
    if unknown:
        raise KeyError(", ".join(unknown))
    return [known[i] for i in ids]


def gate(ids=None, registry=None):
    """For a build or release gate: -> (ok, rows). ok only when every selected canary is OK: a blind check cannot sign
    a release."""
    reg = load(registry)
    rows = [run_one(c) for c in select(reg["canaries"], ids)]
    return all(r["verdict"] == "OK" for r in rows), rows


def lines(rows, witness=False):
    out = []
    for r in rows:
        out.append(f"{r['verdict']:<12} {r['id']}: {r['why']}")
        if r["verdict"] not in ("OK", "AGREE"):
            for side in (("source", "artifact") if witness else ("canary", "control")):
                s = r.get(side, {})
                out.append(f"      {side}: {s.get('answer')} (exit {s.get('exit')}) {s.get('why', '')}"
                           + (f"\n        {s.get('tail', '').strip()[-200:]}" if s.get("tail") else ""))
    bad = [r for r in rows if r["verdict"] not in ("OK", "AGREE")]
    out.append(("WITNESS: " if witness else "CANARY: ")
               + (f"{len(rows)} checked, all {'agree' if witness else 'can fail'}" if not bad else
                  f"{len(bad)} of {len(rows)} not proven: " + ", ".join(f"{r['id']} {r['verdict']}" for r in bad)))
    return out


def main(argv=None, prog="fieldkit canary"):
    ap = argparse.ArgumentParser(prog=prog, description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["list", "run", "witness"])
    ap.add_argument("ids", nargs="*")
    ap.add_argument("--registry", action="append", help="a registry file instead of the default ones (repeatable)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        reg = load(a.registry)
    except (ValueError, KeyError, yaml.YAMLError) as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2
    if a.action == "list":
        rows = [{"kind": "canary", "id": c["id"], "check": c.get("check"), "why": c.get("why")} for c in reg["canaries"]]
        rows += [{"kind": "witness", "id": w["id"], "check": w.get("check"), "why": w.get("why")} for w in reg["witnesses"]]
        print(json.dumps(rows, indent=1) if a.json else "\n".join(f"{r['kind']:<8} {r['id']:<28} {r['why']}" for r in rows))
        return 0
    try:
        items = select(reg["canaries" if a.action == "run" else "witnesses"], a.ids)
    except KeyError as e:
        print(f"REFUSED: unknown id {e}", file=sys.stderr)
        return 2
    rows = [run_one(c) if a.action == "run" else witness_one(c) for c in items]
    good = ("OK",) if a.action == "run" else ("AGREE",)
    print(json.dumps(rows, indent=1) if a.json else "\n".join(lines(rows, witness=a.action == "witness")))
    return 0 if rows and all(r["verdict"] in good for r in rows) else 3


if __name__ == "__main__":
    sys.exit(main())
