"""fieldkit web: fetch URL [--browser auto|firefox|safari] [--timeout S] [--json] | doctor [--install] (see __init__.py).

Exit 0 fine, 1 error (no browser set up, browser failed), 3 findings (a doctor with something missing, a page still
blocked after the browser).
"""
import argparse
import sys

from ..method import emit
from . import browser, doctor, rule


def _http(url, timeout):
    """The evidence fetcher, the HTTP reader. -> (snapshot record, text). Raises evidence.fetch.Refused."""
    from ..evidence.fetch import Fetcher
    from ..evidence.store import Store
    store = Store()
    rec = Fetcher(store, timeout=timeout).fetch(url)
    return rec, (store.text(rec) or "")


def fetch(url, mode="auto", timeout=30, http=_http, browse=browser.fetch):
    """-> result dict: via ('http'|'browser'), reason the browser was tried (auto), title, final_url, text, ok."""
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
        mode = "firefox" if sys.platform != "darwin" else "safari"
    got = browse(url, browser=mode, timeout=timeout)
    return out | {"via": "browser", "ok": True, "browser": mode, "title": got["title"],
                  "final_url": got["final_url"], "text": got["text"]}


def _fetch(a):
    from ..evidence.fetch import Refused
    try:
        r = fetch(a.url, a.browser, a.timeout)
    except (Refused, browser.BrowserError) as e:
        emit({"ok": False, "url": a.url, "error": str(e), "next": "fieldkit web doctor"}, a.json,
             lambda d: [f"REFUSED/FAILED: {d['error']}"])
        return 1
    if r["via"] == "browser":
        r["ok"] = not rule.text_unusable(r["text"])
    r["next"] = "" if r["ok"] else "the page is still blocked or empty in a real browser: cite another source"

    def lines(d):
        yield f"via {d['via']}" + (f" ({d['reason']})" if d["reason"] else "") + f"  {d['final_url']}"
        yield f"title: {d['title']}"
        yield ""
        yield d["text"]
    emit(r, a.json, lines)
    return 0 if r["ok"] else 3


def _doctor(a):
    rows = doctor.check()
    missing = [r for r in rows if not r["ok"]]
    done, refused = [], ""
    if a.install and missing:
        done, refused = doctor.install(rows)
    out = {"ok": not missing, "checks": rows, "installed": done,
           "next": "" if not missing else (refused or ("fieldkit web doctor" if done else
                                                        "fieldkit web doctor --install (at a terminal)"))}

    def lines(d):
        for r in d["checks"]:
            yield f"[{'ok' if r['ok'] else 'MISSING'}] {r['item']}: {r['detail']}"
            if r["fix"]:
                yield f"    fix: {r['fix']}"
        if refused:
            yield refused
    emit(out, a.json, lines)
    return 0 if not missing else (1 if refused else 3)


def main(argv):
    p = argparse.ArgumentParser(prog="fieldkit web", description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch", help="a page as text: HTTP first, a real headless browser when blocked or empty")
    f.add_argument("url")
    f.add_argument("--browser", choices=("auto", "firefox", "safari"), default="auto")
    f.add_argument("--timeout", type=float, default=30)
    f.add_argument("--json", action="store_true")
    f.set_defaults(fn=_fetch)
    d = sub.add_parser("doctor", help="check selenium, geckodriver and the browser path; --install asks a person")
    d.add_argument("--install", action="store_true")
    d.add_argument("--json", action="store_true")
    d.set_defaults(fn=_doctor)
    a = p.parse_args(argv)
    return a.fn(a)
