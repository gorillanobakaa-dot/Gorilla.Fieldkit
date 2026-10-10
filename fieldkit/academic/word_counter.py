"""
WORD COUNTER - Counts words the way MS Word does, over the assessed body only.

Excluded from the count:
  * the title page (everything before the first '##' section heading)
  * the reference list / bibliography and everything after it
  * appendices
  * HTML and markdown comments

Counted, because MS Word counts them: section headings, table cell text,
numbers, and hyphenated compounds as single words.

The old version claimed to exclude appendices but never did, which let an
appendix silently push a report over its limit.

Usage:
    python word_counter.py <markdown_file_path> [--target 2500]
"""
import re
import sys
import argparse

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from .reference_auditor import REF_HEADING                 # every language's heading
APPENDIX_HEADING = (r"(?:Appendix|Appendices|Appendix\s+[A-Z0-9]+|Ap[ée]ndices?|Anexos?|Ap[êe]ndices?|Allegat[oi]|Appendice"
                    r"|Anh[aä]nge?|Anex[ae])(?:\s+[A-Z0-9]+)?")

# MS Word treats a hyphenated or apostrophised compound as one word.
WORD_RE = re.compile(
    r"[^\W_]+"
    r"(?:[-–’'][^\W_]+)*"
)

TOLERANCE = 0.10


def _cut_at_heading(text, heading_pattern):
    m = re.search(r"(?:^|\n)[ \t]*#*[ \t]*" + heading_pattern + r"[ \t]*:?[ \t]*(?:\n|$)",
                  text, flags=re.IGNORECASE)
    return text[:m.start()] if m else text


def extract_body(text):
    """Return only the assessed body of the report."""
    # Drop comments first so they cannot contribute words or false headings.
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)

    # Strip Table of Contents if present
    text = re.sub(r"(?im)^#{1,6}\s+(?:table\s+of\s+)?contents\b.*?(?=^#{1,6}\s+(?!contents|table\s+of\s+contents)\S)", "", text, flags=re.DOTALL | re.MULTILINE)

    # Title page: everything before the first level-2 heading.
    first_section = re.search(r"(?:^|\n)##[ \t]+\S", text)
    if first_section:
        text = text[first_section.start():]

    text = _cut_at_heading(text, REF_HEADING)
    text = _cut_at_heading(text, APPENDIX_HEADING)
    return text


def count_words(filepath, target=2500, verbose=False):
    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()

    body = extract_body(text)
    word_count = len(WORD_RE.findall(body))

    lower = int(target * (1 - TOLERANCE))
    upper = int(target * (1 + TOLERANCE))
    in_range = lower <= word_count <= upper

    if verbose:
        print("  Words:      %s (no title, references or appendices)"
              % format(word_count, ","))
        print("  Target:     %s (accepted: %s - %s, +/-10%%)"
              % (format(target, ","), format(lower, ","), format(upper, ",")))
        print("  Result:     %s"
              % ("OK, within range" if in_range else "OUT of range"))
        if word_count > upper:
            print("  Do:         cut %s words." % format(word_count - upper, ","))
        elif word_count < lower:
            print("  Do:         write %s more words." % format(lower - word_count, ","))

    return word_count


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Count the assessed body words of a report")
    parser.add_argument("filepath", help="Path to markdown file")
    parser.add_argument("--target", type=int, default=2500,
                        help="Target word count (default 2500)")
    args = parser.parse_args()
    n = count_words(args.filepath, args.target)
    lo = int(args.target * (1 - TOLERANCE))
    hi = int(args.target * (1 + TOLERANCE))
    sys.exit(0 if lo <= n <= hi else 1)
