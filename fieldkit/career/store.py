"""The career data folder and the job tracker (SQLite). Nothing in here is ever published.

    <data>/                 local/career by default ("career": {"dir": ...} in fieldkit.local.json moves it)
      profile.yaml          the candidate's facts (profile.py)
      original/             read-only copies of the CVs given, each with its sha256
      cvs/                  CVs written by fieldkit cv render, dated, never overwritten
      adverts/              the full text of each advert read, by job key
      packs/<key>/          one application pack per job
      reports/              latest.json and report.md of each run
      jobs.sqlite           every job seen, its score, its status and its history
"""
import datetime as dt
import hashlib
import json
import re
import shutil
import sqlite3
import stat
from pathlib import Path

from . import profile as prof

SUBDIRS = ("original", "cvs", "adverts", "packs", "reports")
STATUSES = ("new", "shortlisted", "pack_ready", "submitted_by_user", "interview", "offer", "rejected", "skipped")
CLOSED = ("submitted_by_user", "interview", "offer", "rejected", "skipped")
COLUMNS = {  # name -> SQL type; added to an older database by ALTER TABLE
    "key": "TEXT PRIMARY KEY", "source": "TEXT", "title": "TEXT", "employer": "TEXT", "location": "TEXT",
    "url": "TEXT", "apply_url": "TEXT", "description": "TEXT", "salary_min": "REAL", "salary_max": "REAL",
    "salary_predicted": "INTEGER", "salary_text": "TEXT", "contract": "TEXT", "posted": "TEXT", "found": "TEXT",
    "updated": "TEXT", "score": "INTEGER", "cv": "TEXT", "flags": "TEXT", "excluded": "TEXT", "salary_status": "TEXT",
    "status": "TEXT", "history": "TEXT", "advert_text": "TEXT", "advert_facts": "TEXT", "verified_at": "TEXT",
    "verified_salary_min": "REAL", "verified_salary_max": "REAL", "verified_closes": "TEXT", "verified_note": "TEXT"}


def base(local=None):
    return prof.data_dir(local)


ASK_NAME = {"en": "Whose CV is this? Give the full name exactly as it should appear on the CV.",
            "ro": "Pe ce nume vrei să creez acest CV? Scrie numele complet, exact cum trebuie să apară pe CV."}
STARTER = """# The candidate's profile: the ONLY facts a CV, a score or a cover letter may use (fieldkit/career/profile.py).
# Private: this folder is git-ignored, and the name below is on the private word list, so the pre-commit privacy
# scan refuses any file that carries it. Fill it from the CV and the candidate's answers; never invent a fact.
name: {name}
contact: {{phone: "", email: "", location: ""}}
right_to_work: ""
years_experience: null
trade: null              # driver | security | warehouse | ... (fieldkit/career/trades), or leave null
languages_spoken: []
languages: []            # [{{language: English, level: fluent}}]
licences: []             # [{{category: C+E, detail: "..."}}]
certificates: []
salary_floor: null       # the lowest acceptable pay, a year, in pounds
locations: []
remote_ok: false
exclude_title_patterns: []
experience: []           # [{{title, employer, location, start: YYYY-MM, end: YYYY-MM or present, bullets: [...]}}]
education: []
cv_variants: {{}}          # {{name: {{headline, profile, keywords: [...], skills: [...]}}}}
evidence: []             # [{{id, first_person: "I ...", source: "where this fact comes from"}}]
letter: {{pitch: [], extra_allowed: []}}
queries: []              # [{{what: "job title", where: "town"}}]
"""


def remember_private(terms, root=None):
    """Add terms to fieldkit.local.json's privacy.terms (git-ignored), so the pre-commit scan blocks them."""
    from ..core import settings
    import json as _json
    root = Path(root or settings.ROOT)
    f = root / "fieldkit.local.json"
    if (root / "fieldkit.local.yaml").exists() and not f.exists():
        return {"added": [], "note": "fieldkit.local.yaml in use: add the name to privacy.terms there by hand"}
    data = _json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    have = data.setdefault("privacy", {}).setdefault("terms", [])
    added = [t for t in terms if t and len(t) > 2 and t not in have]
    if added:
        have.extend(added)
        f.write_text(_json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"added": added, "file": str(f)}


def init(where=None, name=None, lang="ro", root=None):
    """The data folder, and a starter profile on the candidate's own name. Without a name: the question to ask."""
    b = Path(where or base())
    prof_file = b / "profile.yaml"
    if not prof_file.exists() and not (name or "").strip():
        return {"ok": False, "status": "ask", "question": ASK_NAME[lang],
                "next": 'ask the question; then fieldkit cv init --name "FULL NAME"'}
    made = []
    for s in SUBDIRS:
        if not (b / s).is_dir():
            (b / s).mkdir(parents=True)
            made.append(s)
    out = {"ok": True, "dir": str(b), "made": made}
    if not prof_file.exists():
        prof_file.write_text(STARTER.format(name=json.dumps(name.strip(), ensure_ascii=False)), encoding="utf-8")
        out["profile"] = str(prof_file)
        out["private_terms"] = remember_private([name.strip()] + name.strip().split(), root)
    out["next"] = "fieldkit cv import CV_FILE   (a read-only copy; the original is never changed)"
    return out


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def import_original(src, where=None, today=None):
    """Copy a CV into original/ (read-only, with its sha256). The same file is never copied twice."""
    src, b = Path(src), Path(where or base())
    for d in SUBDIRS:
        (b / d).mkdir(parents=True, exist_ok=True)
    digest = sha256(src)
    for p in sorted((b / "original").iterdir()):
        if p.is_file() and not p.name.endswith(".sha256") and sha256(p) == digest:
            return {"copy": str(p), "sha256": digest, "new": False, "next": f'fieldkit cv check "{p}"'}
    stamp = (today or dt.date.today()).isoformat()
    dst, n = b / "original" / f"{src.stem}_{stamp}{src.suffix}", 2
    while dst.exists():
        dst, n = b / "original" / f"{src.stem}_{stamp}_{n}{src.suffix}", n + 1
    shutil.copy2(src, dst)
    dst.chmod(stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)
    (dst.parent / (dst.name + ".sha256")).write_text(f"{digest}  {dst.name}\n", encoding="utf-8")
    return {"copy": str(dst), "sha256": digest, "new": True, "next": f'fieldkit cv check "{dst}"'}


def safe_key(key):
    """A job key as a folder or file name: source_id."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", key)


def now():
    return dt.datetime.now().replace(microsecond=0).isoformat()


class Tracker:
    def __init__(self, path=None):
        self.path = Path(path or base() / "jobs.sqlite")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("CREATE TABLE IF NOT EXISTS jobs (key TEXT PRIMARY KEY)")
        have = {r[1] for r in self.db.execute("PRAGMA table_info(jobs)")}
        for c, t in COLUMNS.items():
            if c not in have:
                self.db.execute(f"ALTER TABLE jobs ADD COLUMN {c} {t.replace(' PRIMARY KEY', '')}")
        self.db.commit()

    def get(self, key):
        r = self.db.execute("SELECT * FROM jobs WHERE key=?", (key,)).fetchone()
        return dict(r) if r else None

    def all(self):
        return [dict(r) for r in self.db.execute("SELECT * FROM jobs ORDER BY key")]

    def upsert(self, job):
        """A job from a search. A known job keeps its status, advert text, verification and history."""
        old = self.get(job["key"])
        fresh = {k: job.get(k) for k in ("source", "title", "employer", "location", "url", "description",
                                         "salary_min", "salary_max", "salary_predicted", "salary_text", "contract",
                                         "posted")}
        if old:
            fresh["apply_url"] = old.get("apply_url") or job.get("apply_url")
            sets = ", ".join(f"{k}=?" for k in fresh)
            self.db.execute(f"UPDATE jobs SET {sets}, updated=? WHERE key=?", (*fresh.values(), now(), job["key"]))
            self.db.commit()
            return False
        row = dict(fresh, key=job["key"], apply_url=job.get("apply_url"), found=now(), updated=now(), status="new",
                   history=json.dumps([{"at": now(), "status": "new", "note": f"found on {job.get('source')}"}]))
        cols = ", ".join(row)
        self.db.execute(f"INSERT INTO jobs ({cols}) VALUES ({', '.join('?' * len(row))})", tuple(row.values()))
        self.db.commit()
        return True

    def update(self, key, **fields):
        if not self.get(key):
            raise KeyError(f"no job {key!r} in the tracker (fieldkit jobs list --all)")
        sets = ", ".join(f"{k}=?" for k in fields)
        self.db.execute(f"UPDATE jobs SET {sets}, updated=? WHERE key=?", (*fields.values(), now(), key))
        self.db.commit()

    def set_status(self, key, status, note=""):
        if status not in STATUSES:
            raise ValueError(f"status {status!r}: one of {', '.join(STATUSES)}")
        job = self.get(key)
        if not job:
            raise KeyError(f"no job {key!r} in the tracker (fieldkit jobs list --all)")
        hist = json.loads(job.get("history") or "[]") + [{"at": now(), "status": status, "note": note}]
        self.update(key, status=status, history=json.dumps(hist))

    def note(self, key, note):
        job = self.get(key)
        hist = json.loads(job.get("history") or "[]") + [{"at": now(), "status": job["status"], "note": note}]
        self.update(key, history=json.dumps(hist))
