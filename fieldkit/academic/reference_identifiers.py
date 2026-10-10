"""
REFERENCE IDENTIFIERS - Extraction, checksum validation and live resolution of
every bibliographic identifier a reference list may carry.

Fourteen identifier types are recognised. They fall into three honesty tiers,
and the tier is always reported, because "I could not check this" and "this
does not exist" are different findings and must never be shown as the same
thing.

An ACM DOI is an ordinary Crossref DOI (prefix 10.1145), and an ACM
article ID is the part after the slash, so ACM references resolve through the
DOI path rather than needing a type of their own. Thirteen specifications
therefore cover all fourteen identifier types.

  TIER A - RESOLVED: a free public API confirms the record exists, and
           returns metadata that can be compared against the entry.
             DOI, ISSN, eISSN, PMID, PMCID, arXiv ID, ORCID, Handle,
             ADS Bibcode (only when ADS_API_TOKEN is set)
  TIER B - CORROBORATED: a check digit proves the identifier is well formed,
           and a catalogue lookup may corroborate it. A crowd-sourced
           catalogue cannot prove absence, so a miss is not a failure.
             ISBN
  TIER C - FORMAT ONLY: the identifier's shape is checked, but resolution
           needs a paid subscription. These must be confirmed by hand.
             Scopus EID, Web of Science accession number,
             IEEE document number, ADS Bibcode (without a token)

Check digits are verified locally for ISBN-10, ISBN-13, ISSN and ORCID, so an
invented number is caught even with no network at all.

Notes on the resolvers, learned by probing them:
  * DOI existence is checked at doi.org's Handle API, which is registry
    agnostic. Crossref alone is not enough: a DataCite DOI (datasets,
    theses, preprints) is absent from Crossref and would look fabricated.
    Metadata then comes from Crossref, falling back to DataCite.
  * arXiv answers HTTP 200 for a non-existent identifier. Existence must be
    read from <opensearch:totalResults>, not from the status code.
  * Open Library answers 200 for some junk records, so ISBN rests on the
    check digit first and the catalogue second.

Used by external_reference_validator.py. Importable on its own.
"""
import os
import re
import ssl
import json
import time
import socket
import urllib.error
import urllib.parse
import urllib.request

TIMEOUT = 12
RATE_LIMIT_SECONDS = 0.34
BOT_BLOCKING_CODES = (401, 403, 405, 406, 429, 503, 999)

_last_request = [0.0]


def _context():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


SSL_CONTEXT = _context()


def _open(url, headers=None, method="GET"):
    elapsed = time.time() - _last_request[0]
    if elapsed < RATE_LIMIT_SECONDS:
        time.sleep(RATE_LIMIT_SECONDS - elapsed)
    _last_request[0] = time.time()
    req = urllib.request.Request(url, headers=headers or {}, method=method)
    return urllib.request.urlopen(req, timeout=TIMEOUT, context=SSL_CONTEXT)


def _get_json(url, headers=None):
    h = {"User-Agent": "AcademicHarness/2.0", "Accept": "application/json"}
    h.update(headers or {})
    with _open(url, h) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _get_text(url, headers=None):
    h = {"User-Agent": "AcademicHarness/2.0"}
    h.update(headers or {})
    with _open(url, h) as r:
        return r.read().decode("utf-8", "replace")


# ==========================================================================
# Check digits
# ==========================================================================

def isbn10_valid(value):
    digits = re.sub(r"[^0-9Xx]", "", value)
    if len(digits) != 10:
        return False
    total = 0
    for i, ch in enumerate(digits):
        d = 10 if ch in "Xx" else int(ch)
        total += d * (10 - i)
    return total % 11 == 0


def isbn13_valid(value):
    digits = re.sub(r"[^0-9]", "", value)
    if len(digits) != 13:
        return False
    total = sum(int(d) * (1 if i % 2 == 0 else 3)
                for i, d in enumerate(digits))
    return total % 10 == 0


def isbn_valid(value):
    digits = re.sub(r"[^0-9Xx]", "", value)
    if len(digits) == 10:
        return isbn10_valid(digits)
    if len(digits) == 13:
        return isbn13_valid(digits)
    return False


def issn_valid(value):
    """ISSN check digit: weights 8..2 over the first seven digits, mod 11."""
    digits = re.sub(r"[^0-9Xx]", "", value)
    if len(digits) != 8:
        return False
    total = sum(int(d) * (8 - i) for i, d in enumerate(digits[:7]))
    remainder = total % 11
    expected = 0 if remainder == 0 else 11 - remainder
    check = digits[7]
    actual = 10 if check in "Xx" else int(check)
    return expected == actual


def orcid_valid(value):
    """ORCID check digit: ISO 7064 MOD 11-2 over the first 15 digits."""
    digits = re.sub(r"[^0-9Xx]", "", value)
    if len(digits) != 16:
        return False
    total = 0
    for ch in digits[:15]:
        total = (total + int(ch)) * 2
    remainder = total % 11
    result = (12 - remainder) % 11
    expected = "X" if result == 10 else str(result)
    return digits[15].upper() == expected


# ==========================================================================
# Resolvers. Each returns (status, message).
#   "ok"           - confirmed to exist
#   "fail"         - confirmed NOT to exist, or a check digit is wrong
#   "unverifiable" - could not be determined (offline, blocked, needs a key)
# ==========================================================================

def resolve_doi(value, contact_email=None):
    """Existence at doi.org (registry agnostic), metadata from Crossref/DataCite."""
    doi = re.sub(r"^(?:https?://)?(?:dx\.)?doi\.org/", "", value, flags=re.I)
    quoted = urllib.parse.quote(doi, safe="/.()-_")

    # 1. Does this DOI exist in ANY registry?
    try:
        data = _get_json("https://doi.org/api/handles/" + quoted)
        if data.get("responseCode") != 1:
            return "fail", "DOI is not registered - possible fabrication"
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return "fail", "DOI is not registered - possible fabrication"
        if e.code not in BOT_BLOCKING_CODES:
            return "unverifiable", "doi.org returned HTTP %d" % e.code
    except (urllib.error.URLError, socket.timeout, TimeoutError) as e:
        return "unverifiable", "could not reach doi.org (%s)" % e
    except Exception as e:
        return "unverifiable", "unexpected error (%s)" % e

    # 2. Metadata, for the title comparison.
    ua = "AcademicHarness/2.0"
    if contact_email:
        ua += " (mailto:%s)" % contact_email
    try:
        data = _get_json("https://api.crossref.org/works/" + quoted,
                         {"User-Agent": ua})
        titles = (data.get("message", {}).get("title") or [""])
        if titles[0]:
            return "ok", titles[0]
    except Exception:
        pass

    try:
        data = _get_json("https://api.datacite.org/dois/" + quoted)
        title = (data.get("data", {}).get("attributes", {})
                     .get("titles") or [{}])[0].get("title", "")
        if title:
            return "ok", title
    except Exception:
        pass

    return "ok", "(registered, but no title metadata available)"


def resolve_pmid(value):
    pmid = re.sub(r"[^0-9]", "", value)
    url = ("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
           "?db=pubmed&id=%s&retmode=json" % pmid)
    try:
        data = _get_json(url)
        record = data.get("result", {}).get(pmid)
        if not record or record.get("error"):
            return "fail", "PMID not found in PubMed - possible fabrication"
        return "ok", record.get("title", "(no title recorded)")
    except (urllib.error.URLError, socket.timeout, TimeoutError) as e:
        return "unverifiable", "could not reach PubMed (%s)" % e
    except Exception as e:
        return "unverifiable", "unexpected error (%s)" % e


def resolve_pmcid(value):
    pmcid = value.upper()
    if not pmcid.startswith("PMC"):
        pmcid = "PMC" + re.sub(r"[^0-9]", "", pmcid)
    url = ("https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/"
           "?ids=%s&format=json" % urllib.parse.quote(pmcid))
    try:
        data = _get_json(url)
        records = data.get("records") or []
        if not records or records[0].get("status") == "error":
            return "fail", "PMCID not found in PubMed Central"
        rec = records[0]
        detail = "PMID %s" % rec.get("pmid") if rec.get("pmid") else "found in PMC"
        return "ok", detail
    except (urllib.error.URLError, socket.timeout, TimeoutError) as e:
        return "unverifiable", "could not reach PubMed Central (%s)" % e
    except Exception as e:
        return "unverifiable", "unexpected error (%s)" % e


def resolve_arxiv(value):
    """arXiv answers 200 for a fake id, so read totalResults, not the status."""
    aid = re.sub(r"^arxiv:\s*", "", value, flags=re.I).strip()
    url = ("https://export.arxiv.org/api/query?id_list=%s&max_results=1"
           % urllib.parse.quote(aid))
    try:
        body = _get_text(url)
        total = re.search(r"<opensearch:totalResults[^>]*>(\d+)<", body)
        if total and total.group(1) == "0":
            return "fail", "arXiv ID does not exist - possible fabrication"
        title = re.search(r"<entry>.*?<title>(.*?)</title>", body, re.DOTALL)
        if title:
            return "ok", re.sub(r"\s+", " ", title.group(1)).strip()
        if total and total.group(1) != "0":
            return "ok", "(exists on arXiv)"
        return "unverifiable", "arXiv returned an unexpected response"
    except (urllib.error.URLError, socket.timeout, TimeoutError) as e:
        return "unverifiable", "could not reach arXiv (%s)" % e
    except Exception as e:
        return "unverifiable", "unexpected error (%s)" % e


def resolve_orcid(value):
    orcid = value.upper().strip()
    if not orcid_valid(orcid):
        return "fail", "ORCID check digit is wrong - the number is invalid"
    try:
        data = _get_json("https://pub.orcid.org/v3.0/%s/person"
                         % urllib.parse.quote(orcid))
        name = data.get("name") or {}
        given = (name.get("given-names") or {}).get("value", "")
        family = (name.get("family-name") or {}).get("value", "")
        who = (given + " " + family).strip()
        return "ok", who or "(registered, name withheld by the account holder)"
    except urllib.error.HTTPError as e:
        if e.code in (404, 409):
            return "fail", "ORCID is not registered"
        return "unverifiable", "ORCID returned HTTP %d" % e.code
    except (urllib.error.URLError, socket.timeout, TimeoutError) as e:
        return "unverifiable", "could not reach ORCID (%s)" % e
    except Exception as e:
        return "unverifiable", "unexpected error (%s)" % e


def resolve_handle(value):
    handle = value.strip()
    try:
        data = _get_json("https://hdl.handle.net/api/handles/"
                         + urllib.parse.quote(handle, safe="/."))
        if data.get("responseCode") == 1:
            for v in data.get("values", []):
                if v.get("type") == "URL":
                    return "ok", "resolves to %s" % v["data"]["value"][:80]
            return "ok", "(registered)"
        return "fail", "Handle is not registered"
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return "fail", "Handle is not registered"
        return "unverifiable", "Handle system returned HTTP %d" % e.code
    except (urllib.error.URLError, socket.timeout, TimeoutError) as e:
        return "unverifiable", "could not reach the Handle system (%s)" % e
    except Exception as e:
        return "unverifiable", "unexpected error (%s)" % e


def resolve_issn(value):
    issn = value.strip().upper()
    if not issn_valid(issn):
        return "fail", "ISSN check digit is wrong - the number is invalid"
    try:
        data = _get_json("https://api.crossref.org/journals/"
                         + urllib.parse.quote(issn))
        title = data.get("message", {}).get("title", "")
        return "ok", title or "(registered journal)"
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return ("unverifiable",
                    "check digit valid, but Crossref holds no journal record")
        return "unverifiable", "Crossref returned HTTP %d" % e.code
    except (urllib.error.URLError, socket.timeout, TimeoutError) as e:
        return "unverifiable", "could not reach Crossref (%s)" % e
    except Exception as e:
        return "unverifiable", "unexpected error (%s)" % e


def resolve_isbn(value):
    """Check digit is the hard gate; the catalogue only corroborates."""
    digits = re.sub(r"[^0-9Xx]", "", value)
    if not isbn_valid(digits):
        return "fail", "ISBN check digit is wrong - the number is invalid"

    try:
        data = _get_json("https://openlibrary.org/isbn/%s.json"
                         % urllib.parse.quote(digits))
        title = data.get("title", "")
        if title:
            return "ok", title
    except urllib.error.HTTPError:
        pass
    except Exception:
        pass

    try:
        data = _get_json("https://www.googleapis.com/books/v1/volumes?q=isbn:%s"
                         % urllib.parse.quote(digits))
        items = data.get("items") or []
        if items:
            return "ok", items[0].get("volumeInfo", {}).get("title", "(found)")
    except Exception:
        pass

    return ("unverifiable",
            "check digit valid, but no catalogue record found - "
            "confirm in the library catalogue")


def resolve_ads_bibcode(value):
    """NASA ADS needs a token; without one the shape is all that can be checked."""
    token = os.environ.get("ADS_API_TOKEN")
    if not token:
        return ("unverifiable",
                "ADS needs an API token (set ADS_API_TOKEN) - check by hand "
                "at ui.adsabs.harvard.edu")
    url = ("https://api.adsabs.harvard.edu/v1/search/query?q=bibcode:%s&fl=title"
           % urllib.parse.quote(value))
    try:
        data = _get_json(url, {"Authorization": "Bearer " + token})
        docs = data.get("response", {}).get("docs") or []
        if not docs:
            return "fail", "bibcode not found in ADS"
        return "ok", (docs[0].get("title") or ["(no title)"])[0]
    except urllib.error.HTTPError as e:
        return "unverifiable", "ADS returned HTTP %d" % e.code
    except Exception as e:
        return "unverifiable", "unexpected error (%s)" % e


def resolve_ieee(value):
    """IEEE Xplore has no free API; probe the document page instead."""
    number = re.sub(r"[^0-9]", "", value)
    url = "https://ieeexplore.ieee.org/document/%s" % number
    try:
        with _open(url, {"User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36")},
                method="HEAD") as r:
            if r.status in (200, 202):
                return "unverifiable", ("IEEE blocks automated checks - "
                                        "open %s by hand" % url)
            return "unverifiable", "IEEE returned HTTP %d" % r.status
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return "fail", "IEEE document number not found"
        return "unverifiable", ("IEEE blocks automated checks (HTTP %d) - "
                                "open %s by hand" % (e.code, url))
    except Exception:
        return "unverifiable", "could not reach IEEE - open %s by hand" % url


def resolve_scopus(value):
    return ("unverifiable",
            "Scopus needs an institutional subscription - confirm the EID "
            "in Scopus by hand")


def resolve_wos(value):
    return ("unverifiable",
            "Web of Science needs an institutional subscription - confirm "
            "the accession number by hand")


# ==========================================================================
# Identifier specifications
#
# Order matters: patterns are applied in turn and each match is masked out of
# the working text, so a DOI is not later re-read as a Handle, and a PMCID is
# not re-read as a PMID.
# ==========================================================================

IDENTIFIER_SPECS = [
    {
        # Also covers ACM DOIs (10.1145/...) and their article IDs, and
        # DataCite DOIs for datasets, theses and preprints.
        "kind": "DOI",
        "tier": "A",
        "pattern": r"(?:doi:\s*|https?://(?:dx\.)?doi\.org/)?(10\.\d{4,9}/[^\s,;\"'<>()\[\]]+)",
        "group": 1,
        "checksum": None,
        "resolver": resolve_doi,
    },
    {
        "kind": "PMCID",
        "tier": "A",
        "pattern": r"\b(PMC\d{4,9})\b",
        "group": 1,
        "checksum": None,
        "resolver": resolve_pmcid,
    },
    {
        "kind": "PMID",
        "tier": "A",
        "pattern": r"\bPMID:?\s*(\d{4,9})\b",
        "group": 1,
        "checksum": None,
        "resolver": resolve_pmid,
    },
    {
        "kind": "arXiv ID",
        "tier": "A",
        "pattern": (r"\b(?:arXiv:\s*)?((?:\d{4}\.\d{4,5}(?:v\d+)?)"
                    r"|(?:[a-z\-]+(?:\.[A-Z]{2})?/\d{7}(?:v\d+)?))\b"),
        "group": 1,
        "requires_label": r"arxiv",
        "checksum": None,
        "resolver": resolve_arxiv,
    },
    {
        "kind": "ORCID",
        "tier": "A",
        "pattern": (r"(?:https?://orcid\.org/)?\b(\d{4}-\d{4}-\d{4}-\d{3}[\dX])\b"),
        "group": 1,
        "checksum": orcid_valid,
        "resolver": resolve_orcid,
    },
    {
        "kind": "ISBN",
        "tier": "B",
        "pattern": (r"\bISBN(?:-1[03])?:?\s*"
                    r"((?:97[89][\s\-]?)?[\d][\d\s\-]{8,14}[\dXx])\b"),
        "group": 1,
        "checksum": isbn_valid,
        "resolver": resolve_isbn,
    },
    {
        "kind": "eISSN",
        "tier": "A",
        "pattern": r"\b(?:e-?ISSN|online ISSN):?\s*(\d{4}-\d{3}[\dXx])\b",
        "group": 1,
        "checksum": issn_valid,
        "resolver": resolve_issn,
    },
    {
        "kind": "ISSN",
        "tier": "A",
        "pattern": r"\bISSN:?\s*(\d{4}-\d{3}[\dXx])\b",
        "group": 1,
        "checksum": issn_valid,
        "resolver": resolve_issn,
    },
    {
        "kind": "Scopus EID",
        "tier": "C",
        "pattern": r"\b(2-s2\.0-\d{10,12})\b",
        "group": 1,
        "checksum": None,
        "resolver": resolve_scopus,
    },
    {
        "kind": "WoS accession",
        "tier": "C",
        "pattern": r"\b(WOS:\s?\d{15})\b",
        "group": 1,
        "checksum": None,
        "resolver": resolve_wos,
    },
    {
        "kind": "ADS bibcode",
        "tier": "C",
        "pattern": r"\b(\d{4}[A-Za-z][A-Za-z0-9&.]{13}[A-Z])\b",
        "group": 1,
        "checksum": None,
        "resolver": resolve_ads_bibcode,
    },
    {
        "kind": "IEEE document",
        "tier": "C",
        "pattern": (r"(?:IEEE\s+(?:document|doc\.?|article)\s*(?:no\.?|number)?:?\s*"
                    r"|ieeexplore\.ieee\.org/document/)(\d{6,9})\b"),
        "group": 1,
        "checksum": None,
        "resolver": resolve_ieee,
    },
    {
        "kind": "Handle",
        "tier": "A",
        # Handles look like DOIs; DOIs are masked out above, and a '10.'
        # prefix is excluded here so only true non-DOI handles match.
        "pattern": (r"(?:hdl:\s*|https?://hdl\.handle\.net/)"
                    r"((?!10\.)\d+(?:\.\d+)*/[^\s,;\"'<>()\[\]]+)"),
        "group": 1,
        "checksum": None,
        "resolver": resolve_handle,
    },
]

TIER_LABELS = {
    "A": "resolved against a public register",
    "B": "check digit verified, catalogue corroborated",
    "C": "format checked only - needs a subscription, confirm by hand",
}


def extract_identifiers(text):
    """Find every identifier in a block of text.

    Returns a list of {kind, value, tier}. Matches are masked as they are
    found, so no character is claimed by two identifier types.
    """
    working = text
    found = []

    for spec in IDENTIFIER_SPECS:
        # Some shapes are too generic to trust without a nearby label.
        if spec.get("requires_label"):
            if not re.search(spec["requires_label"], working, flags=re.I):
                continue

        for m in re.finditer(spec["pattern"], working, flags=re.IGNORECASE):
            value = m.group(spec["group"]).strip().rstrip(".,;:)]}")
            if not value:
                continue
            found.append({
                "kind": spec["kind"],
                "value": re.sub(r"\s+", "", value)
                         if spec["kind"] in ("ISBN", "ISSN", "eISSN") else value,
                "tier": spec["tier"],
            })
        # Mask everything this spec matched.
        working = re.sub(spec["pattern"],
                         lambda m: " " * len(m.group(0)),
                         working, flags=re.IGNORECASE)

    # Deduplicate, keeping the first occurrence of each (kind, value).
    seen, unique = set(), []
    for item in found:
        key = (item["kind"], item["value"].upper())
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


CHECKSUMS = {
    "ISBN": isbn_valid,
    "ISSN": issn_valid,
    "eISSN": issn_valid,
    "ORCID": orcid_valid,
}

RESOLVERS = {spec["kind"]: spec["resolver"] for spec in IDENTIFIER_SPECS}


def validate_checksum(kind, value):
    """(ok, message). ok is None when the type carries no check digit."""
    fn = CHECKSUMS.get(kind)
    if fn is None:
        return None, ""
    if fn(value):
        return True, "check digit correct"
    return False, "check digit WRONG - this %s cannot be genuine" % kind


def check_identifier(kind, value, contact_email=None, offline=False):
    """Validate one identifier. Returns (status, tier, message)."""
    spec_tier = next((s["tier"] for s in IDENTIFIER_SPECS
                      if s["kind"] == kind), "C")

    ok, msg = validate_checksum(kind, value)
    if ok is False:
        return "fail", spec_tier, msg

    if offline:
        if ok is True:
            return "unverifiable", spec_tier, msg + " (offline: not resolved)"
        return "unverifiable", spec_tier, "offline: not resolved"

    resolver = RESOLVERS.get(kind)
    if resolver is None:
        return "unverifiable", spec_tier, "no resolver for this type"

    if kind == "DOI":
        status, message = resolver(value, contact_email)
    else:
        status, message = resolver(value)
    return status, spec_tier, message


SUPPORTED_KINDS = [spec["kind"] for spec in IDENTIFIER_SPECS]


if __name__ == "__main__":
    import sys
    print("Identifier types recognised (%d):" % len(SUPPORTED_KINDS))
    for spec in IDENTIFIER_SPECS:
        print("  %-16s tier %s  - %s"
              % (spec["kind"], spec["tier"], TIER_LABELS[spec["tier"]]))

    if len(sys.argv) > 1:
        text = open(sys.argv[1], encoding="utf-8", errors="replace").read()
        print("\nFound in %s:" % sys.argv[1])
        for item in extract_identifiers(text):
            print("  %-16s %s" % (item["kind"], item["value"]))
