"""Does the release page tell everything done since the last release?

    fieldkit build-harness release-cover <task> page=<release page .md> [previous=<tag>]

Born 2026-10-09. The maintainer: "all the work you and I done needs to go on the release page as well... When the new
build is uploaded make sure it happens." The work of a build lives in the decision register (one decision per
change, decisions/PRODUCT-DECISIONS.yaml); laymen read only the release page. So the page is held to the register:
  - every decision the previous release's page does not name, and that was decided on or after the day of that
    release, must be named on the new page (its ID, for example D-157-40) - with its story, which the writer owns;
  - the page fits GitHub's release-page limit (125,000 characters; a longer body is refused at publish time);
  - when D-157-40 is among them, the page carries the hidden pages' release edition (hidden-pages-doc release=).
The previous release page is read from GitHub (gh release view), or from a file given as previous=<path>.
Fail closed: no page, no register, or no previous page -> not OK.
"""
import json
import re
import subprocess
from pathlib import Path

import yaml

LIMIT = 125000
DECISION = re.compile(r"\bD-\d{3}-\d{2}\b")
HIDDEN_HEADING = "## What's new: the hidden pages"


def previous_release(repo="gorillanobakaa-dot/gorilla-firefox", tag=None, run=subprocess.run):
    """-> {"tag", "published", "body"} of the given or latest release, read with gh."""
    args = ["gh", "release", "view"] + ([tag] if tag else []) + ["-R", repo, "--json", "tagName,publishedAt,body"]
    r = run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    if r.returncode != 0:
        raise RuntimeError(f"gh release view failed: {(r.stderr or r.stdout).strip()[:200]}")
    d = json.loads(r.stdout)
    return {"tag": d["tagName"], "published": d["publishedAt"][:10], "body": d["body"]}


def due(register, previous_body, since):
    """Decisions the new page must name -> [(id, title)]: decided on or after `since` (YYYY-MM-DD) and not named on
    the previous page."""
    named = set(DECISION.findall(previous_body or ""))
    out = []
    for e in (yaml.safe_load(Path(register).read_text(encoding="utf-8")) or {}).get("decisions") or []:
        if str(e.get("decided", "")) >= since and e["id"] not in named and e.get("status") != "retired":
            out.append((e["id"], e.get("title", "")))
    return sorted(out)


def check(page_text, register, previous_body, since):
    """-> rows [{"check", "ok", "evidence"}]."""
    rows = []
    want = due(register, previous_body, since)
    have = set(DECISION.findall(page_text))
    missing = [(i, t) for i, t in want if i not in have]
    rows.append({"check": "release page: every decision since the last release is on it", "ok": not missing,
                 "evidence": ("missing: " + "; ".join(f"{i} ({t[:60]})" for i, t in missing)) if missing else
                             f"{len(want)} decision(s) since {since}, all named: {', '.join(i for i, _ in want) or 'none'}"})
    rows.append({"check": "release page: fits GitHub's limit", "ok": len(page_text) <= LIMIT,
                 "evidence": f"{len(page_text)} of {LIMIT} characters"})
    # every release page tells how long and how hard the leak test was, scene by scene (owner 2026-10-10)
    from ..leakgate import summary as lsum
    ok = lsum.HEADING in page_text
    rows.append({"check": "release page: carries the leak test scene by scene", "ok": ok,
                 "evidence": "present" if ok else f"no '{lsum.HEADING}' section: build-harness leakgate-summary <task> out=<file>"})
    if any(i == "D-157-40" for i, _ in want):
        ok = HIDDEN_HEADING in page_text
        rows.append({"check": "release page: carries the hidden pages, on the page (D-157-40)", "ok": ok,
                     "evidence": "present" if ok else f"no '{HIDDEN_HEADING}' section: render it with hidden-pages-doc release="})
    return rows


def run(owner, page, previous=None, run_cmd=subprocess.run):
    """-> rows; `previous` is a tag (read from GitHub) or a path to the previous page's text."""
    owner = Path(owner)
    register = owner / "decisions" / "PRODUCT-DECISIONS.yaml"
    if not page or not Path(page).is_file():
        return [{"check": "release page: found", "ok": False, "evidence": f"no release page at {page}"}]
    if not register.is_file():
        return [{"check": "release page: decision register found", "ok": False, "evidence": f"no {register}"}]
    if previous and Path(previous).is_file():
        prev = {"tag": Path(previous).name, "published": None, "body": Path(previous).read_text(encoding="utf-8")}
        m = re.search(r"\b(20\d\d-\d\d-\d\d)\b", prev["body"])
        prev["published"] = m.group(1) if m else "0000-00-00"
    else:
        try:
            prev = previous_release(tag=previous, run=run_cmd)
        except (RuntimeError, OSError, ValueError, KeyError) as e:
            return [{"check": "release page: previous release read", "ok": False, "evidence": str(e)[:200]}]
    rows = check(Path(page).read_text(encoding="utf-8"), register, prev["body"], prev["published"])
    rows.insert(0, {"check": "release page: previous release read", "ok": True,
                    "evidence": f"{prev['tag']} ({prev['published']}), {len(prev['body'])} characters"})
    return rows
