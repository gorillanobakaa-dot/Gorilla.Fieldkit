"""fieldkit web doctor: the browsers found, which method each gets, which cannot be driven and the one step that would
fix it. It only asks; it never installs, downloads or changes a setting."""
from . import browser, discover

OLD_GECKO = ("update {name} (BiDi needs Firefox " + str(discover.BIDI_MIN_FIREFOX) +
             "+), or put a geckodriver on PATH and install selenium")


def check(rows=None, cfg=None):
    """-> list of {"item","ok","detail","fix"} for every browser plus a summary row; fix is a step for a person."""
    cfg = browser.config() if cfg is None else cfg
    rows = browser.installed(cfg) if rows is None else rows
    out = []
    for r in rows:
        ver = f" {r['version']}" if r["version"] else ""
        if r["method"]:
            out.append({"item": r["name"], "ok": True, "detail": f"{r['engine']}{ver}: {r['method']}  {r['path']}",
                        "fix": ""})
            continue
        fix = r["why"]
        if r["engine"] == "gecko" and "too old" in r["why"]:
            fix = OLD_GECKO.format(name=r["name"])
        elif r["engine"] == "unknown":
            fix = "nothing to do: use another browser"
        elif r["engine"] == "webkit":
            fix = r["why"] if "only on macOS" in r["why"] else "in Terminal, once: safaridriver --enable (asks for your password)"
        out.append({"item": r["name"], "ok": False, "detail": f"{r['engine']}{ver}: cannot be driven ({r['why']})",
                    "fix": fix})
    usable = [r for r in rows if r["method"]]
    out.append({"item": "drivable browsers", "ok": bool(usable), "detail": f"{len(usable)} of {len(rows)}",
                "fix": "" if usable else "install any browser (Edge, Chrome, Firefox 129+); nothing else is needed"})
    return out
