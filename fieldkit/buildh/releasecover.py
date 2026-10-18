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


def _no_network_during_gate(run):
    """gh goes to the network; never while a release leak gate measures this machine (2026-10-19: two release-cover
    runs called gh mid-gate). A test's fake runner is not the network."""
    if run is subprocess.run:
        from ..leakgate import status as _st
        pids = _st.gate_running()
        if pids:
            raise RuntimeError(f"a leak gate is running (PID {pids[0]}): release-cover reads GitHub, so it waits until "
                               "the gate has finished (fieldkit eta leakgate <run folder>)")


def previous_release(repo="gorillanobakaa-dot/gorilla-firefox", tag=None, run=subprocess.run):
    """-> {"tag", "published", "body"} of the given or latest release, read with gh."""
    _no_network_during_gate(run)
    args = ["gh", "release", "view"] + ([tag] if tag else []) + ["-R", repo, "--json", "tagName,publishedAt,body"]
    r = run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    if r.returncode != 0:
        raise RuntimeError(f"gh release view failed: {(r.stderr or r.stdout).strip()[:200]}")
    d = json.loads(r.stdout)
    return {"tag": d["tagName"], "published": d["publishedAt"][:10], "body": d["body"]}


def platform_pages(repo="gorillanobakaa-dot/gorilla-firefox", suffix="-win64", run=subprocess.run, limit=12):
    """-> [{"tag", "published", "body"}] of the platform's published releases (tag ends with `suffix`), newest first."""
    _no_network_during_gate(run)
    r = run(["gh", "release", "list", "-R", repo, "--limit", "40", "--json", "tagName,publishedAt"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    if r.returncode != 0:
        raise RuntimeError(f"gh release list failed: {(r.stderr or r.stdout).strip()[:200]}")
    # suffix None: every tag that is not a Windows one (Linux tags carry the package revision, v158.0-1)
    tags = [d for d in json.loads(r.stdout)
            if (d["tagName"].endswith(suffix) if suffix else not d["tagName"].endswith("-win64"))]
    tags.sort(key=lambda d: d["publishedAt"], reverse=True)
    return [previous_release(repo, tag=d["tagName"], run=run) for d in tags[:limit]]


def uncovered(register, pages, since):
    """Decisions decided on or after `since` that NO page of the platform published on or after the decision names
    -> [(id, title)]. 2026-10-18: the previous page (158.0-win64 build 1, written overnight without release-cover)
    named 6 of the 158 decisions; judged only by its date, the other 158 decisions counted as told and would never
    have reached a Windows page."""
    out = []
    for e in (yaml.safe_load(Path(register).read_text(encoding="utf-8")) or {}).get("decisions") or []:
        d = str(e.get("decided", ""))
        if e.get("status") == "retired" or d < since:
            continue
        if not any(p["published"] >= d and e["id"] in set(DECISION.findall(p["body"] or "")) for p in pages):
            out.append((e["id"], e.get("title", "")))
    return sorted(out)


def due_text(register, ids):
    """The register's own words for each due decision, in FULL: title, decided, status, platforms, why, cost fields.
    2026-10-18: a hand-made list cut each `why` at 900 characters and dropped `platforms`; the page then lost three
    costs (D-157-36, -39, -41) and called a Linux-only decision general (D-158-05). The writer works from this."""
    data = {e["id"]: e for e in (yaml.safe_load(Path(register).read_text(encoding="utf-8")) or {}).get("decisions") or []}
    out = ["# Decisions the release page must name, in the register's own words (release-cover)", ""]
    for i in ids:
        e = data.get(i, {})
        out.append(f"## {i}: {e.get('title', '')}")
        out.append(f"- decided {e.get('decided')}, status {e.get('status')}, platforms {e.get('platforms') or 'all'}")
        for k, v in e.items():                       # every descriptive field, whole (costs live in why/audit/...)
            if k in ("id", "title", "decided", "by", "provenance", "status", "platforms", "verify", "covers", "models"):
                continue
            if v:
                out.append(f"- {k}: {' '.join(str(v).split())}")
        out.append("")
    return "\n".join(out)


def due(register, previous_body, since):
    """Decisions the new page must name -> [(id, title)]: decided on or after `since` (YYYY-MM-DD) and not named on
    the previous page."""
    named = set(DECISION.findall(previous_body or ""))
    out = []
    for e in (yaml.safe_load(Path(register).read_text(encoding="utf-8")) or {}).get("decisions") or []:
        if str(e.get("decided", "")) >= since and e["id"] not in named and e.get("status") != "retired":
            out.append((e["id"], e.get("title", "")))
    return sorted(out)


def check(page_text, register, previous_body, since, want=None):
    """-> rows [{"check", "ok", "evidence"}]. `want`: the decisions due, when computed from every platform page."""
    rows = []
    want = want if want is not None else due(register, previous_body, since)
    have = set(DECISION.findall(page_text))
    missing = [(i, t) for i, t in want if i not in have]
    rows.append({"check": "release page: every decision since the last release is on it", "ok": not missing,
                 "evidence": ("missing: " + "; ".join(f"{i} ({t[:60]})" for i, t in missing)) if missing else
                             f"{len(want)} decision(s) since {since}, all named: {', '.join(i for i, _ in want) or 'none'}"})
    # 2026-10-18: a placeholder heading passed the leak-test row; nothing marked TO BE FILLED may reach a release
    holes = [l.strip()[:80] for l in page_text.splitlines() if re.search(r"TO[ -]BE[ -]FILLED", l, re.I)]
    rows.append({"check": "release page: no placeholder left (TO BE FILLED)", "ok": not holes,
                 "evidence": "none" if not holes else f"{len(holes)} left, e.g. {holes[:2]}"})
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
    from .decisions import register_file
    register = register_file(owner)                  # the shared set's register when the owner repo carries it
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
    # every decision a page of this platform never told, since the platform's first release (2026-10-18), plus the
    # previous page's own due list; given a previous page as a file (tests, offline), only that page is judged
    want = None
    if not (previous and Path(previous).is_file()):
        import sys as _sys
        try:
            pages = platform_pages(suffix="-win64" if _sys.platform == "win32" else None, run=run_cmd)
            if pages:
                since = min(p["published"] for p in pages)
                want = sorted(set(uncovered(register, pages, since)) | set(due(register, prev["body"], prev["published"])))
        except (RuntimeError, OSError, ValueError, KeyError) as e:
            return [{"check": "release page: earlier release pages read", "ok": False, "evidence": str(e)[:200]}]
    rows = check(Path(page).read_text(encoding="utf-8"), register, prev["body"], prev["published"], want=want)
    rows.insert(0, {"check": "release page: previous release read", "ok": True,
                    "evidence": f"{prev['tag']} ({prev['published']}), {len(prev['body'])} characters"})
    return rows
