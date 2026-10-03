"""Visual quality of a Gorilla Firefox build: crisp icons everywhere, properly laid-out pages and menus.

Why (2026-10-02): the About window showed a blurry logo (a 500 px PNG made from a soft copy: edge energy 12.6
against 29.3 from the 1200 px master) and Mozilla's "Nightly" wordmark, and the only logo check looked at one
file, about-logo.svg. This module is the whole check, so nobody reinvents it:

    fieldkit build-harness visual TASK [--static] [--install-dir D]

Layer 1, static (static.py; preflight --build and the post-install row): the ported tree's branding rasters
(provenance against the master, with the doctrine's vacuity guard), .ico ladders, size slots, every
Gorilla-touched chrome CSS rule that paints a raster (2x headroom, no unclamped injection), Mozilla leftovers.
Layer 2, runtime (runtime.py; post-install): a throwaway copy of the installed build, measured from inside by an
autoconfig probe at DPR 1 and 2 on every page about:about lists, plus the app menu and a context menu.

Verdicts per item: PASS, FAIL, UNVERIFIABLE. The check fails on any FAIL or UNVERIFIABLE that the allowlist
(docs/visual/ALLOWLIST.yaml, each exception with a reason) does not accept. Fail closed: no evidence is a FAIL.

Measurements reused, not reinvented: the doctrine's edge energy and self-control (Gorilla.firefox
working scripts/check_logo_provenance.py), decisions.image_sharp's Lanczos comparison, IconKit's .ico frame
reader and size ladder (mirrored, see rasters.py), throwaway.MARK/root for the throwaway folder.
"""
from pathlib import Path

ROW = "visual: crisp icons and aligned pages (static tree + runtime copy)"


def task_context(t):
    """A loaded task -> (tree, branding_rel or None, owner_root or None, upstream commit or None)."""
    from ..buildh import buildrun, icons
    from ..buildh.compile import mozconfig_path
    owner = buildrun._owner_root(t)
    branding = None
    if owner:
        mc = mozconfig_path(owner)
        if mc.is_file():
            branding = icons.branding_dir(mc.read_text(encoding="utf-8", errors="replace"))
    meta = t.get("meta") or {}
    commit = (meta.get("upstream") or {}).get("commit") or (meta.get("pinned") or {}).get("commit")
    return Path(t["workdir"]), branding, (Path(owner) if owner else None), commit


def check(t, static_only=False, install_dir=None, say=print, allow=None, only=()):
    """Both layers for a loaded task. -> {"ok", "static", "runtime" (None with static_only)}."""
    from . import allow as allowmod, runtime, static
    allow = allow if allow is not None else allowmod.load()
    tree, branding, owner, commit = task_context(t)
    say(f"  static: {tree} (branding {branding}, upstream {str(commit)[:12]})")
    st = static.check(tree, branding, owner, commit, allow)
    rt = None
    if not static_only:
        if not install_dir or not (Path(install_dir) / "firefox.exe").is_file():
            rt = allowmod.summarise([{"layer": "runtime", "rule": "RT-RUN", "item": "setup", "verdict": "FAIL",
                                      "evidence": f"no installed build at {install_dir}"}], allow, "runtime")
        else:
            rt = runtime.run(install_dir, only=only, say=say, allow=allow)
    ok = st["ok"] and (static_only or bool(rt and rt["ok"]))
    return {"ok": ok, "static": st, "runtime": rt, "static_only": static_only}


def lines(res, all_items=False):
    out = []
    for name in ("static", "runtime"):
        r = res.get(name)
        if not r:
            continue
        c = r["counts"]
        out.append(f"{name.upper()}: {'OK' if r['ok'] else 'NOT OK'}  pass {c['PASS']}, fail {c['FAIL']}, "
                   f"unverifiable {c['UNVERIFIABLE']}, accepted {c['accepted']}")
        out += [f"  PROBLEM: {p}" for p in r.get("problems") or []]
        for h in r.get("how") or []:
            out.append(f"  css: {h}")
        for i in r["items"]:
            if all_items or (i["verdict"] != "PASS"):
                tag = "ACCEPTED" if i.get("accepted") else i["verdict"]
                out.append(f"  {tag:<12} {i['rule']:<13} {i['item'][:120]}")
                out.append(f"      {i['evidence'][:240]}" + (f"  [accepted: {i['accepted'][:100]}]" if i.get("accepted") else ""))
        out += [f"  stale allowlist entry (matched nothing): {s}" for s in r.get("stale_allow") or []]
        if r.get("evidence"):
            out.append(f"  evidence kept in {r['evidence']}")
    out.append("VISUAL " + ("OK" if res["ok"] else "NOT OK") + (" (static layer only)" if res.get("static_only") else ""))
    return out


def _row(res, which):
    parts = []
    for name in which:
        r = res.get(name)
        if r:
            c = r["counts"]
            parts.append(f"{name}: {c['FAIL']} fail, {c['UNVERIFIABLE']} unverifiable, {c['PASS']} pass"
                         + (f", {c['accepted']} accepted" if c["accepted"] else ""))
    bad = [f"{i['rule']} {i['item'][:80]}" for n in which for i in (res.get(n) or {}).get("items", [])
           if i["verdict"] != "PASS" and not i.get("accepted")]
    return {"check": ROW, "ok": res["ok"], "evidence": "; ".join(parts) or "nothing checked", "bad": bad}


def preflight_row(t):
    """The static layer as one preflight row (preflight --build)."""
    try:
        return _row(check(t, static_only=True, say=lambda m: None), ("static",))
    except Exception as e:  # noqa: BLE001 - a check that crashed proves nothing: FAIL
        return {"check": ROW, "ok": False, "evidence": f"the check crashed: {type(e).__name__}: {e}", "bad": []}


def proof_row(t, install_dir, say=print):
    """Both layers as one post-install row."""
    try:
        return _row(check(t, install_dir=install_dir, say=say), ("static", "runtime"))
    except Exception as e:  # noqa: BLE001
        return {"check": ROW, "ok": False, "evidence": f"the check crashed: {type(e).__name__}: {e}", "bad": []}
