# Version: 1.0.0 · updated 26-10-23-22-00
"""D-158-22: no "Learn more" and no link to Mozilla anywhere a person clicks, proven on the installed build.

    fieldkit build-harness probe TASK js=mozilla-links          the raw lines
    fieldkit build-harness post-install TASK --only mozlinks     the row

Owner, 2026-10-23: "let's get rid of 'learn more' or any links to mozilla in the build. that would clear the
controversy", scope "everything a person clicks" (the licence page and the developer tools keep theirs). The probe
walks the pages a person meets (Settings, Add-ons, Protections, Troubleshooting, Private Browsing, Passwords, the new
tab, Help > About) and the link preferences; this reads its lines. Measured before the decision on Linux 158.0-2:
83 preferences with a Mozilla address and about 555 Mozilla addresses in pages and strings.
"""


def parse(lines):
    """Probe lines -> {"total", "links": [(page, what, target)], "prefs": [(name, value)], "pages": {page: n},
    "verdict": "ok" | "FAIL" | None}."""
    out = {"total": None, "links": [], "prefs": [], "pages": {}, "verdict": None}
    for l in lines:
        f = l.strip().split("|")
        if f[0] == "LINK" and len(f) >= 4:
            out["links"].append((f[1], f[2], "|".join(f[3:])))
        elif f[0] == "PREF" and len(f) >= 3:
            out["prefs"].append((f[1], "|".join(f[2:])))
        elif f[0] == "PAGE" and len(f) >= 3:
            out["pages"][f[1]] = int(f[2]) if f[2].isdigit() else f[2]
        elif f[0] == "LINKS" and len(f) >= 4 and f[1] == "verdict":
            out["verdict"] = f[2]
            out["total"] = int(f[3]) if f[3].isdigit() else None
    return out


def rows(install_dir, run=None):
    """The post-install row: 0 links and 0 link preferences; pages the probe could not read are named, never passed."""
    from . import probe
    r = (run or probe.run)(install_dir, "mozilla-links", wait=10, timeout=300, say=lambda m: None)
    got = parse(r.get("lines", []))
    unread = sorted(p for p, n in got["pages"].items() if not isinstance(n, int))
    ok = r.get("done", False) and got["verdict"] == "ok" and not unread
    if got["verdict"] is None:
        ev = "the probe did not finish"
    elif ok:
        ev = f"0 links to Mozilla on {len(got['pages'])} page(s), 0 link preferences with a Mozilla address"
    else:
        ev = (f"{len(got['links'])} link(s), {len(got['prefs'])} preference(s)"
              + (f": {got['links'][0][0]} {got['links'][0][2][:80]}" if got["links"] else "")
              + (f"; e.g. {got['prefs'][0][0]}" if got["prefs"] else "")
              + (f"; unreadable: {unread[:3]}" if unread else ""))
    return [{"check": "D-158-22: no Learn more and no link to Mozilla a person can click", "ok": ok, "evidence": ev}]
