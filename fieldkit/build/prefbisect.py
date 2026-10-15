# Version: 1.0.0 · updated 26-10-15-14-22
"""Which of our pref changes costs something? Halve until one is left, measuring each half on the real machine.

Born 2026-10-15: at 60 fps Gorilla 158 dropped 22.6% of frames where stock Firefox ESR dropped 16.8% on the same
VAIO. Owner: "chase it now and fix it before uploading ... a perfect opportunity to teach the harness how to
troubleshoot". Troubleshooting by bisection, never by guessing which pref looks guilty:

  upstream_default(src, name)  Mozilla's own default, read from the PRISTINE release (the work tree's root commit):
                               StaticPrefList.yaml value, else pref() in all.js / firefox.js. Never guessed: a pref
                               with no upstream default is reported and left out of the bisection
  overrides(src, names)        {name: upstream value} to put in a throwaway test profile, so "reset these to
                               Mozilla's defaults" is exact
  bisect(names, measure)       measure(set of names reset) -> a number (lower is better); keeps the half whose reset
                               moves the number, until one name (or none: the cost is not in these prefs)
"""
import json
import re
import subprocess
from pathlib import Path

# The graphics and presentation prefs Gorilla changes, the default suspects for a frame-drop bisection (2026-10-15).
# Data, not a guess at the culprit: a pref with no single upstream default is skipped by upstream_defaults().
PRESENTATION_SUSPECTS = [
    "gfx.webrender.all", "gfx.webrender.fallback.software", "gfx.webrender.scissored-cache-clears.enabled",
    "gfx.canvas.accelerated.force-enabled", "gfx.canvas.accelerated.cache-items", "gfx.canvas.accelerated.cache-size",
    "gfx.x11-egl.force-enabled", "layers.acceleration.force-enabled", "layers.gpu-process.force-enabled",
    "widget.dmabuf.force-enabled", "media.hardware-video-decoding.force-enabled",
    "gfx.text.subpixel-position.force-enabled",
]
PREF_FILES = ("modules/libpref/init/all.js", "browser/app/profile/firefox.js")
STATIC = "modules/libpref/init/StaticPrefList.yaml"


def _pristine(src, path):
    root = subprocess.run(["git", "-C", str(src), "rev-list", "--max-parents=0", "HEAD"], capture_output=True,
                          text=True).stdout.split()
    if not root:
        return ""
    r = subprocess.run(["git", "-C", str(src), "show", f"{root[0]}:{path}"], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else ""


def _value(text):
    t = text.strip().rstrip(",")
    if t in ("true", "false"):
        return t == "true"
    if re.fullmatch(r"-?\d+", t):
        return int(t)
    if re.fullmatch(r"-?\d+\.\d+", t):
        return float(t)
    m = re.fullmatch(r'"(.*)"', t)
    return m.group(1) if m else None


def upstream_defaults(src):
    """{pref name: Mozilla's default} for every pref the pristine release defines without a platform #if around it
    (a value inside #if blocks is taken only when every branch agrees; otherwise left out, never guessed)."""
    out = {}
    text = _pristine(src, STATIC)
    for m in re.finditer(r"^- name: (\S+)\n(?:  \w+:.*\n)*?((?:#.*\n|  value: .*\n)+)", text, re.M):
        vals = {_value(v) for v in re.findall(r"^  value: (.*)$", m.group(2), re.M)}
        vals.discard(None)
        if len(vals) == 1:
            out[m.group(1)] = vals.pop()
    for f in PREF_FILES:
        seen = {}
        for m in re.finditer(r'^\s*pref\("([^"]+)",\s*(.+?)\s*\);', _pristine(src, f), re.M):
            v = _value(m.group(2))
            if v is not None:
                seen.setdefault(m.group(1), set()).add(json.dumps(v))
        for k, vs in seen.items():
            if len(vs) == 1:
                out[k] = json.loads(vs.pop())
    return out


def overrides(defaults, names):
    return {n: defaults[n] for n in names if n in defaults}


def bisect(names, measure, tolerance, out=print):
    """names: the suspects. measure(frozenset reset) -> score (lower is better). The cost is the gap between
    measure(nothing reset) and measure(all reset); a half is kept when resetting it closes at least half that gap.
    -> {"suspect": name or None, "rounds": [...]}"""
    names = list(names)
    base = measure(frozenset())
    full = measure(frozenset(names))
    rounds = [{"reset": "nothing", "score": base}, {"reset": "all", "score": full}]
    out(f"  bisect: as shipped {base}, all {len(names)} reset {full}")
    if base - full < tolerance:
        return {"suspect": None, "rounds": rounds,
                "why": f"resetting all {len(names)} prefs moved the score {round(base - full, 2)} (< {tolerance}): "
                       "the cost is not in these prefs"}
    while len(names) > 1:
        half = names[:len(names) // 2]
        s = measure(frozenset(half))
        rounds.append({"reset": half, "score": s})
        closes = (base - s) >= (base - full) / 2
        out(f"  bisect: reset {len(half)} of {len(names)} -> {s} ({'kept' if closes else 'cleared'})")
        names = half if closes else names[len(names) // 2:]
    return {"suspect": names[0], "rounds": rounds}


def confirm(suspect, measure, repeats=3, out=print):
    """A bisect verdict is one run per round; runs wobble (2026-10-15: about 2 points between rounds). Confirm it:
    alternate as-shipped and suspect-reset `repeats` times each (alternating cancels drift: heat, background work),
    then compare the means against the spread. -> {"confirmed", "shipped", "reset", "gap", "spread"}"""
    a, b = [], []
    for i in range(repeats):
        a.append(measure(frozenset()))
        b.append(measure(frozenset([suspect])))
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    spread = max(max(a) - min(a), max(b) - min(b))
    gap = round(ma - mb, 1)
    ok = gap > 0 and gap > spread                     # the difference must exceed the run-to-run wobble
    out(f"  confirm {suspect}: shipped {a} (mean {round(ma, 1)}), reset {b} (mean {round(mb, 1)}), gap {gap}, "
        f"spread {round(spread, 1)} -> {'CONFIRMED' if ok else 'NOT confirmed'}")
    return {"confirmed": ok, "shipped": a, "reset": b, "gap": gap, "spread": round(spread, 1)}


def run(src, work, suspects=None, fps=60, seconds=30, tolerance=3.0, out=print):
    """The whole troubleshooting run, isolated once: warn, quiet, bisect with the decode test, reopen.
    Score = % of frames dropped (100 when the clip did not play on the hardware path)."""
    from . import decode_test, isolation
    defaults = upstream_defaults(src)
    names = [n for n in (suspects or PRESENTATION_SUSPECTS) if n in defaults]
    skipped = [n for n in (suspects or PRESENTATION_SUSPECTS) if n not in defaults]
    log = []

    def measure(reset):
        r = decode_test.run(work, fps=fps, seconds=seconds, isolate=False, extra_prefs=overrides(defaults, reset),
                            out=lambda *a: None)
        score = (100.0 if r.get("verdict") != "HARDWARE"
                 else round(100 * r["frames_dropped"] / max(r["frames_shown"], 1), 1))
        log.append({"reset": sorted(reset), "score": score, "detail": r["detail"]})
        out(f"  {len(reset)} reset -> {score}% dropped")
        return score
    still = isolation.wait_own(out=out)
    if still:
        return {"ok": False, "detail": "the harness's own work is still running: " + "; ".join(still)}
    isolation.warn(f"a bisection of {len(names)} settings at {fps} fps, a few minutes", out=out)
    closed = []
    try:
        closed = isolation.quiet(isolation.browsers() + isolation.heavy_apps(), out=out)
        noise = isolation.noisy(isolation.survey())
        res = bisect(names, measure, tolerance, out=out)
        if res.get("suspect"):
            res["confirmation"] = confirm(res["suspect"], measure, out=out)
    finally:
        isolation.restore(closed, out=out)
        isolation.finished("the settings bisection", out=out)
    conf = res.get("confirmation") or {}
    return {"ok": True, "suspect": res.get("suspect"), "confirmed": conf.get("confirmed"), "confirmation": conf,
            "why": res.get("why"), "rounds": res["rounds"], "log": log,
            "skipped_no_upstream_default": skipped, "noise_before": noise,
            "detail": (f"suspect: {res['suspect']} ({'CONFIRMED' if conf.get('confirmed') else 'NOT confirmed'}: "
                       f"gap {conf.get('gap')} vs spread {conf.get('spread')})" if res.get("suspect")
                       else f"no suspect: {res.get('why')}")
                      + (f"; noise before: {'; '.join(noise)}" if noise else "")}
