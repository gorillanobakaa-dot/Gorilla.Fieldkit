"""The work must be in the language the course asks for, and (for English) in the spelling variant it asks for.

Generalised 2026-10-10 from a check built for one student who talks to his AI in Romanian and writes in British
English, where Romanian sentences leaking into the work failed it. Now any of the seven languages can be the work
language, and a sentence in any OTHER language fails, whichever languages the student and the AI speak.

    other-language sentences   FAIL   a sentence whose distinctive words (languages.yaml) are mostly another
                                      language's, with at least two of them
    spelling variant (English) WARN   en-GB work: American forms ('analyze', 'behavior'); en-US work: British forms
                                      ('analyse', 'behaviour'); each with the expected form beside it

Body only: the reference list, quotations, tables and block quotations are left alone (titles and quoted words are
reproduced as the source wrote them).
"""
import re
from pathlib import Path

import yaml

from .reference_auditor import _quote_spans, split_body_and_references

HERE = Path(__file__).parent

# American -> British, for words that turn up in health, social care and general academic writing. Unambiguous forms
# only ('program', 'practice' and 'license' are left out: both spellings are correct in some senses).
AMERICAN = {
    "analyze": "analyse", "analyzed": "analysed", "analyzes": "analyses",
    "analyzing": "analysing", "organize": "organise", "organized": "organised",
    "organizing": "organising", "organization": "organisation",
    "organizations": "organisations", "organizational": "organisational",
    "recognize": "recognise", "recognized": "recognised",
    "prioritize": "prioritise", "prioritized": "prioritised",
    "emphasize": "emphasise", "emphasized": "emphasised",
    "minimize": "minimise", "maximize": "maximise", "realize": "realise",
    "realized": "realised", "utilize": "utilise", "utilized": "utilised",
    "characterize": "characterise", "characterized": "characterised",
    "summarize": "summarise", "categorize": "categorise",
    "criticize": "criticise", "criticized": "criticised",
    "hospitalization": "hospitalisation", "standardized": "standardised",
    "conceptualize": "conceptualise", "marginalized": "marginalised",
    "stigmatized": "stigmatised", "institutionalized": "institutionalised",
    "behavior": "behaviour", "behaviors": "behaviours",
    "behavioral": "behavioural", "color": "colour", "favor": "favour",
    "favorable": "favourable", "honor": "honour", "labor": "labour",
    "neighbor": "neighbour", "neighborhood": "neighbourhood",
    "center": "centre", "centers": "centres", "fiber": "fibre",
    "defense": "defence", "offense": "offence", "pediatric": "paediatric",
    "anemia": "anaemia", "fetus": "foetus", "estrogen": "oestrogen",
    "orthopedic": "orthopaedic", "gynecology": "gynaecology",
    "anesthesia": "anaesthesia", "edema": "oedema", "diarrhea": "diarrhoea",
    "hemorrhage": "haemorrhage", "leukemia": "leukaemia", "tumor": "tumour",
    "counselor": "counsellor", "counseling": "counselling",
    "traveled": "travelled", "modeling": "modelling", "labeling": "labelling",
    "fulfill": "fulfil", "enrollment": "enrolment", "aging": "ageing",
    "gray": "grey", "skeptical": "sceptical", "catalog": "catalogue",
    "dialog": "dialogue", "caregiver": "carer", "caregivers": "carers",
}
# British -> American: the same table turned round, minus pairs where the "British" form is also standard American
# usage ('dialogue', 'catalogue') or a different word, not a spelling ('carer', 'per cent')
BRITISH = {b: a for a, b in AMERICAN.items() if b not in ("dialogue", "catalogue", "carer", "carers")}


def languages():
    return yaml.safe_load((HERE / "languages.yaml").read_text(encoding="utf-8"))


def _words(s):
    return re.findall(r"[^\W\d_]+", s.lower())


def detect(sentence, langs=None):
    """The language a sentence is written in, or None when it is too short or unclear to say."""
    langs = langs or languages()
    words = _words(sentence)
    scores = {code: sum(w in set(map(str.lower, d["distinctive"])) for w in words) for code, d in langs.items()}
    best = max(scores, key=scores.get)
    if scores[best] < 2 or list(scores.values()).count(scores[best]) > 1:
        return None
    return best


def body_prose(text):
    """The assessed prose: no reference list, tables, quotations or comments."""
    body, _ = split_body_and_references(text)
    body = re.sub(r"<!--.*?-->", "", body, flags=re.DOTALL)
    lines = []
    for line in body.split("\n"):
        s = line.strip()
        if s.startswith(("|", ">")):
            continue
        for a, b, _ in reversed(_quote_spans(line)):
            line = line[:a] + " " + line[b:]
        lines.append(line)
    return "\n".join(lines)


def sentences(text):
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text) if s.strip()]


def check(text, work_language="en", spelling=None):
    """-> {work_language, other: [{sentence, language}], spelling: [{word, expected}], ok}."""
    langs = languages()
    if work_language not in langs:
        raise ValueError(f"work language {work_language!r}: one of {', '.join(langs)}")
    other, variant = [], []
    prose = body_prose(text)
    for s in sentences(prose):
        lang = detect(s, langs)
        if lang and lang != work_language:
            other.append({"sentence": s[:160], "language": lang, "name": langs[lang]["name"]})
    table = AMERICAN if spelling == "en-GB" else BRITISH if spelling == "en-US" else {}
    seen = set()
    for w in _words(prose):
        if w in table and w not in seen:
            seen.add(w)
            variant.append({"word": w, "expected": table[w]})
    return {"work_language": work_language, "spelling_variant": spelling, "other": other, "spelling": variant,
            "ok": not other}


def lines(r):
    names = languages()
    out = [f"language: the work must be in {names[r['work_language']]['name']}"
           + (f" ({r['spelling_variant']})" if r["spelling_variant"] else "")]
    for o in r["other"]:
        out.append(f"  FAIL  a sentence in {o['name']}: {o['sentence']}")
    for v in r["spelling"]:
        out.append(f"  WARN  '{v['word']}': in {r['spelling_variant']} write '{v['expected']}'")
    out.append("OK" if r["ok"] else f"{len(r['other'])} sentence(s) in another language")
    return out
