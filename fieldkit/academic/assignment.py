"""One assignment: its two dates, and the one next step.

    fieldkit academic setup FOLDER --deadline DATE --upload DATE [--type T] [--words N] [--module M]
    fieldkit academic next FOLDER
    fieldkit academic accessed FOLDER URL

The dates are ASKED, not guessed: the deadline (when the work is due) and the upload date (when it has to be in the
plagiarism checker, Turnitin or the country's tool; often the same day, sometimes earlier). A date the brief states
is offered as a suggestion inside the question; nothing is set up until the student has answered both. Any of the
seven countries' date forms is read (dates.py) and written back the way the student's country writes it.

next counts down to the EARLIER of the two dates and says one thing to do, in short sentences: the first unticked
step of EXAM_CONFIG.md's workflow. Within a week, it also reminds of what the university agreed (support.agreed in
the profile: extra time, a note on the submission ...).

accessed stamps a web source with the day it is added (the day it was read), once; adding it again keeps the first
date. The line comes back in the profile's referencing style and work language.

State: FOLDER/.assignment.json (inside the student's own work folder, never in the repository).
"""
import datetime as _dt
import json
import os
import re

from . import dates
from . import profile as prof
from . import styles

STATE = ".assignment.json"

ASK = {
    "en": {"deadline": "When is the deadline? (the day the work is due)",
           "upload": "When does it have to be uploaded to {tool}? (often the same day as the deadline; check)",
           "found": " The brief says: {date}. Is that right?",
           "unread": "I could not read {raw!r} as a date. Write it like 28 September 2026 or 2026-09-28."},
    "ro": {"deadline": "Care e termenul limită? (ziua în care trebuie predată lucrarea)",
           "upload": "Până când trebuie urcată în {tool}? (de multe ori aceeași zi cu termenul; verifică)",
           "found": " În cerință scrie: {date}. E corect?",
           "unread": "Nu pot citi {raw!r} ca dată. Scrie așa: 28 septembrie 2026 sau 2026-09-28."},
}


def _profile():
    p = prof.load()
    if p is None:
        raise prof.BadProfile('no student profile yet: fieldkit academic init --name "FULL NAME" --country CODE')
    return p


def _ask(p):
    return ASK.get(p.get("ui"), ASK["en"])


def _fmt(d, p):
    """The country's date form in the country's language; plain '28 September 2026' order for work in another one."""
    lang = p.get("work_language", "en")
    native = prof.locale(p["country"])["work_language"] if p.get("country") else lang
    return dates.fmt(d, p["date_format"] if lang == native else "{day} {month} {year}", lang)


def state_path(folder):
    return os.path.join(os.path.abspath(folder), STATE)


def load_state(folder):
    try:
        with open(state_path(folder), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _save_state(folder, st):
    with open(state_path(folder), "w", encoding="utf-8") as f:
        json.dump(st, f, indent=1, ensure_ascii=False)
        f.write("\n")


def _brief_date(folder):
    if not os.path.isdir(folder):
        return None
    try:
        from .extract_submission_date import find_submission_date_in_exam
        return find_submission_date_in_exam(folder)[0]
    except Exception:
        return None


def setup(folder, deadline=None, upload=None, doc_type=None, words=None, module=None, title=None, today=None):
    """-> {status: ask, questions} until both dates are answered; then the folder is set up."""
    p = _profile()
    q = _ask(p)
    tool = p.get("plagiarism_tool") or "the plagiarism checker"
    questions, unread = [], []
    parsed = {}
    for key, raw in (("deadline", deadline), ("upload", upload)):
        if not (raw or "").strip():
            text = q[key].format(tool=tool)
            found = _brief_date(folder)
            if found:
                text += q["found"].format(date=found)
            questions.append(text)
            continue
        d = dates.parse(raw, p.get("country"))
        if d is None:
            unread.append(q["unread"].format(raw=raw))
        parsed[key] = d
    if questions or unread:
        return {"ok": False, "status": "ask", "questions": unread + questions,
                "next": f'fieldkit academic setup "{folder}" --deadline DATE --upload DATE'}

    from .setup_exam import setup_exam
    label = styles.get(p["style"])["label"] if p.get("style") in styles.names() else p.get("style")
    ok = setup_exam(folder, module_name=module, deadline=_fmt(parsed["deadline"], p), doc_type=doc_type,
                    title=title, student_id=p.get("student_id"), target_words=words, templates=False,
                    verbose=False, upload=_fmt(parsed["upload"], p), style_label=label, plagiarism_tool=tool)
    if not ok:
        return {"ok": False, "status": "refused", "error": f"unknown document type {doc_type!r}",
                "next": "fieldkit academic types"}
    st = load_state(folder) or {"created": (today or _dt.date.today()).isoformat(), "web_sources": []}
    st.update({"deadline": parsed["deadline"].isoformat(), "upload": parsed["upload"].isoformat(),
               "type": doc_type or st.get("type"), "words": words or st.get("words"),
               "module": module or st.get("module")})
    _save_state(folder, st)
    out = {"ok": True, "status": "ready", "folder": os.path.abspath(folder),
           "deadline": _fmt(parsed["deadline"], p), "upload": _fmt(parsed["upload"], p),
           "next": f'fieldkit academic next "{folder}"'}
    if parsed["upload"] > parsed["deadline"]:
        out["check"] = "the upload date is after the deadline: check both in the brief"
    return out


WORKFLOW = "## AI Workflow Status"


def next_step(folder, today=None):
    p = _profile()
    st = load_state(folder)
    if not st or not st.get("deadline"):
        return {"ok": False, "status": "no-dates",
                "say": ["This assignment has no dates yet."],
                "next": f'fieldkit academic setup "{folder}" --deadline DATE --upload DATE'}
    today = today or _dt.date.today()
    deadline, upload = (_dt.date.fromisoformat(st[k]) for k in ("deadline", "upload"))
    first, which = min((upload, "upload"), (deadline, "deadline"))
    days = (first - today).days
    tool = p.get("plagiarism_tool") or "the plagiarism checker"
    what = (f"Deadline and upload to {tool}" if upload == deadline
            else f"Upload to {tool}" if which == "upload" else "Deadline")
    if days < 0:
        say = [f"{what} was {_fmt(first, p)}: {-days} day(s) ago."]
    elif days == 0:
        say = [f"{what} is TODAY ({_fmt(first, p)})."]
    else:
        say = [f"{what}: {_fmt(first, p)}. {days} day(s) left."]
    step = None
    cfg = os.path.join(os.path.abspath(folder), "EXAM_CONFIG.md")
    if os.path.isfile(cfg):
        text = open(cfg, encoding="utf-8").read()
        part = text.split(WORKFLOW, 1)[1] if WORKFLOW in text else text
        m = re.search(r"^- \[ \] (.+)$", part, flags=re.M)
        step = m.group(1).strip() if m else None
    say.append(f"Next: {step}." if step else "Every step is ticked. Check the file, then upload it.")
    if 0 <= days <= 7:
        for item in p["support"].get("agreed") or []:
            say.append(f"Remember: {item}.")
    return {"ok": True, "status": "counting", "days_left": days, "first": which, "date": first.isoformat(),
            "step": step, "say": say,
            "next": "tick the step in EXAM_CONFIG.md when it is done; then fieldkit academic next again"}


def accessed(folder, url, today=None):
    p = _profile()
    st = load_state(folder)
    if st is None:
        raise prof.BadProfile(f'not set up yet: fieldkit academic setup "{folder}" --deadline DATE --upload DATE')
    known = {w["url"]: w for w in st.setdefault("web_sources", [])}
    entry = known.get(url)
    if entry is None:
        entry = {"url": url, "accessed": (today or _dt.date.today()).isoformat()}
        st["web_sources"].append(entry)
        _save_state(folder, st)
    d = _dt.date.fromisoformat(entry["accessed"])
    style = styles.get(p["style"]) if p.get("style") in styles.names() else styles.get("harvard-ctr")
    lang = p.get("work_language", "en")
    template = (style.get("accessed_lang") or {}).get(lang) or style["accessed"]
    return {"ok": True, "url": url, "accessed": entry["accessed"], "new": url not in known,
            "line": template.format(url=url, date=_fmt(d, p)),
            "next": "put the line at the end of the source's entry in the reference list"}
