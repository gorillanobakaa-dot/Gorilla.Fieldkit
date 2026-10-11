"""The academic record: the student's marks and what markers keep saying, in one place.

    fieldkit academic record add FILE|FOLDER [--apply]     read feedback sheets (.txt .md .docx .pdf): propose, then write
    fieldkit academic record set MODULE --mark N [--title T]   a mark only the student knows (from the programme page)
    fieldkit academic record link PATH... [--feedback FOLDER] [--apply]   which module each piece of work belongs to
    fieldkit academic record show                          the whole record
    fieldkit academic record brief [WHAT]                  the three things to watch for before writing the next piece

Markers repeat themselves. Written down and counted, the repetitions are the shortest route to a better mark, and
each one names the check that catches it next time. Nothing is invented: every fault recorded carries the marker's
own sentence as its evidence, and nothing is written until --apply.

Generalised from the academic harness built for one UK student (academic_record.py, 2026-10-10; that harness stays his
and unchanged): the grade bands come from the student's country (locales/*.yaml, `grades`), the record lives in the
private academic folder (local/academic/record.json, never committed) with a readable copy beside it, and a cloud
folder is refused. The fault patterns read feedback written in English; feedback in another language still gives the
mark, the criteria and the similarity score, with an empty fault list rather than a guess.
"""
import json
import os
import re
from datetime import datetime

from . import profile as prof

RECORD_JSON = "record.json"
RECORD_MD = "ACADEMIC RECORD.md"
MODULE_RX = re.compile(r"\b([A-Z]{2,6})\s?(\d{3,4})\b")

# What markers keep saying: key, label, patterns, the check that catches it (None where only a person can judge)
FAULTS = [
    ("citation_not_supporting", "Citations placed beside claims the source does not support",
     [r"citations were added next to content that they did not actually support",
      r"citation[s]?[^.]{0,60}(?:do(?:es)? not|did not)[^.]{0,40}support",
      r"reference is directly relevant to the idea"],
     "fieldkit academic search FOLDER: find the page that says it, before citing"),
    ("paraphrase_too_close", "Paraphrase too close to the source's wording",
     [r"paraphras\w+[^.]{0,80}too close", r"too close to the (?:wording|original)",
      r"remains too close to standard formulations"],
     "fieldkit academic plagiarism FILE --sources FOLDER"),
    ("academic_ownership", "Writing that sounds ready-made rather than the student's own thinking",
     [r"academic ownership", r"ownership and control", r"polished, generalised academic phrasing",
      r"independent argument", r"originality of the claims"], None),
    ("descriptive_not_critical", "Description where analysis was asked for",
     [r"descriptive", r"rather than reflection", r"read more like descriptions", r"lacks? (?:critical|analytical)",
      r"more critical interpretation", r"identification of issues is not the same as critical analysis",
      r"critical depth", r"criticality"], "fieldkit academic structure FILE --type T, and the tutor"),
    ("referencing_inconsistent", "Citations and reference list that do not match: different forms, duplicates",
     [r"consistency is required between in-text citations and the reference list",
      r"referencing is (?:inconsistent|present but not technically secure)", r"inconsistenc\w+ in formatting",
      r"creating duplication for the same source", r"not formatted correctly", r"formatted using \w+ Harvard"],
     "fieldkit academic refs FILE, and fieldkit academic style FILE"),
    ("missing_citations", "Claims without a citation",
     [r"missing in-text citations", r"failure to cite", r"always include a citation and a reference"],
     "fieldkit academic refs FILE"),
    ("theory_not_evaluated", "Theory applied but not evaluated: no limits, no comparison",
     [r"insufficient evaluation of (?:their|its) limitations", r"no sustained comparison",
      r"competent but not critical", r"unpacking studies", r"beyond referencing"], None),
    ("weak_introduction", "An introduction with no clear aim and no plan of the work",
     [r"introduction[^.]{0,80}(?:could be|more) (?:slightly more )?(?:structured|effective)",
      r"clearly state the aim", r"thesis statement"], "fieldkit academic structure FILE --type T"),
    ("weak_conclusion", "A conclusion that summarises but does not discuss limits or barriers",
     [r"conclusion[^.]{0,60}lacks", r"barriers to implementation"], None),
    ("slides_overloaded", "Slides overloaded with text, too few short points",
     [r"excessive written content", r"large blocks of text", r"visual hierarchy", r"concise bullet points"],
     "fieldkit academic structure FILE --type presentation"),
    ("delivery_pacing", "Presentation: pace and reliance on notes",
     [r"pace your presentation", r"reducing reliance on notes", r"complete according to the time provided",
      r"balanc\w+ your time"], None),
    ("vague_action_plan", "Plans for the future that are vague, with no concrete steps",
     [r"could be more specific", r"clearer actionable steps", r"more specific about how you.{0,5}ll improve"], None),
    ("source_quality", "Few, old or weak sources",
     [r"up-to-date journal articles", r"credible sources", r"use credible sources to support your claims"],
     "fieldkit academic check: identifiers online"),
    ("writing_clarity", "Long, repetitive or unfinished sentences",
     [r"sentences are (?:often )?(?:overly |quite )?long", r"not fully completed", r"repetitive",
      r"breaking them into shorter"], None),
]

SIMILARITY = re.compile(r"(\d{1,2})\s*%\s*(?:similarity|which)|similarity score[^.\n%]{0,24}?(\d{1,2})\s*%", re.I)
MARKER = re.compile(r"marker(?:\s*(?:id|code))?\s*:?\s*([A-Z]{2,6}\d{2,4})", re.I)
DATE_RX = [re.compile(r"date\s*:?\s*(\d{1,2}[/.]\d{1,2}[/.]\d{2,4})", re.I),
           re.compile(r"date\s*:?\s*(\d{1,2}\s+[A-Z][a-z]+\s+\d{4})", re.I),
           re.compile(r"\b(\d{1,2}[/.]\d{1,2}[/.]\d{4})\b"), re.compile(r"\b(\d{1,2}\s+[A-Z][a-z]+\s+\d{4})\b")]
TOTAL = [re.compile(r"total mark\s*:?\s*(\d{1,3}(?:[.,]\d)?)", re.I),
         re.compile(r"overall marks?\s*:?\s*(\d{1,3}(?:[.,]\d)?)", re.I),
         re.compile(r"proposed grade[^\d]{0,40}(\d{1,3})\s*%", re.I),
         re.compile(r"\bgrade\s*:?\s*(\d{1,3}(?:[.,]\d)?)\s*%?", re.I)]
CRITERION = re.compile(r"^\s*([A-Z][A-Za-z ,/&'-]{3,60}?)\s*:\s*(\d{1,3})\s*/\s*(\d{1,3})\s*$", re.M)


# -- grades ---------------------------------------------------------------------------------------------------------
def grades(profile=None):
    p = profile if profile is not None else (prof.load() or {})
    try:
        return prof.locale(p.get("country") or "uk").get("grades") or {}
    except prof.BadProfile:
        return {}


def band(mark, g):
    """The band a mark falls in, on the country's scale (Germany: smaller is better)."""
    if mark is None or not g:
        return None
    bands = g["bands"]
    if g.get("higher_is_better", True):
        return next((name for lowest, name in bands if mark >= lowest), bands[-1][1])
    return next((name for highest, name in bands if mark <= highest), bands[-1][1])


def passed(mark, g):
    if mark is None or not g:
        return None
    return mark >= g["pass"] if g.get("higher_is_better", True) else mark <= g["pass"]


# -- reading a feedback sheet ---------------------------------------------------------------------------------------
def _first(patterns, text):
    for rx in patterns:
        m = rx.search(text)
        if m:
            return m.group(1)
    return None


def _sentence_around(text, start, end):
    left = max(text.rfind(".", 0, start), text.rfind("\n", 0, start))
    right = min([p for p in (text.find(".", end), text.find("\n", end)) if p != -1] or [len(text)])
    return re.sub(r"\s+", " ", text[left + 1:right + 1]).strip()[:240]


def find_faults(text):
    found = []
    for key, label, patterns, check in FAULTS:
        for pattern in patterns:
            m = re.search(pattern, text, re.I)
            if m:
                found.append({"fault": key, "label": label, "evidence": _sentence_around(text, m.start(), m.end()),
                              "check": check})
                break
    return found


def module_of(name):
    m = MODULE_RX.search(os.path.basename(name or "").upper())
    return (m.group(1) + m.group(2)) if m else None


def parse_feedback(text, filename="", profile=None):
    """Everything a feedback sheet says, as a proposal. Nothing is written."""
    g = grades(profile)
    module = module_of(filename)
    raw = _first(TOTAL, text)
    mark = float(raw.replace(",", ".")) if raw else None
    if mark is not None and mark.is_integer():
        mark = int(mark)
    sim = SIMILARITY.search(text)
    criteria = {}
    for name, got, out_of in CRITERION.findall(text):
        if len(name.strip()) > 2 and int(out_of) > 0:
            criteria[name.strip()] = [int(got), int(out_of)]
    mm = MARKER.search(text)
    marker = mm.group(1).upper() if mm else None
    if marker and module and marker == module:          # a marker code is not the module code
        marker = None
    return {"module": module, "file": os.path.basename(filename), "marker": marker, "date": _first(DATE_RX, text),
            "mark": mark, "band": band(mark, g), "passed": passed(mark, g), "criteria": criteria,
            "similarity_percent": int(sim.group(1) or sim.group(2)) if sim else None,
            "conduct_breach": bool(re.search(r"breach\w*[^.]{0,60}academic conduct|academic conduct policy due to",
                                             text, re.I)),
            "marks_deducted": bool(re.search(r"marks? (?:have been |were )?deducted", text, re.I)),
            "faults": find_faults(text), "words": len(text.split())}


def read_text(path):
    if path.lower().endswith((".txt", ".md")):
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    from .read_sources import read_file
    text = read_file(path)
    text = text[0] if isinstance(text, tuple) else text
    if text is None:
        raise ValueError(f"cannot read {os.path.basename(path)}")
    return text


# -- the record ------------------------------------------------------------------------------------------------------
def folder(where=None):
    return str(where or prof.base())


def load(where=None):
    p = os.path.join(folder(where), RECORD_JSON)
    if not os.path.isfile(p):
        return {"entries": [], "updated": None}
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def save(record, where=None, now=None):
    from .workspace import refuse_cloud_save
    d = folder(where)
    refuse_cloud_save(d, "the record of your marks")
    os.makedirs(d, exist_ok=True)
    record["updated"] = (now or datetime.now()).strftime("%Y-%m-%d %H:%M")
    with open(os.path.join(d, RECORD_JSON), "w", encoding="utf-8") as f:
        json.dump(record, f, indent=1, ensure_ascii=False)
    with open(os.path.join(d, RECORD_MD), "w", encoding="utf-8") as f:
        f.write("\n".join(markdown(record)) + "\n")
    return os.path.join(d, RECORD_JSON)


def add_entry(record, entry):
    """Add or replace an entry, keyed by module (or file name)."""
    key = entry.get("module") or entry.get("file")
    record["entries"] = [e for e in record["entries"] if (e.get("module") or e.get("file")) != key] + [entry]
    record["entries"].sort(key=lambda e: e.get("module") or e.get("file") or "")
    return record


def fault_counts(record):
    counts = {}
    for e in record["entries"]:
        for f in e.get("faults", []):
            c = counts.setdefault(f["fault"], {"label": f["label"], "count": 0, "modules": [], "check": f["check"]})
            c["count"] += 1
            c["modules"].append(e.get("module") or e["file"])
    return dict(sorted(counts.items(), key=lambda kv: -kv[1]["count"]))


def add(path, apply=False, where=None, profile=None):
    paths = ([os.path.join(path, f) for f in sorted(os.listdir(path))
              if f.lower().endswith((".txt", ".md", ".docx", ".pdf"))] if os.path.isdir(path)
             else [path] if os.path.isfile(path) else [])
    if not paths:
        raise FileNotFoundError(f"no feedback sheets (.txt .md .docx .pdf) in {path}")
    record = load(where)
    entries, unread = [], []
    for p in paths:
        try:
            entries.append(parse_feedback(read_text(p), p, profile))
        except ValueError as e:
            unread.append(str(e))
    if apply:
        for e in entries:
            add_entry(record, e)
        written = save(record, where)
    return {"ok": not unread, "entries": entries, "unread": unread, "written": written if apply else None,
            "next": "fieldkit academic record show" if apply else
                    f'if the proposal is right: fieldkit academic record add "{path}" --apply'}


def set_mark(module, mark=None, title=None, where=None, profile=None, source="programme page"):
    record = load(where)
    module = module.upper().replace(" ", "")
    entry = next((e for e in record["entries"] if e.get("module") == module), None)
    if entry is None:
        entry = {"module": module, "file": "", "faults": [], "criteria": {}}
        record["entries"].append(entry)
    g = grades(profile)
    if mark is not None:
        entry.update(mark=mark, band=band(mark, g), passed=passed(mark, g), mark_source=source)
    if title:
        entry["title"] = title
    record["entries"].sort(key=lambda e: e.get("module") or e.get("file") or "")
    return {"ok": True, "entry": entry, "written": save(record, where), "next": "fieldkit academic record show"}


def show(where=None, profile=None):
    record = load(where)
    g = grades(profile)
    marks = [e["mark"] for e in record["entries"] if e.get("mark") is not None]
    return {"ok": True, "entries": record["entries"], "faults": fault_counts(record), "scale": g.get("scale"),
            "pass_mark": g.get("pass"), "average": round(sum(marks) / len(marks), 1) if marks else None,
            "conduct": [e.get("module") or e["file"] for e in record["entries"] if e.get("conduct_breach")],
            "deducted": [e.get("module") or e["file"] for e in record["entries"] if e.get("marks_deducted")],
            "next": "fieldkit academic record brief" if record["entries"] else
                    'fieldkit academic record add "FOLDER OF FEEDBACK"'}


def brief(what="", where=None):
    record = load(where)
    counts = fault_counts(record)
    top = []
    for key, info in list(counts.items())[:3]:
        quote = next((f["evidence"] for e in record["entries"] for f in e.get("faults", []) if f["fault"] == key), "")
        top.append({"label": info["label"], "times": info["count"], "marker_said": quote, "check": info["check"]})
    conduct = [e.get("module") for e in record["entries"] if e.get("conduct_breach")]
    return {"ok": True, "for": what, "watch": top, "conduct": [c for c in conduct if c],
            "next": "write; then fieldkit academic check FOLDER DRAFT" if top else
                    'the record is empty: fieldkit academic record add "FOLDER OF FEEDBACK"'}


# -- which module a piece of work belongs to ------------------------------------------------------------------------
STOP = {"and", "the", "for", "with", "using", "into", "from", "module", "assignment", "level", "introduction"}
KINDS = [("rubric", [r"marking grid", r"marking criteria", r"rubric", r"assessment criteria"]),
         ("brief", [r"assignment brief", r"assignment guidelines", r"submission date", r"you are tasked to",
                    r"your task is to"]),
         ("handbook", [r"module handbook", r"module leader", r"teaching schedule", r"module summary"]),
         ("template", [r"\[insert", r"\[your ", r"template"])]


def classify_kind(text, filename):
    """The student's own work, or course material about the assignment."""
    hay = (os.path.basename(filename) + "\n" + text[:120000]).lower()
    for kind, patterns in KINDS:
        hits = sum(1 for p in patterns if re.search(p, hay, re.I))
        if hits >= 2 or (hits and kind in ("rubric", "handbook") and re.search(r"learning outcome", hay, re.I)):
            return kind
    return "own work"


def _title_words(title):
    return {w for w in re.findall(r"[a-z]{4,}", (title or "").lower()) if w not in STOP}


def match(text, filename, modules):
    """(module, confidence, evidence) for one file. A module code in the file settles it; otherwise the words of the
    module titles, each weighted by how rare it is across the titles. A weak match is 'unsure', never a guess."""
    hay = (os.path.basename(filename) + " " + text[:200000]).lower()
    words = {code: _title_words(title) for code, title in modules}
    seen = {}
    for ws in words.values():
        for w in ws:
            seen[w] = seen.get(w, 0) + 1
    scores = []
    for code, _ in modules:
        score, evidence = 0.0, []
        if code and code.lower() in hay:
            score += 10
            evidence.append(f"the code {code} is in the file")
        total = sum(1.0 / seen[w] for w in words[code])
        hit = [w for w in words[code] if re.search(r"\b%s" % re.escape(w), hay)]
        if total:
            score += 8 * sum(1.0 / seen[w] for w in hit) / total
        if hit:
            evidence.append("title words: " + ", ".join(sorted(hit)[:6]))
        scores.append((score, code, evidence))
    scores.sort(key=lambda s: -s[0])
    best = scores[0]
    runner = scores[1][0] if len(scores) > 1 else 0.0
    if best[0] >= 10:
        return best[1], "sure", best[2]
    if best[0] >= 5.5 and best[0] >= runner * 1.6:
        return best[1], "likely", best[2]
    if best[0] >= 4 and best[0] >= runner * 1.3:
        return best[1], "maybe", best[2]
    return None, "unsure", []


def _normalise(text):
    return re.sub(r"[^a-z0-9 ]+", " ", text.lower())


def feedback_quotes(text, min_words=6):
    """Sentences a marker quoted from the work: they identify it exactly."""
    out = []
    for rx in (r'"([^"\n]{30,300})"', "\u201c([^\u201c\u201d\n]{30,300})\u201d"):
        out += [re.sub(r"\s+", " ", q).strip() for q in re.findall(rx, text) if len(q.split()) >= min_words]
    return out


def match_by_quote(text, module_quotes):
    hay = re.sub(r"\s+", " ", _normalise(text))
    for module, quotes in module_quotes.items():
        for q in quotes:
            words = re.sub(r"\s+", " ", _normalise(q)).split()
            if len(words) >= 6 and " ".join(words[:12]) in hay:          # a marker may quote a fragment
                return module, f'the marker quoted it: "{q[:60]}..."'
    return None, None


def _content_words(text, limit=120000):
    return re.findall(r"[a-z][a-z-]{4,}", text[:limit].lower())


def build_profiles(module_texts, min_repeat=2, top_n=250):
    """Distinctive words for each module, from its feedback and course files: a word found in most modules says
    nothing; each module gets the same number of words, so three handbooks cannot outweigh one feedback sheet."""
    seen, repeated = {}, {}
    for module, text in module_texts.items():
        counts = {}
        for w in _content_words(text):
            counts[w] = counts.get(w, 0) + 1
        repeated[module] = {w for w, n in counts.items() if n >= min_repeat}
        for w in repeated[module]:
            seen[w] = seen.get(w, 0) + 1
    limit = max(2, len(repeated) // 3)
    return {m: {w: wt for wt, w in sorted(((1.0 / seen[w], w) for w in ws if seen[w] <= limit), reverse=True)[:top_n]}
            for m, ws in repeated.items()}


def match_by_content(text, profiles):
    """(module, confidence, evidence, shortlist) from the words a file actually uses."""
    words = set(_content_words(text))
    scored = []
    for module, profile in profiles.items():
        if not profile:
            continue
        hit = [(w, wt) for w, wt in profile.items() if w in words]
        scored.append((sum(wt for _, wt in hit) / (len(profile) ** 0.5), len(hit), module,
                       [w for w, _ in sorted(hit, key=lambda x: -x[1])[:6]]))
    if not scored:
        return None, "unsure", [], []
    scored.sort(key=lambda s: -s[0])
    shortlist = [(m, round(s, 2)) for s, _, m, _ in scored[:3]]
    best, hits, module, top = scored[0]
    runner = scored[1][0] if len(scored) > 1 else 0.0
    if hits < 5 or best <= 0:
        return None, "unsure", [], shortlist
    if best >= 1.6 * max(runner, 1e-6):
        return module, "likely", ["words of the module: " + ", ".join(top)], shortlist
    if best >= 1.25 * max(runner, 1e-6):
        return module, "maybe", ["words of the module: " + ", ".join(top)], shortlist
    return None, "unsure", [], shortlist


def link(paths, apply=False, where=None, feedback=None, cap=20000):
    """Which module each piece of work belongs to. First the module code and title words; for what is still unplaced,
    a sentence the marker quoted, then the words each module's own feedback and course files use."""
    record = load(where)
    modules = [(e["module"], e.get("title", "")) for e in record["entries"] if e.get("module")]
    if not modules:
        raise ValueError("the record has no modules yet: fieldkit academic record add FOLDER, or record set MODULE")
    files = []
    for root in paths:
        if os.path.isfile(root):
            files.append(root)
        for d, _, names in os.walk(root) if os.path.isdir(root) else []:
            files += [os.path.join(d, n) for n in sorted(names) if n.lower().endswith((".docx", ".pptx", ".pdf"))]
    out, texts = [], {}
    for f in files:
        try:
            texts[f] = read_text(f)
        except Exception as e:
            out.append({"file": f, "module": None, "confidence": "unreadable", "evidence": [str(e)[:80]]})
            continue
        mod, conf, ev = match(texts[f], f, modules)
        out.append({"file": f, "module": mod, "confidence": conf, "evidence": ev, "kind": classify_kind(texts[f], f)})
    module_texts = {}
    if feedback and os.path.isdir(feedback):
        for n in sorted(os.listdir(feedback)):
            m = module_of(n)
            if m and n.lower().endswith((".txt", ".md", ".docx", ".pdf")):
                try:
                    module_texts[m] = module_texts.get(m, "") + "\n" + read_text(os.path.join(feedback, n))
                except Exception:
                    pass
    for x in out:                                  # course material of a module adds the assignment's vocabulary
        if x["module"] and x["confidence"] == "sure" and x.get("kind") != "own work":
            module_texts[x["module"]] = module_texts.get(x["module"], "") + "\n" + texts[x["file"]][:cap]
    module_texts = {m: t[:cap * 2] for m, t in module_texts.items()}
    profiles = build_profiles(module_texts)
    quotes = {m: q for m, q in ((m, feedback_quotes(t)) for m, t in module_texts.items()) if q}
    for x in out:
        if x["module"] or x["file"] not in texts:
            continue
        mod, ev = match_by_quote(texts[x["file"]], quotes)
        if mod:
            x.update(module=mod, confidence="sure", evidence=[ev])
            continue
        mod, conf, ev, short = match_by_content(texts[x["file"]], profiles)
        x["shortlist"] = short
        if mod:
            x.update(module=mod, confidence=conf, evidence=ev)
    if apply:
        for e in record["entries"]:
            mine = sorted({os.path.basename(x["file"]) for x in out
                           if x["module"] == e.get("module") and x["confidence"] in ("sure", "likely")})
            if mine:
                e["submissions"] = mine
        save(record, where)
    unsure = [x for x in out if x["confidence"] in ("unsure", "maybe")]
    return {"ok": True, "files": out, "to_confirm": len(unsure), "written": bool(apply),
            "next": "confirm the unsure ones by hand" if unsure else "fieldkit academic record show"}


# -- text -------------------------------------------------------------------------------------------------------------
def markdown(record):
    lines = ["# Academic record", "", f"Updated: {record.get('updated', '')}", "",
             "| Module | Mark | Band | Similarity | Marker | Date |", "| --- | --- | --- | --- | --- | --- |"]
    for e in record["entries"]:
        lines.append("| %s | %s | %s | %s | %s | %s |" % (
            e.get("module") or e["file"], "" if e.get("mark") is None else e["mark"], e.get("band") or "",
            "" if e.get("similarity_percent") is None else f"{e['similarity_percent']}%", e.get("marker") or "",
            e.get("date") or ""))
    lines += ["", "## What markers say most often", ""]
    for info in fault_counts(record).values():
        lines.append(f"- **{info['label']}** ({info['count']}x: {', '.join(sorted(set(info['modules'])))})")
    lines += ["", "## Module by module", ""]
    for e in record["entries"]:
        lines.append(f"### {e.get('module') or e['file']}")
        if e.get("conduct_breach"):
            lines += ["", "**An academic conduct breach is on record.**"]
        for name, (got, of) in (e.get("criteria") or {}).items():
            lines.append(f"- {name}: {got} / {of}")
        lines.append("")
        for f in e.get("faults", []):
            lines += [f"- {f['label']}", f"  > {f['evidence']}"]
        lines.append("")
    return lines


def lines(r):
    out = []
    if "entries" in r and "faults" in r and isinstance(r["faults"], dict):                 # show
        for e in r["entries"]:
            out.append(f"  {e.get('module') or e['file']:<10} {'' if e.get('mark') is None else e['mark']:>5}  "
                       f"{e.get('band') or '':<20} similarity {e.get('similarity_percent') if e.get('similarity_percent') is not None else '-'}")
        if r["average"] is not None:
            out.append(f"average {r['average']} on {r['scale']}; pass mark {r['pass_mark']}")
        if r["conduct"]:
            out.append(f"ACADEMIC CONDUCT breach on record: {', '.join(r['conduct'])}: every citation from a source "
                       "you read, that says what you write")
        for info in r["faults"].values():
            out.append(f"  {info['count']}x  {info['label']}  ({', '.join(sorted(set(info['modules'])))})")
    elif "entries" in r:                                                               # add
        for e in r["entries"]:
            out.append(f"{e['file']}: module {e['module'] or '?'}, mark {e['mark'] if e['mark'] is not None else '?'}"
                       f"{' (' + e['band'] + ')' if e['band'] else ''}, similarity "
                       f"{e['similarity_percent'] if e['similarity_percent'] is not None else '-'}%")
            for f in e["faults"]:
                out.append(f"  - {f['label']}: \"{f['evidence'][:140]}\"")
        out += [f"  could not read: {u}" for u in r["unread"]]
        out.append("written" if r["written"] else "nothing written yet")
    elif "watch" in r:                                                                 # brief
        for i, w in enumerate(r["watch"], 1):
            out.append(f"{i}. {w['label']} ({w['times']}x)")
            if w["marker_said"]:
                out.append(f"   the marker: \"{w['marker_said'][:140]}\"")
            if w["check"]:
                out.append(f"   caught by: {w['check']}")
        if r["conduct"]:
            out.append(f"A conduct breach is on record ({', '.join(r['conduct'])}): no exceptions on citations.")
    elif "files" in r:                                                                 # link
        for x in r["files"]:
            out.append(f"  [{x['confidence']:<10}] {x.get('kind', ''):<9} {os.path.basename(x['file'])} -> "
                       f"{x['module'] or '?'}" + (f"   ({'; '.join(x['evidence'])})" if x["evidence"] else ""))
    elif "entry" in r:                                                                 # set
        e = r["entry"]
        out.append(f"{e['module']}: mark {e.get('mark', '-')} ({e.get('band') or '-'}) {e.get('title', '')}")
    return out
