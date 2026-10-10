"""
PLAGIARISM PRE-CHECK - Finds runs of consecutive words shared between the
report and the source materials.

Enforces the 3-Word Rule from the harness config: no more than three
consecutive non-technical words may match a source, so any run of four or
more is reported.

What changed from the first version, and why it matters:
  * It now reads EVERY format the harness can read - PDF, PowerPoint, Excel
    and scanned images via OCR - not just .txt/.md/.docx. Previously the
    readings that actually get set (PDFs and lecture slides) were skipped
    in silence, so a clean result meant almost nothing.
  * Each match is attributed to the source file it came from, so a hit can
    be checked against the original.
  * The report's own reference list is excluded. Bibliographies legitimately
    match their sources word for word and were generating pure noise.
  * Matches falling inside quotation marks are listed separately: a properly
    quoted and cited passage is not plagiarism, but its citation still needs
    checking.
  * Matching is indexed rather than rescanning the whole source for every
    extension step, which is what made the old version crawl on real inputs.

Usage:
    python plagiarism_precheck.py <report_file> <source_materials_folder>
    python plagiarism_precheck.py <report> <sources> --threshold 5
"""
import os
import re
import sys
import argparse

HARNESS_DIR = os.path.dirname(os.path.abspath(__file__))
if HARNESS_DIR not in sys.path:
    sys.path.insert(0, HARNESS_DIR)

from .read_sources import READERS, read_file, is_error_text, _skip, _role

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from .reference_auditor import REF_HEADING                 # every language's heading

# A run of this many words or more is reported (the 3-Word Rule: more than 3).
DEFAULT_THRESHOLD = 4

# Runs are triaged into bands. Short runs are usually unavoidable subject
# collocations; long runs are not.
SEVERITY_BANDS = ((12, "HIGH"), (7, "MEDIUM"), (0, "LOW"))

QUOTE_PAIRS = ((u"“", u"”"), (u'"', u'"'), (u"‘", u"’"), (u"„", u"“"), (u"„", u"”"), (u"«", u"»"),
               (u"‚", u"‘"))


# --------------------------------------------------------------------------
# Text preparation
# --------------------------------------------------------------------------

def strip_reference_list(text):
    """Remove the report's reference list; it matches sources by design."""
    m = re.search(r"(?:^|\n)[ \t]*#*[ \t]*" + REF_HEADING + r"[ \t]*:?[ \t]*\n",
                  text, flags=re.IGNORECASE)
    return text[:m.start()] if m else text


def tokenise(text):
    """Lowercase word tokens with their character offsets."""
    return [(m.group(0).lower(), m.start(), m.end())
            for m in re.finditer(r"[^\W\d_][\w'’\-]*", text)]


def quoted_spans(text):
    """Character ranges inside quotation marks, for classifying matches."""
    spans = []
    for open_q, close_q in QUOTE_PAIRS:
        if open_q == close_q:
            positions = [m.start() for m in re.finditer(re.escape(open_q), text)]
            for i in range(0, len(positions) - 1, 2):
                spans.append((positions[i], positions[i + 1]))
        else:
            pattern = re.escape(open_q) + r"(.{0,2000}?)" + re.escape(close_q)
            for m in re.finditer(pattern, text, flags=re.DOTALL):
                spans.append((m.start(), m.end()))
    return spans


def _in_quotes(start, end, spans):
    return any(s <= start and end <= e for s, e in spans)


def _severity(length):
    for minimum, label in SEVERITY_BANDS:
        if length >= minimum:
            return label
    return "LOW"


# --------------------------------------------------------------------------
# Matching
# --------------------------------------------------------------------------

def _build_index(words, k):
    """Map each k-gram to the positions where it starts."""
    index = {}
    for i in range(len(words) - k + 1):
        index.setdefault(tuple(words[i:i + k]), []).append(i)
    return index


def find_matches_against(report_tokens, source_words, threshold, max_run=200):
    """Longest consecutive runs shared with one source.

    Indexes the source once, then extends each candidate directly instead of
    rescanning the source for every extra word.
    """
    report_words = [t[0] for t in report_tokens]
    if len(source_words) < threshold or len(report_words) < threshold:
        return []

    index = _build_index(source_words, threshold)
    matches = []

    i = 0
    n = len(report_words)
    while i <= n - threshold:
        gram = tuple(report_words[i:i + threshold])
        starts = index.get(gram)
        if not starts:
            i += 1
            continue

        best = threshold
        for j in starts:
            length = threshold
            while (i + length < n
                   and j + length < len(source_words)
                   and length < max_run
                   and report_words[i + length] == source_words[j + length]):
                length += 1
            if length > best:
                best = length

        matches.append({
            "position": i,
            "length": best,
            "tokens": report_words[i:i + best],
            "char_start": report_tokens[i][1],
            "char_end": report_tokens[i + best - 1][2],
        })
        # Step past this run; overlapping sub-runs add nothing.
        i += best

    return matches


def _select_non_overlapping(matches):
    """Keep the longest run wherever runs overlap."""
    chosen = []
    for m in sorted(matches, key=lambda x: (-x["length"], x["position"])):
        start, end = m["position"], m["position"] + m["length"]
        if any(start < c["position"] + c["length"] and c["position"] < end
               for c in chosen):
            continue
        chosen.append(m)
    return sorted(chosen, key=lambda x: x["position"])


# --------------------------------------------------------------------------
# Driver
# --------------------------------------------------------------------------

def load_sources(sources_folder, verbose=True):
    """Read every supported source file.

    Returns (per_file_words, problems, roles). A file's role - 'source',
    'brief' or 'notes' - comes from the folder it sits in.
    """
    per_file = {}
    problems = []
    roles = {}

    for root, dirs, files in os.walk(sources_folder):
        for name in sorted(files):
            if _skip(name):
                continue
            ext = os.path.splitext(name)[1].lower()
            fpath = os.path.join(root, name)
            fname = os.path.relpath(fpath, sources_folder).replace(os.sep, "/")

            if ext not in READERS:
                problems.append((fname, "unsupported format %s" % ext))
                continue

            # The extracted library's copy when it is up to date, so scanned
            # pages are not OCR'd again on every run; otherwise read afresh.
            # Page and slide markers are left out of the comparison.
            text = None
            try:
                from .extract_library import cached_text
                text = cached_text(fpath)
            except Exception:
                text = None
            if text is None:
                text = read_file(fpath, markers=False)
            if is_error_text(text):
                problems.append((fname, text))
                if verbose:
                    print("  !! %s -> %s" % (fname, text))
                continue

            words = [t[0] for t in tokenise(text)]
            if not words:
                problems.append((fname, "no text extracted"))
                continue

            per_file[fname] = words
            roles[fname] = _role(fpath)
            if verbose:
                print("  Source loaded: %s (%s words)%s"
                      % (fname, format(len(words), ","),
                         "" if roles[fname] == "source"
                         else "  [%s]" % roles[fname]))

    return per_file, problems, roles


def run_precheck(report_path, sources_folder, threshold=DEFAULT_THRESHOLD,
                 verbose=False):
    """Compare a report against a folder of sources. Returns a match list."""
    from .reference_auditor import load_document
    raw = load_document(report_path)[0]                   # .md, .txt, .docx or .pptx

    body = strip_reference_list(raw)
    report_tokens = tokenise(body)
    spans = quoted_spans(body)

    if verbose:
        line = "=" * 70
        print(line)
        print("  PLAGIARISM PRE-CHECK - 3-Word Rule Enforcement")
        print(line)
        print("  Reporting runs of %d+ consecutive matching words." % threshold)
        print("  The report's reference list is excluded from the comparison.\n")

    per_file, problems, roles = load_sources(sources_folder, verbose=verbose)

    if not per_file:
        if verbose:
            print("\n  WARNING: no readable source materials found.")
            print("  A PASS here would be meaningless - nothing was compared.")
            if problems:
                print("  Unreadable or unsupported files:")
                for fname, why in problems:
                    print("    - %s (%s)" % (fname, why))
        return []

    all_matches = []
    for fname, source_words in per_file.items():
        for m in find_matches_against(report_tokens, source_words, threshold):
            m = dict(m)
            m["source"] = fname
            m["role"] = roles.get(fname, "source")
            all_matches.append(m)

    matches = _select_non_overlapping(all_matches)
    for m in matches:
        m["text"] = " ".join(m["tokens"])
        m["quoted"] = _in_quotes(m["char_start"], m["char_end"], spans)
        m["severity"] = _severity(m["length"])

    if verbose:
        _print_report(report_tokens, per_file, problems, matches, threshold)

    return matches


def is_serious(match):
    """A long unquoted run copied from a study source: plagiarism.

    A match against the brief is usually the question being restated, and a
    match against the student's own notes means the note should be checked;
    neither is counted here, though both are reported.
    """
    return (not match["quoted"] and match.get("role", "source") == "source"
            and match["length"] >= 12)


def _print_report(report_tokens, per_file, problems, matches, threshold):
    total_source_words = sum(len(w) for w in per_file.values())
    print("\n  Report words compared: %s" % format(len(report_tokens), ","))
    print("  Source words indexed:  %s across %d file(s)"
          % (format(total_source_words, ","), len(per_file)))

    if problems:
        print("\n--- [WARNING] %d SOURCE FILE(S) COULD NOT BE COMPARED ---"
              % len(problems))
        for fname, why in problems:
            print("  !! %s (%s)" % (fname, why))
        print("  Anything copied from these files will NOT be detected here.")

    quoted = [m for m in matches if m["quoted"]]
    notes = [m for m in matches if not m["quoted"] and m.get("role") == "notes"]
    brief = [m for m in matches if not m["quoted"] and m.get("role") == "brief"]
    unquoted = [m for m in matches if not m["quoted"]
                and m.get("role", "source") == "source"]

    matched_words = sum(m["length"] for m in unquoted)
    pct = (100.0 * matched_words / len(report_tokens)) if report_tokens else 0.0

    print("\n--- RESULTS ---")
    if not unquoted:
        print("  [PASS] No unquoted runs of %d+ words match the sources."
              % threshold)
    else:
        print("  [WARNING] %d unquoted run(s) found; %s words (%.1f%% of body)."
              % (len(unquoted), format(matched_words, ","), pct))
        for band in ("HIGH", "MEDIUM", "LOW"):
            group = [m for m in unquoted if m["severity"] == band]
            if not group:
                continue
            print("\n  %s severity (%d):" % (band, len(group)))
            for i, m in enumerate(group[:15], 1):
                print("    %d. [%d words] \"%s\"" % (i, m["length"], m["text"]))
                print("       source: %s" % m["source"])
            if len(group) > 15:
                print("    ... and %d more" % (len(group) - 15))

    if notes:
        print("\n--- [REVIEW] %d MATCH(ES) WITH YOUR OWN NOTES ---" % len(notes))
        print("  Reusing your own notes is fine - unless the note itself was")
        print("  copied from a source. Check each of these notes.")
        for i, m in enumerate(notes[:10], 1):
            print("    %d. [%d words] \"%s\"  (%s)"
                  % (i, m["length"], m["text"][:80], m["source"]))

    if brief:
        print("\n--- [INFO] %d MATCH(ES) WITH THE ASSIGNMENT BRIEF ---" % len(brief))
        print("  Usually the question being restated. Paraphrase it where you")
        print("  can, rather than repeating the brief word for word.")
        for i, m in enumerate(brief[:5], 1):
            print("    %d. [%d words] \"%s\"" % (i, m["length"], m["text"][:80]))

    if quoted:
        print("\n--- [REVIEW] %d MATCH(ES) INSIDE QUOTATION MARKS ---" % len(quoted))
        print("  Direct quotation is legitimate. Check each has a citation")
        print("  with a page number, per Rule 3.3.")
        for i, m in enumerate(quoted[:10], 1):
            print("    %d. [%d words] \"%s\"  (%s)"
                  % (i, m["length"], m["text"], m["source"]))

    print("\n" + "=" * 70)
    if unquoted:
        worst = max(m["length"] for m in unquoted)
        if worst >= 12:
            print("  RESULT: FAIL - a %d-word verbatim run needs rewriting." % worst)
        else:
            print("  RESULT: REVIEW - short overlaps only; check they are")
            print("  technical terms or established phrases, not borrowed prose.")
    else:
        print("  RESULT: PASS - no unquoted overlap above the threshold.")
    print("=" * 70)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Detect consecutive word overlap with source materials")
    parser.add_argument("report", help="Path to the report (markdown or text)")
    parser.add_argument("sources", help="Folder of source materials")
    parser.add_argument("--threshold", type=int, default=DEFAULT_THRESHOLD,
                        help="Minimum run length to report (default 4)")
    args = parser.parse_args()

    found = run_precheck(args.report, args.sources, args.threshold)
    sys.exit(1 if any(is_serious(m) for m in found) else 0)
