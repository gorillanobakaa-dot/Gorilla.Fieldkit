"""How the student's pages look, decided by data: the country (page size, margins), the referencing style (line
spacing), the work language (headings, labels, the language Word spell-checks in) and the profile (any of it).

    layout.current()           -> the layout for the student profile (or a UK default without one)
    layout.apply(lay)          -> sets it on the builders (build_word, build_pptx, build_xlsx, office_finalise)
    layout.heading(h, lang)    -> a document type's heading in the work language ("2. Discussion" -> "2. Diskussion")
    layout.to_english(h)       -> the other way, so the structure check reads a heading in any of the languages

The profile may override any key under "layout": {"font": "Times New Roman", "size": 12, "line_spacing": 2.0, ...}.
"""
import re
from functools import lru_cache
from pathlib import Path

import yaml

HERE = Path(__file__).parent
ORDER = ["es", "pt", "it", "de", "ro"]
LANG_TAGS = {"en-GB": "en-GB", "en-US": "en-US", "es": "es-ES", "pt": "pt-PT", "it": "it-IT", "de": "de-DE",
             "ro": "ro-RO"}
PAGES = {"A4": (21.0, 29.7), "Letter": (21.59, 27.94)}
DEFAULT = {"font": "Arial", "size": 12, "line_spacing": 1.5, "page": "A4", "margins_cm": 2.54, "language": "en",
           "lang_tag": "en-GB"}
_NUM = re.compile(r"^(\d+(?:\.\d+)*[.)]\s*)?(.*)$")


@lru_cache(maxsize=1)
def _data():
    return yaml.safe_load((HERE / "headings.yaml").read_text(encoding="utf-8"))


def labels(language="en"):
    lab = _data()["labels"]
    return dict(lab["en"], **lab.get(language, {}))


def heading(text, language="en"):
    if language not in ORDER:
        return text
    num, rest = _NUM.match(text.strip()).groups()
    row = _data()["headings"].get(rest)
    return (num or "") + (row[ORDER.index(language)] if row else rest)


@lru_cache(maxsize=1)
def _reverse():
    rev = {}
    for en, row in _data()["headings"].items():
        for t in row:
            rev.setdefault(t.lower(), en)
    return rev


def to_english(text):
    num, rest = _NUM.match(text.strip()).groups()
    en = _reverse().get(rest.strip().lower())
    return (num or "") + en if en else text


def current(profile=None):
    if profile is None:
        from .profile import load
        profile = load() or {}
    from . import styles
    lay = dict(DEFAULT)
    if profile:
        lang = profile.get("work_language", "en")
        style = profile.get("style")
        spacing = styles.get(style).get("line_spacing") if style in styles.names() else None
        lay.update(page=profile.get("page", "A4"), margins_cm=profile.get("margins_cm", 2.54), language=lang,
                   lang_tag=LANG_TAGS.get(profile.get("spelling") if lang == "en" else lang, "en-GB"))
        if spacing:
            lay["line_spacing"] = spacing
        lay.update(profile.get("layout") or {})
    lay["page_cm"] = PAGES.get(lay["page"], PAGES["A4"])
    lay["labels"] = labels(lay["language"])
    return lay


def apply(lay=None):
    """Set the layout on the builder modules (they read module-level settings)."""
    lay = lay or current()
    from docx.shared import Pt
    from . import build_word, office_finalise
    build_word.LAYOUT = lay
    build_word.BODY_FONT = lay["font"]
    build_word.BODY_SIZE = Pt(lay["size"])
    office_finalise.LANG = lay["lang_tag"]
    try:
        from . import build_pptx
        build_pptx.FONT = lay["font"]
        build_pptx.LAYOUT = lay
    except ImportError:                      # python-pptx missing: Word still works
        pass
    return lay
