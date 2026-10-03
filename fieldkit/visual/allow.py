"""The visual allowlist: known, accepted exceptions, each with a reason, and where each raster's master is.

The file is docs/visual/ALLOWLIST.yaml in the Fieldkit folder (committed). An entry without a reason, rule or item
is a problem, and a problem fails the check: an exception nobody can explain is not an exception.
"""
import fnmatch
from pathlib import Path

import yaml

from ..core import settings

DEFAULT = settings.ROOT / "docs" / "visual" / "ALLOWLIST.yaml"
VERDICTS = ("PASS", "FAIL", "UNVERIFIABLE")


def load(path=None):
    p = Path(path) if path else DEFAULT
    if not p.is_file():
        return {"path": str(p), "masters": [], "css_include": [], "css_exclude": [], "accept": [],
                "problems": [f"no allowlist file at {p}"]}
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    problems = []
    accept = data.get("accept") or []
    for i, e in enumerate(accept):
        for need in ("rule", "item", "why"):
            if not str(e.get(need) or "").strip():
                problems.append(f"accept[{i}] has no {need}: an accepted exception needs a rule, an item and a reason")
    masters = data.get("masters") or []
    for i, m in enumerate(masters):
        if not m.get("files") or not m.get("master") or not str(m.get("why") or "").strip():
            problems.append(f"masters[{i}] needs files, master and why")
    css = data.get("css") or {}
    excl = css.get("exclude") or []
    for i, e in enumerate(excl):
        if not e.get("glob") or not str(e.get("why") or "").strip():
            problems.append(f"css.exclude[{i}] needs glob and why")
    return {"path": str(p), "masters": masters, "css_include": list(css.get("include") or []), "css_exclude": excl,
            "accept": accept, "problems": problems}


def master_for(rel, allow, owner_root=None, tree=None):
    """-> (master Path or None, the masters entry or None) for a raster's tree-relative path."""
    for m in allow.get("masters") or []:
        if fnmatch.fnmatch(rel, m["files"]):
            raw = str(m["master"]).replace("{owner}", str(owner_root or "")).replace("{tree}", str(tree or ""))
            if "{owner}" in str(m["master"]) and not owner_root:
                return None, m
            return Path(raw), m
    return None, None


def excluded(rel, allow):
    for e in allow.get("css_exclude") or []:
        if fnmatch.fnmatch(rel, e["glob"]):
            return e["why"]
    return None


def apply(items, allow):
    """Mark every FAIL/UNVERIFIABLE item an entry accepts (rule equal, item glob, optional evidence substring).
    -> the entries that matched nothing (reported as stale)."""
    used = set()
    for it in items:
        if it["verdict"] == "PASS":
            continue
        for i, e in enumerate(allow.get("accept") or []):
            if str(e.get("rule")) != it["rule"] and str(e.get("rule")) != "*":
                continue
            if not fnmatch.fnmatch(it["item"], str(e.get("item"))):
                continue
            if e.get("match") and str(e["match"]) not in it["evidence"]:
                continue
            if e.get("verdict") and str(e["verdict"]) != it["verdict"]:
                continue
            it["accepted"] = str(e["why"]).strip()
            used.add(i)
            break
    return [e for i, e in enumerate(allow.get("accept") or []) if i not in used]


def layer_of(rule):
    return "runtime" if str(rule).startswith("RT-") else "static"


def summarise(items, allow, layer):
    mine = dict(allow, accept=[e for e in allow.get("accept") or [] if layer_of(e.get("rule")) == layer or e.get("rule") == "*"])
    stale = apply(items, mine)
    bad = [i for i in items if i["verdict"] != "PASS" and not i.get("accepted")]
    counts = {v: sum(i["verdict"] == v for i in items) for v in VERDICTS}
    counts["accepted"] = sum(bool(i.get("accepted")) for i in items)
    ok = bool(items) and not bad and not allow["problems"]
    return {"layer": layer, "ok": ok, "items": items, "counts": counts, "bad": len(bad),
            "problems": list(allow["problems"]) + ([] if items else ["no items were checked: nothing proves anything"]),
            "stale_allow": [f"{e.get('rule')} {e.get('item')}" for e in stale], "allowlist": allow["path"]}
