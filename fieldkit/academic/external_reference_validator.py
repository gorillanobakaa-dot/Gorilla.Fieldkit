"""
EXTERNAL REFERENCE VALIDATOR - Pre-flight web check for academic references.

Proves that every cited source actually exists, by resolving whatever
identifier the reference carries. A fabricated reference is the most
detectable fault in a bibliography, so this runs before submission.

Fourteen identifier types are covered, via reference_identifiers.py:

  DOI (incl. ACM DOIs and DataCite DOIs), ISSN, eISSN, ISBN, PMID, PMCID,
  arXiv ID, ORCID, Handle, ADS Bibcode, Scopus EID, Web of Science accession
  number, IEEE document number - plus plain URLs.

Each result carries the tier it was established at, because "this does not
exist" and "I could not check this" are different findings:

  TIER A  resolved against a free public register (authoritative)
  TIER B  check digit verified and catalogue corroborated (ISBN)
  TIER C  format checked only; resolution needs a paid subscription

A check digit is verified locally for ISBN, ISSN and ORCID, so an invented
number is caught with no network at all.

Changes from the first version:
  * It resolved only DOI, PMID and bare URLs. Eleven other identifier types
    were ignored entirely, so a reference carrying an ISBN or an arXiv ID
    passed unchecked.
  * DOI existence is now established at doi.org, which is registry agnostic.
    Checking Crossref alone branded every DataCite DOI - datasets, theses,
    preprints - as a possible fabrication.
  * SSL certificate verification is no longer switched off. The old version
    set CERT_NONE process-wide.
  * A fake Crossref contact address (a personal example address) has been removed.
    Set HARNESS_CONTACT_EMAIL, or pass --contact, for their polite pool.
  * 'No internet' is distinguished from 'dead link'. Previously every
    reference failed when offline, which would block the whole pipeline.
  * The title a register returns is compared against the title in the entry.
    A real identifier pasted onto the wrong reference is the characteristic
    signature of a hallucinated citation; the old version passed it as valid.
  * References carrying no identifier at all are counted and listed, because
    that is where an invented source hides.

Usage:
    python external_reference_validator.py <markdown_file_path>
    python external_reference_validator.py <file> --contact you@example.com
    python external_reference_validator.py <file> --offline   (check digits only)
    python external_reference_validator.py <file> --list      (extract only)
"""
import os
import re
import sys
import ssl
import json
import socket
import argparse
import urllib.error
import urllib.parse
import urllib.request
from difflib import SequenceMatcher

HARNESS_DIR = os.path.dirname(os.path.abspath(__file__))
if HARNESS_DIR not in sys.path:
    sys.path.insert(0, HARNESS_DIR)

from .reference_identifiers import (
    extract_identifiers, check_identifier, SUPPORTED_KINDS, TIER_LABELS,
    SSL_CONTEXT, _open,
)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from .reference_auditor import REF_HEADING                 # every language's heading

URL_RE = re.compile(r"(https?://[^\s,;\"'<>()\[\]]+)")

BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

BOT_BLOCKING_CODES = (401, 403, 405, 406, 429, 503, 999)
# Measured against genuine swaps (0.09-0.37) and legitimate title variants
# such as a dropped subtitle or "&" for "and" (0.61-0.69). The band between
# is reported for review rather than failed, so a harmless variant in wording
# never blocks a submission.
TITLE_MISMATCH_BELOW = 0.45
TITLE_AGREE_FROM = 0.60

# Identifier types whose returned metadata is a title worth comparing.
TITLE_BEARING = ("DOI", "PMID", "arXiv ID", "ISBN")


def internet_available(host="doi.org", port=443):
    """True if the network is reachable, so offline runs are not read as errors."""
    try:
        with socket.create_connection((host, port), timeout=5):
            return True
    except OSError:
        return False


# --------------------------------------------------------------------------
# Extraction
# --------------------------------------------------------------------------

def extract_references_text(text):
    m = re.search(r"(?:^|\n)[ \t]*#*[ \t]*" + REF_HEADING + r"[ \t]*:?[ \t]*\n(.*)",
                  text, flags=re.IGNORECASE | re.DOTALL)
    return m.group(1) if m else ""


def split_entries(ref_text):
    """One string per reference entry, folding hanging-indent continuations."""
    entries = []
    for raw_line in ref_text.splitlines():
        if not raw_line.strip():
            continue
        stripped = re.sub(r"[*_`]", "", raw_line).strip()
        indented = bool(re.match(r"^[ \t]{2,}", raw_line))
        if indented and entries:
            entries[-1] = entries[-1] + " " + stripped
        else:
            entries.append(stripped)
    return entries


def build_records(ref_text):
    """Per-entry identifiers and URLs, so a failure traces to its reference."""
    records = []
    for entry in split_entries(ref_text):
        identifiers = extract_identifiers(entry)
        claimed = {i["value"] for i in identifiers}
        urls = []
        for u in URL_RE.findall(entry):
            u = u.rstrip(".,;:)]}'\"")
            # Skip URLs that are just a wrapper around an identifier we
            # already resolved (doi.org/..., orcid.org/..., hdl.handle.net/...).
            if any(c.lower() in u.lower() for c in claimed):
                continue
            if any(h in u.lower() for h in ("doi.org", "orcid.org",
                                            "hdl.handle.net", "arxiv.org/abs")):
                continue
            urls.append(u)
        records.append({
            "entry": entry,
            "identifiers": identifiers,
            "urls": list(dict.fromkeys(urls)),
        })
    return records


def _entry_title_guess(entry):
    """In a Harvard entry the title follows the year bracket."""
    m = re.search(r"\((?:\d{4}[a-z]?|n\.d\.)\)\s*(.+?)(?:\.\s|\.$|$)", entry)
    return m.group(1).strip() if m else ""


def compare_titles(entry, remote_title):
    """Compare the entry's title against the register's.

    Returns (verdict, ratio) where verdict is one of:
      "agree"    - the same work
      "review"   - too different to confirm, too close to call fabricated
      "mismatch" - a different work; the signature of an invented citation
      "nobasis"  - no title to compare on either side
    """
    local = _entry_title_guess(entry)
    if not local or not remote_title or remote_title.startswith("("):
        return "nobasis", 0.0
    a = " ".join(re.sub(r"[^a-z0-9 ]", " ", local.lower()).split())
    b = " ".join(re.sub(r"[^a-z0-9 ]", " ", remote_title.lower()).split())
    if not a or not b:
        return "nobasis", 0.0
    # A short local title contained in the longer official one still agrees.
    if a in b or b in a:
        return "agree", 1.0
    ratio = SequenceMatcher(None, a, b).ratio()
    if ratio >= TITLE_AGREE_FROM:
        return "agree", ratio
    if ratio < TITLE_MISMATCH_BELOW:
        return "mismatch", ratio
    return "review", ratio


# --------------------------------------------------------------------------
# URL checking
# --------------------------------------------------------------------------

def check_url(url):
    for method in ("HEAD", "GET"):
        try:
            with _open(url, {"User-Agent": BROWSER_UA}, method=method) as r:
                return "ok", "reachable (HTTP %d)" % r.status
        except urllib.error.HTTPError as e:
            if e.code in BOT_BLOCKING_CODES:
                return "unverifiable", ("host blocks automated checks "
                                        "(HTTP %d) - open it by hand" % e.code)
            if e.code == 404:
                return "fail", "dead link (HTTP 404)"
            if method == "GET":
                return "fail", "dead link (HTTP %d)" % e.code
        except ssl.SSLCertVerificationError as e:
            return "fail", "certificate could not be verified (%s)" % e.reason
        except (urllib.error.URLError, socket.timeout, TimeoutError) as e:
            return "unverifiable", "could not connect (%s)" % e
        except Exception as e:
            return "unverifiable", "unexpected error (%s)" % e
    return "unverifiable", "no response"


# --------------------------------------------------------------------------
# Driver
# --------------------------------------------------------------------------

def run_validation(file_path, contact_email=None, offline=False, verbose=True,
                   list_only=False):
    """Validate every external identifier in a report's reference list.

    Returns True when nothing failed. Unverifiable items do not fail the run:
    being offline, or blocked by a publisher, is not the same as citing a
    source that does not exist.
    """
    contact_email = contact_email or os.environ.get("HARNESS_CONTACT_EMAIL")

    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError as e:
        print("[ERROR] Could not read file: %s" % e)
        return False

    if verbose:
        print("=== EXTERNAL PRE-FLIGHT REFERENCE CHECK ===")
        print("Scanning: %s" % os.path.basename(file_path))
        print("Identifier types recognised: %d\n" % len(SUPPORTED_KINDS))

    ref_text = extract_references_text(text)
    if not ref_text.strip():
        if verbose:
            print("[WARNING] No reference list found in this document.")
        return True

    records = build_records(ref_text)

    counts = {}
    for rec in records:
        for i in rec["identifiers"]:
            counts[i["kind"]] = counts.get(i["kind"], 0) + 1
    n_urls = sum(len(r["urls"]) for r in records)
    no_id = [r for r in records if not (r["identifiers"] or r["urls"])]

    if verbose:
        print("Reference entries: %d" % len(records))
        if counts:
            print("Identifiers found: %s"
                  % ", ".join("%d %s" % (v, k) for k, v in sorted(counts.items())))
        if n_urls:
            print("Plain URLs found:  %d" % n_urls)
        print("")

    if list_only:
        if verbose:
            for rec in records:
                if rec["identifiers"] or rec["urls"]:
                    print("  %s" % rec["entry"][:80])
                    for i in rec["identifiers"]:
                        print("     %-15s %s" % (i["kind"], i["value"]))
                    for u in rec["urls"]:
                        print("     %-15s %s" % ("URL", u))
            _report_entries_without_identifiers(no_id, len(records))
        return True

    online = internet_available()
    if not online and not offline:
        if verbose:
            print("[NOTE] No network connection. Falling back to check digits")
            print("       only. This is NOT a full pass - re-run when online.\n")
        offline = True

    failures, mismatches, unverifiable, verified = [], [], [], []

    for rec in records:
        label = rec["entry"][:70]

        for ident in rec["identifiers"]:
            status, tier, msg = check_identifier(
                ident["kind"], ident["value"], contact_email, offline)
            row = (label, "%s %s" % (ident["kind"], ident["value"]), msg, tier)

            if status == "ok":
                verdict, ratio = ("nobasis", 0.0)
                if ident["kind"] in TITLE_BEARING:
                    verdict, ratio = compare_titles(rec["entry"], msg)

                if verdict == "mismatch":
                    mismatches.append(row)
                    if verbose:
                        print("  [MISMATCH] %s %s  (similarity %.2f)"
                              % (ident["kind"], ident["value"], ratio))
                        print("     reference says: %s"
                              % _entry_title_guess(rec["entry"])[:70])
                        print("     register says:  %s" % msg[:70])
                elif verdict == "review":
                    unverifiable.append(
                        (label, "%s %s" % (ident["kind"], ident["value"]),
                         "title only %.0f%% similar to the register - check it "
                         "is the right work" % (ratio * 100), tier))
                    if verbose:
                        print("  [?  title] %-14s %-28s similarity %.2f"
                              % (ident["kind"], ident["value"], ratio))
                        print("     reference says: %s"
                              % _entry_title_guess(rec["entry"])[:70])
                        print("     register says:  %s" % msg[:70])
                else:
                    verified.append(row)
                    if verbose:
                        print("  [OK  tier %s] %-14s %-28s %s"
                              % (tier, ident["kind"], ident["value"], msg[:50]))
            elif status == "fail":
                failures.append(row)
                if verbose:
                    print("  [FAIL] %-14s %-28s %s"
                          % (ident["kind"], ident["value"], msg))
            else:
                unverifiable.append(row)
                if verbose:
                    print("  [?  tier %s] %-14s %-28s %s"
                          % (tier, ident["kind"], ident["value"], msg[:50]))

        if not offline:
            for url in rec["urls"]:
                status, msg = check_url(url)
                row = (label, url, msg, "A")
                if status == "ok":
                    verified.append(row)
                    if verbose:
                        print("  [OK  tier A] %-14s %s" % ("URL", url[:60]))
                elif status == "fail":
                    failures.append(row)
                    if verbose:
                        print("  [FAIL] %-14s %s -> %s" % ("URL", url[:50], msg))
                else:
                    unverifiable.append(row)
                    if verbose:
                        print("  [?  tier A] %-14s %s -> %s"
                              % ("URL", url[:45], msg[:40]))

    if verbose:
        _print_summary(verified, failures, mismatches, unverifiable, no_id,
                       len(records), contact_email, offline)

    return not (failures or mismatches)


def _report_entries_without_identifiers(no_id, total):
    if not no_id:
        return
    print("\n--- [ADVISORY] %d of %d ENTRIES CARRY NO IDENTIFIER AT ALL ---"
          % (len(no_id), total))
    print("  Nothing here can be checked automatically, and this is where an")
    print("  invented source hides. Older books and chapters legitimately have")
    print("  no DOI or ISBN, but confirm each exists in the library catalogue.")
    for rec in no_id[:10]:
        print("  -  %s" % rec["entry"][:90])
    if len(no_id) > 10:
        print("     ... and %d more" % (len(no_id) - 10))


def _print_summary(verified, failures, mismatches, unverifiable, no_id,
                   total, contact_email, offline):
    print("\n" + "=" * 70)
    print("  Verified:        %d" % len(verified))
    print("  Failed:          %d" % len(failures))
    print("  Title mismatch:  %d" % len(mismatches))
    print("  Unverifiable:    %d" % len(unverifiable))

    if failures:
        print("\n--- [FAIL] IDENTIFIERS THAT DO NOT RESOLVE ---")
        for label, ident, msg, tier in failures:
            print("  !! %s" % ident)
            print("     %s" % msg)
            print("     in: %s" % label)

    if mismatches:
        print("\n--- [FAIL] IDENTIFIER RESOLVES TO A DIFFERENT WORK ---")
        print("  This is the classic signature of an invented citation: a real")
        print("  identifier attached to a reference it does not belong to.")
        for label, ident, msg, tier in mismatches:
            print("  !! %s -> %s" % (ident, msg[:60]))
            print("     in: %s" % label)

    if unverifiable:
        by_tier = {}
        for row in unverifiable:
            by_tier.setdefault(row[3], []).append(row)
        print("\n--- [REVIEW] COULD NOT BE CONFIRMED EITHER WAY ---")
        for tier in sorted(by_tier):
            print("  Tier %s (%s):" % (tier, TIER_LABELS.get(tier, "")))
            for label, ident, msg, _ in by_tier[tier][:8]:
                print("    ?  %-34s %s" % (ident[:34], msg[:44]))
            if len(by_tier[tier]) > 8:
                print("       ... and %d more" % (len(by_tier[tier]) - 8))

    _report_entries_without_identifiers(no_id, total)

    if not contact_email:
        print("\n  Note: set HARNESS_CONTACT_EMAIL to a real address to use")
        print("  Crossref's polite pool and avoid throttling.")

    print("\n" + "=" * 70)
    if failures or mismatches:
        print("  PRE-FLIGHT CHECK FAILED: %d reference(s) need attention."
              % (len(failures) + len(mismatches)))
    elif offline:
        print("  CHECK DIGITS PASSED, but nothing was resolved online.")
        print("  This is NOT a full pass. Re-run with a connection.")
    else:
        print("  PRE-FLIGHT CHECK PASSED: every resolvable identifier checks out.")
    print("=" * 70)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Verify that cited identifiers actually exist")
    parser.add_argument("filepath", help="Path to the report markdown file")
    parser.add_argument("--contact", default=None,
                        help="Contact email for Crossref's polite pool")
    parser.add_argument("--offline", action="store_true",
                        help="Verify check digits only, without resolving")
    parser.add_argument("--list", dest="list_only", action="store_true",
                        help="List the identifiers found, without checking")
    args = parser.parse_args()

    ok = run_validation(args.filepath, args.contact, args.offline,
                        list_only=args.list_only)
    sys.exit(0 if ok else 1)
