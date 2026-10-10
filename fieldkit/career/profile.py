"""The candidate's profile: the only facts the CV, the scoring and the cover letters may use.

profile.yaml lives in the data folder (local/career by default, git-ignored). Any text field may be a string, or
{en: ..., ro: ...} when the CV is wanted in both languages: the code never translates; the AI writes the translation
into the profile and the person confirms it.

    name, contact {phone, email, location, linkedin}, right_to_work, years_experience, trade (optional: trades/*.yaml)
    languages_spoken [..], languages [{language, level}], licences [{category, detail}], certificates [..]
    salary_floor (a year, £), locations [..], remote_ok, exclude_title_patterns [..], shortlist_min_score
    experience [{title, employer, location, start: YYYY-MM, end: YYYY-MM|present,
                 bullets: [text | {text, variants: [names]}]}]
    education [{title, place, year, detail}], other [..]
    cv_variants {name: {headline, profile, keywords [..], skills [..], title_patterns [..]}}
    evidence [{id, first_person, source}]   one true sentence each, the only sentences a letter may carry
    letter {pitch: [sentences], extra_allowed: [sentences]}
    queries [{what, where, distance_miles}]  what the job boards are asked
"""
import re
from pathlib import Path

import yaml

from ..core import settings

FORBIDDEN_KEYS = {"date_of_birth": "date of birth", "dob": "date of birth", "birth": "date of birth",
                  "age": "age", "photo": "photo", "picture": "photo", "marital_status": "marital status",
                  "cnp": "CNP", "national_insurance": "NI number", "ni_number": "NI number",
                  "passport": "passport number"}
LANGS = ("en", "ro")


class BadProfile(ValueError):
    pass


def data_dir(local=None):
    local = settings.local_settings() if local is None else local
    d = (local.get("career") or {}).get("dir")
    return Path(settings.expand(d)) if d else settings.ROOT / "local" / "career"


def load_yaml(path):
    return yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}


def T(v, lang="en", where=""):
    """A text field in one language. A missing translation is an error, never a silent fallback."""
    if isinstance(v, dict):
        if lang in v and v[lang]:
            return str(v[lang])
        raise BadProfile(f"{where}: no '{lang}' text (has {', '.join(v)}); the AI writes it, the person confirms it")
    return "" if v is None else str(v)


def ym(v):
    v = str(v).strip().lower()
    if v in ("present", "current", "now", "prezent"):
        return None
    m = re.fullmatch(r"((?:19|20)\d\d)(?:-(\d\d))?", v)
    if not m:
        raise BadProfile(f"date {v!r}: write YYYY-MM or YYYY, or 'present'")
    return int(m.group(1)), int(m.group(2) or 0)


def validate(p):
    """-> the profile, experience newest first. Every problem at once, so one edit fixes them all."""
    errs = []

    def walk(o, path=""):
        if isinstance(o, dict):
            for k, v in o.items():
                if str(k).lower() in FORBIDDEN_KEYS:
                    errs.append(f"{path}{k}: a UK CV does not carry the {FORBIDDEN_KEYS[str(k).lower()]}; remove it")
                walk(v, f"{path}{k}.")
        elif isinstance(o, list):
            for i, v in enumerate(o):
                walk(v, f"{path}{i}.")
    walk(p)
    for k in ("name", "contact", "experience", "cv_variants", "evidence", "letter"):
        if not p.get(k):
            errs.append(f"{k}: missing or empty")
    for k in ("phone", "email"):
        if not (p.get("contact") or {}).get(k):
            errs.append(f"contact.{k}: missing")
    for name, v in (p.get("cv_variants") or {}).items():
        for k in ("headline", "profile", "keywords", "skills"):
            if not v.get(k):
                errs.append(f"cv_variants.{name}.{k}: missing")
    names = set(p.get("cv_variants") or {})
    for i, e in enumerate(p.get("experience") or []):
        for k in ("title", "employer", "start"):
            if not e.get(k):
                errs.append(f"experience.{i}.{k}: missing")
        try:
            ym(e.get("start", "")), ym(e.get("end", "present"))
        except BadProfile as x:
            errs.append(f"experience.{i}: {x}")
        for j, b in enumerate(e.get("bullets") or []):
            for vn in (b.get("variants") or []) if isinstance(b, dict) else []:
                if vn not in names:
                    errs.append(f"experience.{i}.bullets.{j}: variant {vn!r} is not in cv_variants")
    ids = [e.get("id") for e in p.get("evidence") or []]
    for i, e in enumerate(p.get("evidence") or []):
        if not e.get("id") or not e.get("first_person"):
            errs.append(f"evidence.{i}: needs id and first_person")
        if not e.get("source"):
            errs.append(f"evidence.{i} ({e.get('id')}): source missing - where the fact comes from "
                        "(the original CV, a certificate, the person's own words on a date)")
    if len(ids) != len(set(ids)):
        errs.append("evidence: two entries share an id")
    if not (p.get("letter") or {}).get("pitch"):
        errs.append("letter.pitch: missing (one to three first-person sentences)")
    if errs:
        raise BadProfile("; ".join(errs))
    out = dict(p)
    out["experience"] = sorted(p["experience"], key=lambda e: (ym(e.get("end", "present")) or (9999, 99),
                                                              ym(e["start"])), reverse=True)
    return out


def load(path=None):
    path = Path(path or data_dir() / "profile.yaml")
    if not path.is_file():
        raise BadProfile(f"{path}: no profile yet. Stage 1 builds it from the CV and the answers; "
                         f"fieldkit career init makes the folder")
    return validate(load_yaml(path))
