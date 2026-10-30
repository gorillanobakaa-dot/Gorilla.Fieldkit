"""fieldkit web: browsers | fetch URL [--browser auto|NAME] [--engine E] | search QUERY | doctor (see __init__.py).

Exit 0 fine, 1 error (no browser can be driven, all failed), 3 findings (a page still blocked after the browser, a
doctor with something that cannot be driven, a search with no results).
"""
import argparse

from ..method import emit
from . import browser, doctor, rule
from . import search as searchmod


def _http(url, timeout):
    """The evidence fetcher, the HTTP reader. -> (snapshot record, text). Raises evidence.fetch.Refused."""
    from ..evidence.fetch import Fetcher
    from ..evidence.store import Store
    store = Store()
    rec = Fetcher(store, timeout=timeout).fetch(url)
    return rec, (store.text(rec) or "")


def fetch(url, mode="auto", engine=None, timeout=30, http=_http, browse=browser.fetch):
    """-> result dict: via ('http'|'browser'), reason the browser was tried (auto), browser, title, final_url, text."""
    out = {"url": url, "via": "", "reason": ""}
    if mode == "auto":
        try:
            rec, text = http(url, timeout)
        except OSError as e:                              # a network failure: the browser may do better
            out["reason"] = f"HTTP reader failed: {e}"
        else:                                             # Refused (robots.txt, private address) is not caught: obeyed
            why = rule.blocked_or_empty(rec)
            if not why:
                return out | {"via": "http", "ok": True, "title": rec.get("title", ""), "final_url": rec["final_url"],
                              "status": rec["status"], "text": text}
            out["reason"] = why
    got = browse(url, browser=mode, engine=engine, timeout=timeout)
    return out | {"via": "browser", "ok": True, "browser": got["browser"], "engine": got["engine"],
                  "method": got["method"], "tried": got["tried"], "title": got["title"],
                  "final_url": got["final_url"], "text": got["text"]}


def _fetch(a):
    from ..evidence.fetch import Refused
    try:
        r = fetch(a.url, a.browser, a.engine, a.timeout)
    except (Refused, browser.BrowserError) as e:
        emit({"ok": False, "url": a.url, "error": str(e), "next": "fieldkit web doctor"}, a.json,
             lambda d: [f"REFUSED/FAILED: {d['error']}"])
        return 1
    if r["via"] == "browser":
        r["ok"] = not rule.text_unusable(r["text"])
    r["next"] = "" if r["ok"] else "the page is still blocked or empty in a real browser: cite another source"

    def lines(d):
        who = f"browser {d['browser']} ({d['method']})" if d["via"] == "browser" else "http"
        yield f"via {who}" + (f" ({d['reason']})" if d["reason"] else "") + f"  {d['final_url']}"
        for t in d.get("tried") or ():
            yield f"  first failed: {t['browser']}: {t['error']}"
        yield f"title: {d['title']}"
        yield ""
        yield d["text"]
    emit(r, a.json, lines)
    return 0 if r["ok"] else 3


def _browsers(a):
    rows = browser.installed()
    emit({"ok": True, "browsers": rows, "next": "fieldkit web doctor (the ones that cannot be driven)"}, a.json,
         lambda d: [f"{r['name']:<22} {r['version']:<18} {r['engine']:<9} {r['method'] or 'NOT DRIVABLE':<18} {r['path']}"
                    for r in d["browsers"]] or ["no browser found"])
    return 0 if rows else 3


def _search(a):
    try:
        r = searchmod.search(a.query, a.engine, a.n, a.timeout)
    except browser.BrowserError as e:
        emit({"ok": False, "error": str(e)}, a.json, lambda d: [d["error"]])
        return 2
    r["ok"] = bool(r["results"])
    r["next"] = "" if r["ok"] else "no engine answered: " + "; ".join(r["tried"])

    def lines(d):
        yield f"engine {d['engine'] or '-'} via {d['via'] or '-'}"
        for i, x in enumerate(d["results"], 1):
            yield f"{i}. {x['title']}\n   {x['url']}\n   {x['snippet']}"
        if not d["results"]:
            yield d["next"]
    emit(r, a.json, lines)
    return 0 if r["ok"] else 3


def _doctor(a):
    rows = doctor.check()
    bad = [r for r in rows if not r["ok"]]
    out = {"ok": not bad, "checks": rows, "next": "" if not bad else "do the fix lines yourself; nothing was changed"}

    def lines(d):
        for r in d["checks"]:
            yield f"[{'ok' if r['ok'] else 'CANNOT'}] {r['item']}: {r['detail']}"
            if r["fix"]:
                yield f"    fix: {r['fix']}"
    emit(out, a.json, lines)
    return 0 if not bad else 3


def main(argv):
    p = argparse.ArgumentParser(prog="fieldkit web", description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("browsers", help="every browser installed, its engine and the method that drives it")
    b.add_argument("--json", action="store_true")
    b.set_defaults(fn=_browsers)
    f = sub.add_parser("fetch", help="a page as text: HTTP first, an installed headless browser when blocked or empty")
    f.add_argument("url")
    f.add_argument("--browser", default="auto", help="auto, or part of a browser's name (edge, chrome, firefox, safari)")
    f.add_argument("--engine", choices=("chromium", "gecko", "webkit"))
    f.add_argument("--timeout", type=float, default=30)
    f.add_argument("--json", action="store_true")
    f.set_defaults(fn=_fetch)
    s = sub.add_parser("search", help="a web search through the engines Gorilla OpenCode uses")
    s.add_argument("query")
    s.add_argument("--engine", help="brave, ecosia, mojeek or marginalia (default: in that order)")
    s.add_argument("-n", type=int, default=10)
    s.add_argument("--timeout", type=float, default=30)
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=_search)
    d = sub.add_parser("doctor", help="the browsers found, how each is driven, what cannot be and the one step to fix it")
    d.add_argument("--json", action="store_true")
    d.set_defaults(fn=_doctor)
    a = p.parse_args(argv)
    return a.fn(a)
