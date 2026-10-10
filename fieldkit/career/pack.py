"""One application pack per job: job.md, cover_letter.md, checklist.md, pack.json, in packs/<key>/.

The cover letter is assembled, not written: the opening and closing come from templates.yaml, everything else is a
sentence the candidate approved in profile.yaml (letter.pitch, evidence[*].first_person, letter.extra_allowed),
chosen by how many of the advert's words it shares. The AI may cut or reorder; check_letter then refuses any
sentence that is not approved (exit 6), so nothing invented reaches an employer.
"""
import datetime as dt
import json
import re
from pathlib import Path

import yaml

from . import profile as prof
from . import store, takehome

HERE = Path(__file__).parent
PACK_VERSION = 1
MAX_EVIDENCE = 4


def _tpl():
    return yaml.safe_load((HERE / "templates.yaml").read_text(encoding="utf-8"))


def _norm(s):
    return re.sub(r"\s+", " ", s).strip()


def sentences(text):
    return [_norm(s) for s in re.split(r"(?<=[.!?])\s+(?=[A-ZĂÂÎȘȚ\"'(])", _norm(text)) if _norm(s)]


def _words(t):
    return set(re.findall(r"[a-zăâîșț0-9+]{3,}", (t or "").lower()))


def lang_of(p):
    return (p.get("letter") or {}).get("lang", "en")


def approved(p, job, lang=None, tpl=None):
    """Every sentence a letter for this job may carry."""
    lang, tpl = lang or lang_of(p), tpl or _tpl()
    L = tpl["letter"][lang]
    ok = {_norm(L[k].format(title=job.get("title") or "", employer=job.get("employer") or "the company"))
          for k in ("greeting", "opening", "closing", "signoff")}
    texts = list(p["letter"]["pitch"]) + [e["first_person"] for e in p["evidence"]] + \
        list(p["letter"].get("extra_allowed") or [])
    for t in texts:
        ok |= set(sentences(prof.T(t, lang, "letter")))
    return ok


def build_letter(p, job, lang=None, tpl=None):
    lang, tpl = lang or lang_of(p), tpl or _tpl()
    L = tpl["letter"][lang]
    ad = _words(" ".join(str(job.get(k) or "") for k in ("title", "description", "advert_text")))
    ranked = sorted(enumerate(p["evidence"]), key=lambda ie: (-len(_words(prof.T(ie[1]["first_person"], lang)) & ad),
                                                              ie[0]))
    chosen = [e for _, e in ranked[:p["letter"].get("max_evidence", MAX_EVIDENCE)]]
    c = p["contact"]
    parts = [L["greeting"], L["opening"].format(title=job.get("title"), employer=job.get("employer") or "the company"),
             " ".join(prof.T(x, lang, "letter.pitch") for x in p["letter"]["pitch"]),
             " ".join(prof.T(e["first_person"], lang, f"evidence.{e['id']}") for e in chosen),
             L["closing"], L["signoff"], p["name"], f"{c['phone']} | {c['email']}"]
    return "\n\n".join(parts[:5]) + "\n\n" + "\n".join(parts[5:]) + "\n", [e["id"] for e in chosen]


def check_letter(p, job, text, lang=None):
    """-> the sentences in `text` that are not approved (empty list = fine)."""
    lang = lang or lang_of(p)
    ok = approved(p, job, lang)
    c = p["contact"]
    skip = {_norm(p["name"]), _norm(f"{c['phone']} | {c['email']}")}
    bad = []
    for line in text.splitlines():
        line = _norm(line.lstrip("#>*- "))
        if not line or line in skip:
            continue
        bad += [s for s in sentences(line) if s not in ok]
    return bad


def latest_cv(variant, lang, where=None):
    folder = Path(where or store.base()) / "cvs"
    found = sorted(folder.glob(f"CV_{variant}_{lang}_*.docx")) if folder.is_dir() else []
    return str(found[-1]) if found else None


def build(p, job, packs=None, pension=0.0, lang=None):
    from .scoring import salary_status
    lang = lang or lang_of(p)
    d = Path(packs or store.base() / "packs") / store.safe_key(job["key"])
    d.mkdir(parents=True, exist_ok=True)
    letter, used = build_letter(p, job, lang)
    flags = json.loads(job.get("flags") or "[]")
    st = salary_status(job)
    top = job.get("verified_salary_max") or job.get("salary_max") or job.get("salary_min")
    th = takehome.take_home(top, pension) if top and top >= 1000 else None
    cv = latest_cv(job.get("cv"), lang)
    facts = json.loads(job.get("advert_facts") or "null") or {}
    job_md = [f"# {job['title']} - {job.get('employer') or ''}", "",
              f"- Where: {job.get('location') or '-'}", f"- Link: {job.get('apply_url') or job.get('url')}",
              f"- Pay: {job.get('verified_salary_min') or job.get('salary_min') or '?'} - {top or '?'} ({st})",
              f"- Take-home at the top of the range: " + (f"£{th['take_home_month']:,.2f} a month "
                                                         f"({th['tax_year']}, {th['region']})" if th else "-"),
              f"- Score: {job.get('score')}  CV variant: {job.get('cv')}",
              f"- Closes: {facts.get('closes') or job.get('verified_closes') or '-'}", "", "## Flags", ""]
    job_md += [f"- {f}" for f in flags] or ["- none"]
    job_md += ["", "## Advert", "", job.get("advert_text") or job.get("description") or "(not read yet)"]
    render_line = f"none yet - fieldkit cv render --variant {job.get('cv')} --lang {lang}"
    check = ["# Before you apply", "",
             f"- [ ] Pay, closing date and apply link checked on the real advert"
             + ("" if st == "verified" else "  **(not verified yet: fieldkit jobs ingest-advert or verify)**"),
             *[f"- [ ] {f}" for f in flags],
             f"- [ ] CV: {cv or render_line}",
             "- [ ] Cover letter read; after any edit: fieldkit jobs check-letter " + job["key"],
             "- [ ] YOU press Submit on the employer's site. Then: fieldkit jobs status " + job["key"]
             + " submitted_by_user"]
    (d / "job.md").write_text("\n".join(job_md) + "\n", encoding="utf-8")
    (d / "cover_letter.md").write_text(letter, encoding="utf-8")
    (d / "checklist.md").write_text("\n".join(check) + "\n", encoding="utf-8")
    meta = {"version": PACK_VERSION, "built": store.now(), "key": job["key"], "cv_variant": job.get("cv"),
            "cv_file": cv, "lang": lang, "evidence_used": used, "flags": flags, "salary_status": st,
            "verified_at": job.get("verified_at")}
    (d / "pack.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return d


def stale(job, packs=None):
    """A pack is rebuilt when missing, from an older PACK_VERSION, or older than the job's verification."""
    f = Path(packs or store.base() / "packs") / store.safe_key(job["key"]) / "pack.json"
    if not f.is_file():
        return True
    m = json.loads(f.read_text(encoding="utf-8"))
    return m.get("version") != PACK_VERSION or (job.get("verified_at") or "") > m.get("built", "")
