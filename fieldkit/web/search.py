"""fieldkit web search: the engines Gorilla OpenCode searches with (internal/llm/tools/websearch_lynx.go), through the
same fetch, the results read as title / url / snippet. Plain HTTP first; a browser when the engine blocks it.
"""
import re
import urllib.request
from urllib.parse import quote_plus, urlsplit

from . import browser, htmltext

ENGINES = [  # (name, URL with %s, the engine's own domain)
    ("brave", "https://search.brave.com/search?q=%s", "brave.com"),
    ("ecosia", "https://www.ecosia.org/search?q=%s", "ecosia.org"),
    ("mojeek", "https://www.mojeek.com/search?q=%s", "mojeek.com"),
    ("marginalia", "https://old-search.marginalia.nu/search?query=%s", "marginalia.nu")]
BLOCKED = re.compile(r"captcha|complete this challenge|barraged by queries|are you a (robot|human)|unusual traffic|"
                     r"access denied|too many requests", re.I)
UA = "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0"


def parse_results(html, own_domain, n=10):
    """-> [{"title","url","snippet"}]: external links with a real title, the next long text as the snippet."""
    ev = htmltext.parse(html)["events"]
    out, seen = [], set()
    for i, e in enumerate(ev):
        if e[0] != "a" or not e[1].startswith(("http://", "https://")) or len(e[2]) < 8:
            continue
        host = urlsplit(e[1]).hostname or ""
        if own_domain in host or e[1] in seen:
            continue
        seen.add(e[1])
        snip = ""
        for f in ev[i + 1:i + 16]:
            if f[0] == "a" and f[1].startswith("http") and len(f[2]) >= 8:
                break
            if f[0] == "t" and len(f[1]) >= 40:
                snip = f[1][:300]
                break
        out.append({"title": e[2][:200], "url": e[1], "snippet": snip})
        if len(out) >= n:
            break
    return out


def _http_html(url, timeout):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(2_000_000).decode("utf-8", "replace")


def search(query, engine=None, n=10, timeout=30, http=_http_html, browse=browser.fetch):
    """-> {"query","engine","via","results","tried"}. Engines in order until one gives results."""
    engines = [e for e in ENGINES if not engine or e[0] == engine.lower()]
    if not engines:
        raise browser.BrowserError(f"unknown engine {engine!r}; known: {', '.join(e[0] for e in ENGINES)}")
    tried = []
    for name, tmpl, domain in engines:
        url = tmpl % quote_plus(query)
        for via in ("http", "browser"):
            try:
                html = http(url, timeout) if via == "http" else browse(url, timeout=timeout)["html"]
            except (OSError, browser.BrowserError) as e:
                tried.append(f"{name}/{via}: {e}")
                continue
            page = htmltext.parse(html)
            if len(page["text"]) < 2500 and BLOCKED.search(page["title"] + " " + page["text"]):
                tried.append(f"{name}/{via}: blocked ({page['title'] or page['text'][:60]})")
                continue
            res = parse_results(html, domain, n)
            if res:
                return {"query": query, "engine": name, "via": via, "results": res, "tried": tried}
            tried.append(f"{name}/{via}: no results in the page")
    return {"query": query, "engine": "", "via": "", "results": [], "tried": tried}
