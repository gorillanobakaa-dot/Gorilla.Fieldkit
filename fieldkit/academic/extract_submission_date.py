"""
EXTRACT SUBMISSION DATE - Finds the assignment submission deadline and
writes it the way the student's country writes dates (locales/*.yaml).

Looks in:
  1. Deadline notes (Deadline.txt, due.txt, etc.) in the exam folder or brief.
  2. Files in "1 - DROP YOUR STUDY MATERIALS HERE/Assignment brief/" (handbooks, briefs).
  3. Any coursework/handbook/assignment file in study materials.

Returns:
  date: in the country's form, e.g. "28 September 2026" (uk), "28. September 2026" (de)
  evidence: the text passage it was found in
  source: file name where it was found

A date found here is only a suggestion: fieldkit academic setup still asks the student to confirm it.
"""
import os
import re
import sys
import json
import argparse
from datetime import datetime

HARNESS_DIR = os.path.dirname(os.path.abspath(__file__))
if HARNESS_DIR not in sys.path:
    sys.path.insert(0, HARNESS_DIR)

from .workspace import Workspace
from .read_sources import READERS, read_file, is_error_text

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from . import dates as _dates
from .language import languages as _languages

# Month names and abbreviations of all seven languages (languages.yaml), longest first so 'September' wins over 'Sep'
MONTH_REGEX = "(?:" + "|".join(sorted({re.escape(k) for k in _dates.month_table()}, key=len, reverse=True)) + r")\.?"

DEADLINE_HINTS = (
    r"(?:submission\s+date|submission\s+deadline|due\s+date|due|deadline|"
    r"submit\s+by|hand[\s-]?in|closing\s+date|first\s+submission|"
    r"resubmission\s+deadline|final\s+submission|"
    r"fecha\s+(?:l[íi]mite|de\s+entrega)|entrega|plazo|"                       # es
    r"prazo(?:\s+de\s+entrega)?|data\s+(?:de|di)\s+(?:entrega|consegna)|"    # pt / it
    r"scadenza|consegna\s+entro|"                                             # it
    r"abgabe(?:termin|datum)?|einreichung(?:sfrist)?|frist|"                   # de
    r"termen(?:ul)?(?:\s+(?:limit[aă]|de\s+predare))?|predare)"              # ro
)


def _profile():
    try:
        from .profile import load
        return load() or {}
    except Exception:
        return {}


def format_date(date, profile=None):
    """A date in the student's country form ('28 September 2026', 'September 28, 2026', '28. September 2026' ...)."""
    p = profile if profile is not None else _profile()
    lang = p.get("work_language", "en")
    return _dates.fmt(date, p.get("date_format", "{day} {month} {year}"), lang if lang in _languages() else "en")


def parse_british_date(raw_text, profile=None):
    """Kept name; any of the seven countries' forms -> the student's country form, or None."""
    p = profile if profile is not None else _profile()
    d = _dates.parse(raw_text, p.get("country"))
    return format_date(d, p) if d else None


def extract_date_from_text(text):
    """Scan text for explicit deadline phrases and return (british_date, snippet)."""
    if not text:
        return None, None

    date_pattern = (
        r"(?:(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)[,\s]+)?"
        r"(?:\d{1,2}(?:st|nd|rd|th|\.)?\s+(?:de\s+)?" + MONTH_REGEX + r"\s+(?:de\s+)?\d{4}"
        r"|" + MONTH_REGEX + r"\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}"
        r"|\d{1,2}[/.]\d{1,2}[/.]\d{2,4}|\d{4}-\d{2}-\d{2})"
    )

    # Priority 1: Keyword followed closely by a date
    pattern1 = re.compile(DEADLINE_HINTS + r"[^\n\r]{0,60}?(" + date_pattern + r")", flags=re.I)
    for m in pattern1.finditer(text):
        raw_date = m.group(1)
        b_date = parse_british_date(raw_date)
        if b_date:
            start = max(0, m.start() - 20)
            end = min(len(text), m.end() + 30)
            snippet = re.sub(r"\s+", " ", text[start:end]).strip()
            return b_date, snippet

    # Priority 2: Invert - date followed by deadline keyword (e.g. "28 September 2026 is the deadline")
    pattern2 = re.compile(r"(" + date_pattern + r")[^\n\r]{0,40}?" + DEADLINE_HINTS, flags=re.I)
    for m in pattern2.finditer(text):
        raw_date = m.group(1)
        b_date = parse_british_date(raw_date)
        if b_date:
            start = max(0, m.start() - 10)
            end = min(len(text), m.end() + 20)
            snippet = re.sub(r"\s+", " ", text[start:end]).strip()
            return b_date, snippet

    return None, None


def find_submission_date_in_exam(exam_folder):
    """Search the assignment folder and return (british_date, evidence, source_file)."""
    ws = Workspace(exam_folder) if not isinstance(exam_folder, Workspace) else exam_folder

    # 1. Dedicated deadline files (e.g., Deadline.txt, due.txt)
    deadline_candidates = ["deadline.txt", "deadline.md", "due.txt", "submission_date.txt"]
    search_dirs = [ws.root, ws.brief, ws.materials]

    for d in search_dirs:
        if d and os.path.isdir(d):
            for name in os.listdir(d):
                if name.lower() in deadline_candidates or "deadline" in name.lower():
                    fpath = os.path.join(d, name)
                    if os.path.isfile(fpath):
                        text = read_file(fpath)
                        b_date, snip = extract_date_from_text(text)
                        if b_date:
                            return b_date, snip, os.path.basename(fpath)

    # 2. Assignment brief folder files (handbooks, brief documents)
    if ws.brief and os.path.isdir(ws.brief):
        for fname in sorted(os.listdir(ws.brief)):
            fpath = os.path.join(ws.brief, fname)
            if os.path.isfile(fpath) and not fname.startswith("~") and not fname.startswith("."):
                text = read_file(fpath)
                b_date, snip = extract_date_from_text(text)
                if b_date:
                    return b_date, snip, fname

    # 3. Materials folder root files (e.g. module handbook placed in materials)
    if ws.materials and os.path.isdir(ws.materials):
        for fname in sorted(os.listdir(ws.materials)):
            fpath = os.path.join(ws.materials, fname)
            if os.path.isfile(fpath) and not fname.startswith("~") and not fname.startswith("."):
                if any(k in fname.lower() for k in ("handbook", "brief", "assessment", "coursework", "task")):
                    text = read_file(fpath)
                    b_date, snip = extract_date_from_text(text)
                    if b_date:
                        return b_date, snip, fname

    # 4. Folder name itself might contain the date (e.g. "Assignment.for. 28 September 2026")
    b_date_folder = parse_british_date(os.path.basename(ws.root))
    if b_date_folder:
        return b_date_folder, "Detected from folder name: %s" % os.path.basename(ws.root), "folder_name"

    # 5. Check EXAM_CONFIG.md if it already has a deadline recorded
    if os.path.isfile(ws.config):
        try:
            with open(ws.config, "r", encoding="utf-8") as f:
                c_text = f.read()
            m = re.search(r"(?:Deadline|Submission Date)[:\s]+([^\n\r]+)", c_text, flags=re.I)
            if m:
                b_date = parse_british_date(m.group(1))
                if b_date:
                    return b_date, m.group(0).strip(), "EXAM_CONFIG.md"
        except Exception:
            pass

    return None, None, None
