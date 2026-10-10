"""The student profile: who the student is, where they study, and how their work must look. Asked, never assumed.

    fieldkit academic init --name "..." --country uk|us|es|pt|it|de|ro [--university ...] [--programme ...]
                           [--style apa7|harvard-ctr|iso690] [--language en|es|pt|it|de|ro] [--ui en|ro|...]

Without a name or a country, init answers with the questions to ask (in the interface language), and changes
nothing. The profile is local/academic/profile.json (git-ignored), and the name goes on the private word list so the
pre-commit privacy scan blocks it. Everything a country decides (page size, date format, spelling, the default
referencing style) comes from locales/<country>.yaml; the student may change any of it here.

Designed first for a student with ADHD and dyslexia; what helped him is kept for everyone, and can be turned on:
    support.short_messages   one thing at a time, short sentences (default on)
    support.agreed           adjustments the university agreed (extra time, a dyslexia note on submissions ...);
                             the harness reminds of them in time, never nags about what is already excused
    support.refuse_cloud     never save the work into OneDrive/Dropbox/Google Drive/iCloud (for slow or paid
                             internet, where a synced file can vanish when the signal drops); default off
"""
import json
import os
from pathlib import Path

import yaml

from ..core import settings

HERE = Path(__file__).parent
LOCALES = HERE / "locales"

QUESTIONS = {
    "en": {"name": "What is your full name, exactly as the university has it?",
           "country": "In which country is your university? (uk, us, es, pt, it, de, ro)",
           "university": "What is your university called?",
           "programme": "Which programme or course are you on?",
           "style": "Which referencing style does your course ask for? (if you are not sure, say so: the brief "
                    "or the module handbook will say)",
           "student_id": "What is your student number? (it goes on your work instead of your name)"},
    "ro": {"name": "Care este numele tău complet, exact cum îl are universitatea?",
           "country": "În ce țară este universitatea ta? (uk, us, es, pt, it, de, ro)",
           "university": "Cum se numește universitatea ta?",
           "programme": "La ce program sau curs ești?",
           "style": "Ce stil de citare cere cursul tău? (dacă nu știi, spune: scrie în cerința temei sau în "
                    "ghidul modulului)",
           "student_id": "Care este numărul tău de student? (apare pe lucrare în locul numelui)"},
}


class BadProfile(ValueError):
    pass


def base(local=None):
    if os.environ.get("FIELDKIT_ACADEMIC_DIR"):
        return Path(os.environ["FIELDKIT_ACADEMIC_DIR"])
    local = settings.local_settings() if local is None else local
    d = (local.get("academic") or {}).get("dir")
    return Path(settings.expand(d)) if d else settings.ROOT / "local" / "academic"


def countries():
    return sorted(p.stem for p in LOCALES.glob("*.yaml"))


def locale(country):
    p = LOCALES / f"{country}.yaml"
    if not p.is_file():
        raise BadProfile(f"country {country!r}: one of {', '.join(countries())}")
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def path(where=None):
    return Path(where or base()) / "profile.json"


def load(where=None):
    """The profile merged over its country's defaults, or None when there is none yet."""
    p = path(where)
    if not p.is_file():
        return None
    data = json.loads(p.read_text(encoding="utf-8"))
    loc = locale(data["country"])
    merged = {"work_language": loc["work_language"], "spelling": loc["spelling"], "page": loc["page"],
              "margins_cm": loc["margins_cm"], "date_format": loc["date_format"], "quotes": loc["quotes"],
              "style": loc["default_style"], "plagiarism_tool": loc["plagiarism_tool"],
              "avoid_long_dashes": loc["avoid_long_dashes"], "ui": "en"}
    merged.update({k: v for k, v in data.items() if v is not None})
    merged["support"] = dict({"short_messages": True, "agreed": [], "refuse_cloud": False},
                             **(data.get("support") or {}))
    return merged


def init(name=None, country=None, university=None, programme=None, style=None, language=None, ui="en",
         student_id=None, where=None, root=None):
    """Write the profile, or return the questions still to ask (nothing is written then)."""
    from ..career.store import remember_private
    q = QUESTIONS.get(ui, QUESTIONS["en"])
    p = path(where)
    if p.is_file():
        return {"ok": True, "status": "exists", "profile": str(p), "next": "fieldkit academic setup ASSIGNMENT"}
    missing = [k for k, v in (("name", name), ("country", country)) if not (v or "").strip()]
    if missing:
        return {"ok": False, "status": "ask", "questions": [q[k] for k in missing],
                "later": [q[k] for k in ("university", "programme", "style", "student_id")],
                "next": 'ask the questions; then fieldkit academic init --name "FULL NAME" --country CODE'}
    loc = locale(country)
    if style and style not in loc["styles"]:
        from . import styles
        if style not in styles.names():
            raise BadProfile(f"style {style!r}: one of {', '.join(styles.names())}")
    data = {"name": name.strip(), "country": country, "university": university, "programme": programme,
            "style": style or loc["default_style"], "work_language": language or loc["work_language"],
            "ui": ui, "student_id": student_id,
            "support": {"short_messages": True, "agreed": [], "refuse_cloud": False}}
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    terms = [name.strip()] + name.strip().split() + ([student_id] if student_id else [])
    private = remember_private(terms, root)
    return {"ok": True, "status": "created", "profile": str(p), "country": loc["country"], "style": data["style"],
            "work_language": data["work_language"], "private_terms": private,
            "next": "fieldkit academic setup ASSIGNMENT --deadline DATE --upload DATE"}
