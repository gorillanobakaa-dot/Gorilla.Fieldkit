# Version: 1.0.0 · updated 26-10-18-22-00
"""The Gorilla theme held to its one spec (gorilla-patchset/decisions/THEME.yaml), before and after the build.

Owner, 2026-10-18, after user feedback (the search-mode list next to the address bar: white with cyan text, "heavy
on the eye"; the app menu cut "Gorilla Extensions and Gorilla The..." short): "create the deterministic tool that
checks for that kind of mistakes ... and checks all the aspects of the style ... to make sure it is consistent and
uses the same background everywhere. That should be done before we reupload." The theme is a constant, so:

  static(tree)        BEFORE the build: every colour a Gorilla line (a line the patch set added, not upstream's) sets
                      in a stylesheet is one of the spec's colours. A new colour is a decision, not a default.
  judge_popups(items) AFTER the build, from probe ui-contrast's UI|popup lines: every item of every menu, popup and
                      panel a person opens is painted in an allowed (text, background) pair; its hover is the spec's
                      highlight; its contrast is at least the weakest allowed pair's (today's palette is the floor);
                      its label is not clipped.
"""
import os
import re
import subprocess
from pathlib import Path

import yaml

SPEC_PLACES = [
    os.environ.get("FIELDKIT_THEME_SPEC", ""),
    str(Path.home() / "Documents" / "Gorilla.firefox" / "gorilla-patchset" / "decisions" / "THEME.yaml"),
    str(Path.home() / "Documents" / "Gorilla.firefox" / "decisions" / "THEME.yaml"),
]
HEX = re.compile(r"#([0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b")
RGB = re.compile(r"rgba?\(\s*(\d+)\s*[, ]\s*(\d+)\s*[, ]\s*(\d+)")
COLOUR_PROPS = re.compile(r"(^|[\s;{])(-?-?[a-z-]*colou?r[a-z-]*|background|fill|stroke|border[a-z-]*|outline[a-z-]*|"
                          r"--[a-z0-9-]+)\s*:", re.I)


def find_spec(places=None):
    for p in places or SPEC_PLACES:
        if p and Path(p).is_file():
            return Path(p)
    return None


def load(path=None):
    p = Path(path) if path else find_spec()
    if not p:
        raise FileNotFoundError("no theme spec (gorilla-patchset/decisions/THEME.yaml)")
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    cols = {k: _rgb(v) for k, v in data["colours"].items()}
    pairs = [(cols[t], cols[b]) for t, b in data["pairs"]]
    floor = min(contrast(t, b) for t, b in pairs) if data.get("contrast_floor_from_pairs", True) else 4.5
    return {"path": str(p), "colours": cols, "names": {v: k for k, v in cols.items()}, "pairs": pairs,
            "hover": cols[data["hover"]], "floor": round(floor - 0.05, 2), "tol": int(data.get("tolerance", 6))}


def _rgb(s):
    s = s.strip()
    m = HEX.fullmatch(s) or HEX.search(s)
    if m:
        h = m.group(1)
        h = "".join(c * 2 for c in h) if len(h) == 3 else h
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    m = RGB.search(s)
    if m:
        return tuple(int(x) for x in m.groups())
    raise ValueError(f"not a colour: {s}")


def lum(c):
    f = lambda v: v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (f(x / 255) for x in c)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    x, y = sorted((lum(a), lum(b)), reverse=True)
    return (x + 0.05) / (y + 0.05)


def near(a, b, tol):
    return all(abs(p - q) <= tol for p, q in zip(a, b))


def name(spec, c):
    return next((n for v, n in spec["names"].items() if near(c, v, spec["tol"])), None)


# ---- after the build: the popups as painted ------------------------------------------------------------------------
def judge_popups(items, spec):
    """items: [{surface, label, contrast, text, background, hover, clipped}] -> rows (check, ok, evidence, bad)."""
    rows = []
    if not items:
        return [{"check": "theme: the probe measured the menus, popups and panels", "ok": False,
                 "evidence": "no popup item measured: the probe did not reach them", "bad": []}]
    allowed = lambda t, b: any(near(t, pt, spec["tol"]) and near(b, pb, spec["tol"]) for pt, pb in spec["pairs"])
    off = [i for i in items if not allowed(i["text"], i["background"])]
    rows.append({"check": "theme: every menu, popup and panel item is painted in a Gorilla colour pair",
                 "ok": not off,
                 "evidence": f"{len(items)} item(s) in {len({i['surface'] for i in items})} surface(s)" if not off else
                 f"{len(off)} off-theme: " + "; ".join(
                     f"{i['surface']} '{i['label'][:28]}' {_desc(spec, i['text'])} on {_desc(spec, i['background'])}"
                     for i in off[:4]),
                 "bad": [f"{i['surface']} | {i['label']}" for i in off]})
    hov = [i for i in items if i.get("hover") and not near(i["hover"], spec["hover"], spec["tol"])]
    rows.append({"check": f"theme: every hover is the highlight {_hex(spec['hover'])}",
                 "ok": not hov,
                 "evidence": "every hovered item" if not hov else f"{len(hov)} other hover(s): " + "; ".join(
                     f"{i['surface']} '{i['label'][:28]}' {_desc(spec, i['hover'])}" for i in hov[:4]),
                 "bad": [f"{i['surface']} | {i['label']}" for i in hov]})
    low = [i for i in items if i["contrast"] < spec["floor"]]
    rows.append({"check": f"theme: no text below the palette's weakest pair ({spec['floor']}:1)",
                 "ok": not low,
                 "evidence": "none" if not low else "; ".join(
                     f"{i['surface']} '{i['label'][:28]}' {i['contrast']:.2f}:1" for i in low[:4]),
                 "bad": [f"{i['surface']} | {i['label']}" for i in low]})
    cut = [i for i in items if i.get("clipped")]
    rows.append({"check": "theme: no menu or panel label is cut short",
                 "ok": not cut,
                 "evidence": "none" if not cut else "; ".join(f"{i['surface']} '{i['label'][:40]}'" for i in cut[:6]),
                 "bad": [f"{i['surface']} | {i['label']}" for i in cut]})
    return rows


def _hex(c):
    return "#%02X%02X%02X" % tuple(c)


def _desc(spec, c):
    n = name(spec, c)
    return f"{_hex(c)} ({n})" if n else f"{_hex(c)} (not in the theme)"


# ---- before the build: the colours Gorilla's own CSS lines set -------------------------------------------------------
# not the browser's own theme: a bundled extension draws its own interface, the web installer is a separate program
NOT_THEME = (re.compile(r"^browser/extensions/"), re.compile(r"/stubinstaller/"), re.compile(r"(^|/)tests?/"))


def static(tree, spec=None, files_glob=("*.css",)):
    """-> {"ok", "found": [(file, line no, colour, text)]} for colours in CSS lines the patch set ADDED (not in the
    tree's first commit, the pristine release) that are not in the spec."""
    spec = spec or load()
    tree = Path(tree)
    root = subprocess.run(["git", "-C", str(tree), "rev-list", "--max-parents=0", "HEAD"], capture_output=True,
                          text=True).stdout.split()
    if not root:
        return {"ok": False, "found": [], "why": "the tree has no pristine root commit"}
    diff = subprocess.run(["git", "-C", str(tree), "diff", "-U0", "--no-color", root[0], "--", *files_glob],
                          capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
    found, cur, n, skip = [], None, 0, False
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            cur = line[6:]
            skip = any(rx.search(cur) for rx in NOT_THEME)
        elif line.startswith("@@"):
            m = re.search(r"\+(\d+)", line)
            n = int(m.group(1)) - 1 if m else 0
        elif line.startswith("+") and not line.startswith("+++"):
            n += 1
            if skip:
                continue
            body = line[1:].split("/*")[0]
            if not COLOUR_PROPS.search(body):
                continue
            for m in list(HEX.finditer(body)) + list(RGB.finditer(body)):
                try:
                    c = _rgb(m.group(0))
                except ValueError:
                    continue
                if not name(spec, c):
                    found.append((cur, n, _hex(c), body.strip()[:120]))
    return {"ok": not found, "found": found}


def static_row(tree, spec=None):
    try:
        r = static(tree, spec)
    except FileNotFoundError as e:
        return {"check": "theme spec present", "ok": False, "evidence": str(e)}
    return {"check": "theme: every colour Gorilla's stylesheets set is in the theme spec (before the build)",
            "ok": r["ok"], "evidence": r.get("why") or ("all in the spec" if r["ok"] else
            f"{len(r['found'])} colour(s) outside the theme: " + "; ".join(f"{f}:{n} {c}" for f, n, c, _ in r["found"][:5]))}


# ---- applying the theme: every off-theme colour in a Gorilla line becomes its role's colour ------------------------
ROLE_BY_PROPERTY = [
    # a focus ring is the owner's cyan whatever property carries it (owner 2026-10-18); then by property
    (re.compile(r"focus(?!.*background)|outline|shadow", re.I), "cyan"),
    (re.compile(r"background|banner|-bg|bgcolor", re.I), "black"),
    (re.compile(r"border|separator", re.I), "teal_dark"),
    (re.compile(r"colou?r|fill|stroke|text", re.I), "cyan"),
]


def role_for(declaration):
    """The theme role a colour plays, from the property it is set on (the text before the colon)."""
    prop = declaration.split(":", 1)[0]
    return next((role for rx, role in ROLE_BY_PROPERTY if rx.search(prop)), None)


def plan_apply(tree, spec=None):
    """-> [(file, line, old colour, new colour, role, text)] for every off-theme colour static() finds."""
    spec = spec or load()
    out = []
    for f, n, c, text in static(tree, spec)["found"]:
        role = role_for(text)
        if role and role in spec["names"].values():
            out.append((f, n, c, _hex(spec["colours"][role]), role, text))
        else:
            out.append((f, n, c, None, role, text))      # no role: left for the owner, never guessed
    return out


def apply(tree, spec=None, dry_run=True):
    """Rewrite each planned colour on its own line (hex or rgb(a); an rgba keeps its alpha). -> the plan."""
    plan = plan_apply(tree, spec)
    if dry_run:
        return plan
    by_file = {}
    for item in plan:
        if item[3]:
            by_file.setdefault(item[0], []).append(item)
    for rel, items in by_file.items():
        p = Path(tree) / rel
        raw = p.read_bytes().decode("utf-8")
        nl = "\r\n" if "\r\n" in raw else "\n"
        lines = raw.split(nl)
        for _f, n, old, new, _role, _t in items:
            line = lines[n - 1]
            r, g, b = (int(new[i:i + 2], 16) for i in (1, 3, 5))

            def sub_hex(m, old=old, new=new):
                h = m.group(1)
                h = "".join(ch * 2 for ch in h) if len(h) == 3 else h
                return new if ("#" + h).upper() == old else m.group(0)

            def sub_rgb(m, old=old):
                c = _hex(tuple(int(x) for x in m.groups()[:3]))
                if c != old:
                    return m.group(0)
                alpha = m.group(4)
                return f"rgba({r}, {g}, {b}, {alpha})" if alpha else f"rgb({r}, {g}, {b})"
            line = HEX.sub(sub_hex, line)
            line = re.sub(r"rgba?\(\s*(\d+)\s*[, ]\s*(\d+)\s*[, ]\s*(\d+)\s*(?:[,/]\s*([\d.]+%?))?\s*\)", sub_rgb, line)
            lines[n - 1] = line
        p.write_bytes(nl.join(lines).encode("utf-8"))
    return plan
