"""A research run as records, and the rules every record is checked against.

A record file is JSON lines, one object per line:
    {"type": "run",   "question": ..., "doctrine": "standard"|"dossier", "call": ...}
    {"type": "lane",  "lane": "LOCAL", "headings_missing": [...], "basis": {...}|null, "confidence": {...},
                      "sources_tried": [...], "audit": "..."|null, "uncovered": false}
    {"type": "claim", "lane": "LOCAL", "n": 1, "claim": ..., "evidence": ..., "urls": [...],
                      "tier": "primary_source" | "grade": "B2", "origin": ..., "quote": ..., "line": 12}
    {"type": "fetched", "lane": "LOCAL", "url": ...}      what the lane's helper opened (Gorilla OpenCode writes these)

import reads the files Gorilla OpenCode writes (findings-*.md: every lane's report under its own heading, in the
output contract of internal/llm/agent/research-tool.go) and single lane replies.
"""
import json
import re

from . import text as textmod
from . import urls

HEADINGS = ["ANSWER", "FINDINGS", "SOURCES TRIED", "CONFIDENCE", "NOT ESTABLISHED"]
TIERS = ["primary_source", "config", "multiple_reports", "single_claim", "unsourced"]
CONFIDENCE_WORDS = ["proven", "strong", "plausible", "speculative"]
YARDSTICK = ["remote chance", "highly unlikely", "unlikely", "realistic possibility", "likely", "probable",
             "highly likely", "almost certain"]
GRADE_RE = re.compile(r"^[A-F][1-6]$")
FIELD_RE = re.compile(r"\b(CLAIM|EVIDENCE|TIER|GRADE|ORIGIN|QUOTE|SOURCE)\s*:\s*", re.I)
QUOTED_RE = re.compile(r"[\"“”«»]([^\"“”«»]{20,})[\"“”«»]")


# -- import ---------------------------------------------------------------------------------------------------
def _fields(line):
    """'- CLAIM: a | EVIDENCE: b | TIER: c' -> {claim: a, evidence: b, tier: c}. Tolerates bold markers, other
    separators and extra fields; a field's value runs to the next known field name."""
    line = re.sub(r"\*\*|__", "", line).strip().lstrip("-*• ").strip()
    marks = list(FIELD_RE.finditer(line))
    out = {}
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(line)
        out[m.group(1).lower()] = line[m.end():end].strip().strip("|").strip()
    return out


def _sections(body):
    """A lane reply -> {HEADING: text}. Headings are whole lines (## X), matched as headings, not substrings."""
    out, cur = {}, None
    for line in body.splitlines():
        m = re.match(r"^#{2,3}\s+(.+?)\s*#*\s*$", line)
        if m and m.group(1).strip().upper() in HEADINGS:
            cur = m.group(1).strip().upper()
            out[cur] = []
            continue
        if cur:
            out[cur].append(line)
    return {k: "\n".join(v).strip() for k, v in out.items()}


def _confidence(text, doctrine):
    t = text.strip()
    if doctrine == "dossier":
        lik = re.search(r"LIKELIHOOD\s*:\s*([^\n.;]+)", t, re.I)
        conf = re.search(r"CONFIDENCE\s*:\s*(HIGH|MODERATE|LOW)\b", t, re.I)
        return {"likelihood": lik.group(1).strip().lower() if lik else None,
                "level": conf.group(1).upper() if conf else None}
    word = t.split()[0].strip(".*").lower() if t.split() else ""
    return {"word": word, "alone": len(t.split()) == 1}


def lane_records(lane, body, doctrine):
    secs = _sections(body)
    recs = []
    basis = None
    for line in secs.get("ANSWER", "").splitlines():
        if re.match(r"^\s*\**BASIS\**\s*:", line, re.I):
            f = _fields(line.replace("BASIS", "EVIDENCE", 1))
            basis = {"evidence": f.get("evidence", ""), "tier": f.get("tier"), "grade": f.get("grade"),
                     "urls": urls.extract(f.get("evidence", ""))}
    lane_rec = {"type": "lane", "lane": lane, "headings_missing": [h for h in HEADINGS if h not in secs],
                "basis": basis, "confidence": _confidence(secs.get("CONFIDENCE", ""), doctrine) if
                "CONFIDENCE" in secs else None,
                "sources_tried": [l.strip().lstrip("-*• ").strip() for l in secs.get("SOURCES TRIED", "").splitlines()
                                  if l.strip()], "audit": None, "uncovered": False}
    recs.append(lane_rec)
    n = 0
    for i, line in enumerate(secs.get("FINDINGS", "").splitlines(), 1):
        if not re.search(r"\bCLAIM\s*:", line, re.I):
            continue
        f = _fields(line)
        n += 1
        rec = {"type": "claim", "lane": lane, "n": n, "claim": f.get("claim", ""), "evidence": f.get("evidence", ""),
               "urls": urls.extract(f.get("evidence", "") + " " + f.get("source", "")), "line": i}
        for k in ("tier", "grade", "origin", "quote"):
            if f.get(k):
                rec[k] = f[k].strip().strip("`").strip()
        if "quote" not in rec:
            q = QUOTED_RE.search(f.get("evidence", ""))
            if q:
                rec["quote"] = q.group(1).strip()
        if "tier" in rec:
            rec["tier"] = rec["tier"].lower()
        if "grade" in rec:
            rec["grade"] = rec["grade"].upper()
        recs.append(rec)
    return recs


def import_text(body, lane_hint="LANE"):
    """A findings file (or one lane reply) -> records."""
    header, _, rest = body.partition("\n---\n")
    run = {"type": "run", "question": "", "doctrine": "standard", "call": ""}
    m = re.search(r"^# Raw findings\s*[—-]\s*(.+)$", header, re.M)
    if not m:                                          # a single lane reply, no file header
        doctrine = "dossier" if re.search(r"\|\s*GRADE\s*:", body) else "standard"
        run["doctrine"] = doctrine
        return [run] + lane_records(lane_hint, body, doctrine)
    run["question"] = m.group(1).strip()
    d = re.search(r"^Doctrine:\s*(\w+)", header, re.M)
    run["doctrine"] = "dossier" if (d and d.group(1) == "dossier") or (not d and "| GRADE:" in body) else "standard"
    c = re.search(r"^Call:\s*(\S+)", header, re.M)
    run["call"] = c.group(1) if c else ""
    recs = [run]
    # Lanes: a '## TITLE' line that is not one of the contract's own headings opens a lane.
    lanes, cur, buf = [], None, []
    for line in rest.splitlines():
        h = re.match(r"^##\s+(.+?)\s*$", line)
        if h and h.group(1).strip().upper() not in HEADINGS:
            if cur:
                lanes.append((cur, "\n".join(buf)))
            cur, buf = h.group(1).strip(), []
            continue
        if cur is not None:
            buf.append(line)
    if cur:
        lanes.append((cur, "\n".join(buf)))
    for title, text in lanes:
        text = re.sub(r"\n---\s*$", "", text.strip())
        audit = None
        if "### Supervisor audit of this lane" in text:
            text, _, audit = text.partition("### Supervisor audit of this lane")
            audit = audit.strip()
        if "LANE UNCOVERED" in text and "## ANSWER" not in text:
            recs.append({"type": "lane", "lane": title, "headings_missing": [], "basis": None, "confidence": None,
                         "sources_tried": [], "audit": None, "uncovered": True})
            continue
        lr = lane_records(title, text, run["doctrine"])
        lr[0]["audit"] = audit
        recs += lr
    return recs


def load(path):
    """A record file (JSON lines) or a findings/lane markdown file -> records."""
    raw = open(path, encoding="utf-8").read()
    if str(path).endswith((".jsonl", ".json")):
        return [json.loads(l) for l in raw.splitlines() if l.strip()]
    return import_text(raw)


# -- check ----------------------------------------------------------------------------------------------------
def _v(value, n=40):
    """A value from the record, as the check repeats it: short, one line, printable. The check's answer is labelled
    as Fieldkit's own words (no taint), so what it echoes from a model or a page must not carry a paragraph."""
    s = re.sub(r"[^\w .:/#?=&%@+~-]", "", str(value).replace("\n", " "))
    return repr(s[:n] + ("..." if len(s) > n else ""))


def _u(url):
    return _v(url, 120)[1:-1]


def _fetched_set(path):
    out = set()
    for line in open(path, encoding="utf-8").read().splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("{"):
            try:
                line = json.loads(line).get("url", "")
            except json.JSONDecodeError:
                continue
        out.add(urls.normalize(line))
    return out


def check(records, store=None, fetched=None, run=None):
    """-> {"findings": [...], "notes": [...], "counts": {...}}. A finding breaks a rule the output contract or the
    evidence sets; a note is worth knowing and breaks nothing."""
    findings, notes = [], []
    run_rec = next((r for r in records if r.get("type") == "run"), {"doctrine": "standard"})
    doctrine = run_rec.get("doctrine", "standard")
    claims = [r for r in records if r.get("type") == "claim"]
    lanes = [r for r in records if r.get("type") == "lane"]
    snaps = store.snapshots() if store else None
    opened = _fetched_set(fetched) if fetched else None
    # Gorilla OpenCode writes what each lane fetched into the record itself ({"type": "fetched", "lane", "url"}):
    # then each claim is held to what ITS lane opened, not what any lane did.
    by_lane = {}
    for r in records:
        if r.get("type") == "fetched":
            by_lane.setdefault(r.get("lane"), set()).add(urls.normalize(r.get("url", "")))

    def where(r):
        lane = _v(r.get("lane", "?"), 30)[1:-1]
        return f"{lane} claim {r.get('n', '?')}" if r.get("type") == "claim" else lane

    def add(kind, rule, r, msg):
        (findings if kind == "finding" else notes).append({"rule": rule, "where": where(r), "message": msg})

    for ln in lanes:
        if ln.get("uncovered"):
            add("note", "lane-uncovered", ln, "the lane produced nothing: its ground is unexamined")
            continue
        if ln.get("headings_missing"):
            add("finding", "contract-headings", ln, "missing heading(s): " + ", ".join(ln["headings_missing"]))
        b = ln.get("basis")
        if b is None and "ANSWER" not in ln.get("headings_missing", []):
            add("finding", "contract-basis", ln, "the ANSWER has no BASIS line: an unsourced answer reads as a "
                                                  "checked one once it is written down")
        conf = ln.get("confidence")
        if conf is not None:
            if doctrine == "dossier":
                if conf.get("level") is None:
                    add("finding", "contract-confidence", ln, "CONFIDENCE must be HIGH, MODERATE or LOW")
                if conf.get("likelihood") and conf["likelihood"].split("/")[0].strip() not in YARDSTICK:
                    add("finding", "contract-likelihood", ln, f"LIKELIHOOD {_v(conf['likelihood'])} is not one of the "
                                                              "seven yardstick terms")
            elif conf.get("word") not in CONFIDENCE_WORDS or not conf.get("alone"):
                add("finding", "contract-confidence", ln, "CONFIDENCE must be exactly one word: "
                                                          + " | ".join(CONFIDENCE_WORDS))
        if not ln.get("sources_tried") and "SOURCES TRIED" not in ln.get("headings_missing", []):
            add("finding", "contract-sources", ln, "SOURCES TRIED is empty: an investigation that lists nothing "
                                                   "it tried cannot be audited")

    by_url = {}
    for c in claims:
        if not c.get("claim"):
            add("finding", "claim-empty", c, "a CLAIM with no sentence")
        if not c.get("evidence"):
            add("finding", "claim-evidence", c, "no EVIDENCE: the claim cannot be traced")
        tier, grade = c.get("tier"), c.get("grade")
        if doctrine == "dossier":
            if tier and not grade:
                add("finding", "claim-scale", c, "a TIER on a dossier run: dossier claims carry a two-axis GRADE")
            elif not grade:
                add("finding", "claim-grade", c, "no GRADE")
            elif not GRADE_RE.match(grade):
                add("finding", "claim-grade", c, f"GRADE {_v(grade)} is not a letter A-F and a digit 1-6")
        else:
            if grade and not tier:
                add("finding", "claim-scale", c, "a GRADE on a standard run: standard claims carry an evidence TIER")
            elif not tier:
                add("finding", "claim-tier", c, "no TIER")
            elif tier not in TIERS:
                add("finding", "claim-tier", c, f"TIER {_v(tier)} is not one of: {', '.join(TIERS)}")
        if tier == "unsourced" and c.get("urls"):
            add("note", "claim-unsourced-url", c, "marked unsourced, yet its evidence names a URL")
        if tier in ("primary_source", "config") and not c.get("urls") and not re.search(
                r"[\w./-]+:\d+|\b[0-9a-f]{7,40}\b|\bv?\d+\.\d+", c.get("evidence", "")):
            add("note", "claim-locator", c, f"{tier} with nothing a reader could open: no URL, file:line, commit "
                                            "or version")
        for u in c.get("urls") or []:
            by_url.setdefault(u, set()).add(c.get("lane"))
            lane_opened = by_lane.get(c.get("lane"), set()) if by_lane and opened is None else None
            seen = opened if opened is not None else lane_opened
            if seen is not None and u not in seen and urls.fetchable(u) not in seen and not (
                    u.startswith("doi:") and any(u[4:] in x.lower() for x in seen)):
                add("finding", "cited-not-opened", c, f"{_u(u)} is cited, and "
                    + ("this run never fetched it" if opened is not None else "its lane never fetched it"))
            if store is not None:
                snap = store.latest(u, run, snaps)
                if snap is None:
                    add("finding", "no-snapshot", c, f"no snapshot of {_u(u)}: fieldkit evidence fetch {_u(u)}"
                                                     + (f" --run {run}" if run else ""))
                    continue
                if snap.get("challenge"):
                    add("finding", "snapshot-challenge", c, f"{_u(u)} answered with a bot check, not the page "
                                                            f"({_v(snap.get('title') or 'challenge page')})")
                    continue
                if not 200 <= int(snap.get("status", 0)) < 300:
                    add("finding", "snapshot-status", c, f"{_u(u)} answered HTTP {snap.get('status')} when it was "
                                                         f"fetched ({snap.get('fetched_at')})")
                    continue
                if c.get("quote"):
                    body = store.text(snap)
                    if body is None:
                        add("note", "quote-uncheckable", c, f"{_u(u)} has no text to check the quote in "
                                                            f"({snap.get('text_how')})")
                    elif not textmod.contains(body, c["quote"]):
                        add("finding", "quote-not-found", c, f"the quote is not in the snapshot of {_u(u)} "
                                                             f"({snap.get('sha256', '')[:12]})")
                else:
                    add("note", "no-quote", c, f"no quote: the snapshot of {_u(u)} shows the page was read, not that "
                                               "it says this")
    for u, ls in sorted(by_url.items()):
        if len(ls) > 1:
            notes.append({"rule": "same-source", "where": ", ".join(sorted(_v(x, 30)[1:-1] for x in ls)),
                          "message": f"{_u(u)} is cited by {len(ls)} lanes: one source, not {len(ls)} confirmations"})
    origins = {}
    for c in claims:
        if c.get("origin"):
            origins.setdefault(textmod.fold(c["origin"]), []).append(c)
    for o, cs in sorted(origins.items()):
        if len({c.get("lane") for c in cs}) > 1:
            notes.append({"rule": "one-origin", "where": ", ".join(sorted({where(c) for c in cs})),
                          "message": f"{len(cs)} claims trace to one origin ({_v(cs[0]['origin'])}): one voice"})
    counts = {"lanes": len(lanes), "claims": len(claims), "urls": len(by_url),
              "findings": len(findings), "notes": len(notes), "doctrine": doctrine}
    return {"findings": findings, "notes": notes, "counts": counts}
