"""Search the free scholarly and data APIs, as data: the same query gives the same JSON, in the API's own order.

    fieldkit evidence search SOURCE QUERY [--limit N] [--run RUN] [--json]
    fieldkit evidence search list

Ported from Gorilla OpenCode's web_search backends (internal/llm/tools/websearch*.go, MIT), for any agent. Differences
on purpose: full dates where the API gives them (not only the year), results in the API's order (World Bank's came
back in Go's random map order), and with --run every raw answer is kept in the evidence store, so "the search
returned this, on this date" is on record like any page.

These are documented APIs for programs, so robots.txt (which is about crawling pages) is not consulted; every other
rule of `evidence fetch` holds: public addresses only, paced per host, 429 waits, an honest User-Agent. Unpaywall and
SEC EDGAR require a contact address: FIELDKIT_CONTACT_EMAIL. SearXNG is the user's own instance (FIELDKIT_SEARXNG or
SEARXNG_URL), the one address allowed to be private, because the user, not a model, chose it.
"""
import json
import os
import re
from datetime import datetime, timezone
from urllib.parse import quote, quote_plus, urlsplit

from . import fetch as fetchmod

MAX_ABSTRACT = 700


def _contact():
    v = os.environ.get("FIELDKIT_CONTACT_EMAIL", "").strip()
    return v if "@" in v and not re.search(r"[\s()<>]", v) else ""


def _date(v):
    """YYYY-MM-DD from the shapes these APIs use, or ''."""
    v = str(v or "").strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}", v):
        return v[:10]
    m = re.match(r"^(\d{4})(\d{2})(\d{2})(T|$)", v)
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else ""


def _parts_date(parts):
    """Crossref date-parts [[2021, 3, 9]] -> 2021-03-09 (or 2021-03, or 2021)."""
    p = (parts or [[]])[0] or []
    return "-".join(f"{x:02d}" if i else str(x) for i, x in enumerate(p[:3]) if isinstance(x, int))


def _authors(names, cap=4):
    names = [n for n in names if n]
    return ", ".join(names[:cap] + (["et al."] if len(names) > cap else []))


def _strip_tags(s):
    return " ".join(re.sub(r"<[^>]*>", " ", s or "").split())


def _abstract(s):
    s = " ".join((s or "").split())
    return s if len(s) <= MAX_ABSTRACT else s[:MAX_ABSTRACT] + "... [cut: open the url for the rest]"


def _doi(v):
    v = (v or "").strip()
    v = re.sub(r"^(https?://(dx\.)?doi\.org/|doi:)", "", v, flags=re.I)
    return v.lower() if v.startswith("10.") else ""


def hit(**kw):
    h = {"title": "", "authors": "", "date": "", "year": "", "venue": "", "doi": "", "url": "", "free_pdf": "",
         "abstract": "", "backend": "", "peer_reviewed": None}
    h.update({k: v for k, v in kw.items() if v not in (None,)})
    h["abstract"] = _abstract(_strip_tags(h["abstract"]))
    h["doi"] = _doi(h["doi"])
    if not h["year"] and h["date"]:
        h["year"] = h["date"][:4]
    h["year"] = "" if str(h["year"]) in ("0", "None") else str(h["year"])
    return h


# -- the backends: (url for a query, hits from the answer) --------------------------------------------------------
def _openalex(q, n):
    c = _contact()
    return (f"https://api.openalex.org/works?search={quote_plus(q)}&per-page={n}"
            + (f"&mailto={quote_plus(c)}" if c else ""))


def _openalex_hits(d):
    out = []
    for r in d.get("results") or []:
        idx = r.get("abstract_inverted_index") or {}
        words = sorted((p, w) for w, ps in idx.items() for p in ps)
        loc = r.get("primary_location") or {}
        oa = r.get("best_oa_location") or {}
        out.append(hit(title=r.get("title") or "", date=_date(r.get("publication_date")),
                       year=r.get("publication_year") or "",
                       authors=_authors([(a.get("author") or {}).get("display_name") for a in r.get("authorships") or []]),
                       venue=((loc.get("source") or {}).get("display_name")) or "", doi=r.get("doi") or "",
                       url=loc.get("landing_page_url") or r.get("doi") or "",
                       free_pdf=oa.get("pdf_url") or oa.get("landing_page_url") or "",
                       abstract=" ".join(w for _, w in words), backend="OpenAlex"))
    return out


def _crossref(q, n):
    c = _contact()
    return f"https://api.crossref.org/works?query={quote_plus(q)}&rows={n}" + (f"&mailto={quote_plus(c)}" if c else "")


def _crossref_hits(d):
    out = []
    for r in (d.get("message") or {}).get("items") or []:
        out.append(hit(title=(r.get("title") or [""])[0], date=_parts_date((r.get("issued") or {}).get("date-parts")),
                       authors=_authors([" ".join(x for x in (a.get("given"), a.get("family")) if x)
                                         for a in r.get("author") or []]),
                       venue=(r.get("container-title") or [""])[0], doi=r.get("DOI") or "", url=r.get("URL") or "",
                       abstract=r.get("abstract") or "", backend="Crossref"))
    return out


EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"


def _epmc(q, n):
    return f"{EPMC}?query={quote_plus(q)}&format=json&pageSize={n}&resultType=core"


def _epmc_hits(d, preprints=False):
    out = []
    for r in (d.get("resultList") or {}).get("result") or []:
        if preprints:
            link = f"https://doi.org/{r['doi']}" if r.get("doi") else (
                f"https://europepmc.org/article/PPR/{r['id']}" if r.get("id") else "")
            venue = (r.get("publisher") or "") + " (preprint)" if r.get("publisher") else "preprint"
        else:
            link = (f"https://pmc.ncbi.nlm.nih.gov/articles/{r['pmcid']}/" if r.get("pmcid") else
                    f"https://pubmed.ncbi.nlm.nih.gov/{r['pmid']}/" if r.get("pmid") else
                    f"https://doi.org/{r['doi']}" if r.get("doi") else "")
            venue = r.get("journalTitle") or ""
        out.append(hit(title=r.get("title") or "", authors=r.get("authorString") or "",
                       date=_date(r.get("firstPublicationDate")), year=r.get("pubYear") or "", venue=venue,
                       doi=r.get("doi") or "", url=link, abstract=r.get("abstractText") or "",
                       backend="Europe PMC preprints" if preprints else "Europe PMC",
                       peer_reviewed=False if preprints else None))
    return out


def _unpaywall(q, n):
    doi, c = _doi(q), _contact()
    if not doi:
        raise ValueError(f"unpaywall needs a DOI (e.g. 10.1038/nature12373), got {q!r}")
    if not c:
        raise ValueError("unpaywall requires a contact address: set FIELDKIT_CONTACT_EMAIL")
    return f"https://api.unpaywall.org/v2/{quote(doi, safe='/')}?email={quote_plus(c)}"


def _unpaywall_hits(d):
    oa = d.get("best_oa_location") or {}
    note = ("OPEN ACCESS: a legal free full text exists "
            f"({oa.get('host_type') or 'unknown host'}, licence {oa.get('license') or 'unspecified'})." if d.get("is_oa")
            else "No open-access copy is known for this DOI.")
    return [hit(title=d.get("title") or "", date=_date(d.get("published_date")), year=d.get("year") or "",
                venue=d.get("journal_name") or "", doi=d.get("doi") or "",
                url=oa.get("url") or (f"https://doi.org/{d['doi']}" if d.get("doi") else ""),
                free_pdf=oa.get("url_for_pdf") or oa.get("url") or "", abstract=note, backend="Unpaywall")]


def _doaj(q, n):
    return f"https://doaj.org/api/search/articles/{quote(q, safe='')}?pageSize={n}"


def _doaj_hits(d):
    out = []
    for r in d.get("results") or []:
        b = r.get("bibjson") or {}
        link = next((l.get("url") for l in b.get("link") or [] if l.get("url")), "")
        doi = next((i.get("id") for i in b.get("identifier") or [] if str(i.get("type", "")).lower() == "doi"), "")
        out.append(hit(title=b.get("title") or "", authors=_authors([a.get("name") for a in b.get("author") or []]),
                       year=b.get("year") or "", venue=(b.get("journal") or {}).get("title") or "", doi=doi,
                       url=link, free_pdf=link, abstract=b.get("abstract") or "", backend="DOAJ (all open access)"))
    return out


def _gutendex(q, n):
    return f"https://gutendex.com/books?search={quote_plus(q)}"


def _gutendex_hits(d):
    out = []
    for r in d.get("results") or []:
        f = r.get("formats") or {}
        txt = f.get("text/plain; charset=utf-8") or f.get("text/plain") or f.get("text/html") or ""
        out.append(hit(title=r.get("title") or "", authors=_authors([a.get("name") for a in r.get("authors") or []], 99),
                       url=txt, free_pdf=txt, venue="Project Gutenberg",
                       abstract="Public domain. Full text is free to read and download.", backend="Gutenberg"))
    return out


def _openlib(q, n):
    return f"https://openlibrary.org/search.json?q={quote_plus(q)}&limit={n}"


def _openlib_hits(d):
    return [hit(title=x.get("title") or "", authors=", ".join(x.get("author_name") or []),
                year=x.get("first_publish_year") or "", venue="Open Library",
                url="https://openlibrary.org" + (x.get("key") or ""),
                free_pdf=f"https://archive.org/details/{x['ia'][0]}" if x.get("ia") else "", backend="Open Library")
            for x in d.get("docs") or []]


def _wikipedia(q, n):
    return f"https://en.wikipedia.org/w/api.php?action=query&list=search&srsearch={quote_plus(q)}&format=json&srlimit={n}"


def _wikipedia_hits(d):
    return [hit(title=r.get("title") or "", venue="Wikipedia", date=_date(r.get("timestamp")),
                url="https://en.wikipedia.org/wiki/" + quote(r.get("title", "").replace(" ", "_"), safe="_()'!*,:"),
                abstract=r.get("snippet") or "", backend="Wikipedia")
            for r in (d.get("query") or {}).get("search") or []]


def _gdelt(q, n):
    return f"https://api.gdeltproject.org/api/v2/doc/doc?query={quote_plus(q)}&mode=artlist&format=json&maxrecords={n}&sort=datedesc"


def _gdelt_hits(d):
    out = []
    for a in d.get("articles") or []:
        venue = a.get("domain") or ""
        if a.get("language") and a["language"].lower() != "english":
            venue += f" ({a['language']})"
        out.append(hit(title=a.get("title") or "", date=_date(a.get("seendate")), venue=venue, url=a.get("url") or "",
                       backend="GDELT"))
    return out


def _worldbank(q, n):
    return (f"https://search.worldbank.org/api/v3/wds?format=json&qterm={quote_plus(q)}&rows={n}"
            "&fl=display_title,pdfurl,txturl,docdt,docty,count")


def _worldbank_hits(d):
    out = []
    for key, doc in (d.get("documents") or {}).items():       # JSON object order = the API's relevance order
        if key == "facets" or not isinstance(doc, dict) or not doc.get("display_title"):
            continue
        out.append(hit(title=doc["display_title"], date=_date(doc.get("docdt")),
                       venue=("World Bank " + (doc.get("docty") or "")).strip(),
                       url=doc.get("pdfurl") or doc.get("txturl") or "", free_pdf=doc.get("pdfurl") or "",
                       backend="World Bank"))
    return out


def _hdx(q, n):
    return f"https://data.humdata.org/api/3/action/package_search?q={quote_plus(q)}&rows={n}"


def _hdx_hits(d):
    return [hit(title=r.get("title") or "", date=_date(r.get("last_modified") or r.get("metadata_modified")),
                venue=(r.get("organization") or {}).get("title") or "HDX",
                url="https://data.humdata.org/dataset/" + (r.get("name") or ""), backend="HDX")
            for r in (d.get("result") or {}).get("results") or []]


def _sec(q, n):
    if not _contact():
        raise ValueError("SEC EDGAR requires a contact address in the User-Agent: set FIELDKIT_CONTACT_EMAIL")
    return f"https://efts.sec.gov/LATEST/search-index?q={quote_plus(q)}"


def _sec_hits(d):
    out = []
    for h in (d.get("hits") or {}).get("hits") or []:
        s = h.get("_source") or {}
        acc, _, fname = (h.get("_id") or "").partition(":")
        link = (f"https://www.sec.gov/Archives/edgar/data/{(s.get('ciks') or ['0'])[0].lstrip('0')}/"
                f"{acc.replace('-', '')}/{fname}") if fname and s.get("ciks") else ""
        out.append(hit(title=" — ".join(x for x in ((s.get("display_names") or [""])[0], s.get("file_type")) if x),
                       date=_date(s.get("file_date")), venue="SEC EDGAR", url=link, backend="SEC EDGAR"))
    return out


def _searxng_base():
    return (os.environ.get("FIELDKIT_SEARXNG") or os.environ.get("SEARXNG_URL") or "").rstrip("/")


def _searxng(q, n):
    base = _searxng_base()
    if not base:
        raise ValueError("no SearXNG configured: set FIELDKIT_SEARXNG=http://your-instance:8888")
    return f"{base}/search?q={quote_plus(q)}&format=json"


def _searxng_hits(d):
    return [hit(title=r.get("title") or "", url=r.get("url") or "", abstract=r.get("content") or "",
                date=_date(r.get("publishedDate")), venue=", ".join(r.get("engines") or []), backend="SearXNG")
            for r in d.get("results") or []]


BACKENDS = {
    "openalex": (_openalex, _openalex_hits, "scholarly works, any field (OpenAlex)"),
    "crossref": (_crossref, _crossref_hits, "DOIs and their metadata (Crossref)"),
    "europepmc": (_epmc, _epmc_hits, "biomedical and life sciences, peer reviewed (Europe PMC)"),
    "preprints": (lambda q, n: _epmc(f"(SRC:PPR) AND ({q})", n), lambda d: _epmc_hits(d, True),
                  "preprints, NOT peer reviewed: bioRxiv, medRxiv and the rest (Europe PMC)"),
    "unpaywall": (_unpaywall, _unpaywall_hits, "a DOI -> is there a legal free copy? (needs FIELDKIT_CONTACT_EMAIL)"),
    "doaj": (_doaj, _doaj_hits, "open-access journal articles only (DOAJ)"),
    "gutenberg": (_gutendex, _gutendex_hits, "public-domain books, full text (Project Gutenberg)"),
    "openlibrary": (_openlib, _openlib_hits, "books (Open Library)"),
    "wikipedia": (_wikipedia, _wikipedia_hits, "encyclopedia articles (English Wikipedia)"),
    "gdelt": (_gdelt, _gdelt_hits, "news coverage worldwide, newest first (GDELT)"),
    "worldbank": (_worldbank, _worldbank_hits, "World Bank documents and reports"),
    "hdx": (_hdx, _hdx_hits, "humanitarian datasets: UN agencies, IOM, UNHCR, WFP (HDX)"),
    "sec": (_sec, _sec_hits, "US company filings (SEC EDGAR full text; needs FIELDKIT_CONTACT_EMAIL)"),
    "searxng": (_searxng, _searxng_hits, "the open web, through your own SearXNG (FIELDKIT_SEARXNG)"),
}


def search(source, query, limit=10, fetcher=None, run=None, get=None):
    """-> {"source", "query", "hits": [...], "searched_at", "url"}. get(url, allow_private) -> (status, body) is the
    transport (a test passes its own); the default goes through fetch.py's guarded connection."""
    if source not in BACKENDS:
        raise ValueError(f"unknown source {source!r}: {', '.join(BACKENDS)}")
    build, parse, _ = BACKENDS[source]
    url = build(query, limit)
    private_ok = source == "searxng"
    if get is None:
        get = _getter(fetcher)
    status, body, sha = get(url, private_ok, run)
    if not 200 <= status < 300:
        raise RuntimeError(f"{source} answered HTTP {status}")
    try:
        data = json.loads(body.decode("utf-8", "replace"))
    except json.JSONDecodeError as e:
        raise RuntimeError(f"{source} did not answer JSON: {e}") from None
    hits = parse(data)[:limit]
    return {"source": source, "query": query, "url": url, "searched_at":
            datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "count": len(hits), "hits": hits,
            "raw_sha256": sha}


def _getter(fetcher):
    def get(url, private_ok, run):
        f = fetcher or fetchmod.Fetcher(_store_or_none(), respect_robots=False)
        f.respect_robots = False
        allow = f.allow_private
        if private_ok:
            f.allow_private = True
        try:
            f._pace(urlsplit(url).netloc.lower())
            ua = f.ua
            if url.startswith("https://efts.sec.gov/") and _contact():
                ua = f"{fetchmod.user_agent().split(' (')[0]} ({_contact()})"
            tries = 0
            while True:
                status, hdrs, body, _ = fetchmod._one(url, ua, f.timeout, f.allow_private, f.max_bytes, f.connect)
                if status in fetchmod.RETRY_STATUS and tries < 3:
                    wait = fetchmod._retry_after(hdrs.get("retry-after"))
                    f.sleep(wait if wait is not None else 2 ** (tries + 1))
                    tries += 1
                    continue
                break
        finally:
            f.allow_private = allow
        sha = ""
        if run:
            sha = f.store.put(body)
            f.store.record({"kind": "search", "url": url, "url_norm": url, "final_url": url, "final_norm": url,
                            "status": status, "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "sha256": sha, "bytes": len(body), "content_type": hdrs.get("content-type", ""),
                            "has_text": False, "run": run})
        return status, body, sha
    return get


def _store_or_none():
    from .store import Store
    return Store()
