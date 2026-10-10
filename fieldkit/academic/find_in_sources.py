"""
FIND IN SOURCES - Search every study file at once and get back only the
matching passages, each with the file and the page or slide it is on.

This is how a model should read study material: search first, then open
only what the search points to. A search result costs a few hundred tokens;
re-sending a 60-slide lecture costs tens of thousands.

It searches the extracted library (Extracted PDF.PowerPoint), refreshing it
first if any source has changed - so the originals are never re-read for a
search, and scanned pages are never OCR'd twice.

Matching:
    safeguarding                   words starting "safeguarding"
    safeguard adult                both words on the same page or slide
    "duty of care"                 the exact phrase
    safeguard --any                either word
    --file lecture                 only sources whose name contains "lecture"

Each hit is the sentence or two around the match, with its citation:

    Lecture 3 - Safeguarding.pptx
      slide 4 (Making Safeguarding Personal): ...person-centred approach...
    Care Act guidance.pdf
      p. 14: ...the local authority must make enquiries...

Usage:
    python find_in_sources.py <exam_folder> "search words" [options]

Options:
    --any            match pages containing ANY of the words (default: all)
    --file TEXT      only sources whose name contains TEXT
    --limit N        at most N passages (default 25)
    --width N        characters of context per passage (default 320)
    --no-refresh     search the library as it is, without checking sources
"""
import os
import re
import sys
import argparse
from collections import OrderedDict

HARNESS_DIR = os.path.dirname(os.path.abspath(__file__))
if HARNESS_DIR not in sys.path:
    sys.path.insert(0, HARNESS_DIR)

from .workspace import Workspace
from .extract_library import (build_library, load_manifest, library_units,
                             estimate_tokens)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def parse_query(query):
    """Split a query into exact phrases and single-word prefixes."""
    phrases = [p.strip().lower() for p in re.findall(r'"([^"]+)"', query)
               if p.strip()]
    rest = re.sub(r'"[^"]*"', " ", query)
    words = [w.lower() for w in re.findall(r"[\w'\-]+", rest) if len(w) > 1]
    return phrases, words


def _patterns(phrases, words):
    pats = [re.compile(r"\b" + r"\s+".join(re.escape(p) for p in ph.split())
                       + r"\b", re.IGNORECASE) for ph in phrases]
    pats += [re.compile(r"\b" + re.escape(w), re.IGNORECASE) for w in words]
    return pats


def _snippet(text, patterns, width):
    """The passage around the first match, trimmed to whole words."""
    flat = re.sub(r"\s+", " ", text)
    first = None
    for p in patterns:
        m = p.search(flat)
        if m and (first is None or m.start() < first):
            first = m.start()
    if first is None:
        first = 0
    start = max(0, first - width // 3)
    end = min(len(flat), start + width)
    # Prefer to start at a sentence boundary shortly before the match.
    at_sentence = False
    boundary = flat.rfind(". ", max(0, start - 60), first)
    if boundary != -1 and first - boundary < width // 2:
        start = boundary + 2
        end = min(len(flat), start + width)
        at_sentence = True
    piece = flat[start:end]
    if start > 0 and not at_sentence:
        # Mid-sentence: drop the partial first word and show the cut.
        piece = "..." + piece.split(" ", 1)[-1]
    if end < len(flat):
        piece = piece.rsplit(" ", 1)[0] + "..."
    for p in patterns:
        piece = p.sub(lambda m: "**" + m.group(0) + "**", piece)
    return piece.replace("****", "")


def search(exam_path, query, any_word=False, file_filter=None, limit=25,
           width=320, refresh=True):
    """Search the library. Returns (hits, total_matching_sections, files).

    Each hit: {"file", "cite", "label", "snippet", "score"}.
    """
    if refresh:
        build_library(exam_path, verbose=False)
    ws = Workspace(exam_path)
    manifest = load_manifest(ws)
    phrases, words = parse_query(query)
    if not phrases and not words:
        return [], 0, 0
    patterns = _patterns(phrases, words)

    hits = []
    for rel, entry in sorted(manifest.get("files", {}).items()):
        if file_filter and file_filter.lower() not in rel.lower():
            continue
        path = entry.get("output_path", "")
        if not os.path.isfile(path):
            continue
        for label, cite, text in library_units(path):
            found = [len(p.findall(text)) for p in patterns]
            matched = (any(found) if any_word else all(found))
            if not matched:
                continue
            hits.append({"file": rel, "cite": cite, "label": label,
                         "snippet": _snippet(text, patterns, width),
                         "score": sum(found)})

    total = len(hits)
    files = len(set(h["file"] for h in hits))
    # Strongest sections first, but keep each file's hits together in page
    # order so the output reads naturally.
    best = sorted(hits, key=lambda h: -h["score"])[:limit]
    keep = set(id(h) for h in best)
    ordered = [h for h in hits if id(h) in keep]
    return ordered, total, files


def format_results(query, hits, total, files, limit):
    if not total:
        return ("No passages match %s. Try fewer words, --any, or a shorter "
                "word stem." % query)
    lines = ["%s - %d passage%s in %d file%s%s"
             % (query, total, "" if total == 1 else "s", files,
                "" if files == 1 else "s",
                " (showing the %d strongest)" % len(hits) if total > limit else "")]
    grouped = OrderedDict()
    for h in hits:
        grouped.setdefault(h["file"], []).append(h)
    for rel, items in grouped.items():
        lines.append("")
        lines.append(rel)
        for h in items:
            where = h["label"] or "(whole file)"
            lines.append("  %s: %s" % (where, h["snippet"]))
    text = "\n".join(lines)
    lines.append("")
    lines.append("(~%d tokens of results. Open a source's .md file in "
                 "'Extracted PDF.PowerPoint' only if you need more.)"
                 % estimate_tokens(len(text.split())))
    return "\n".join(lines)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Search every study file, with page and slide numbers")
    parser.add_argument("exam_folder")
    parser.add_argument("query", help='Words, or "an exact phrase"')
    parser.add_argument("--any", action="store_true",
                        help="Match sections containing any of the words")
    parser.add_argument("--file", default=None,
                        help="Only sources whose name contains this")
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--width", type=int, default=320)
    parser.add_argument("--no-refresh", action="store_true")
    args = parser.parse_args()

    found, total, nfiles = search(args.exam_folder, args.query, args.any,
                                  args.file, args.limit, args.width,
                                  refresh=not args.no_refresh)
    print(format_results(args.query, found, total, nfiles, args.limit))
    sys.exit(0 if total else 1)
