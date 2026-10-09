"""Every inactive tab carries its thin cyan outline, always, and nothing about a tab changes under the mouse (D-157-37).

    run by build-verify (on dist/bin) and post-install (row group "ui"); the probe alone: build-harness probe <task> js=tab-borders

Born 2026-10-09. The decision's own checks read the source (master-redirect.css); the release page says the outline
is on the installed browser, so the installed browser is measured: probe tab-borders (headless, throwaway copy,
fresh profile, nothing leaves the machine) reports what an inactive tab's .tab-background computes to and every
style rule that styles an inactive tab's background under the pointer. A hover rule cannot be triggered headless,
so it is judged from the rule text: any such rule that sets an outline, border or shadow fails (the hover-only
outline was what D-157-37 replaced). Fail closed: no inactive-tab lines -> not OK.
"""
import re

RGB = re.compile(r"rgba?\(\s*(\d+),\s*(\d+),\s*(\d+)(?:,\s*([\d.]+))?\s*\)")


def parse(lines):
    """Probe lines -> ({state: {property: value}}, [rule text])."""
    tabs, rules = {}, []
    for l in lines:
        f = l.strip().split("|", 3)
        if f[0] != "TAB" or len(f) < 4:
            continue
        if f[1] == "rule":
            rules.append(f[3])
        else:
            tabs.setdefault(f[1], {})[f[2]] = f[3]
    return tabs, rules


def cyan(value):
    """An opaque cyan: red low, green and blue high."""
    m = RGB.search(value or "")
    if not m:
        return False
    r, g, b = (int(x) for x in m.groups()[:3])
    a = float(m.group(4)) if m.group(4) is not None else 1.0
    return a > 0 and r <= 64 and g >= 192 and b >= 192


def judge(tabs, rules):
    """-> rows."""
    t = tabs.get("inactive") or {}
    if not t:
        return [{"check": "tab outline: the probe read an inactive tab (D-157-37)", "ok": False,
                 "evidence": "no inactive-tab lines from probe tab-borders"}]
    width = t.get("outline-width", "")
    drawn = t.get("outline-style", "none") not in ("none", "hidden", "") and width not in ("0px", "")
    ok = drawn and cyan(t.get("outline-color"))
    rows = [{"check": "tab outline: an inactive tab is outlined in cyan, without the mouse (D-157-37)", "ok": ok,
             "evidence": f"outline {t.get('outline-style')} {width} {t.get('outline-color')}"}]
    # the scan must have seen Gorilla's own inactive-tab rule, or "no hover rule" proves nothing (a flat walk of the
    # style sheets missed every @import-ed rule, 2026-10-09)
    own = [r for r in rules if ":hover" not in r and "outline" in r]
    hover = [r for r in rules if ":hover" in r and re.search(r"\b(outline|border|box-shadow)\b", r)]
    rows.append({"check": "tab outline: no rule changes an inactive tab's outline under the mouse (D-157-37)",
                 "ok": bool(own) and not hover,
                 "evidence": (hover[0][:160] if hover else
                              f"{len(own)} inactive-tab rule(s) read, none under the mouse ({tabs.get('sheets', {}).get('walked', '?')} sheets)")
                             if own else "the rule scan did not find the inactive-tab rule (scan blind)"})
    return rows


def rows(install_dir, say=print, timeout=180, **change):
    """Probe a copy of `install_dir` -> rows."""
    from . import probe as pb
    r = pb.run(install_dir, "tab-borders", wait=8, timeout=timeout, say=say, **change)
    return judge(*parse(r["lines"]))
