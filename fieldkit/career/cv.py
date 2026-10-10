"""fieldkit cv: read and check a CV against fixed rules, and write CVs from the candidate's profile.

    fieldkit cv check FILE [--trade NAME] [--lang en|ro]   findings by importance, and the questions to ask
    fieldkit cv render [--variant NAME] [--lang en|ro]     profile.yaml -> CV_<variant>_<lang>_<date>.docx and .pdf

Born 2026-10-10 from the owner's brief "CV si cautare joburi" (a driver's UK CV, in English and Romanian), then made
general the same day: rules.yaml holds what every UK CV needs; trades/*.yaml what one trade looks for (driver,
security, warehouse ...); a new trade is a new file. Nothing here invents a fact: render only lays out the profile,
and refuses what a UK CV must not carry (date of birth, photo, marital status, ID numbers).

The layout is ATS-friendly: one column, real headings, and a real tab between the job line and its dates (a
positional tab disappeared in plain-text extraction, "London10/2022", and applicant-tracking systems read the text).
"""
import datetime as dt
import math
import re
from pathlib import Path

import yaml

from . import profile as prof

HERE = Path(__file__).parent
RULES = HERE / "rules.yaml"
TEMPLATES = HERE / "templates.yaml"
TRADES = HERE / "trades"
SEVERITY = {"high": 0, "medium": 1, "low": 2}
LONG = {"CE": "C+E", "BE": "B+E", "DE": "D+E", "C1E": "C1+E"}   # the short spellings of the licence categories


def _load(path):
    return yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}


# -- check --------------------------------------------------------------------------------------------------------------
def _norm_heading(line):
    return re.sub(r"\s+", " ", line.strip().strip("#*_:|-=").strip().lower())


def _images(path):
    """How many pictures the file carries (a UK CV has no photo)."""
    path = Path(path)
    try:
        if path.suffix.lower() == ".docx":
            import docx
            return len(docx.Document(path).inline_shapes)
        if path.suffix.lower() == ".pdf":
            import pypdfium2 as pdfium
            import pypdfium2.raw as raw
            doc = pdfium.PdfDocument(str(path))
            try:
                return sum(1 for page in doc for o in page.get_objects() if o.type == raw.FPDF_PAGEOBJ_IMAGE)
            finally:
                doc.close()
    except Exception:                                      # an unreadable picture count is "not known", never 0
        return None
    return 0


def _pages(path, words):
    if Path(path).suffix.lower() == ".pdf":
        import pypdfium2 as pdfium
        doc = pdfium.PdfDocument(str(path))
        try:
            return len(doc), "counted"
        finally:
            doc.close()
    return max(1, math.ceil(words / 450)), "estimated (about 450 words a page)"


def detect_lang(text):
    ro = len(re.findall(r"[ăâîșşțţ]", text.lower())) + 3 * len(re.findall(
        r"\b(experiență|experienta|permis|competențe|limbi|șofer|sofer|despre)\b", text.lower()))
    return "ro" if ro >= 5 else "en"


def load_trade(name):
    p = TRADES / f"{name}.yaml"
    if not p.is_file():
        raise prof.BadProfile(f"trade {name!r}: no {p.name}; there are: {', '.join(trade_names())}")
    return _load(p)


def trade_names():
    return sorted(p.stem for p in TRADES.glob("*.yaml"))


def detect_trades(text):
    """The trades the CV is about: a trade's pattern on at least two lines, and on at least half as many lines as the
    main trade's (a driver's CV that mentions a forklift licence once is not a warehouse CV; 2026-10-10)."""
    lines = text.splitlines()
    n = {t: sum(1 for l in lines if re.search(load_trade(t)["detect"], l, re.I)) for t in trade_names()}
    top = max(n.values(), default=0)
    return [t for t, c in n.items() if c >= 2 and c * 2 >= top]


def check_text(text, path=None, rules=None, lang=None, trades=None):
    rules = rules or _load(RULES)
    trades = [load_trade(n) for n in (detect_trades(text) if trades is None else trades)]
    lines = text.splitlines()
    lang = lang or detect_lang(text)
    findings = []

    def add(fid, sev, msg, line=None, excerpt=None):
        findings.append({"id": fid, "severity": sev, "message": msg, "line": line,
                         "excerpt": (excerpt or "").strip()[:160]})

    # sections
    where = {}
    for i, line in enumerate(lines, 1):
        h = _norm_heading(line)
        for name, s in rules["sections"].items():
            if h in s["headings"] and name not in where:
                where[name] = i
    c = rules["contact"]
    has_email = bool(re.search(c["email"], text))
    has_phone = bool(re.search(c["phone"], text))
    if not has_email:
        add("no-email", "high", "No e-mail address: an employer cannot reply.")
    if not has_phone:
        add("no-phone", "high", "No phone number (UK mobile): drivers are hired by phone.")
    sev = {"experience": "high", "licences": "high", "profile": "medium", "skills": "medium", "languages": "low"}
    for name, s in rules["sections"].items():
        if s["required"] and name != "contact" and name not in where:
            add(f"no-{name}", sev.get(name, "low"), f"No '{name}' section (a heading such as "
                f"'{s['headings'][0].title()}' or '{s['headings'][-1].title()}').")
    order = [n for n in ("profile", "experience", "licences", "skills") if n in where]
    if order != sorted(order, key=lambda n: where[n]) or ("profile" in where and "experience" in where
                                                          and where["profile"] > where["experience"]):
        add("section-order", "low", "Sections are out of the usual UK order: profile, experience, licences, skills.")

    # newest job first: the start years of the date ranges in the experience section
    if "experience" in where:
        nxt = min([v for k, v in where.items() if v > where["experience"]] + [len(lines) + 1])
        starts = []
        for i in range(where["experience"], nxt - 1):
            m = re.search(r"((?:19|20)\d\d)\s*(?:[-–—]|to|până la|pana la|până|pana)\s*"
                          r"((?:19|20)\d\d|present|current|now|prezent|în prezent|in prezent)", lines[i], re.I)
            if not m:
                m = re.search(r"\b(?:\d{1,2}[/.])?((?:19|20)\d\d)\b.{0,25}?(?:[-–—]|to|până|pana).{0,25}?"
                              r"((?:19|20)\d\d|present|current|now|prezent)", lines[i], re.I)
            if m:
                starts.append((int(m.group(1)), i + 1, lines[i]))
        if any(a[0] < b[0] for a, b in zip(starts, starts[1:])):
            bad = next(b for a, b in zip(starts, starts[1:]) if a[0] < b[0])
            add("not-newest-first", "high", "Jobs are not newest first: a UK CV starts with the current job.",
                bad[1], bad[2])
        if not starts:
            add("no-dates", "medium", "No date ranges found in the experience section (e.g. 'Mar 2019 – Present').")

    # what a UK CV must not carry
    for r in rules["uk_forbidden"]:
        for i, line in enumerate(lines, 1):
            if re.search(r["pattern"], line, re.I):
                add(r["id"], r["severity"], r["message"], i, line)
                break
    if path is not None:
        n = _images(path)
        if n:
            add("photo", "high", f"The file carries {n} picture(s): a UK CV has no photo.")
    words = len(re.findall(r"\w+", text))
    pages, how = _pages(path, words) if path is not None else (max(1, math.ceil(words / 450)), "estimated")
    if pages > rules["max_pages"]:
        add("too-long", "medium", f"{pages} pages ({how}); a UK CV is at most {rules['max_pages']}.")

    # vague phrases, repeated words
    for pat in rules["vague"]:
        rx = re.compile(r"\b" + pat, re.I)
        for i, line in enumerate(lines, 1):
            m = rx.search(line)
            if m:
                add("vague", "low", f"Vague phrase '{m.group(0)}': replace it with a fact a recruiter can check.",
                    i, line)
    for i, line in enumerate(lines, 1):
        m = re.search(r"\b(\w{2,})\s+\1\b", line, re.I)
        if m:
            add("repeated-word", "low", f"Repeated word '{m.group(1)}'.", i, line)

    # facts any CV is stronger with, then the trade's own; the questions for what the CV does not say
    def first(pattern):
        return next(((i, l) for i, l in enumerate(lines, 1) if re.search(pattern, l, re.I)), None)
    for chk in rules.get("checks") or []:
        if not first(chk["pattern"]):
            add(f"no-{chk['id']}", chk.get("severity", "medium"), chk["message"])
    q = rules["questions"]
    questions = [{"id": k, "text": q[k][lang], "cv_says": None, "trade": None} for k in q]
    cats = []
    for t in trades:
        if t.get("licence_line"):
            for line in lines:
                if re.search(t["licence_line"], line, re.I):
                    cats += [LONG.get(m.group(1).upper(), m.group(1).upper())
                             for m in re.finditer(t["licence_category"], line)]
            cats = list(dict.fromkeys(cats))
            if not cats:
                add("no-licence-categories", "high", "The licence categories (B, C, C+E, D ...) are not stated.")
        tq = t.get("questions") or {}
        if "licence" in tq:
            questions.insert(0, {"id": "licence", "text": tq["licence"][lang], "cv_says": ", ".join(cats) or None,
                                 "trade": t["name"]})
        for chk in t.get("checks") or []:
            m = first(chk["pattern"])
            if not m and chk.get("message"):
                add(f"no-{chk['id']}", chk.get("severity", "medium"), chk["message"])
            if chk.get("ask"):
                questions.append({"id": chk["ask"], "text": tq[chk["ask"]][lang],
                                  "cv_says": m[1].strip()[:120] if m else None, "trade": t["name"]})

    findings.sort(key=lambda f: (SEVERITY[f["severity"]], f["line"] or 0))
    worst = findings[0]["severity"] if findings else None
    return {"file": str(path) if path else None, "lang": lang, "words": words, "pages": pages, "pages_how": how,
            "sections": where, "contact": {"email": has_email, "phone": has_phone}, "licences": cats,
            "trades": [t["name"] for t in trades],
            "findings": findings, "questions": questions, "ok": not findings,
            "verdict": ("well written" if not findings else
                        "needs work" if worst == "high" else "good, with improvements"),
            "next": "show the findings and ask every question; wait for the answers before rewriting (stage 1)"}


def check(path, lang=None, rules=None, trades=None):
    from ..office.read import read
    return check_text(read(path), path=path, rules=rules, lang=lang, trades=trades)


# -- render -------------------------------------------------------------------------------------------------------------
def _when(e, L):
    def one(v):
        d = prof.ym(v)
        if d is None:
            return L["present"]
        return f"{L['months'][d[1] - 1]} {d[0]}" if d[1] else str(d[0])
    return f"{one(e['start'])} – {one(e.get('end', 'present'))}"


def _bullets(e, variant, lang, where):
    out = []
    for j, b in enumerate(e.get("bullets") or []):
        if isinstance(b, dict) and "text" in b:
            if b.get("variants") and variant not in b["variants"]:
                continue
            b = b["text"]
        out.append(prof.T(b, lang, f"{where}.bullets.{j}"))
    return out


def blocks(p, variant, lang, tpl=None):
    """The CV as a list of (kind, payload): one layout for .docx and .pdf. Only what the profile says."""
    tpl = tpl or _load(TEMPLATES)
    L = tpl["labels"][lang]
    v = p["cv_variants"][variant]
    c = p["contact"]
    T = prof.T
    out = [("name", p["name"]), ("headline", T(v["headline"], lang, f"cv_variants.{variant}.headline"))]
    line = [str(c[k]) for k in ("location", "phone", "email", "linkedin") if c.get(k)]
    if p.get("right_to_work"):
        line.append(T(p["right_to_work"], lang, "right_to_work"))
    out.append(("contact", "  |  ".join(line)))
    out += [("h", L["profile"]), ("p", T(v["profile"], lang, f"cv_variants.{variant}.profile").strip())]
    out += [("h", L["skills"]), ("bullets", [T(s, lang, f"cv_variants.{variant}.skills") for s in v["skills"]])]
    out.append(("h", L["experience"]))
    for i, e in enumerate(p["experience"]):
        if e.get("variants") and variant not in e["variants"]:
            continue
        head = f"{T(e['title'], lang, f'experience.{i}.title')}  |  {e['employer']}"
        if e.get("location"):
            head += f", {e['location']}"
        out.append(("job", (head, _when(e, L))))
        out.append(("bullets", _bullets(e, variant, lang, f"experience.{i}")))
    if p.get("licences") or p.get("certificates"):
        out.append(("h", L["licences"]))
        items = []
        for l in p.get("licences") or []:
            if isinstance(l, str):
                items.append(l)
                continue
            head = f"{L['category']} {l['category']}" if l.get("category") else T(l.get("name"), lang, "licences")
            items.append(" — ".join([head] + ([T(l["detail"], lang, "licences")] if l.get("detail") else [])))
        items += [T(x, lang, "certificates") for x in p.get("certificates") or []]
        out.append(("bullets", items))
    if p.get("education"):
        out.append(("h", L["education"]))
        out.append(("bullets", [e if isinstance(e, str) else ", ".join(
            T(e[k], lang, "education") if k != "year" else str(e[k]) for k in ("title", "place", "year") if e.get(k))
            for e in p["education"]]))
    if p.get("languages"):
        out.append(("h", L["languages"]))
        out.append(("p", "  |  ".join(l if isinstance(l, str) else
                                      f"{T(l['language'], lang, 'languages')} ({T(l.get('level', ''), lang, 'languages')})"
                                      for l in p["languages"])))
    if p.get("other"):
        out += [("h", L["other"]), ("bullets", [T(x, lang, "other") for x in p["other"]])]
    return out


def _write_docx(blocks, out):
    import docx
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Cm, Pt, RGBColor
    doc = docx.Document()
    for s in doc.sections:
        s.top_margin = s.bottom_margin = Cm(1.6)
        s.left_margin = s.right_margin = Cm(1.9)
    st = doc.styles["Normal"]
    st.font.name, st.font.size = "Calibri", Pt(10.5)
    accent = RGBColor(0x1F, 0x3A, 0x5F)
    for kind, v in blocks:
        if kind == "name":
            p = doc.add_paragraph()
            r = p.add_run(v)
            r.bold, r.font.size, r.font.color.rgb = True, Pt(20), accent
            p.paragraph_format.space_after = Pt(0)
        elif kind == "headline":
            r = doc.add_paragraph().add_run(v)
            r.font.size, r.italic = Pt(12), True
        elif kind == "contact":
            p = doc.add_paragraph(v)
            p.paragraph_format.space_after = Pt(6)
        elif kind == "h":
            p = doc.add_paragraph()
            r = p.add_run(v.upper())
            r.bold, r.font.size, r.font.color.rgb = True, Pt(11.5), accent
            p.paragraph_format.space_before, p.paragraph_format.space_after = Pt(10), Pt(3)
        elif kind == "p":
            doc.add_paragraph(v)
        elif kind == "job":
            p = doc.add_paragraph()
            p.add_run(v[0]).bold = True
            p.add_run("\t" + v[1])
            p.paragraph_format.tab_stops.add_tab_stop(Cm(17.2), alignment=2)   # right-aligned dates
            p.paragraph_format.space_before, p.paragraph_format.space_after = Pt(4), Pt(0)
        elif kind == "bullets":
            for b in v:
                bp = doc.add_paragraph(str(b), style="List Bullet")
                bp.paragraph_format.space_after = Pt(0)
    doc.core_properties.author = ""
    doc.core_properties.last_modified_by = ""
    doc.save(out)


FONT_CANDIDATES = [  # a font with Romanian ș ț (comma below); Helvetica has none
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf", "C:/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/calibri.ttf", "/Library/Fonts/Arial.ttf", "/System/Library/Fonts/Supplemental/Arial.ttf"]
BOLD = {"DejaVuSans.ttf": "DejaVuSans-Bold.ttf", "LiberationSans-Regular.ttf": "LiberationSans-Bold.ttf",
        "arial.ttf": "arialbd.ttf", "calibri.ttf": "calibrib.ttf", "Arial.ttf": "Arial Bold.ttf"}


def _font():
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    for f in FONT_CANDIDATES:
        p = Path(f)
        if p.is_file():
            b = p.with_name(BOLD.get(p.name, p.name))
            pdfmetrics.registerFont(TTFont("CV", str(p)))
            pdfmetrics.registerFont(TTFont("CV-Bold", str(b if b.is_file() else p)))
            return "CV", "CV-Bold"
    raise RuntimeError("no font with Romanian letters (ș ț) found for the PDF; install one: "
                       "sudo apt-get install -y fonts-dejavu-core (Linux) - Windows and macOS have Arial")


def _write_pdf(blocks, out):
    from reportlab.lib.colors import HexColor
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import (HRFlowable, ListFlowable, ListItem, Paragraph, SimpleDocTemplate, Table,
                                    TableStyle)
    from xml.sax.saxutils import escape
    reg, bold = _font()
    accent = HexColor("#1F3A5F")
    base = ParagraphStyle("b", fontName=reg, fontSize=10, leading=13)
    S = {"name": ParagraphStyle("n", parent=base, fontName=bold, fontSize=19, leading=23, textColor=accent),
         "headline": ParagraphStyle("hl", parent=base, fontSize=12, leading=15),
         "contact": ParagraphStyle("c", parent=base, spaceAfter=6),
         "h": ParagraphStyle("h", parent=base, fontName=bold, fontSize=11.5, textColor=accent, spaceBefore=9,
                             spaceAfter=1),
         "p": base, "job": ParagraphStyle("j", parent=base, fontName=bold, spaceBefore=4)}
    width = A4[0] - 3.8 * cm - 12                      # the frame keeps 6 pt of padding on each side
    flow = []
    for kind, v in blocks:
        if kind == "job":
            t = Table([[Paragraph(escape(v[0]), S["job"]), Paragraph(escape(v[1]), ParagraphStyle(
                "d", parent=base, alignment=2, spaceBefore=4))]], colWidths=[width * 0.7, width * 0.3])
            t.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                                   ("VALIGN", (0, 0), (-1, -1), "TOP")]))
            t.hAlign = "LEFT"
            flow.append(t)
        elif kind == "bullets":
            if v:
                flow.append(ListFlowable([ListItem(Paragraph(escape(str(b)), base), leftIndent=12) for b in v],
                                         bulletType="bullet", start="•", leftIndent=12, bulletFontName=reg))
        elif kind == "h":
            flow.append(Paragraph(escape(v.upper()), S["h"]))
            flow.append(HRFlowable(width="100%", thickness=0.6, color=accent, spaceBefore=0, spaceAfter=4))
        else:
            flow.append(Paragraph(escape(v), S[kind]))
    doc = SimpleDocTemplate(str(out), pagesize=A4, leftMargin=1.9 * cm, rightMargin=1.9 * cm, topMargin=1.6 * cm,
                            bottomMargin=1.6 * cm, title="CV", author="", creator="fieldkit", invariant=1)
    doc.build(flow)


def _free(folder, stem, exts):
    """The first stem, stem_2, stem_3 ... for which no file with any of exts exists: nothing is overwritten."""
    n, name = 1, stem
    while any((folder / f"{name}{e}").exists() for e in exts):
        n += 1
        name = f"{stem}_{n}"
    return name


def render(variant=None, lang="en", profile_path=None, out_dir=None, today=None, tpl=None):
    """profile.yaml -> CV_<variant>_<lang>_<date>.docx and .pdf in cvs/; both checked, never overwritten."""
    from ..office import check as ocheck
    from . import store
    tpl = tpl or _load(TEMPLATES)
    p = prof.load(profile_path)
    names = list(p["cv_variants"])
    variant = variant or names[0]
    if variant not in names:
        raise prof.BadProfile(f"variant {variant!r}: the profile has {', '.join(names)}")
    if lang not in tpl["labels"]:
        raise prof.BadProfile(f"lang {lang!r}: one of {', '.join(tpl['labels'])}")
    out_dir = Path(out_dir or store.base() / "cvs")
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = _free(out_dir, f"CV_{variant}_{lang}_{(today or dt.date.today()).isoformat()}", (".docx", ".pdf"))
    b = blocks(p, variant, lang, tpl)
    files = {}
    for ext, writer in ((".docx", _write_docx), (".pdf", _write_pdf)):
        out = out_dir / f"{stem}{ext}"
        writer(b, out)
        rep = ocheck.check(out)
        if rep["problems"]:
            out.unlink()
            raise RuntimeError(f"{out.name} failed its own check and was removed: {rep['problems']}")
        files[ext[1:]] = str(out)
    # read back as an applicant-tracking system would: plain text, every job line with its dates apart
    re_check = check(files["pdf"], lang=lang, trades=[p["trade"]] if p.get("trade") else [])
    left = [f for f in re_check["findings"] if f["id"] in ("too-long", "photo", "date-of-birth", "not-newest-first",
                                                           "no-email", "no-phone", "no-licence-categories")]
    return {"files": files, "variant": variant, "lang": lang, "pages": re_check["pages"], "findings": left,
            "ok": not left, "next": ("open both files and show them; apply every correction asked for in "
                                     "profile.yaml, then render again" if not left else
                                     "fix the profile for these findings, then render again")}
