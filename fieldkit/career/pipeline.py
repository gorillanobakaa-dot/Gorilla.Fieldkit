"""The job-hunt cycle and the steps around it. Every step goes through the tracker, so a run can be repeated.

    run:            search every query on every source with a key -> upsert (known jobs keep their status and
                    advert) -> rescore all -> shortlist -> build missing or stale packs -> reports/latest.json
    ingest_advert:  the advert's full text -> facts; pay recorded as verified ONLY if the advert states it
    verify:         pay / closing date / apply link confirmed another way (employer site, recruiter), with its source

Exit codes (report["exit_code"]): 0 clean; 3 no source could search; 4 no source has a key; 5 finished with
search errors.
"""
import json
from pathlib import Path

from . import advert, pack, scoring, sources, store
from . import profile as prof


def rescore(p, t):
    W = scoring.weights()
    n = 0
    for job in t.all():
        r = scoring.score(job, p, W)
        t.update(job["key"], score=r["score"], cv=r["cv"], flags=json.dumps(r["flags"]), excluded=r["excluded"],
                 salary_status=r["salary_status"])
        n += 1
    return n


def shortlist(p, t, top=8):
    floor = p.get("shortlist_min_score") or scoring.weights()["shortlist_min_score"]
    rows = [j for j in t.all() if not j["excluded"] and j["status"] not in store.CLOSED and (j["score"] or 0) >= floor]
    return sorted(rows, key=lambda j: (-(j["score"] or 0), j["key"]))[:top]


def run(p, t, top=8, do_search=True, opener=None, pension=0.0, where=None):
    where = Path(where or store.base())
    report = {"schema": "fieldkit.career.report/1", "at": store.now(), "searched": 0, "results_seen": 0,
              "new_jobs": 0, "errors": [], "human_actions": []}
    if do_search:
        k = sources.keys()
        usable = [s for s in sources.SOURCES if (s == "reed" and k["reed_key"]) or
                  (s == "adzuna" and k["adzuna_app_id"] and k["adzuna_app_key"])]
        for s in sources.SOURCES:
            if s not in usable:
                report["human_actions"].append(f"register for a free {s} API key: {sources.REGISTER[s]}, then add it "
                                               "to fieldkit.local.json (see fieldkit jobs --help)")
        for q in p.get("queries") or []:
            for s in usable:
                report["searched"] += 1
                try:
                    found = sources.SOURCES[s](q, k, p.get("salary_floor"), opener)
                except Exception as e:                    # one failed source never stops the others
                    report["errors"].append({"source": s, "query": q["what"], "error": str(e)[:300]})
                    continue
                report["results_seen"] += len(found)
                report["new_jobs"] += sum(t.upsert(j) for j in found)
        if not p.get("queries"):
            report["human_actions"].append("the profile has no queries: add queries: [{what, where}] to profile.yaml")
    report["rescored"] = rescore(p, t)
    sl = shortlist(p, t, top)
    for j in sl:
        built = False
        if pack.stale(j, where / "packs"):
            pack.build(p, j, where / "packs", pension)
            built = True
            if j["status"] == "new":
                t.set_status(j["key"], "pack_ready", "pack built by run")
        j["pack_built_now"] = built
    report["shortlist"] = [{"key": j["key"], "score": j["score"], "title": j["title"], "employer": j["employer"],
                            "location": j["location"], "salary_min": j["salary_min"], "salary_max": j["salary_max"],
                            "salary_status": j["salary_status"], "cv": j["cv"], "flags": json.loads(j["flags"] or "[]"),
                            "url": j["apply_url"] or j["url"], "status": t.get(j["key"])["status"],
                            "pack_dir": str(where / "packs" / store.safe_key(j["key"])),
                            "pack_built_now": j["pack_built_now"]} for j in sl]
    for j in report["shortlist"]:
        if j["salary_status"] != "verified":
            report["human_actions"].append(f"{j['key']}: open the advert, save its text, then "
                                           f"fieldkit jobs ingest-advert {j['key']} --text-file FILE")
    if not do_search or not p.get("queries"):
        code = 0
    elif not report["searched"]:
        code = 4                                          # no source has a key
    elif report["errors"] and not report["results_seen"]:
        code = 3                                          # no source could search
    else:
        code = 5 if report["errors"] else 0
    report["exit_code"] = code
    rd = where / "reports"
    rd.mkdir(parents=True, exist_ok=True)
    (rd / "latest.json").write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    md = [f"# Job hunt {report['at']}", "", f"searched {report['searched']}, seen {report['results_seen']}, "
          f"new {report['new_jobs']}, errors {len(report['errors'])}", "", "| score | title | employer | pay | cv |",
          "|---|---|---|---|---|"]
    md += [f"| {j['score']} | [{j['title']}]({j['url']}) | {j['employer']} | {j['salary_min'] or '?'}-"
           f"{j['salary_max'] or '?'} {j['salary_status']} | {j['cv']} |" for j in report["shortlist"]]
    md += ["", "## For the person to do", ""] + [f"- {a}" for a in report["human_actions"]]
    (rd / "report.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    report["report_md"] = str(rd / "report.md")
    return report


def ingest_advert(p, t, key, text, apply_url=None, where=None, today=None):
    where = Path(where or store.base())
    job = t.get(key)
    if not job:
        raise KeyError(f"no job {key!r} in the tracker")
    f = advert.facts(text, today=today)
    arch = where / "adverts" / f"{store.safe_key(key)}.txt"
    arch.parent.mkdir(parents=True, exist_ok=True)
    arch.write_text(text, encoding="utf-8")
    t.update(key, advert_text=text, advert_facts=json.dumps(f), **({"apply_url": apply_url} if apply_url else {}))
    if advert.pay_is_advertised(f):
        lo, hi = (advert.annual(f["salary_min"], f["salary_period"]), advert.annual(f["salary_max"], f["salary_period"]))
        t.update(key, verified_at=store.now(), verified_salary_min=lo, verified_salary_max=hi,
                 verified_closes=f["closes"], verified_note=f"pay from the advert's own text: {f['salary_text']}")
        verdict = f"pay VERIFIED from the advert: {f['salary_text']}"
    else:
        why = "an estimate" if f["salary_estimated"] else "not stated"
        t.note(key, f"advert read: pay {f['salary_text'] or '-'} - {why}, NOT verified")
        verdict = f"pay NOT verified ({why})"
    rescore(p, t)
    job = t.get(key)
    rebuilt = False
    if not job["excluded"] and (where / "packs" / store.safe_key(key)).exists():
        pack.build(p, job, where / "packs")
        rebuilt = True
    return {"key": key, "title": job["title"], "employer": job["employer"], "verdict": verdict, "facts": f,
            "score": job["score"], "flags": json.loads(job["flags"] or "[]"), "excluded": job["excluded"],
            "pack_rebuilt": rebuilt, "next": f"fieldkit jobs apply {key}" if not job["excluded"] else
            f"fieldkit jobs status {key} skipped --note \"{job['excluded']}\""}


def verify(p, t, key, salary_min=None, salary_max=None, closes=None, apply_url=None, note="", where=None):
    if not note:
        raise ValueError("--note: where the figures come from (employer site, recruiter's e-mail on a date); "
                         "an estimate is never verified")
    fields = {"verified_at": store.now(), "verified_note": note}
    if salary_min is not None:
        fields["verified_salary_min"] = salary_min
    if salary_max is not None:
        fields["verified_salary_max"] = salary_max
    if closes:
        fields["verified_closes"] = closes
    if apply_url:
        fields["apply_url"] = apply_url
    t.update(key, **fields)
    t.note(key, f"verified: {note}")
    rescore(p, t)
    job = t.get(key)
    where = Path(where or store.base())
    if not job["excluded"] and (where / "packs" / store.safe_key(key)).exists():
        pack.build(p, job, where / "packs")
    return {"key": key, "score": job["score"], "salary_status": job["salary_status"], "excluded": job["excluded"],
            "next": f"fieldkit jobs apply {key}"}


def add_manual(t, jobs):
    """Jobs found by hand on sites without an API (Civil Service Jobs, an employer's own page): key manual:<n>."""
    import hashlib
    n_new = 0
    for j in jobs:
        if not j.get("url") or not j.get("title"):
            raise ValueError(f"every job needs title and url: {j}")
        key = "manual:" + hashlib.sha1(j["url"].strip().encode()).hexdigest()[:10]
        n_new += t.upsert(dict(j, key=key, source="manual", apply_url=j.get("apply_url") or j["url"],
                               salary_predicted=0))
    return n_new
