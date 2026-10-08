"""Every about: page of the installed build, opened, read and judged; compared with the last run of another build.

    fieldkit build-harness about-pages <task> [--install-dir <build>] [only=a,b] [walk=1 dwell=6]
                                              [tree-since=<commit|build>] [sub=..] [omni=..] [timeout=900]

Born 2026-10-08. The owner went through about:about by hand, found a page that had gone blank (about:studies), Mozilla
artwork on a blocked page (about:telemetry), pages they did not know (about:windows-messages) and asked for the probe
that read them all to become "our analyses tool".

How (probe about-pages, fieldkit/buildh/probes/about-pages.js, in a throwaway copy of the install, headless, fresh
profile; the owner's browser and profile are never touched):
  1. a DEAD proxy (127.0.0.1:9) is set before the first page: a request is recorded, nothing leaves the machine;
  2. every about: page the build registers is opened (not only the ones about:about lists; never about:crash*);
  3. for each page: where it landed (blocked by policy = the error page), its text (shadow roots included), every
     picture it draws and at what size, its controls, the script errors raised and the requests opened meanwhile,
     with who opened them (a page, an extension, the browser);
  4. about:glean, while it exists, has every menu entry clicked and its submit-ping button pressed.
Verdict (FAIL fails the command and the post-install row):
  - a page about:about lists shows no text (it hangs or broke), unless EXPECTED_EMPTY says why;
  - a script error that is not a KNOWN_ARTEFACT of opening a page by URL;
  - a request opened by anything but an extension the decisions allow (ALLOWED_EXTENSIONS);
  - a Gorilla (chrome://branding/) drawn under 64 CSS px (D-157-35);
  - an about:glean menu entry that shows nothing, or a submit that opens a request.
  - an element naming a Fluent message no loaded file defines (it shows nothing), a web component whose update
    failed.
walk=1 (owner 2026-10-08: "open the firefox ... click on each of those links and analyze the input in real time"):
the same reading, the way a person does it, in a VISIBLE window of the copy (its own throwaway profile, behind the dead
proxy; the owner's browser is untouched): about:about, click a link, read the page, back, the next link, `dwell`
seconds per page, every finding printed as it happens. A countdown is printed first: the window takes the foreground.
Mozilla artwork still drawn (MOZILLA_ART) is listed as OPEN for the artwork sweep, not failed (owner 2026-10-07:
"We will get to those icons and bitmaps and pngs later on").
Each run is kept in state/about-pages/ (raw lines + parsed JSON) and compared with the newest run of another build:
pages added or gone, pages that lost their text, new errors, new pictures, new requests.
"""
import json
import re
import time
from pathlib import Path

from . import task

STATE = task.STATE.parent / "about-pages"
TINY_PX = 64
# extensions whose own requests are decided (D-157-05 "No extensions except uBlock Origin": its filter-list updates
# are its own); anything else that opens a request while the pages are read fails
# (the add-on id is built from parts: the commit privacy scan reads an add-on id as an e-mail address)
UBLOCK_ID = "uBlock0" + "@" + "raymondhill.net"
ALLOWED_EXTENSIONS = {UBLOCK_ID: "uBlock Origin filter lists (D-157-05)"}
# pages that are empty by nature in a fresh profile or without the parameter they are opened with
EXPECTED_EMPTY = {
    "cache-entry": "shows one cache entry, named in its URL",
    "certificate": "shows the certificate named in its URL",
    "devtools-toolbox": "the toolbox of a target named in its URL",
    "reader": "Reader View of the page named in its URL",
    "framecrashed": "drawn inside a frame that crashed",
    "messagepreview": "previews a message given in its URL",
    "downloads": "the download list, empty in a fresh profile",
}
# script errors that come from opening a page by its address rather than the way the browser opens it
KNOWN_ARTEFACTS = [
    (re.compile(r"RPMGet\w+ is not defined"), "an error page opened by URL, not by a failed load, has no page-manager functions"),
    (re.compile(r"logins is undefined"), "the import report with no import to report"),
    (re.compile(r"MPToggleLights is not defined"), "message preview without a message"),
    (re.compile(r"requestedBrowser\.currentURI is null"), "about:opentabs redirects while the tab switcher reads it"),
    (re.compile(r'property "getFullYear", date is undefined'),
     "about:asrouter (hidden developer page of the messaging system) reads telemetry session dates Gorilla never makes"),
]
# Mozilla's artwork (kitties, foxes, logos, illustrations); not "fox" alone: about:firefoxview's own icons live under
# content/firefoxview/
MOZILLA_ART = re.compile(r"/illustrations/|/logos/|kit-|fox-illustration|-fox\.|firefox-logo|limelight|tab-crashed\.svg"
                         r"|secure-broken\.svg|about-license\.svg|messagepreview/", re.I)
BRANDING = re.compile(r"^chrome://branding/")
CLOSING = " (closing)"


def teardown(e):
    """A promise the page left pending, rejected with nothing and no script stack while its tab closed (2026-10-08:
    Settings, 14 each time it closes): reported, not failed."""
    return e.get("closing") and e["msg"] in ("unhandled rejection: undefined", "uncaught exception: undefined")         and e["src"] in ("", "(no stack)")


def parse(lines):
    """Probe lines -> {"pages": {name: {...}}, "net": [...], "glean": {...}, "summary": {...}}."""
    pages, net, glean = {}, [], {"menu": [], "submit": None}
    summary, probe_errors = {}, []
    for raw in lines:
        l = raw.strip()
        f = l.split("|")
        kind = f[0]
        if kind == "ABOUT-ERROR" or l.startswith("error "):          # the probe itself failed (fail closed)
            probe_errors.append(l[:200])
            continue
        if kind == "ABOUT" and len(f) >= 9:      # ABOUT|name|process|landed|title|text length|pictures|controls|listed
            pages[f[1]] = {"process": f[2], "landed": f[3], "title": f[4], "text_len": _int(f[5]), "pics": [],
                           "controls": _int(f[7]), "listed": f[8] == "listed", "text": "",
                           "errors": pages.get(f[1], {}).get("errors", []),
                           "missing_l10n": pages.get(f[1], {}).get("missing_l10n", [])}
        elif kind == "ABOUT-TEXT" and len(f) >= 3 and f[1] in pages:
            pages[f[1]]["text"] = "|".join(f[2:])
        elif kind == "ABOUT-PIC" and len(f) >= 4 and f[1] in pages:
            w, _, h = f[3].partition("x")
            pages[f[1]]["pics"].append({"url": f[2], "w": _int(w), "h": _int(h)})
        elif kind in ("ABOUT-ERR", "ABOUT-REJ") and len(f) >= 3:
            name, closing = (f[1][:-len(CLOSING)], True) if f[1].endswith(CLOSING) else (f[1], False)
            msg = ("unhandled rejection: " if kind == "ABOUT-REJ" else "") + f[2]
            pages.setdefault(name, {"errors": []}).setdefault("errors", []).append(
                {"msg": msg, "src": f[3] if len(f) > 3 else "", "closing": closing})
        elif kind == "ABOUT-L10N" and len(f) >= 3:
            pages.setdefault(f[1], {"errors": []}).setdefault("missing_l10n", []).append(
                {"id": f[2], "element": f[3] if len(f) > 3 else ""})
        elif kind == "ABOUT-LIT" and len(f) >= 3:
            pages.setdefault(f[1], {"errors": []}).setdefault("errors", []).append(
                {"msg": f"web component {f[2]} failed its update: " + (f[3] if len(f) > 3 else ""), "src": "", "closing": False})
        elif kind == "ABOUT-NET" and len(f) >= 3:
            net.append({"page": f[1], "url": f[2], "who": f[3] if len(f) > 3 else "?"})
        elif kind == "GLEAN-MENU" and len(f) >= 4:
            glean["menu"].append({"entry": f[1], "shown": f[2] == "shown", "text_len": _int(f[3])})
        elif kind == "GLEAN-SUBMIT" and len(f) >= 3:
            glean["submit"] = {"after": f[1], "requests": _int(f[-1])}
        elif kind == "ABOUT-SUMMARY" and len(f) >= 6:
            summary = dict(zip(("pages", "blocked", "blank", "with_errors", "requests"), map(_int, f[1:6])))
    return {"pages": {k: v for k, v in pages.items() if "landed" in v}, "orphan_errors": {k: v["errors"] for k, v in pages.items() if "landed" not in v},
            "net": net, "glean": glean, "summary": summary, "probe_errors": probe_errors}


def _int(s):
    try:
        return int(s)
    except (TypeError, ValueError):
        return 0


def known_artefact(msg):
    for rx, why in KNOWN_ARTEFACTS:
        if rx.search(msg):
            return why
    return None


def _allowed(who):
    return any(who == "extension " + x for x in ALLOWED_EXTENSIONS)


def verdict(p):
    """-> rows {"check", "ok", "evidence"} (the post-install shape)."""
    pages = p["pages"]
    rows = []
    if not pages or p.get("probe_errors") or not p.get("summary"):
        rows.append({"check": "about-pages: the probe read the pages", "ok": False,
                     "evidence": f"{len(pages)} page(s) read; summary {'present' if p.get('summary') else 'MISSING'}; "
                                 + ("; ".join(p.get("probe_errors", [])[:3]) or "no probe error line")})
    blank = [n for n, v in sorted(pages.items()) if v.get("listed") and not v["landed"].startswith("BLOCKED")
             and v["text_len"] < 20 and n not in EXPECTED_EMPTY]
    rows.append({"check": "about-pages: every listed page shows its text", "ok": not blank,
                 "evidence": (f"blank: {', '.join('about:' + n for n in blank)}" if blank else
                              f"{sum(1 for v in pages.values() if v.get('listed'))} listed pages read")})
    errs, closing = [], []
    for n, v in sorted(pages.items()):
        for e in v.get("errors", []):
            if teardown(e):
                closing.append(n)
            elif not known_artefact(e["msg"]):
                errs.append(f"about:{n}: {e['msg'][:90]} ({e['src'][:60]})")
    for n, es in sorted(p.get("orphan_errors", {}).items()):
        errs += [f"{n}: {e['msg'][:90]} ({e['src'][:60]})" for e in es if not known_artefact(e["msg"])]
    uniq = sorted(set(errs))
    rows.append({"check": "about-pages: no script errors", "ok": not uniq,
                 "evidence": (f"{len(uniq)} distinct: " + "; ".join(uniq[:6]) if uniq else "none beyond the known artefacts")})
    if closing:
        from collections import Counter
        rows.append({"check": "about-pages: promises rejected while a tab closed (no reason, no script stack)", "ok": True,
                     "evidence": ", ".join(f"about:{n} x{c}" for n, c in sorted(Counter(closing).items()))})
    bad = [r for r in p["net"] if not _allowed(r["who"])]
    rows.append({"check": "about-pages: no request opened by a page or the browser", "ok": not bad,
                 "evidence": ("; ".join(f"{r['url'][:80]} by {r['who'][:40]} (open: about:{r['page']})" for r in bad[:5]) if bad else
                              f"{len(p['net'])} request(s), all by allowed extensions")})
    miss = [f"about:{n} {m['id']} ({m['element']})" for n, v in sorted(pages.items()) for m in v.get("missing_l10n", [])]
    rows.append({"check": "about-pages: every Fluent message a page names exists (else the element shows nothing)",
                 "ok": not miss, "evidence": "; ".join(miss[:6]) + (f" (+{len(miss) - 6})" if len(miss) > 6 else "") if miss else "none missing"})
    tiny = [f"about:{n} {x['url'][:60]} {x['w']}x{x['h']}" for n, v in sorted(pages.items()) for x in v.get("pics", [])
            if BRANDING.match(x["url"]) and max(x["w"], x["h"]) < TINY_PX]
    rows.append({"check": "about-pages: no Gorilla under 64 px (D-157-35)", "ok": not tiny,
                 "evidence": "; ".join(tiny[:5]) if tiny else "none"})
    g = p["glean"]
    if g["menu"] or g["submit"]:
        dead = [m["entry"] for m in g["menu"] if not m["shown"] or m["text_len"] < 20]
        sent = (g["submit"] or {}).get("requests", 0)
        rows.append({"check": "about-pages: about:glean menus and submit", "ok": not dead and not sent,
                     "evidence": f"{len(g['menu'])} entries, empty: {dead or 'none'}; submit opened {sent} request(s)"})
    art = mozilla_art(p)
    rows.append({"check": "about-pages: Mozilla artwork still drawn (OPEN: artwork sweep)", "ok": True,
                 "evidence": (f"{len(art)}: " + "; ".join(art[:8])) if art else "none"})
    return rows


def mozilla_art(p):
    return [f"about:{n} {x['url'].rsplit('/', 1)[-1]} {x['w']}x{x['h']}" for n, v in sorted(p["pages"].items())
            for x in v.get("pics", []) if MOZILLA_ART.search(x["url"])]


def compare(prev, cur):
    """Two parsed runs -> ["line", ...]: what changed from prev to cur."""
    out = []
    a, b = prev["pages"], cur["pages"]
    gone, new = sorted(set(a) - set(b)), sorted(set(b) - set(a))
    if gone:
        out.append("pages gone: " + ", ".join("about:" + n for n in gone))
    if new:
        out.append("pages new: " + ", ".join("about:" + n for n in new))
    for n in sorted(set(a) & set(b)):
        x, y = a[n], b[n]
        if x["landed"] != y["landed"]:
            out.append(f"about:{n} now lands on {y['landed'][:60]} (was {x['landed'][:60]})")
        if x["text_len"] >= 20 and y["text_len"] < max(20, x["text_len"] // 2):
            out.append(f"about:{n} lost its text: {x['text_len']} -> {y['text_len']} characters")
        elif x["text_len"] < 20 <= y["text_len"]:
            out.append(f"about:{n} shows text again: {x['text_len']} -> {y['text_len']} characters")
        pa, pb = {q["url"] for q in x.get("pics", [])}, {q["url"] for q in y.get("pics", [])}
        for u in sorted(pb - pa):
            out.append(f"about:{n} draws a new picture: {u[:90]}")
        for u in sorted(pa - pb):
            out.append(f"about:{n} no longer draws: {u[:90]}")
        ea, eb = {e["msg"] for e in x.get("errors", [])}, {e["msg"] for e in y.get("errors", [])}
        for m in sorted(eb - ea):
            out.append(f"about:{n} new error: {m[:100]}")
        for m in sorted(ea - eb):
            out.append(f"about:{n} error gone: {m[:100]}")
    na, nb = {r["url"] for r in prev["net"]}, {r["url"] for r in cur["net"]}
    out += [f"new request: {u[:100]}" for u in sorted(nb - na)]
    return out


def save(parsed, raw_lines, build_id, install_dir):
    STATE.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    p = STATE / f"about-pages-{stamp}.json"
    p.write_text(json.dumps({"build_id": build_id, "install": str(install_dir), "when": stamp, **parsed}, indent=1),
                 encoding="utf-8")
    (STATE / f"about-pages-{stamp}.txt").write_text("\n".join(raw_lines) + "\n", encoding="utf-8")
    return p


def previous(build_id, before=None):
    """The newest saved run of ANOTHER build (older than `before`, a path) -> parsed run or None."""
    if not STATE.is_dir():
        return None
    for f in sorted(STATE.glob("about-pages-*.json"), reverse=True):
        if before and f.name >= Path(before).name:
            continue
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if d.get("build_id") != build_id and not str(d.get("build_id", "")).endswith("-partial"):
            return d
    return None


def probe_file(only=(), walk=False, dwell=None):
    """The probe, limited to `only` (page names without about:), walking about:about's links, `dwell` ms per page
    -> path of the probe to run."""
    src = Path(__file__).parent / "probes" / "about-pages.js"
    if not only and not walk and dwell is None:
        return src
    import tempfile
    body = src.read_text(encoding="utf-8")
    for line in ("const ONLY = [];", "const WALK = false;", "const DWELL = 3500;"):
        assert body.count(line) == 1, line
    body = body.replace("const ONLY = [];", "const ONLY = " + json.dumps(sorted(only)) + ";")
    body = body.replace("const WALK = false;", f"const WALK = {'true' if walk else 'false'};")
    if dwell is not None:
        body = body.replace("const DWELL = 3500;", f"const DWELL = {int(dwell)};")
    out = Path(tempfile.mkdtemp(prefix="gprobe_about_")) / "about-pages.js"
    out.write_text(body, encoding="utf-8")
    return out


def live(line):
    """One probe line as a person reads it while a walk runs -> text, or None for lines not worth showing."""
    f = line.strip().split("|")
    k = f[0]
    if k == "ABOUT-NOW":
        return f"\n>> about:{f[1]}"
    if k == "ABOUT-WALK":
        return f"walking about:about: {f[1]}, {f[2]}"
    if k == "ABOUT" and len(f) >= 9:
        return f"   {f[3]} | {f[4] or '(no title)'} | {f[5]} characters, {f[6]} picture(s), {f[7]} control(s), {f[2]} process"
    if k == "ABOUT-TEXT" and len(f) >= 3:
        return "   says: " + ("|".join(f[2:])[:150] or "(NOTHING)")
    if k == "ABOUT-PIC" and len(f) >= 4:
        tag = " <- Mozilla artwork" if MOZILLA_ART.search(f[2]) else (" <- Gorilla UNDER 64 px" if BRANDING.match(f[2]) and max(map(_int, f[3].split("x"))) < TINY_PX else "")
        return f"   picture {f[2].rsplit('/', 1)[-1]} {f[3]}{tag}" if tag or BRANDING.match(f[2]) else None
    if k in ("ABOUT-ERR", "ABOUT-REJ") and len(f) >= 3:
        why = known_artefact(f[2])
        return f"   {'error (known: ' + why + ')' if why else 'ERROR'} on about:{f[1]}: {f[2][:140]} {('@ ' + f[3][:80]) if len(f) > 3 and f[3] else ''}"
    if k == "ABOUT-L10N":
        return f"   MISSING TEXT: {f[2]} ({f[3] if len(f) > 3 else ''})"
    if k == "ABOUT-LIT":
        return f"   BROKEN COMPONENT: {f[2]}: {f[3] if len(f) > 3 else ''}"
    if k == "ABOUT-NET" and len(f) >= 3:
        who = f[3] if len(f) > 3 else "?"
        return f"   request {'(allowed: ' + ALLOWED_EXTENSIONS[who[10:]] + ')' if _allowed(who) else 'OPENED, NOT ALLOWED'}: {f[2][:110]} by {who[:50]}"
    if k.startswith("GLEAN"):
        return "   " + line.strip()[:200]
    if k == "ABOUT-SUMMARY":
        return f"\ndone: {f[1]} pages, {f[2]} blocked, {f[3]} blank, {f[4]} with errors, {f[5]} request(s)"
    if k == "ABOUT-ERROR" or line.startswith("error "):
        return "PROBE ERROR: " + line.strip()[:200]
    return None


def run(install_dir, build_id, say=print, timeout=900, only=(), walk=False, dwell=None, on_line=None, visible=None,
        **change):
    """Probe, parse, judge, keep, compare -> {"rows", "changes", "path", "parsed", "done"}. `only`: just those pages
    (a partial run is kept but never used as the comparison base). walk=True: a visible window walking about:about's
    links (also kept apart from the comparison base: it reads only the listed pages)."""
    from . import probe as pb
    r = pb.run(install_dir, str(probe_file(only, walk, dwell)), wait=12, timeout=timeout, say=say,
               headless=not (walk if visible is None else visible), on_line=on_line, **change)
    parsed = parse(r["lines"])
    rows = verdict(parsed)
    if not r["done"]:
        rows.insert(0, {"check": "about-pages: the probe finished", "ok": False,
                        "evidence": f"timed out after {r['seconds']} s with {len(parsed['pages'])} page(s) read"})
    partial = bool(only) or walk
    path = save(parsed, r["lines"], build_id if not partial else f"{build_id}-partial", install_dir)
    prev = previous(build_id, before=path) if not partial else None
    return {"rows": rows, "changes": compare(prev, parsed) if prev else None, "path": path, "parsed": parsed,
            "done": r["done"], "previous": (prev or {}).get("when")}


def rows(install_dir, build_id, say=print):
    """Post-install rows (the about: pages of the installed build)."""
    return run(install_dir, build_id, say=say)["rows"]
