"""
DASH CHECK - Finds em dashes and en dashes in academic work.

Markers treat the em dash and the en dash as a style fault in academic
writing, and AI models scatter them through everything they write. This
check finds every one, so none reaches a submitted document.

What is found:
    em dash         U+2014   FAIL
    en dash         U+2013   FAIL   (also in number ranges: 14-16, not 14–16)
    other dashes    U+2012 figure dash, U+2015 horizontal bar   FAIL
    double hyphen   "--" between words (Word turns it into a dash)   FAIL
    spaced hyphen   "word - word" in running prose, used as a dash
                    substitute   WARNING only (headings, tables and the
                    reference list are left alone)

Nothing is changed automatically. A dash in prose needs rewriting (a comma,
a colon, brackets or two sentences), which is a writing decision, not a
character swap. The report says how to fix each kind.

Usage:
    python dash_check.py <file.md | file.docx | file.pptx>
    fieldkit academic dashes <file>
"""
import os
import re
import sys
import argparse

HARNESS_DIR = os.path.dirname(os.path.abspath(__file__))
if HARNESS_DIR not in sys.path:
    sys.path.insert(0, HARNESS_DIR)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

EM, EN, FIGURE, BAR = "—", "–", "‒", "―"
DASHES = {EM: "em dash (—)", EN: "en dash (–)",
          FIGURE: "figure dash (‒)", BAR: "horizontal bar (―)"}

_DOUBLE_HYPHEN = re.compile(r"(?<=\w)\s*--\s*(?=\w)")
_SPACED_HYPHEN = re.compile(r"(?<=[A-Za-z,)])\s-\s(?=[A-Za-z(])")
_RANGE = re.compile(r"\d\s*[‒–—―]\s*\d")


def find_dashes(text):
    """Return (faults, warnings), each a list of dicts {line, kind, snippet}.

    faults:   em/en/other dashes and '--' anywhere, reference list included
    warnings: ' - ' used as a dash in running prose
    """
    faults, warnings = [], []
    in_refs = False
    for number, line in enumerate(text.split("\n"), 1):
        stripped = line.strip()
        if re.match(r"^#*\s*(?:References?|Bibliography|Reference\s+List)\s*:?\s*$",
                    stripped, re.I):
            in_refs = True

        for ch, name in DASHES.items():
            for m in re.finditer(re.escape(ch), line):
                window = line[max(0, m.start() - 2):m.end() + 2]
                kind = "range" if _RANGE.search(window) else "prose"
                faults.append({"line": number, "kind": kind, "what": name,
                               "snippet": _snippet(line, m.start())})
        for m in _DOUBLE_HYPHEN.finditer(line):
            faults.append({"line": number, "kind": "prose",
                           "what": "double hyphen (--)",
                           "snippet": _snippet(line, m.start())})

        prose = not (in_refs or stripped.startswith(("#", "|", ">"))
                     or re.match(r"^\s*[-*]\s", line) and len(stripped) < 60)
        if prose:
            for m in _SPACED_HYPHEN.finditer(line):
                warnings.append({"line": number, "kind": "spaced",
                                 "what": "hyphen used as a dash ( - )",
                                 "snippet": _snippet(line, m.start())})
    return faults, warnings


def _snippet(line, pos, width=34):
    start = max(0, pos - width)
    end = min(len(line), pos + width)
    text = line[start:end].strip()
    return ("..." if start else "") + text + ("..." if end < len(line) else "")


def check_file(path):
    """find_dashes over a .md, .txt, .docx or .pptx file (notes included)."""
    from .reference_auditor import load_document
    text, notes = load_document(path)
    if notes:
        text = text + "\n" + notes
    return find_dashes(text)
