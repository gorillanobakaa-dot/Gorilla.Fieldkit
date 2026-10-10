"""Short status labels and report notes, in the interface language (profile 'ui'); English when unset.

Kept small on purpose: one word per state, the same word every time, so a report can be read at a glance.
"""
LABELS = {
    "en": {"PASS": "OK", "FAIL": "FIX", "WARN": "CHECK", "INFO": "NOTE"},
    "ro": {"PASS": "OK", "FAIL": "DE REPARAT", "WARN": "DE VERIFICAT", "INFO": "NOTĂ"},
    "es": {"PASS": "OK", "FAIL": "CORREGIR", "WARN": "REVISAR", "INFO": "NOTA"},
    "pt": {"PASS": "OK", "FAIL": "CORRIGIR", "WARN": "VERIFICAR", "INFO": "NOTA"},
    "it": {"PASS": "OK", "FAIL": "CORREGGERE", "WARN": "CONTROLLARE", "INFO": "NOTA"},
    "de": {"PASS": "OK", "FAIL": "KORRIGIEREN", "WARN": "PRÜFEN", "INFO": "HINWEIS"},
}


def _ui():
    try:
        from .profile import load
        return (load() or {}).get("ui") or "en"
    except Exception:
        return "en"


def status(state, ui=None):
    labels = LABELS.get(ui or _ui(), LABELS["en"])
    return "[%s]" % labels.get(state, state)


INTRO_REFERENCES = ("Checking citations, quotations and the reference list: every citation has an entry, every entry "
                    "is cited, the list is in order, every direct quotation has a page.")
WARNING_END_REFERENCES = ("This checks the links between citations and entries, not whether a source says what the "
                          "work says it does. That part is yours.")
