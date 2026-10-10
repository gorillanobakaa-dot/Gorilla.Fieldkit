"""Does a committed generated file still match its generator? (docs/METHODS.md #12) Reports; never rewrites.

    fieldkit drift --gen "go run ./cmd/schema" opencode-schema.json --cwd PATH
    fieldkit drift --gen "python tools/make_table.py" docs/table.md

When a committed artefact has drifted from its generator, regenerating it to land a one-line change turns that
change into a diff of hundreds of unrelated lines, and hides the drift inside your change. This runs the generator
(its stdout is the generated file), compares, and reports where they differ:

  - JSON: compared as data (key order ignored), and the differing paths named (mcpServers.x.trust, models[3]...)
  - anything else: compared line by line, the first differing lines shown

Then you decide: edit only your own entry by hand and report the drift, or regenerate in a commit of its own.
Exit 0 when they match, 3 when they drift, 2 when the generator fails or the file is missing.
"""
import argparse
import difflib
import json
import subprocess
from pathlib import Path

from . import emit, split_command


def json_paths(a, b, path="", out=None, limit=60):
    """The paths where two JSON values differ (lists compared by position)."""
    out = [] if out is None else out
    if len(out) >= limit:
        return out
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            p = f"{path}.{k}" if path else str(k)
            if k not in a:
                out.append(f"+ {p}")
            elif k not in b:
                out.append(f"- {p}")
            else:
                json_paths(a[k], b[k], p, out, limit)
    elif isinstance(a, list) and isinstance(b, list):
        for i in range(max(len(a), len(b))):
            p = f"{path}[{i}]"
            if i >= len(a):
                out.append(f"+ {p}")
            elif i >= len(b):
                out.append(f"- {p}")
            else:
                json_paths(a[i], b[i], p, out, limit)
    elif a != b:
        out.append(f"~ {path}: {json.dumps(a)[:60]} -> {json.dumps(b)[:60]}")
    return out


def drift(gen, file, cwd=None, timeout=600):
    f = Path(cwd or ".") / file if not Path(file).is_absolute() else Path(file)
    if not f.is_file():
        return {"ok": False, "error": f"no such file: {f}"}
    try:
        r = subprocess.run(gen, capture_output=True, text=True, cwd=cwd, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"ok": False, "error": f"the generator did not run: {e}"}
    if r.returncode != 0:
        return {"ok": False, "error": f"the generator failed (exit {r.returncode}): {r.stderr.strip()[-300:]}"}
    committed, generated = f.read_text(encoding="utf-8"), r.stdout
    try:
        a, b = json.loads(committed), json.loads(generated)
        diffs = json_paths(a, b)
        kind = "json"
    except ValueError:
        diffs = [l for l in difflib.unified_diff(committed.splitlines(), generated.splitlines(), "committed",
                                                 "generated", n=0, lineterm="") if not l.startswith(("---", "+++", "@@"))]
        kind = "text"
    return {"ok": True, "drift": bool(diffs), "kind": kind, "file": str(f), "differences": len(diffs),
            "first": diffs[:30],
            "next": ("edit only your own entry by hand and say the file has drifted; regenerate it in a commit of "
                     "its own if the owner wants that") if diffs else ""}


def main(argv=None, prog="fieldkit drift"):
    ap = argparse.ArgumentParser(prog=prog, description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file", help="the committed generated file")
    ap.add_argument("--gen", required=True, help="the generator command, as one string; it prints the file")
    ap.add_argument("--cwd")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    r = drift(split_command(a.gen), a.file, a.cwd)
    if not r["ok"]:
        emit(r, a.json, lambda d: [f"REFUSED: {d['error']}"])
        return 2
    emit(r, a.json, lambda d: ([f"DRIFT ({d['kind']}): {d['differences']} difference(s) between {d['file']} and "
                                "its generator"] + [f"  {x}" for x in d["first"]]) if d["drift"] else
         [f"OK: {d['file']} matches its generator"])
    return 3 if r["drift"] else 0
