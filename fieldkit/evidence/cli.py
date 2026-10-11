"""fieldkit evidence: fetch, import, check, show (see fieldkit/evidence/__init__.py).

    fieldkit evidence fetch URL... [--run RUN] [--quote TEXT] [--store DIR] [--ignore-robots] [--json]
    fieldkit evidence import FINDINGS.md [--out RECORD.jsonl] [--json]
    fieldkit evidence check RECORD.jsonl|FINDINGS.md [--store DIR] [--run RUN] [--fetched FILE] [--json]
    fieldkit evidence show URL [--store DIR] [--json]
    fieldkit evidence sources find|check|probe ...     the 985-source registry (fieldkit/evidence/sources.py)
    fieldkit evidence search SOURCE QUERY [--limit N] [--run RUN]   free scholarly/data APIs as JSON (search.py)
    fieldkit evidence render RECORD|FINDINGS --out report.md|.docx|.pdf [--store]   the checked run as a report

fetch goes online (an MCP client must say network=true). The store is FIELDKIT_EVIDENCE, else the user's data
folder (~/.local/share/fieldkit/evidence, %LOCALAPPDATA%\\fieldkit\\evidence).
check: --store checks every cited URL has a snapshot, answered 2xx, and contains the claim's quote;
--fetched FILE (one URL per line, or JSON lines with "url") checks every cited URL was opened in the run.
Exit 0 clean, 3 findings (or a fetch that was refused, failed, or lacks its quote), 2 bad input.
"""
import argparse
import http.client
import json
import sys
from pathlib import Path

from ..method import emit
from . import record as recmod
from . import text as textmod
from .fetch import Fetcher, Refused
from .store import Store


def _fetch(a):
    store = Store(a.store)
    f = Fetcher(store, ua=a.user_agent, timeout=a.timeout, allow_private=a.allow_private,
                respect_robots=not a.ignore_robots, min_interval=a.min_interval)
    rows, bad = [], 0
    for u in a.urls:
        try:
            r = f.fetch(u, a.run)
        except Refused as e:
            rows.append({"url": u, "ok": False, "refused": str(e)})
            bad += 1
            continue
        except (OSError, http.client.HTTPException) as e:
            rows.append({"url": u, "ok": False, "error": f"{type(e).__name__}: {e}"})
            bad += 1
            continue
        row = {"url": u, "ok": 200 <= r["status"] < 300, "status": r["status"], "final_url": r["final_url"],
               "sha256": r["sha256"], "bytes": r["bytes"], "text_chars": r["text_chars"], "text_how": r["text_how"],
               "title": r["title"], "fetched_at": r["fetched_at"], "truncated": r["truncated"],
               "text_path": str(store.text_path(r["sha256"])) if r["has_text"] else None,
               "challenge": r["challenge"]}
        if r["challenge"]:
            row["ok"] = False                          # a bot check is not the page, whatever the status says
        if a.quote:
            body = store.text(r)
            row["quote_found"] = bool(body) and textmod.contains(body, a.quote)
            row["ok"] = row["ok"] and row["quote_found"]
        bad += not row["ok"]
        rows.append(row)
    out = {"ok": not bad, "store": str(store.root), "fetched": rows,
           "next": "" if not bad else "a refused or failed page is not evidence: cite another, or say it is gone"}

    def lines(d):
        for r in d["fetched"]:
            if "status" in r:
                q = "" if "quote_found" not in r else ("  quote FOUND" if r["quote_found"] else "  quote NOT FOUND")
                c = "  BOT CHALLENGE, not the page" if r.get("challenge") else ""
                yield f"{r['status']}  {r['sha256'][:12]}  {r['text_chars']:>8} chars  {r['url']}{q}{c}"
            else:
                yield f"---  {r.get('refused') or r.get('error')}  {r['url']}"
    emit(out, a.json, lines)
    return 0 if not bad else 3


def _import(a):
    recs = recmod.load(a.file)
    if a.out:
        Path(a.out).write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs), encoding="utf-8")
    claims = sum(r["type"] == "claim" for r in recs)
    lanes = sum(r["type"] == "lane" for r in recs)
    out = {"ok": True, "lanes": lanes, "claims": claims, "doctrine": recs[0].get("doctrine"), "out": a.out,
           "next": f"fieldkit evidence check {a.out or a.file}" + (" --store DIR" if claims else "")}
    if not a.out and a.json:
        out["records"] = recs
    emit(out, a.json, lambda d: [f"{d['lanes']} lane(s), {d['claims']} claim(s), {d['doctrine']}"
                                 + (f" -> {d['out']}" if d["out"] else "")])
    return 0


def _check(a):
    recs = recmod.load(a.file)
    store = Store(a.store) if a.store is not None else None
    if a.fetched and not Path(a.fetched).is_file():
        print(f"REFUSED: no such file: {a.fetched}")
        return 2
    r = recmod.check(recs, store=store, fetched=a.fetched, run=a.run)
    r["ok"] = not r["findings"]
    r["next"] = "" if r["ok"] else "each finding names the claim and the rule; fix the claim or drop it"

    def lines(d):
        c = d["counts"]
        yield f"{c['lanes']} lane(s), {c['claims']} claim(s), {c['urls']} source(s), {c['doctrine']}"
        for f in d["findings"]:
            yield f"FINDING  {f['where']}: {f['message']}  [{f['rule']}]"
        for n in d["notes"]:
            yield f"note     {n['where']}: {n['message']}  [{n['rule']}]"
        if d["ok"]:
            yield "every claim holds"
    emit(r, a.json, lines)
    return 0 if r["ok"] else 3


def _show(a):
    store = Store(a.store)
    snap = store.latest(a.url)
    if snap is None:
        emit({"ok": False, "url": a.url, "next": f"fieldkit evidence fetch {a.url}"}, a.json,
             lambda d: [f"no snapshot of {d['url']}"])
        return 3
    snap = dict(snap, object=str(store.object_path(snap["sha256"])),
                text_path=str(store.text_path(snap["sha256"])) if snap.get("has_text") else None, ok=True)
    emit(snap, a.json, lambda d: [f"{d['status']}  {d['fetched_at']}  {d['sha256']}", f"object: {d['object']}"]
         + ([f"text:   {d['text_path']}"] if d["text_path"] else []))
    return 0


def _sources(a):
    from . import sources as srcmod
    try:
        srcs = srcmod.load(a.registry)
    except (OSError, ValueError) as e:
        print(f"REFUSED: cannot read the registry: {e}")
        return 2
    if a.what == "check":
        problems, notes = srcmod.check(srcs)
        out = {"ok": not problems, "sources": len(srcs), "problems": problems, "notes": notes,
               "next": "" if not problems else "correct the registry entries named"}
        emit(out, a.json, lambda d: [f"{d['sources']} sources, {len(d['problems'])} problem(s), "
                                     f"{len(d['notes'])} note(s)"] + [f"PROBLEM  {p}" for p in d["problems"]]
             + [f"note     {n}" for n in d["notes"][:40]])
        return 0 if not problems else 3
    found = srcmod.find(srcs, a.words, a.category, a.access, True if a.free else None, a.grade,
                        None if a.what == "probe" and not a.limit else a.limit)
    if a.what == "find":
        emit({"ok": True, "count": len(found), "sources": found}, a.json,
             lambda d: [f"[{s['reliability']} {s['access']}] {s['name']}  {s.get('url') or '(no URL)'}  "
                        f"- {s.get('category', '')}" for s in d["sources"]] or ["nothing matches"])
        return 0
    f = Fetcher(Store(a.store), timeout=a.timeout, min_interval=a.min_interval)
    rows = srcmod.probe(found, f, a.run or "sources-probe")
    dead = [r for r in rows if r["ok"] is False]
    out = {"ok": not dead, "probed": len(rows), "dead": len(dead), "rows": rows,
           "next": "" if not dead else "a dead address is a registry fix: correct its url or note why it is gone"}
    emit(out, a.json, lambda d: [f"{d['probed']} probed, {d['dead']} not reachable"]
         + [f"{'ok  ' if r['ok'] else ('--  ' if r['ok'] is None else 'DEAD')}  {r['name']}  {r['why']}"
            for r in d["rows"] if not r["ok"]])
    return 0 if not dead else 3


def _search(a):
    from . import search as searchmod
    if a.source == "list":
        emit({"ok": True, "sources": {k: v[2] for k, v in searchmod.BACKENDS.items()}}, a.json,
             lambda d: [f"{k:12} {v}" for k, v in d["sources"].items()])
        return 0
    if not a.query:
        print("REFUSED: a query is needed: fieldkit evidence search SOURCE QUERY")
        return 2
    f = Fetcher(Store(a.store), timeout=a.timeout, min_interval=a.min_interval, respect_robots=False)
    try:
        r = searchmod.search(a.source, " ".join(a.query), a.limit, fetcher=f, run=a.run or None)
    except ValueError as e:
        print(f"REFUSED: {e}")
        return 2
    except (Refused, RuntimeError, OSError, http.client.HTTPException) as e:
        emit({"ok": False, "source": a.source, "error": str(e)[:300],
              "next": "nothing was searched: that is not evidence that nothing exists"}, a.json,
             lambda d: [f"{d['source']}: {d['error']}"])
        return 3
    r["ok"] = True

    def lines(d):
        yield f"{d['count']} hit(s) from {d['source']} for {d['query']!r} ({d['searched_at']})"
        for i, h in enumerate(d["hits"], 1):
            meta = " · ".join(x for x in (h["date"] or h["year"], h["venue"]) if x)
            yield f"{i:2}. {h['title']}" + (f"  [{meta}]" if meta else "")
            if h["authors"]:
                yield f"    {h['authors']}"
            yield f"    {h['url'] or '(no url)'}" + (f"  free: {h['free_pdf']}" if h["free_pdf"] and
                                                     h["free_pdf"] != h["url"] else "")
    emit(r, a.json, lines)
    return 0


def _render(a):
    from . import render as rendermod
    recs = recmod.load(a.file)
    store = Store(a.store) if a.store is not None else None
    try:
        r = rendermod.render(recs, a.out, store=store, run=a.run, fetched=a.fetched)
    except ValueError as e:
        print(f"REFUSED: {e}")
        return 2
    c = r["counts"]
    out = {"ok": True, "out": a.out, "claims": c["claims"], "findings": c["findings"],
           "next": f"fieldkit office check {a.out}" if not a.out.endswith(".md") else ""}
    emit(out, a.json, lambda d: [f"{d['claims']} claim(s), {d['findings']} finding(s) -> {d['out']}"])
    return 0


def main(argv=None, prog="fieldkit evidence"):
    ap = argparse.ArgumentParser(prog=prog, description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="action", required=True)
    f = sub.add_parser("fetch", help="snapshot pages into the evidence store (goes online)")
    f.add_argument("urls", nargs="+")
    f.add_argument("--run", default="", help="the research run this fetch belongs to")
    f.add_argument("--quote", help="also check this passage is in the page")
    f.add_argument("--store")
    f.add_argument("--ignore-robots", action="store_true", help="do not consult robots.txt")
    f.add_argument("--allow-private", action="store_true", help="allow loopback and private addresses (a test server)")
    f.add_argument("--user-agent")
    f.add_argument("--timeout", type=float, default=30)
    f.add_argument("--min-interval", type=float, default=1.0, help="seconds between requests to one host")
    f.add_argument("--json", action="store_true")
    i = sub.add_parser("import", help="a findings file or lane reply -> one record per claim")
    i.add_argument("file")
    i.add_argument("--out")
    i.add_argument("--json", action="store_true")
    c = sub.add_parser("check", help="every claim against the output contract and the snapshots")
    c.add_argument("file")
    c.add_argument("--store", nargs="?", const="", default=None,
                   help="check snapshots and quotes (the default store when no folder is given)")
    c.add_argument("--run", default=None, help="only snapshots fetched for this run")
    c.add_argument("--fetched", help="the URLs the run actually opened, one per line")
    c.add_argument("--json", action="store_true")
    s = sub.add_parser("show", help="the latest snapshot of a URL")
    s.add_argument("url")
    s.add_argument("--store")
    s.add_argument("--json", action="store_true")
    so = sub.add_parser("sources", help="the source registry: find, check, probe")
    so.add_argument("what", choices=["find", "check", "probe"])
    so.add_argument("words", nargs="*", help="find: words in the name, category or note")
    so.add_argument("--category")
    so.add_argument("--access", nargs="+", help="access classes, e.g. api_keyless fetch_pattern")
    so.add_argument("--free", action="store_true", help="free sources only")
    so.add_argument("--grade", choices=list("ABCDEF"), help="this grade or better")
    so.add_argument("--limit", type=int, default=20)
    so.add_argument("--registry", help="a source-registry.json (default: FIELDKIT_SOURCES, else Fieldkit's copy)")
    so.add_argument("--store", help="probe: the evidence store")
    so.add_argument("--run", default="", help="probe: the run label in the store")
    so.add_argument("--timeout", type=float, default=20)
    so.add_argument("--min-interval", type=float, default=1.0)
    so.add_argument("--json", action="store_true")
    se = sub.add_parser("search", help="search a free scholarly or data API (goes online); 'list' names them")
    se.add_argument("source")
    se.add_argument("query", nargs="*")
    se.add_argument("--limit", type=int, default=10)
    se.add_argument("--run", default="", help="keep the raw answer in the evidence store under this run")
    se.add_argument("--store")
    se.add_argument("--timeout", type=float, default=30)
    se.add_argument("--min-interval", type=float, default=1.0)
    se.add_argument("--json", action="store_true")
    re_ = sub.add_parser("render", help="the checked run as a report (.md, .docx, .pdf)")
    re_.add_argument("file")
    re_.add_argument("--out", required=True)
    re_.add_argument("--store", nargs="?", const="", default=None)
    re_.add_argument("--run", default=None)
    re_.add_argument("--fetched")
    re_.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if getattr(a, "store", None) == "":
        a.store = None if a.action not in ("check", "render") else ""
    if a.action in ("check", "render") and a.store == "":
        a.store = str(Store().root)
    if a.action in ("import", "check", "render") and not Path(a.file).is_file():
        print(f"REFUSED: no such file: {a.file}")
        return 2
    return {"fetch": _fetch, "import": _import, "check": _check, "show": _show, "sources": _sources,
            "search": _search, "render": _render}[a.action](a)


if __name__ == "__main__":
    sys.exit(main())
