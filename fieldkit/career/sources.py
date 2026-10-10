"""Job boards, through their official APIs only: Reed and Adzuna.

Hard boundaries, enforced here and by tests (tests/test_career.py):
  - only the hosts in ALLOWED are ever fetched; Indeed (and any site without a public API) is refused, its terms
    forbid automation and the candidate's own account would be at risk;
  - no account is created, no password handled, no CAPTCHA or bot check is got past;
  - nothing is ever submitted: there is no code here that posts an application.

Keys (the person registers for them; free): reed.co.uk/developers (one key), developer.adzuna.com (app id and
key). They go in fieldkit.local.json, never in the repository:
    {"career": {"reed_key": "...", "adzuna_app_id": "...", "adzuna_app_key": "..."}}
or in the environment: REED_API_KEY, ADZUNA_APP_ID, ADZUNA_APP_KEY.
"""
import base64
import json
import os
import urllib.parse
import urllib.request

from ..core import settings

ALLOWED = {"www.reed.co.uk", "api.adzuna.com"}
REFUSED_WORDS = ("indeed.", "linkedin.", "glassdoor.", "totaljobs.", "cv-library.")
REGISTER = {"reed": "https://www.reed.co.uk/developers/jobseeker", "adzuna": "https://developer.adzuna.com/signup"}


class MissingKey(RuntimeError):
    pass


class NotAllowed(RuntimeError):
    pass


def keys(local=None, env=None):
    local = settings.local_settings() if local is None else local
    env = os.environ if env is None else env
    c = local.get("career") or {}
    return {"reed_key": c.get("reed_key") or env.get("REED_API_KEY"),
            "adzuna_app_id": c.get("adzuna_app_id") or env.get("ADZUNA_APP_ID"),
            "adzuna_app_key": c.get("adzuna_app_key") or env.get("ADZUNA_APP_KEY")}


def check_url(url):
    host = urllib.parse.urlsplit(url).hostname or ""
    if any(w in host for w in REFUSED_WORDS) or host not in ALLOWED:
        raise NotAllowed(f"{host}: not an official job-board API this harness may call ({', '.join(sorted(ALLOWED))}). "
                         "Read such a site in a browser and paste the advert: fieldkit jobs add / ingest-advert")
    return url


def fetch_json(url, headers=None, opener=None):
    check_url(url)
    req = urllib.request.Request(url, headers={"User-Agent": "fieldkit-career/1", **(headers or {})})
    if opener:
        return opener(req)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


# -- Reed -------------------------------------------------------------------------------------------------------------
def reed_url(what, where=None, distance=None, minimum_salary=None, take=100):
    q = {"keywords": what, "resultsToTake": take}
    if where:
        q["locationName"] = where
    if distance:
        q["distanceFromLocation"] = distance
    if minimum_salary:
        q["minimumSalary"] = int(minimum_salary)
    return "https://www.reed.co.uk/api/1.0/search?" + urllib.parse.urlencode(q)


def reed(query, k, floor=None, opener=None):
    if not k.get("reed_key"):
        raise MissingKey(f"Reed: no key. Register (free) at {REGISTER['reed']}, then put it in fieldkit.local.json "
                         '{"career": {"reed_key": "..."}}')
    auth = base64.b64encode(f"{k['reed_key']}:".encode()).decode()
    data = fetch_json(reed_url(query["what"], query.get("where"), query.get("distance_miles"), floor),
                      {"Authorization": f"Basic {auth}"}, opener)
    out = []
    for r in data.get("results", []):
        out.append({"key": f"reed:{r['jobId']}", "source": "reed", "title": r.get("jobTitle"),
                    "employer": r.get("employerName"), "location": r.get("locationName"),
                    "url": r.get("jobUrl"), "apply_url": r.get("jobUrl"), "description": r.get("jobDescription"),
                    "salary_min": r.get("minimumSalary"), "salary_max": r.get("maximumSalary"),
                    "salary_predicted": 0, "salary_text": None, "posted": r.get("date"), "contract": None})
    return out


# -- Adzuna -----------------------------------------------------------------------------------------------------------
def adzuna_url(app_id, app_key, what, where=None, distance=None, salary_min=None, page=1, per_page=50):
    q = {"app_id": app_id, "app_key": app_key, "what": what, "results_per_page": per_page,
         "content-type": "application/json"}
    if where:
        q["where"] = where
    if distance:
        q["distance"] = round(float(distance) * 1.609)          # Adzuna takes kilometres
    if salary_min:
        q["salary_min"] = int(salary_min)
    return f"https://api.adzuna.com/v1/api/jobs/gb/search/{page}?" + urllib.parse.urlencode(q)


def adzuna(query, k, floor=None, opener=None):
    if not (k.get("adzuna_app_id") and k.get("adzuna_app_key")):
        raise MissingKey(f"Adzuna: no app id and key. Register (free) at {REGISTER['adzuna']}, then put them in "
                         'fieldkit.local.json {"career": {"adzuna_app_id": "...", "adzuna_app_key": "..."}}')
    data = fetch_json(adzuna_url(k["adzuna_app_id"], k["adzuna_app_key"], query["what"], query.get("where"),
                                 query.get("distance_miles"), floor), opener=opener)
    out = []
    for r in data.get("results", []):
        out.append({"key": f"adzuna:{r['id']}", "source": "adzuna", "title": r.get("title"),
                    "employer": (r.get("company") or {}).get("display_name"),
                    "location": (r.get("location") or {}).get("display_name"), "url": r.get("redirect_url"),
                    "apply_url": None, "description": r.get("description"), "salary_min": r.get("salary_min"),
                    "salary_max": r.get("salary_max"),
                    "salary_predicted": 1 if str(r.get("salary_is_predicted")) == "1" else 0,
                    "salary_text": None, "posted": r.get("created"),
                    "contract": " ".join(x for x in (r.get("contract_type"), r.get("contract_time")) if x) or None})
    return out


SOURCES = {"reed": reed, "adzuna": adzuna}


def adzuna_histogram(k, what, where=None, opener=None):
    """Adzuna's salary histogram for a title: {lower bound: vacancies}."""
    if not (k.get("adzuna_app_id") and k.get("adzuna_app_key")):
        raise MissingKey(f"Adzuna: no app id and key. Register (free) at {REGISTER['adzuna']}")
    q = {"app_id": k["adzuna_app_id"], "app_key": k["adzuna_app_key"], "what": what,
         "content-type": "application/json"}
    if where:
        q["where"] = where
    data = fetch_json("https://api.adzuna.com/v1/api/jobs/gb/histogram?" + urllib.parse.urlencode(q), opener=opener)
    return {int(a): int(b) for a, b in (data.get("histogram") or {}).items()}
