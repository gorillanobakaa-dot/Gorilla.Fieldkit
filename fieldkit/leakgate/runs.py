# Version: 1.0.0 · updated 26-10-16-17-10
"""Every leak-gate run of a task: verdict, build, size, and EVERY file that cites it. A run that anything cites is
evidence and is kept; only an uncited run is free to remove.

    fieldkit build-harness leakgate-runs TASK            the table, then which runs are free and how much they hold

Born 2026-10-16: 139 GiB of runs were cleaned up "keeping the latest" by date. The run that the leak-gate baseline
came from (baseline.json from_run), the runs the 157 release notes, the claims audit and the dispositions cite, and
the run holding the observation behind the Widevine exception in allow.json were all chosen for removal. Three were
put back from the Recycle Bin in time; the Widevine observation's capture was already deleted for good. Decide by
what cites a run, never by its date.
"""
import json
import os
import re
from pathlib import Path

STAMP = re.compile(r"^\d{8}-\d{6}$")
#: where evidence is cited from (text files only); patches and source trees are not citations
TEXT = {".json", ".md", ".yaml", ".yml", ".txt", ".jsonl"}
SKIP_DIRS = {".git", "src", "patches", "node_modules", "__pycache__"}


def _size(d):
    n = b = 0
    for root, dirs, files in os.walk(d):
        for f in files:
            try:
                b += os.path.getsize(os.path.join(root, f))
                n += 1
            except OSError:
                pass
    return n, b


def _json(p):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def citations(stamps, roots, skip=()):
    """{stamp: [file, ...]} for every text file under `roots` that names a stamp (files inside `skip` excluded)."""
    want = set(stamps)
    found = {s: [] for s in want}
    pat = re.compile(r"\d{8}-\d{6}")
    skip = [Path(s) for s in skip]
    for r in roots:
        r = Path(r)
        if not r.exists():
            continue
        for root, dirs, files in os.walk(r):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            if any(Path(root) == s or s in Path(root).parents for s in skip):
                continue
            for f in files:
                p = Path(root) / f
                if p.suffix.lower() not in TEXT:
                    continue
                try:
                    if p.stat().st_size > 20_000_000:
                        continue
                    text = p.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                for m in set(pat.findall(text)) & want:
                    found[m].append(str(p))
    return found


def runs(task_dir, owner_root=None, extra_roots=()):
    """-> [{stamp, path, final, build, files, bytes, cited_by: [...], why_kept: [...]}] newest first."""
    task_dir = Path(task_dir)
    rows = []
    for d in sorted(task_dir.iterdir(), reverse=True) if task_dir.is_dir() else []:
        if not (d.is_dir() and STAMP.match(d.name)):
            continue
        t = _json(d / "test-results.json")
        m = _json(d / "build-manifest.json")
        n, b = _size(d)
        rows.append({"stamp": d.name, "path": str(d), "final": t.get("FINAL_RESULT"), "release_run": t.get("release_run"),
                     "build": m.get("BUILD") or t.get("BUILD"), "files": n, "bytes": b, "cited_by": [], "why_kept": []})
    stamps = [r["stamp"] for r in rows]
    roots = [task_dir] + ([Path(owner_root)] if owner_root else []) + [Path(x) for x in extra_roots]
    cites = citations(stamps, roots, skip=[Path(r["path"]) for r in rows])
    base = _json(Path(owner_root) / "leakgate" / "baseline.json") if owner_root else {}
    record = _json(Path(owner_root) / "state" / "leakgate_result.json") if owner_root else {}
    newest = rows[0]["stamp"] if rows else None
    for r in rows:
        r["cited_by"] = sorted(set(cites.get(r["stamp"], [])))
        if base.get("from_run") == r["stamp"]:
            r["why_kept"].append("the leak-gate baseline came from it (leakgate/baseline.json from_run)")
        if str(record.get("artifacts", "")).endswith(r["stamp"]):
            r["why_kept"].append("the harness record's last gate (state/leakgate_result.json)")
        if r["final"] == "PASS":
            r["why_kept"].append("a PASS")
        if r["stamp"] == newest:
            r["why_kept"].append("the newest run")
        if r["cited_by"]:
            r["why_kept"].append(f"cited by {len(r['cited_by'])} file(s)")
    return rows


def lines(rows, human):
    out = [f"{'run':16} {'final':6} {'build':15} {'size':>10}  kept because"]
    free = [r for r in rows if not r["why_kept"]]
    for r in rows:
        out.append(f"{r['stamp']:16} {str(r['final'] or '-'):6} {str(r['build'] or '-'):15} {human(r['bytes']):>10}  "
                   + ("; ".join(r["why_kept"]) if r["why_kept"] else "FREE: nothing cites it"))
        for c in r["cited_by"][:4]:
            out.append(f"{'':50}{c}")
        if len(r["cited_by"]) > 4:
            out.append(f"{'':50}... {len(r['cited_by']) - 4} more")
    out.append(f"{len(free)} run(s) free to remove, {human(sum(r['bytes'] for r in free))}; "
               f"{len(rows) - len(free)} kept as evidence")
    for r in free:
        out.append(f"  free: {r['path']}")
    return out
