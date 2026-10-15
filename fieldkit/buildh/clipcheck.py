# Version: 1.1.0 · updated 26-10-15-16-38
"""Clipped text in the browser's own dialogs: measured, repeated, compared with stock, and traced to its cause.

Born 2026-10-15. The owner sent a screenshot of web.whatsapp.com's "Leave page?" dialog in Gorilla 158: the message
was cut to a sliver. How it was found by hand, and what this module now does by itself:

  1. MEASURE, do not look. Probe clipped-text (probes/clipped-text.js) opens each standard tab-modal dialog in a
     throwaway copy of the install and reports every element that shows less than its text needs. First numbers:
     the message needed 40 px and got 8 px; the dialog frame was 124 px.
  2. CONTROL. The same probe on stock Firefox of the same version (decode_test.stock_firefox, cached): 156 px frame,
     40 px of text. A difference the control does not have is ours.
  3. REPEAT. One run is not a verdict. A single run with the pristine CommonDialog.sys.mjs swapped in gave 156 px and
     looked like the cause; two more runs with the same swap gave 124 px. The dialog is sized ONCE, when it loads
     (SubDialog.resizeVertically reads the document height then), so a timing race can pass one run and fail the
     next. Every verdict here counts clipped runs out of `repeats`.
  4. SWAP, with a control swap. To blame a file, run the copy with the file replaced by its PRISTINE version (the
     work tree's root commit) AND, alternating, with an identical copy of the shipped file. Only "swap fixes every
     run while the identical copy clips every run" convicts the file.
  5. PREF A/B. A pref is tested the same way: set it in the probe's profile (GPROBE_PREFS), alternate with the
     shipped value. (ui.prefersReducedMotion 1 vs 0 was tested this way on 2026-10-15: not the cause.)
  6. THE MOMENT OF MEASUREMENT. The probe hooks SubDialog.prototype.resizeVertically and records every element's box
     right then and again when the dialog has settled. An element whose padding or margin CHANGED in between was
     measured too early. 2026-10-15: dialog#commonDialog padding 0 at measure time, 16 px after (16 + 16 = the
     32 px the frame lacked); in stock it was 16 px at measure time already.
  7. WHAT WAS MOVING. document.getAnimations() at that moment: a CSSTransition on padding-top, 0.01 ms long. While a
     transition runs, getComputedStyle returns the in-between value, so the rule says 16 px and the box says 0.
  8. WHICH RULE. InspectorUtils.getMatchingCSSRules(el) + getRuleLine(rule) (on a WINDOW global: win.InspectorUtils;
     Firefox 158 renamed getCSSStyleRules) name the sheet, line and selector of both: the padding
     (commonDialog.css:91 `:root[subdialog] dialog`) and the transition (master-redirect.css:67 `*, ::before,
     ::after { transition-duration: 0.01ms }`). The fix: `transition-property: none` inside :root[subdialog]: a
     0.01 ms transition still STARTS and is measured mid-way; `none` starts nothing.

    fieldkit build-harness clip-check <task> [repeats=3] [stock=0] [surface=leave-page]
                                             [swap=<path in the build>=<path in the tree> ...]   pristine file
                                             [try=<path in the build>=<local file> ...]          candidate fix
                                             [pref=<name>=<value> ...] [--install-dir D]
"""
import os
import re
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path

PROBE = "clipped-text"
LINE = re.compile(r"^CLIP\|(\w+)\|(.*)$")


def parse(lines):
    """Probe lines -> {"surfaces": {name: {"frame": [doc h, frame h] | None, "texts": [...], "size": "WxH",
    "measured": n}}, "errors": [...], "prefs": {...}}"""
    out = {"surfaces": {}, "errors": [], "prefs": {}}
    surf = lambda n: out["surfaces"].setdefault(n, {"frame": None, "texts": [], "size": "", "measured": 0,
                                                   "moved": [], "anims": [], "rules": [], "boxes": {}})
    for line in lines:
        m = LINE.match(line.strip())
        if not m:
            continue
        kind, f = m.group(1), m.group(2).split("|")
        if kind == "frame":
            surf(f[0])["frame"] = [int(f[1]), int(f[2])]
        elif kind == "text":
            surf(f[0])["texts"].append({"element": f[1], "need": int(f[2]), "got": int(f[3]), "text": "|".join(f[4:])})
        elif kind == "surface":
            s = surf(f[0])
            s["size"], s["measured"] = f[1], int(f[2])
        elif kind == "moved":
            surf(f[0])["moved"].append({"element": f[1], "property": f[2], "at_measure": f[3], "settled": f[4]})
        elif kind == "anim":
            surf(f[0])["anims"].append({"element": f[1], "kind": f[2], "property": f[3], "ms": f[4]})
        elif kind == "rule":
            surf(f[0])["rules"].append({"element": f[1], "property": f[2], "where": f[3], "selector": f[4],
                                        "value": "|".join(f[5:])})
        elif kind == "box" and len(f) >= 6:
            surf(f[0])["boxes"][f[2]] = {"height": f[3], "padding": f[4], "margin": f[5]}
        elif kind == "error":
            out["errors"].append(f"{f[0]}: {'|'.join(f[1:])}")
        elif kind == "pref":
            out["prefs"][f[0]] = f[1]
    return out


def _ms(v):
    try:
        return round(float(v), 3)
    except ValueError:
        return v


def explain(surface):
    """One surface of one run -> plain sentences saying WHY its text is cut (empty when nothing says why)."""
    out = []
    for m in surface["moved"]:
        el, prop = m["element"], m["property"]
        out.append(f"{el} {prop} was {m['at_measure']} when the dialog height was measured, {m['settled']} after")
        running = [a for a in surface["anims"] if a["element"].split(".")[0] == el.split(".")[0]
                   and a["property"] == prop]
        for a in running:
            out.append(f"  a {a['kind']} on {prop} ({_ms(a['ms'])} ms) was running at that moment, so the box still "
                       f"had its starting value")
        for r in surface["rules"]:
            if r["element"] == el and r["property"] == prop:
                out.append(f"  {prop} is set by {r['where']} `{r['selector']}`: {r['value']}")
        if running:
            for r in surface["rules"]:
                if r["element"] == el and r["property"] == "transition":
                    out.append(f"  the transition comes from {r['where']} `{r['selector']}`: {r['value']}")
    if not out and surface["anims"]:
        props = sorted({a["property"] for a in surface["anims"]})
        out.append(f"transitions/animations were running when the height was measured: {', '.join(props)}")
    return out


def box_diff(ours, stock):
    """Box trees at measure time, ours vs stock -> the first elements whose padding/margin differ."""
    rows = []
    for key, b in ours.get("boxes", {}).items():
        s = stock.get("boxes", {}).get(key)
        if s and (b["padding"] != s["padding"] or b["margin"] != s["margin"]):
            rows.append(f"{key.rsplit('/', 1)[-1]}: ours padding {b['padding']} margin {b['margin']}, "
                        f"stock padding {s['padding']} margin {s['margin']}")
    return rows[:5]


def clipped(parsed):
    """-> {surface: True when something in it does not fit} for every surface the run measured."""
    return {n: bool(s["frame"] or s["texts"]) for n, s in parsed["surfaces"].items() if s["size"]}


@contextmanager
def _env(prefs):
    old = os.environ.get("GPROBE_PREFS")
    os.environ["GPROBE_PREFS"] = ";".join(f"{k}={v}" for k, v in (prefs or {}).items())
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("GPROBE_PREFS", None)
        else:
            os.environ["GPROBE_PREFS"] = old


def run_once(install, prefs=None, files=None, probe_run=None):
    """One probe run on a throwaway copy of `install` -> parsed lines (plus "done")."""
    if probe_run is None:
        from .probe import run as probe_run
    with _env(prefs):
        r = probe_run(str(install), PROBE, wait=5, timeout=180, files=files, say=lambda m: None)
    p = parse(r["lines"])
    p["done"] = r["done"]
    return p


def tally(runs):
    """[parsed run] -> {surface: {"clipped": k, "runs": n, "worst": the largest shortfall seen}}"""
    out = {}
    for p in runs:
        for name, bad in clipped(p).items():
            t = out.setdefault(name, {"clipped": 0, "runs": 0, "worst": None})
            t["runs"] += 1
            if bad:
                t["clipped"] += 1
                s = p["surfaces"][name]
                cands = [(x["need"] - x["got"], f"{x['element']} needs {x['need']} px, gets {x['got']}") for x in s["texts"]]
                if s["frame"]:
                    cands.append((s["frame"][0] - s["frame"][1], f"frame {s['frame'][1]} px, content {s['frame'][0]} px"))
                worst = max(cands)
                if not t["worst"] or worst[0] > t["worst"][0]:
                    t["worst"] = worst
    return out


def verdict(ours, stock=None):
    """Per surface: CLEAN, OURS (clips here, never in stock), UPSTREAM (clips in stock too), or CLIPS (no stock
    control was run). 'always' or 'k of n' says whether it is steady or a race."""
    rows = []
    for name in sorted(ours):
        o = ours[name]
        s = (stock or {}).get(name)
        how = "always" if o["clipped"] == o["runs"] else f"{o['clipped']} of {o['runs']} runs"
        if not o["clipped"]:
            v = "CLEAN"
        elif s is None:
            v = "CLIPS"
        elif s["clipped"] == 0:
            v = "OURS"
        else:
            v = "UPSTREAM"
        rows.append({"surface": name, "verdict": v, "how": how if o["clipped"] else "",
                     "ours": f"{o['clipped']}/{o['runs']}", "stock": f"{s['clipped']}/{s['runs']}" if s else "-",
                     "worst": o["worst"][1] if o["worst"] else ""})
    return rows


def confirm(measure, repeats=3):
    """A swap (or pref) convicts only against a control: alternate `repeats` times between the control (identical
    copy / shipped value) and the change. measure(changed: bool) -> True when the surface clipped.
    -> {"confirmed", "control": [bool], "changed": [bool]}"""
    control, changed = [], []
    for _ in range(repeats):
        control.append(measure(False))
        changed.append(measure(True))
    ok = all(control) and not any(changed)
    return {"confirmed": ok, "control": control, "changed": changed}


def pristine_copy(workdir, tree_path, dest_dir):
    """The PRISTINE version of `tree_path` (the work tree's root commit) written to dest_dir -> Path, or None."""
    g = lambda *a: subprocess.run(["git", "-C", str(workdir), *a], capture_output=True)
    root = g("rev-list", "--max-parents=0", "HEAD").stdout.decode().split()
    if not root:
        return None
    r = g("show", f"{root[0]}:{tree_path}")
    if r.returncode != 0:
        return None
    p = Path(dest_dir) / ("pristine." + Path(tree_path).name)
    p.write_bytes(r.stdout)
    return p


def run(install, workdir=None, repeats=3, stock_dir=None, swaps=None, prefs=None, surface="leave-page",
        out=print, probe_run=None, tries=None):
    """The whole procedure. Without swaps/prefs: the build (and stock) `repeats` times each -> verdict rows.
    With swaps ({path in the build: path in the tree}, the tree file's PRISTINE version), tries ({path in the build:
    a local file}, a candidate fix, e.g. the edited tree file) or prefs ({name: value}): confirm() each against its
    control (an identical copy of the shipped file / the shipped pref) on `surface`. -> {"ok", "rows", "tests", "detail"}"""
    ours_runs, stock_runs, tests = [], [], []
    for i in range(repeats):
        ours_runs.append(run_once(install, probe_run=probe_run))
        if stock_dir:
            stock_runs.append(run_once(stock_dir, probe_run=probe_run))
        out(f"  run {i + 1}/{repeats}: ours {clipped(ours_runs[-1])}"
            + (f", stock {clipped(stock_runs[-1])}" if stock_dir else ""))
    rows = verdict(tally(ours_runs), tally(stock_runs) if stock_dir else None)
    for r in rows:
        if r["verdict"] in ("CLEAN", "UPSTREAM"):
            continue
        bad = [p for p in ours_runs if clipped(p).get(r["surface"])]
        if bad:
            r["why"] = explain(bad[-1]["surfaces"][r["surface"]])
            if stock_runs and r["surface"] in stock_runs[-1]["surfaces"]:
                r["vs_stock"] = box_diff(bad[-1]["surfaces"][r["surface"]], stock_runs[-1]["surfaces"][r["surface"]])
    with tempfile.TemporaryDirectory(prefix="clipcheck_") as tmp:
        for build_path, tree_path in (swaps or {}).items():
            same = Path(tmp) / ("shipped." + Path(build_path).name)
            src = Path(install) / build_path
            if not src.is_file() or workdir is None:
                tests.append({"test": f"swap {build_path}", "confirmed": False,
                              "why": "not a plain file in the build (packed omni.ja?) or no work tree"})
                continue
            same.write_bytes(src.read_bytes())
            prist = pristine_copy(workdir, tree_path, tmp)
            if prist is None:
                tests.append({"test": f"swap {build_path}", "confirmed": False,
                              "why": f"{tree_path} is not in the pristine release"})
                continue
            m = lambda changed: clipped(run_once(install, files={build_path: prist if changed else same},
                                                 probe_run=probe_run)).get(surface, False)
            tests.append({"test": f"swap {build_path} to pristine", **confirm(m, repeats)})
        for build_path, local in (tries or {}).items():
            src, cand = Path(install) / build_path, Path(local)
            if not src.is_file() or not cand.is_file():
                tests.append({"test": f"try {build_path}", "confirmed": False,
                              "why": f"no plain file {src} in the build, or no {cand}"})
                continue
            same = Path(tmp) / ("shipped." + src.name)
            same.write_bytes(src.read_bytes())
            m = lambda changed: clipped(run_once(install, files={build_path: cand if changed else same},
                                                 probe_run=probe_run)).get(surface, False)
            tests.append({"test": f"try {build_path} = {cand}", **confirm(m, repeats)})
        for name, value in (prefs or {}).items():
            m = lambda changed: clipped(run_once(install, prefs={name: value} if changed else None,
                                                 probe_run=probe_run)).get(surface, False)
            tests.append({"test": f"pref {name}={value}", **confirm(m, repeats)})
    lines = []
    for r in rows:
        lines.append(f"  {r['verdict']:9} {r['surface']:11} ours {r['ours']}  stock {r['stock']}"
                     + (f"  ({r['how']}; worst: {r['worst']})" if r["worst"] else ""))
        lines += [f"      why: {w}" for w in r.get("why", [])]
        lines += [f"      differs from stock at measure time: {d}" for d in r.get("vs_stock", [])]
    for t in tests:
        if "control" in t:
            lines.append(f"  {'CONFIRMED' if t['confirmed'] else 'not confirmed':13} {t['test']}: control clipped "
                         f"{sum(t['control'])}/{len(t['control'])}, changed clipped {sum(t['changed'])}/{len(t['changed'])}")
        else:
            lines.append(f"  {'not tested':13} {t['test']}: {t['why']}")
    ok = all(r["verdict"] in ("CLEAN", "UPSTREAM") for r in rows)
    lines.append("CLIP-CHECK OK: no dialog text of ours is cut" if ok else
                 "CLIP-CHECK NOT OK: dialog text is cut in this build (see OURS / CLIPS rows)")
    return {"ok": ok, "rows": rows, "tests": tests, "detail": "\n".join(lines)}



def rows(install, say=print, probe_run=None):
    """Post-install proof rows (group "ui"): one clipped-text run on a copy of the installed build, one row per
    dialog. A failure names the cause in words; `clip-check <task>` then repeats it against stock to confirm."""
    p = run_once(install, probe_run=probe_run)
    out = []
    for name, bad in sorted(clipped(p).items()):
        s = p["surfaces"][name]
        why = explain(s)
        worst = [f"{x['element']} needs {x['need']} px, gets {x['got']}" for x in s["texts"]][:1]
        out.append({"check": f"UI dialog text fits: {name}", "ok": not bad,
                    "evidence": ("; ".join(worst + why[:3]) + "; confirm with: fieldkit build-harness clip-check <task>")
                    if bad else f"frame {s['size']}, {s['measured']} text element(s), nothing cut"})
    if not out:
        out.append({"check": "UI dialog text fits", "ok": False,
                    "evidence": "the clipped-text probe measured no dialog: " + "; ".join(p["errors"][:2])})
    return out
