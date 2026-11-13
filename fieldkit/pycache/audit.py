"""Is Python compiled once on the hot path? Read-only, offline, no model.

    fieldkit pycache audit [--repo PATH] [--timing] [--no-pth] [--json]

Prints: .pyc counts (fresh/stale/missing) of fieldkit/*.py against the cache this Python reads (PYTHONPYCACHEPREFIX
aware); the same for every module the hot-path hooks load (fieldkit/pycache/hotpath.yaml); whether the .pth that keeps
bytecode writing on despite PYTHONDONTWRITEBYTECODE=1 is installed, and where that variable comes from; with --timing,
the import time of each hot module cold (no cache) vs warm.
Exit 3: a hot module is stale, missing or does not import; the .pth is missing (not in CI, or with --no-pth); a
hot module's cold-minus-warm cost or warm cost is above the YAML budget.
"""
import argparse
import os
import sys

from . import core
from .out import emit


def audit(repo, with_timing=False, need_pth=True, runs=None):
    cfg = core.hotpath()
    mods = [m["module"] for m in cfg["modules"]]
    allf = core.sources(repo)
    total = core.count(allf)
    files, bad = core.closure(repo, mods)
    hot = core.count(files)
    hot_bad = [str(f) for f in files if core.state(f) != "fresh"]
    findings = []
    for m, why in bad.items():
        findings.append(f"hot module {m} does not import: {why}")
    if hot_bad:
        findings.append(f"{len(hot_bad)} file(s) the hooks load are not compiled: {', '.join(os.path.relpath(f, repo) for f in hot_bad[:6])}"
                        + (" ..." if len(hot_bad) > 6 else ""))
    pth_ok, pth_path = core.pth_status(cfg)
    if need_pth and not pth_ok:
        findings.append(f"the bytecode .pth is missing ({pth_path}): hooks that run with PYTHONDONTWRITEBYTECODE=1 recompile on every call")
    times = {}
    if with_timing:
        times = core.timing(repo, mods, runs or cfg.get("timing_runs", 3))
        b = cfg["budget"]
        for m, (cold, warm) in times.items():
            if cold is None or warm is None:
                findings.append(f"timing: {m} did not import in the copy")
                continue
            if cold - warm > b["extra_cold_ms"]:
                findings.append(f"timing: {m} costs {cold - warm:.0f} ms extra when cold (budget {b['extra_cold_ms']})")
            if warm > b["warm_ms"]:
                findings.append(f"timing: {m} imports in {warm:.0f} ms warm (budget {b['warm_ms']})")
    return {"repo": str(repo), "cache": os.environ.get("PYTHONPYCACHEPREFIX") or "next to the sources (__pycache__)",
            "all": total, "all_files": len(allf), "hot": hot, "hot_files": len(files), "hot_bad": hot_bad,
            "pth": {"ok": pth_ok, "path": str(pth_path)}, "env_origin": core.env_origin(),
            "timing_ms": {m: {"cold": c, "warm": w} for m, (c, w) in times.items()}, "findings": findings}


def main(argv):
    ap = argparse.ArgumentParser(prog="fieldkit pycache audit", description=__doc__.split("\n\n")[0])
    ap.add_argument("--repo")
    ap.add_argument("--timing", action="store_true", help="measure cold vs warm import time of each hot module")
    ap.add_argument("--no-pth", action="store_true", help="do not require the .pth (CI runners have no such env)")
    ap.add_argument("--runs", type=int)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    repo = core.repo_of(a.repo)
    need_pth = not a.no_pth and not os.environ.get("CI")
    d = audit(repo, a.timing, need_pth, a.runs)
    lines = [f"python: {sys.executable} (the .pth is checked in THIS interpreter: run the audit with the hooks' python)",
             f"repo {d['repo']}", f"cache: {d['cache']}",
             f"all fieldkit/*.py: ok {d['all']['fresh']} stale {d['all']['stale']} no-cache {d['all']['missing']} (of {d['all_files']})",
             f"hot path ({d['hot_files']} files the hooks load): ok {d['hot']['fresh']} stale {d['hot']['stale']} no-cache {d['hot']['missing']}",
             f"bytecode .pth: {'installed' if d['pth']['ok'] else 'MISSING'} ({d['pth']['path']})",
             "PYTHONDONTWRITEBYTECODE: " + (d["env_origin"] or "not set here")]
    for m, t in d["timing_ms"].items():
        c, w = t["cold"], t["warm"]
        lines.append(f"  {m:36s} cold {c:6.0f} ms  warm {w:6.0f} ms  extra {c - w:5.0f} ms" if c and w else f"  {m}: no timing")
    for f in d["findings"]:
        lines.append("FINDING: " + f)
    if d["findings"]:
        emit(a.json, "FINDINGS", f"{len(d['findings'])} problem(s)",
             "fieldkit pycache warm --install-pth (then audit again)", d, lines)
        return 3
    emit(a.json, "OK", "the hot path runs from bytecode", "nothing to do", d, lines)
    return 0
