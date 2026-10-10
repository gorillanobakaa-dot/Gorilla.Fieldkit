"""Score one vacancy against the candidate's profile: 0-100, the CV variant to send, flags to check, and why it is
excluded if it is. Fixed rules (scoring.yaml) plus the candidate's own (profile.yaml); no judgement here."""
import json
import re
from pathlib import Path

import yaml

from . import advert

HERE = Path(__file__).parent


def weights():
    return yaml.safe_load((HERE / "scoring.yaml").read_text(encoding="utf-8"))


def _templates():
    return yaml.safe_load((HERE / "templates.yaml").read_text(encoding="utf-8"))


def _hits(words, text):
    t = (text or "").lower()
    return [w for w in words if re.search(r"(?<!\w)" + re.escape(str(w).lower()) + r"(?!\w)", t)]


def salary_status(job):
    if job.get("verified_salary_min") is not None:
        return "verified"
    if job.get("salary_min") is None:
        return "unknown"
    return "estimate" if job.get("salary_predicted") else "stated"


def score(job, p, W=None, tpl=None):
    W, tpl = W or weights(), tpl or _templates()
    facts = json.loads(job.get("advert_facts") or "null") or {}
    title, text = job.get("title") or "", " ".join(str(job.get(k) or "") for k in ("description", "advert_text"))
    flags, excluded = [], None

    # which CV, and how relevant
    best, rel = None, -1
    R = W["relevance"]
    for name, v in p["cv_variants"].items():
        th, xh = _hits(v["keywords"], title), _hits(v["keywords"], text)
        r = min(R["title_max"], R["title_hit"] * len(th)) + min(R["text_max"], R["text_hit"] * len(xh))
        if any(re.search(rx, title, re.I) for rx in v.get("title_patterns") or []):
            r += R["pattern_bonus"]
        if r > rel:
            best, rel = name, r
    rel = min(rel, R["max"])

    # where
    loc = 0
    where = (job.get("location") or "").lower()
    if any(l.lower() in where for l in p.get("locations") or []):
        loc = W["location"]["match"]
    elif p.get("remote_ok") and re.search(r"\bremote\b", f"{title} {where} {text}", re.I):
        loc = W["location"]["remote"]
    else:
        flags.append(f"outside the chosen areas ({job.get('location') or 'no location'})")

    # pay, against the floor
    st = salary_status(job)
    floor = float(p.get("salary_floor") or 0)
    if st == "verified":
        top = job.get("verified_salary_max") or job.get("verified_salary_min")
    else:
        top = job.get("salary_max") or job.get("salary_min")
    yearly = top if (top or 0) >= 1000 else advert.annual(top, "hour", tpl["hours_year"])
    if st == "unknown":
        pay = W["pay"]["unknown"]
        flags.append("pay not stated")
    elif floor and yearly is not None and yearly < floor:
        pay = W["pay"]["below"]
        flags.append(f"pay £{yearly:,.0f} a year is below the floor £{floor:,.0f}")
    else:
        pay = W["pay"][st]
        if st == "estimate":
            flags.append("pay is the job board's ESTIMATE: read the advert (fieldkit jobs ingest-advert)")

    # what the advert asks for
    F = W["fit"]
    fit = F["base"]
    held = set()
    for lic in p.get("licences") or []:
        c = lic.get("category") if isinstance(lic, dict) else str(lic)
        held |= set(tpl["covers"].get(c, [c]))
    need = facts.get("licences_required") or advert.licences_in(f"{title} {text}", tpl)
    missing = [n for n in need if n not in held]
    if missing and (p.get("licences") or p.get("trade") == "driver"):
        excluded = f"needs licence {'/'.join(missing)}, not held"
    yr = facts.get("years_required")
    if yr and p.get("years_experience") is not None and yr > float(p["years_experience"]):
        fit += F["years_short"]
        flags.append(f"asks for {yr} years; the profile says {p['years_experience']}")
    held_c = {c.upper() for c in p.get("clearances") or []}
    for c in facts.get("clearance") or []:
        if c.upper() not in held_c:
            fit += F["clearance_missing"]
            flags.append(f"{c} needed: check it can be obtained")
    for s in facts.get("sector_background") or []:
        fit += F["background"]
        flags.append(f"asks for a background in {s}: check the profile has it")
    spoken = {l.lower() for l in p.get("languages_spoken") or []}
    for lang in facts.get("languages_required") or []:
        if lang.lower() not in spoken:
            excluded = excluded or f"needs {lang}, not spoken"
    if facts.get("closed"):
        excluded = excluded or f"closed on {facts['closes']}"
    elif facts.get("closing_soon"):
        flags.append(f"closes soon: {facts['closes']}")
    if facts.get("no_sponsorship") and p.get("needs_sponsorship"):
        excluded = excluded or "no visa sponsorship"
    for rx in p.get("exclude_title_patterns") or []:
        if re.search(rx, title, re.I):
            excluded = excluded or f"title matches the excluded pattern {rx!r}"
    total = max(0, min(100, rel + loc + pay + max(0, fit)))
    return {"score": 0 if excluded else total, "cv": best, "flags": flags, "excluded": excluded,
            "salary_status": st, "parts": {"relevance": rel, "location": loc, "pay": pay, "fit": max(0, fit)}}
