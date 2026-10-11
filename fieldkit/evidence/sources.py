"""The source registry: 985 research sources, each with how it can be reached for free and how far it can be
trusted before anything it says is checked. Data, not prose: queried, checked, and probed.

    fieldkit evidence sources find [WORDS...] [--category C] [--access A] [--free] [--grade B] [--limit N]
    fieldkit evidence sources check                        the registry as data: fields, classes, grades, URLs
    fieldkit evidence sources probe [--category C] [--limit N] [--run RUN]   is each address still there? (online)

The registry is Gorilla OpenCode's docs/source-registry.json (its atlas is rendered from it by cmd/atlas). Fieldkit
carries a copy (fieldkit/evidence/data/source-registry.json); --registry or FIELDKIT_SOURCES points at another, for
example the OpenCode clone, and `fieldkit drift` tells when the copy is behind.
"""
import json
import os
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
ACCESS = ["api_keyless", "searxng_enabled", "searxng_available", "api_key", "fetch_pattern", "bulk_download",
          "paid_only", "unknown"]                      # best free reach first: the registry's own order
GRADES = "ABCDEF"


def default_path():
    return Path(os.environ.get("FIELDKIT_SOURCES") or HERE / "data" / "source-registry.json")


def load(path=None):
    data = json.loads(Path(path or default_path()).read_text(encoding="utf-8"))
    return data.get("sources", data) if isinstance(data, dict) else data


def check(sources):
    """-> (problems, notes), the same rules as Gorilla OpenCode's cmd/atlas."""
    problems, notes, seen = [], [], set()
    for i, s in enumerate(sources, 1):
        where = f"source {i} ({s.get('name', '')!r})"
        name = str(s.get("name", "")).strip()
        if not name:
            problems.append(f"{where}: no name")
        if name.lower() in seen:
            problems.append(f"{where}: the name is used twice")
        seen.add(name.lower())
        if s.get("access") not in ACCESS:
            problems.append(f"{where}: access {s.get('access')!r} is not one of the known classes")
        if s.get("reliability") not in tuple(GRADES):
            problems.append(f"{where}: reliability {s.get('reliability')!r} is not A-F")
        if not isinstance(s.get("free"), bool):
            problems.append(f"{where}: free is not true or false")
        if not str(s.get("category", "")).strip():
            problems.append(f"{where}: no category")
        url = s.get("url") or ""
        if not url:
            notes.append(f"{where}: no URL")
        elif not re.match(r"^https?://\S+$", url):
            problems.append(f"{where}: URL {url!r} is not http(s)")
        extra = set(s) - {"name", "access", "category", "free", "reliability", "url", "note"}
        if extra:
            notes.append(f"{where}: unknown field(s) {', '.join(sorted(extra))}")
    return problems, notes


def find(sources, words=(), category=None, access=None, free=None, grade=None, limit=20):
    """Sources matching every word (in the name, category or note), best first: grade, then how freely reachable."""
    words = [w.lower() for w in words]
    out = []
    for s in sources:
        hay = " ".join(str(s.get(k, "")) for k in ("name", "category", "note", "url")).lower()
        if any(w not in hay for w in words):
            continue
        if category and category.lower() not in str(s.get("category", "")).lower():
            continue
        if access and s.get("access") not in access:
            continue
        if free is not None and s.get("free") is not free:
            continue
        if grade and GRADES.find(s.get("reliability", "F")) > GRADES.find(grade):
            continue
        out.append(s)
    out.sort(key=lambda s: (GRADES.find(s.get("reliability", "F")),
                            ACCESS.index(s["access"]) if s.get("access") in ACCESS else 99, s.get("name", "")))
    return out[:limit] if limit else out


def probe(sources, fetcher, run=None):
    """Fetch each source's address (robots.txt obeyed, paced per host, kept in the evidence store). -> rows."""
    from .fetch import Refused
    import http.client
    rows = []
    for s in sources:
        url = s.get("url")
        if not url:
            rows.append({"name": s["name"], "url": None, "ok": False, "why": "no URL in the registry"})
            continue
        try:
            r = fetcher.fetch(url, run=run)
            ok = 200 <= r["status"] < 400 and not r.get("challenge")
            rows.append({"name": s["name"], "url": url, "ok": ok, "status": r["status"],
                         "why": "" if ok else ("a bot check" if r.get("challenge") else f"HTTP {r['status']}")})
        except Refused as e:
            rows.append({"name": s["name"], "url": url, "ok": None, "why": str(e)})       # not asked: not dead
        except (OSError, http.client.HTTPException) as e:
            rows.append({"name": s["name"], "url": url, "ok": False, "why": f"{type(e).__name__}: {e}"[:200]})
    return rows
