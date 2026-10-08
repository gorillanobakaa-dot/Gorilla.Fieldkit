"""What a change costs or saves in memory and CPU, measured: the installed build against the same build with the change.

    fieldkit build-harness weigh <task> [pages=about:newtab,about:home,...] [reps=3]
                                        [tree-since=<commit|build>] [tree-until=<commit>]
                                        [sub=<old text>=><new text> ...] [omni=<jar>:<member>=<file> ...] [--json]

Born 2026-10-08. The owner asked what the one-logo technique and the removal of the small Gorilla icons save in RAM
and CPU cycles, and wanted the throwaway measuring scripts to become a tool "so when we introduce new changes or want
to see how much RAM and CPU we can save following a change or an icon/bmp/png/svg we remove, we have a tool for that".

How:
  1. two throwaway copies of the installed build are prepared: "before" (as installed) and "after" (with the change:
     the tree's commits in a range where the files are packaged as-is, URL rewrites, replaced members);
  2. every page is opened in a FRESH browser (new profile, nothing cached), REPS times per copy, interleaved
     before/after so a busy machine hurts both alike; probe image-memory reports the decoded pictures per process
     (Firefox's own memory reporter) and the CPU of every process from opening the page until it settled (6 s);
  3. medians are compared per page, and per picture.
Why one page per browser: measured in one browser one page after another, the 1400 px logo's full-size decode landed
on whichever page asked for a second size while an earlier page's copy was still cached (Settings in one run, Add-ons
in the next): the order was measured, not the change.

Limits, said in the report: CPU while a page opens is noisy (start-up work, the machine's own load); a headless
browser paints in software. Files built at build time (SCSS, bundles, preprocessed files, C++) cannot be applied to a
copy: use sub= for what they name, or measure a real build. Nothing is sent anywhere; only processes this started
are stopped. The result is kept as JSON under Fieldkit's state/weigh/.
"""
import json
import shutil
import statistics
import time
from pathlib import Path

from . import probe, task

DEFAULT_PAGES = ("about:newtab", "about:home", "about:preferences", "about:addons", "about:privatebrowsing", "about:blank")
WAIT = 8            # seconds after start-up before the probe opens the page


def parse(lines):
    """Probe image-memory lines for ONE page -> {"total": bytes, "cpu": ms or None, "pictures": {key: bytes}}."""
    out = {"total": None, "cpu": None, "pictures": {}}
    for line in lines:
        f = line.strip().split("|")
        if f[0] == "IMG" and len(f) >= 6:
            out["pictures"][f"{f[2]}|{f[3]}|{f[4]}"] = int(f[5])
        elif f[0] == "IMG-PAGE" and len(f) >= 4:
            out["total"] = int(f[3])
        elif f[0] == "IMG-CPU" and len(f) >= 4:
            out["cpu"] = float(f[3])
    return out


def summarise(runs):
    """{variant: {page: [parse() result per run]}} -> {page: {variant: {total, cpu, cpu_runs, pictures}}} (medians)."""
    out = {}
    for variant, pages in runs.items():
        for page, rs in pages.items():
            ok = [r for r in rs if r["total"] is not None]
            keys = {k for r in ok for k in r["pictures"]}
            out.setdefault(page, {})[variant] = {
                "runs": len(ok),
                "total": statistics.median([r["total"] for r in ok]) if ok else None,
                "cpu": statistics.median([r["cpu"] for r in ok if r["cpu"] is not None]) if any(r["cpu"] is not None for r in ok) else None,
                "cpu_runs": [r["cpu"] for r in ok],
                "pictures": {k: statistics.median([r["pictures"].get(k, 0) for r in ok]) for k in keys},
            }
    return out


def lines(summary, notes=()):
    mb = lambda n: "-" if n is None else f"{n / 1048576:6.1f}"
    out = [f"{'page':24} {'RAM before':>10} {'after':>7} {'saved':>7}   {'CPU before':>10} {'after':>7} {'saved':>7}  (median ms; runs)"]
    tot_b = tot_a = 0
    for page, v in summary.items():
        b, a = v.get("before", {}), v.get("after", {})
        if b.get("total") is not None and a.get("total") is not None:
            tot_b, tot_a = tot_b + b["total"], tot_a + a["total"]
        saved = (b["total"] - a["total"]) if b.get("total") is not None and a.get("total") is not None else None
        cpu_saved = (b["cpu"] - a["cpu"]) if b.get("cpu") is not None and a.get("cpu") is not None else None
        out.append(f"{page:24} {mb(b.get('total')):>10} {mb(a.get('total')):>7} {mb(saved):>7}   "
                   f"{b.get('cpu') or 0:>10.0f} {a.get('cpu') or 0:>7.0f} {cpu_saved or 0:>7.0f}  "
                   f"(before {', '.join(f'{x:.0f}' for x in b.get('cpu_runs', []))}; after {', '.join(f'{x:.0f}' for x in a.get('cpu_runs', []))})")
    out.append(f"{'all pages':24} {mb(tot_b):>10} {mb(tot_a):>7} {mb(tot_b - tot_a):>7}   (RAM in MB: decoded pictures, every process)")
    out.append("pictures whose memory changed most (median bytes, before -> after):")
    diffs = []
    for page, v in summary.items():
        pb, pa = v.get("before", {}).get("pictures", {}), v.get("after", {}).get("pictures", {})
        for k in set(pb) | set(pa):
            d = pb.get(k, 0) - pa.get(k, 0)
            if abs(d) >= 100_000:
                diffs.append((d, page, k, pb.get(k, 0), pa.get(k, 0)))
    for d, page, k, x, y in sorted(diffs, reverse=True)[:15]:
        proc, url, size = k.split("|")
        name = url.replace("\\", "/").rsplit("/", 1)[-1]          # the memory reporter writes chrome:\branding\...
        out.append(f"  {page:22} {proc:16} {name[:34]:34} {size:>10} {mb(x)} -> {mb(y)} MB")
    out += [f"note: {n}" for n in notes]
    return out


def run(install_dir, pages=DEFAULT_PAGES, reps=3, omni=None, subs=None, say=print, timeout=120):
    """-> {"summary", "runs", "patched"}; runs are interleaved before/after, page by page."""
    copies, runs, patched = {}, {"before": {}, "after": {}}, []
    try:
        copies["before"] = probe.prepare_copy(install_dir, "image-memory", wait=WAIT, say=say)
        copies["after"] = probe.prepare_copy(install_dir, "image-memory", wait=WAIT, omni=omni, subs=subs, say=say)
        patched = copies["after"][2]
        say(f"  weigh: {len(pages)} page(s) x {reps} run(s) x 2 builds, each in a fresh browser; after = {len(patched)} change(s)")
        for page in pages:
            for rep in range(1, reps + 1):
                for variant in ("before", "after") if rep % 2 else ("after", "before"):
                    app = copies[variant][1]
                    (Path(app) / "gprobe-image-memory.json").write_text(json.dumps({"pages": [page], "reps": 1}), encoding="utf-8")
                    r = probe.launch(app, timeout=timeout)
                    got = parse(r["lines"])
                    runs[variant].setdefault(page, []).append(got)
                    say(f"  weigh: {page} {variant} run {rep}: "
                        + (f"{got['total'] / 1048576:.1f} MB, {got['cpu']:.0f} ms" if got["total"] is not None and got["cpu"] is not None
                           else "no measurement" + ("" if r["done"] else " (timed out)")))
        return {"summary": summarise(runs), "runs": runs, "patched": patched}
    finally:
        for copy, _app, _p in copies.values():
            shutil.rmtree(copy, ignore_errors=True)


def save(result, notes=()):
    d = task.STATE.parent / "weigh"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"weigh-{time.strftime('%Y%m%d-%H%M%S')}.json"
    p.write_text(json.dumps({**result, "notes": list(notes)}, indent=1, default=str), encoding="utf-8")
    return p
