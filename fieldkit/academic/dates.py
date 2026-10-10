"""Dates in the seven countries' forms: read any of them, write the one the student's country uses.

    28 September 2026 / 28th Sept 2026      28 de septiembre de 2026      28. September 2026
    September 28, 2026                      28 settembre 2026             28 septembrie 2026
    28/09/2026 (day first; month first only for us)                       2026-09-28

Month names come from languages.yaml, so a new language brings its months with it. A date that cannot be read is
None, never a guess: the harness then asks.
"""
import datetime as _dt
import re
import unicodedata

from .language import languages


def _fold(s):
    return "".join(c for c in unicodedata.normalize("NFKD", s.lower()) if not unicodedata.combining(c)).strip(".")


def month_table(langs=None):
    """{folded name or 3+ letter abbreviation: month number}, every language at once."""
    table = {}
    for d in (langs or languages()).values():
        for i, name in enumerate(d["months"], 1):
            f = _fold(name)
            table[f] = i
            for n in (3, 4):
                table.setdefault(f[:n], i)     # 'sep', 'sept', 'gen', 'ene', 'okt', 'mrz' via März -> 'mar'
    table["mrz"] = 3
    return table


_MONTH_WORD = r"([^\W\d_]{3,}\.?)"
_ORD = r"(?:st|nd|rd|th|º|ª|\.)?"


def parse(text, country=None, langs=None):
    """-> datetime.date, or None when no date can be read for certain."""
    if not text:
        return None
    t = text.strip()
    months = month_table(langs)

    def mk(y, m, d):
        try:
            y = int(y)
            return _dt.date(y + 2000 if y < 100 else y, int(m), int(d))
        except ValueError:
            return None

    m = re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", t)
    if m:
        return mk(m.group(1), m.group(2), m.group(3))
    # day month year: 28 September 2026, 28 de septiembre de 2026, 28. September 2026, 28th Sept. 2026
    for m in re.finditer(r"\b(\d{1,2})" + _ORD + r"\s+(?:de\s+)?" + _MONTH_WORD + r"\s+(?:de\s+)?(\d{4})\b", t, re.I):
        mo = months.get(_fold(m.group(2)))
        if mo:
            return mk(m.group(3), mo, m.group(1))
    # month day, year: September 28, 2026
    for m in re.finditer(_MONTH_WORD + r"\s+(\d{1,2})" + _ORD + r",?\s+(\d{4})\b", t, re.I):
        mo = months.get(_fold(m.group(1)))
        if mo:
            return mk(m.group(3), mo, m.group(2))
    m = re.search(r"\b(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})\b", t)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        if country == "us" and a <= 12:
            a, b = b, a
        return mk(m.group(3), b, a)
    return None


def fmt(date, date_format="{day} {month} {year}", language="en", langs=None):
    """The date written the way the country writes it (date_format and month names from the data files)."""
    name = (langs or languages())[language]["months"][date.month - 1]
    return date_format.format(day=date.day, month=name, year=date.year)


def iso(date):
    return date.isoformat() if date else None
