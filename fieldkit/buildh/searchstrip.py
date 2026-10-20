# Version: 1.0.0 · updated 26-10-18-23-30
"""D-158-13 to the end: the search data of every engine but Google leaves the source, not only the engine list.

Owner, 2026-10-18: "keep only US google search as a search engine and remove everything else from the source code as
well ... we don't want the additional source code in our build anyways ... this way we disable a few more surface
attacks". Patch 025 reduced the engine list (search-config-v2.json) to Google, but the build still shipped the icons
of ~70 other engines (search-config-icons.json, 94 attachment files listed in moz.build) and the logo images
("wordmarks") of Bing, DuckDuckGo, eBay, Perplexity, Startpage and Wikipedia (jar.inc.mn, urlbar.css,
SearchModeSwitcher.mjs).

    plan(tree)          what would go
    apply(tree)         remove it (files deleted from the tree; the lists and rules that name them edited)
    check(tree)         -> {"ok", "left": [...]}: anything non-Google still there (the build gate's row)
"""
import json
import re
from pathlib import Path

KEEP = re.compile(r"^google(-|\*|$)", re.I)       # the engine identifiers D-158-13 keeps
ICONS_JSON = "services/settings/dumps/main/search-config-icons.json"
ICONS_DIR = "services/settings/dumps/main/search-config-icons"
DUMPS_MOZBUILD = "services/settings/dumps/main/moz.build"
WORDMARKS = "browser/themes/shared/urlbar/engine-wordmarks"
JAR = "browser/themes/shared/jar.inc.mn"
URLBAR_CSS = "browser/themes/shared/urlbar.css"
SWITCHER = "browser/components/urlbar/content/SearchModeSwitcher.mjs"
WORDMARK_FAMILY = {"Bing.svg": "bing", "DuckDuckGo.svg": "ddg", "eBay.svg": "ebay", "Google.svg": "google",
                   "Perplexity.svg": "perplexity", "Startpage.svg": "startpage", "Wikipedia.svg": "wikipedia"}


def _keep_record(r):
    return any(KEEP.match(i) for i in r.get("engineIdentifiers", []))


def _read(tree, rel):
    p = Path(tree) / rel
    return p.read_bytes().decode("utf-8") if p.is_file() else None


def _write(tree, rel, text):
    (Path(tree) / rel).write_bytes(text.encode("utf-8"))


def plan(tree):
    tree = Path(tree)
    out = {"icon_records": [], "icon_files": [], "wordmarks": [], "families": []}
    raw = _read(tree, ICONS_JSON)
    if raw:
        for r in json.loads(raw).get("data", []):
            if not _keep_record(r):
                out["icon_records"].append(r["id"])
                for f in (f"{ICONS_DIR}/{r['id']}", f"{ICONS_DIR}/{r['id']}.meta.json"):
                    if (tree / f).is_file():
                        out["icon_files"].append(f)
    wd = tree / WORDMARKS
    if wd.is_dir():
        for svg in sorted(wd.glob("*.svg")):
            if WORDMARK_FAMILY.get(svg.name, "?") != "google":
                out["wordmarks"].append(f"{WORDMARKS}/{svg.name}")
    sw = _read(tree, SWITCHER) or ""
    m = re.search(r"const WORDMARK_ENGINE_FAMILIES = new Set\(\[(.*?)\]\);", sw, re.S)
    if m:
        out["families"] = [f for f in re.findall(r'"([^"]+)"', m.group(1)) if f != "google"]
    return out


def apply(tree):
    tree = Path(tree)
    p = plan(tree)
    gone_ids = set(p["icon_records"])
    raw = _read(tree, ICONS_JSON)
    if raw and gone_ids:
        nl = "\r\n" if "\r\n" in raw else "\n"
        d = json.loads(raw)
        d["data"] = [r for r in d["data"] if r["id"] not in gone_ids]
        _write(tree, ICONS_JSON, json.dumps(d, indent=2, ensure_ascii=False).replace("\n", nl) + nl)
    for f in p["icon_files"] + p["wordmarks"]:
        (tree / f).unlink()
    mb = _read(tree, DUMPS_MOZBUILD)
    if mb and gone_ids:
        nl = "\r\n" if "\r\n" in mb else "\n"
        lines = [l for l in mb.split(nl) if not any(i in l for i in gone_ids)]
        _write(tree, DUMPS_MOZBUILD, nl.join(lines))
    gone_svg = {Path(w).name for w in p["wordmarks"]}
    jar = _read(tree, JAR)
    if jar and gone_svg:
        nl = "\r\n" if "\r\n" in jar else "\n"
        _write(tree, JAR, nl.join(l for l in jar.split(nl) if not any(f"engine-wordmarks/{s}" in l for s in gone_svg)))
    css = _read(tree, URLBAR_CSS)
    if css and p["families"]:
        for fam in p["families"]:
            css = re.sub(r"\n[ \t]*\.searchmode-switcher\[wordmark=\"" + re.escape(fam) + r"\"\] & \{[^}]*\}[ \t]*(?=\r?\n)",
                         "", css)
        _write(tree, URLBAR_CSS, css)
    sw = _read(tree, SWITCHER)
    if sw and p["families"]:
        sw = re.sub(r"(const WORDMARK_ENGINE_FAMILIES = new Set\(\[)(.*?)(\]\);)",
                    lambda m: m.group(1) + '\n  // GORILLA D-158-13: Google is the only engine; the other wordmarks are gone\n  "google",\n' + m.group(3),
                    sw, count=1, flags=re.S)
        _write(tree, SWITCHER, sw)
    return p


def check(tree):
    p = plan(tree)
    tree = Path(tree)
    left = [f"icon record {i}" for i in p["icon_records"]] + p["icon_files"] + p["wordmarks"] + \
           [f"wordmark family {f}" for f in p["families"]]
    css = _read(tree, URLBAR_CSS) or ""
    left += [f"urlbar.css rule for {f}" for f in re.findall(r'\.searchmode-switcher\[wordmark="([^"]+)"\]', css) if f != "google"]
    jar = _read(tree, JAR) or ""
    left += [f"jar.inc.mn line {s}" for s in re.findall(r"engine-wordmarks/([A-Za-z]+\.svg)", jar)
             if WORDMARK_FAMILY.get(s) != "google"]
    return {"ok": not left, "left": sorted(set(left))}


def row(tree):
    c = check(tree)
    return {"check": "D-158-13: no search data of any engine but Google is left in the source",
            "ok": c["ok"], "evidence": "Google only" if c["ok"] else f"{len(c['left'])} left, e.g. {c['left'][:4]}"}


def engines_offered(install_dir, profile=None, run=None):
    """-> [(id, name, default)] the installed browser offers, on a COPY of `profile` (or a fresh one)."""
    from . import probe
    r = (run or probe.run)(install_dir, "search-engines", wait=10, timeout=180, say=lambda m: None,
                           **({"profile": profile} if profile else {}))
    out = []
    for l in r.get("lines", []):
        p = l.strip().split("|")
        if len(p) >= 5 and p[0] == "SEARCH" and p[1] == "engine":
            out.append((p[2], p[3], p[4] == "1"))
    return out, r.get("done", False)


def offered_rows(install_dir, profiles, run=None):
    """Post-install: Google is the only engine offered on a fresh profile AND on a copy of each real profile."""
    rows = []
    for label, prof in [("a fresh profile", None)] + [(f"a copy of {Path(p).name}", p) for p in profiles]:
        eng, done = engines_offered(install_dir, prof, run=run)
        bad = [f"{n} ({i})" for i, n, _ in eng if not KEEP.match(i)]
        ok = done and bool(eng) and not bad
        rows.append({"check": f"D-158-13: Google is the only search engine offered, on {label}", "ok": ok,
                     "evidence": ("the probe did not finish" if not done else "no engine at all" if not eng else
                                  ", ".join(n for _, n, _ in eng) if not bad else "also offered: " + ", ".join(bad))})
    return rows
