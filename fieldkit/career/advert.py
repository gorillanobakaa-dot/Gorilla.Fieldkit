"""Facts from an advert's full text (advert.yaml): pay and whether it is stated, closing date, contract, years,
clearance, languages, licences, sector backgrounds. The same text always gives the same facts."""
import datetime as dt
import re
from pathlib import Path

import yaml

HERE = Path(__file__).parent


def rules():
    return yaml.safe_load((HERE / "advert.yaml").read_text(encoding="utf-8"))


def _num(s, k):
    v = float(s.replace(",", ""))
    return v * 1000 if k else v


def _date(s):
    s = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", s.strip())
    for f in ("%d %B %Y", "%d %b %Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return dt.datetime.strptime(s, f).date()
        except ValueError:
            pass
    return None


def pay(text, R=None):
    """-> {salary_text, salary_min, salary_max, salary_period, salary_estimated} from the first pay line."""
    R = R or rules()
    P = R["pay"]
    for line in text.splitlines():
        if not re.search(P["line"], line, re.I):
            continue
        amounts = [_num(m.group(1), m.group(2)) for m in re.finditer(P["amount"], line, re.I)]
        if not amounts:
            continue
        period = next((k for k, rx in P["period"].items() if re.search(rx, line, re.I)), None)
        if period is None:
            period = "hour" if max(amounts) < 100 else "year"
        return {"salary_text": line.strip()[:200], "salary_min": min(amounts), "salary_max": max(amounts),
                "salary_period": period, "salary_estimated": bool(re.search(P["estimated"], line, re.I))}
    est = re.search(P["estimated"], text, re.I)
    return {"salary_text": est.group(0) if est else None, "salary_min": None, "salary_max": None,
            "salary_period": None, "salary_estimated": bool(est)}


def facts(text, today=None, tpl=None):
    R = rules()
    today = today or dt.date.today()
    f = pay(text, R)
    m = re.search(R["closes"], text, re.I)
    closes = _date(m.group(2)) if m else None
    f["closes"] = closes.isoformat() if closes else None
    f["closing_soon"] = bool(closes and 0 <= (closes - today).days <= R["closing_soon_days"])
    f["closed"] = bool(closes and closes < today)
    f["contract"] = [k for k, rx in R["contract"].items() if re.search(rx, text, re.I)]
    yrs = [int(x) for x in re.findall(R["years_required"], text, re.I)]
    f["years_required"] = max(yrs) if yrs else None
    f["clearance"] = [k for k, rx in R["clearance"].items() if re.search(rx, text, re.I)]
    langs = []
    for lang in R["languages"]:
        if re.search(R["language_required"].replace("{lang}", lang), text, re.I):
            langs.append(lang)
    f["languages_required"] = langs
    f["sector_background"] = sorted({m.group(1).strip().lower() for m in re.finditer(R["sector_background"], text, re.I)})
    f["no_sponsorship"] = bool(re.search(R["no_sponsorship"], text, re.I))
    if tpl is None:
        tpl = yaml.safe_load((HERE / "templates.yaml").read_text(encoding="utf-8"))
    f["licences_required"] = licences_in(text, tpl)
    return f


def licences_in(text, tpl):
    t = (text or "").lower()
    return list(dict.fromkeys(cat for pat, cat in tpl["licence_words"].items() if re.search(pat, t)))


def pay_is_advertised(f):
    """Verified pay: stated as a figure in the advert, and not marked as an estimate."""
    return f.get("salary_min") is not None and not f.get("salary_estimated")


def annual(v, period, hours_year=2080):
    if v is None:
        return None
    return {"hour": v * hours_year, "day": v * hours_year / 8, "week": v * 52}.get(period, v)
