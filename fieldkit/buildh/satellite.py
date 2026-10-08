"""Gorilla.Satellite mode, judged: the identity and scripts every kind of site gets at every level.

    fieldkit build-harness satellite <task> [--install-dir <build>] [omni=<jar>:<member>=<file> ...] [tree-since=..]

Born 2026-10-08 (owner: "make sure you save all the satellite mobile and calls probes and make them part of the
harness, so we don't have to rebuild them again the next test or the next build"). Two probes, each in a throwaway
copy of the build with its own profile, headless, against a local page on 127.0.0.1:8765 that logs the User-Agent
header it receives and puts navigator.userAgent in its title when its script runs (nothing leaves the machine):
  - satellite-mobile (D-157-33): Off is the desktop browser; Satellite asks for phone pages; Very slow link also runs
    no scripts; "This site: desktop version", the mobile-pages tick-box and "This site: allow JavaScript" each do
    what they say; a tab opened in Very slow runs scripts again at Off; Off restores every value.
  - satellite-calls (D-157-39, build 28 item 2): a site allowed the camera or microphone (remembered, or for this
    tab only), or on gorilla.linkmode.call_sites, gets the desktop identity and its scripts at every level; any
    other site does not; the toolbar menu shows a call site's switches ticked and greyed, with the reason.
Each case has an expected identity (the header the page received) and script state; a mismatch fails. Run after
every build (build-verify, on dist/bin) and every install (post-install row "satellite").
"""
import re

ANDROID, DESKTOP = "android", "desktop"

# satellite-mobile: case -> (identity the page must receive, scripts must run); None = not judged
MOBILE_CASES = {
    "level0": (DESKTOP, True),
    "level1": (ANDROID, True),
    "level2": (ANDROID, False),
    "level2-desktop-site": (DESKTOP, False),
    "level2-mobile-again": (ANDROID, False),
    "level2-mobile-unticked": (DESKTOP, False),
    "level2-site-allowed-javascript": (DESKTOP, True),
    "level2-javascript-unticked": (DESKTOP, True),
    "level0-again": (DESKTOP, True),
}
# satellite-calls
CALL_CASES = {
    "very-slow-plain-site": (ANDROID, False),
    "very-slow-microphone-remembered": (DESKTOP, True),
    "very-slow-camera-this-tab-only": (DESKTOP, True),
    "very-slow-after-permissions-gone": (ANDROID, False),
    "very-slow-on-call-list": (DESKTOP, True),
    "satellite-on-call-list": (DESKTOP, True),
    "satellite-plain-site": (ANDROID, True),
    "off-plain-site": (DESKTOP, True),
}
CALL_MENU_CASES = {"very-slow-plain-site": False, "very-slow-microphone-remembered": True, "very-slow-on-call-list": True}

SERVER = re.compile(r"GET /([\w.-]+) \| user-agent: (.*)$")


def identity(ua):
    return ANDROID if "Android" in ua else DESKTOP if ("Windows NT" in ua or "X11" in ua or "Macintosh" in ua) else None


def parse(lines):
    """Probe output (lines and SERVER lines) -> {"header": {case: identity}, "js": {case: bool}, "menu": {...},
    "extra": [other lines], "calls_list": str or None}."""
    out = {"header": {}, "js": {}, "menu": {}, "extra": [], "calls_list": None}
    for raw in lines:
        l = raw.strip()
        m = SERVER.search(l)
        if l.startswith("SERVER") and m:
            out["header"].setdefault(m.group(1), identity(m.group(2)))
            continue
        if l.startswith("CALLS-MENU|"):
            f = l.split("|")
            if len(f) >= 4:
                out["menu"][f[1]] = {"desktop": "checked=true" in f[2], "desktop_disabled": "disabled=true" in f[2],
                                     "js": "checked=true" in f[3], "js_disabled": "disabled=true" in f[3],
                                     "why": f[4] if len(f) > 4 else ""}
            continue
        if l.startswith("CALLS|"):
            f = l.split("|", 2)
            if len(f) == 3 and f[1] == "list":
                out["calls_list"] = f[2]
            elif len(f) == 3:
                out["js"][f[1]] = f[2].startswith("JS ran")
            continue
        m2 = re.match(r"^([\w-]+) \| (JS ran|JS OFF|\(page did not load\))", l)      # satellite-mobile's visit lines
        if m2:
            out["js"][m2.group(1)] = m2.group(2) == "JS ran"
            continue
        out["extra"].append(l)
    return out


def judge(parsed, cases):
    """-> [mismatch text]: every case with an expectation that the run does not meet (a missing case fails too)."""
    bad = []
    for case, (want_id, want_js) in cases.items():
        got_id, got_js = parsed["header"].get(case), parsed["js"].get(case)
        if got_id is None and got_js is None:
            bad.append(f"{case}: not run")
            continue
        if want_id and got_id != want_id:
            bad.append(f"{case}: identity {got_id}, expected {want_id}")
        if want_js is not None and got_js != want_js:
            bad.append(f"{case}: scripts {'ran' if got_js else 'off'}, expected {'on' if want_js else 'off'}")
    return bad


def rows(app_dir, say=print, need_call_list=True, **change):
    """Both probes on a copy of `app_dir` -> post-install / build-verify rows."""
    from . import probe
    out = []
    r = probe.run(app_dir, "satellite-mobile", wait=12, timeout=240, say=say, **change)
    p = parse(r["timeline"])
    bad = judge(p, MOBILE_CASES) + ([] if r["done"] else ["the probe did not finish"])
    kept = [x for x in p["extra"] if x.startswith("tab kept open")]
    if kept and "JS STILL OFF" in kept[0]:
        bad.append("a tab opened in Very slow link still runs no scripts at Off")
    restored = [x for x in p["extra"] if x.startswith("level 0:")]
    if restored and ("clear cache at shutdown true" not in restored[0] or "ua override (none)" not in restored[0]):
        bad.append("Off did not restore the shipped values: " + restored[0][:120])
    out.append({"check": "satellite: identity and scripts per level, per-site switches (D-157-33)", "ok": not bad,
                "evidence": "; ".join(bad[:5]) if bad else f"{len(MOBILE_CASES)} cases as expected; Off restores the shipped values"})
    r = probe.run(app_dir, "satellite-calls", wait=12, timeout=240, say=say, **change)
    p = parse(r["timeline"])
    bad = judge(p, CALL_CASES) + ([] if r["done"] else ["the probe did not finish"])
    for case, call in CALL_MENU_CASES.items():
        m = p["menu"].get(case)
        if not m:
            bad.append(f"menu {case}: not read")
        elif call and not (m["desktop"] and m["desktop_disabled"] and m["js"] and m["js_disabled"] and "calls" in m["why"]):
            bad.append(f"menu {case}: a call site's switches are not shown ticked and greyed with the reason")
        elif not call and (m["desktop_disabled"] or m["js_disabled"]):
            bad.append(f"menu {case}: an ordinary site's switches are greyed")
    listed = p["calls_list"] or ""
    if need_call_list and (not listed or listed.startswith("(no ")):
        bad.append("the build ships no gorilla.linkmode.call_sites list")
    out.append({"check": "satellite: call sites keep their desktop version and scripts at every level (D-157-39)", "ok": not bad,
                "evidence": "; ".join(bad[:5]) if bad else f"{len(CALL_CASES)} cases as expected; list: {listed[:80]}"})
    return out
