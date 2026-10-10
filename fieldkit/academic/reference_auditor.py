"""
REFERENCE AUDITOR - Validates author-date referencing (Harvard, APA 7, ISO 690 author-date) in academic work.

Built for one UK student's Harvard work and generalised on 2026-10-10 for seven countries:
the logic is unchanged; the words it recognises ("and", "no date", "page", the reference-list heading, leading
articles, capitals with diacritics) now come from languages.yaml, so a Spanish, Portuguese, Italian, German or
Romanian text is read the same way as an English one. The style's own shape (APA's "(2020)." against Harvard's
"(2020)", ISO 690's SURNAME) is checked by styles.py.

Checks:
1. Bidirectional 1:1 mapping (in-text <-> reference list)
2. Duplicate reference entries
3. Orphan citations (cited but NOT in the reference list)
4. Unused references (listed but NEVER cited)
5. Alphabetical ordering of the reference list (author-date styles require it)
6. Page-number presence on citations (advisory)
7. Legislation citations, reported separately (statute is cited by name and
   year, not author-date, so it cannot be matched the same way)
8. Direct quotations: each one needs author, year and page beside it.
   A WARNING, never a failure: the work is still delivered, and the student
   adds the citation by hand.
9. Slide decks: a source cited only in the speaker notes (warning).

Reads .md, .txt, .docx and .pptx, so a finished Word document or slide deck
can be checked as well as a markdown draft. audit_text() returns data; the
report is rendered by fieldkit academic refs (academic/cli.py).

Author forms understood on BOTH sides, so the comparison is like with like:
    Smith, J. (2020)                        -> smith (2020)
    Smith, J. and Jones, K. (2020)          -> smith (2020)
    Brown, A., Taylor, R. and Nguyen, T.    -> brown (2021)
    Fernandez, M., Okonkwo, C. et al.       -> fernandez (2019)
    NHS Digital (2017)                      -> nhs digital (2017)
    World Health Organization (2022)        -> world health organization (2022)
    Marmot (2020a) / Marmot (2020b)         -> kept distinct
    Smith, J. (n.d.)                        -> smith (n.d.)

Usage:
    fieldkit academic refs <file.md | file.docx | file.pptx>
"""
import os
import re
import sys
import argparse
from collections import Counter

# The report itself is ASCII, but source text may not be; avoid cp1252 crashes.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# --------------------------------------------------------------------------
# Token patterns
# --------------------------------------------------------------------------

YEAR = r"(?:1[6-9]\d{2}|20\d{2})[a-z]?"
NO_DATE = (r"(?:n\.\s?d\.|no\s+date|forthcoming|in\s+press"
           r"|s\.\s?f\.|sin\s+fecha|s\.\s?d\.|sem\s+data|senza\s+data|o\.\s?J\.|ohne\s+Jahr"
           r"|f\.\s?a\.|f[ăa]r[ăa]\s+an|en\s+prensa|no\s+prelo|in\s+stampa|im\s+Druck|[îi]n\s+curs\s+de\s+apari[țt]ie)")
YEAR_OR_ND = r"(?:" + YEAR + r"|" + NO_DATE + r")"

# "p. 14" / "pp. 227-229" / "pp. 14, 19" / roman numerals
PAGE_REF = r"(?:pp?\.|pg\.|p[áa]gs?\.|pagg?\.|S\.)\s*[\divxlcIVXLC][\divxlcIVXLC\-–,\s]*"

# A capital of any of the seven languages, then letters (2026-10-10: 'Álvarez', 'Ștefan', 'Öztürk' were not names)
UPPER = "A-ZÀ-ÖØ-ÞĂĄĆČĎĐĘĚĹĽŁŃŇŐŔŘŚŞŠŢŤŮŰŹŻŽȘȚ"
NAME_WORD = r"[" + UPPER + r"][A-Za-z\-'’À-ſșțȘȚ.]*"
# the word for "and" between authors, in every supported language
JOIN = r"(?:and|&|y|e|und|u\.|și|si|et)"

LEADING_ARTICLES = {"the", "a", "an", "el", "la", "los", "las", "un", "una", "o", "os", "as", "um", "uma",
                    "il", "lo", "gli", "le", "der", "die", "das", "ein", "eine"}

# Trailing nouns that mark a citation as legislation / statutory instrument.
LEGISLATION_NOUNS = {
    "act", "acts", "regulations", "regulation", "order", "orders", "bill",
    "directive", "convention", "code", "rules", "scheme", "charter",
}

# Capitalised words that precede a bracketed year without being authors.
NON_AUTHOR_LEADS = {
    "table", "tables", "figure", "figures", "fig", "appendix", "appendices",
    "section", "chapter", "volume", "box", "part", "step", "phase", "stage",
    "model", "version", "question", "item", "cohort", "wave",
    "tabla", "tabela", "tabella", "tabelle", "figura", "abbildung", "abb", "anexo", "anexa", "apéndice", "apêndice",
    "appendice", "anhang", "capítulo", "capitolo", "kapitel", "capitol", "sección", "secção", "sezione", "abschnitt",
}

# Capitalised words that open a sentence in front of a narrative citation
# ('As Smith (2020) notes'); dropped so they are not read as part of a name.
SENTENCE_LEADS = {
    "as", "in", "by", "for", "from", "with", "on", "at", "to", "of", "per",
    "while", "whilst", "whereas", "although", "though", "when", "since",
    "según", "segundo", "secondo", "laut", "conform", "potrivit", "como", "wie", "come", "para", "per", "nach",
    "en", "em", "nel", "nella", "im", "în", "in",
    "because", "if", "after", "before", "unlike", "like", "following",
    "according", "similarly", "however", "moreover", "furthermore",
    "likewise", "conversely", "indeed", "notably", "thus", "hence", "also",
    "and", "but", "yet", "so", "both", "here", "there", "then", "this",
    "these", "those", "that", "later", "earlier", "recently", "only", "even",
    "where",
}


# --------------------------------------------------------------------------
# Normalisation
# --------------------------------------------------------------------------

def _strip_markdown(s):
    return re.sub(r"[*_`]", "", s)


def _clean_author_string(raw):
    """Tidy whitespace, markdown and any leading article off an author string."""
    raw = re.sub(r"\s+", " ", _strip_markdown(raw)).strip()
    words = raw.split(" ")
    if words and words[0].lower().strip(".,") in LEADING_ARTICLES:
        words = words[1:]
    return " ".join(words).strip(" ,;:&.")


def _is_personal_name(author):
    """Harvard personal entries carry initials: 'Surname, I.' / 'Surname, I.J.'"""
    return bool(re.search(r",\s*[A-Z]\.", author))


def _normalise_year(year):
    y = re.sub(r"\s+", " ", (year or "")).strip().lower()
    if re.match(r"^(?:n\.\s?d\.|no date)$", y):
        return "n.d."
    return y


def normalise_key(author, year):
    """Reduce any author form to a comparable 'lead-name (year)' key.

    The same function is applied to in-text citations and to reference list
    entries; that is what makes the bidirectional comparison meaningful.
    """
    a = _clean_author_string(author)
    a = re.sub(r"\bet\s+al\.?", "", a, flags=re.I).strip(" ,;&.")
    if _is_personal_name(a):
        lead = a.split(",")[0]
    else:
        # 'Holt-Lunstad and Smith' -> 'Holt-Lunstad'
        # 'Brown, Taylor and Nguyen' -> 'Brown'
        # institutional names contain neither, so they survive whole
        lead = re.split(r"\s+" + JOIN + r"\s+|,\s*", a)[0]
    lead = lead.strip(" .,;&")
    return lead.lower() + " (" + _normalise_year(year) + ")"


def is_legislation(key):
    name = key.rsplit("(", 1)[0].strip()
    last = name.split(" ")[-1].lower() if name else ""
    return last in LEGISLATION_NOUNS


def _lead_word(author):
    a = _clean_author_string(author)
    return a.split(" ")[0].lower().strip(".,") if a else ""


# --------------------------------------------------------------------------
# In-text citation extraction
# --------------------------------------------------------------------------

def _split_author_year(chunk):
    """Split 'Smith and Jones, 2015, pp. 227-229' into author, year, page flag."""
    # not \b...\b: a word boundary cannot follow the final '.' of 'n.d.' / 's.f.', so '(Smith, n.d.)' was never
    # seen and its entry was reported as never cited (found 2026-10-10)
    m = re.search(r"(?<!\w)(" + YEAR_OR_ND + r")(?!\w)", chunk, flags=re.I)
    if not m:
        return None
    author = chunk[:m.start()].strip(" ,;&")
    year = m.group(1)
    tail = chunk[m.end():]
    has_page = bool(re.search(PAGE_REF, tail, flags=re.I))
    if not author or not re.match(r"^" + NAME_WORD, author):
        return None
    return author, year, has_page


def _narrative_author(author, year, known_keys):
    """Pick the author out of the words captured before a narrative year.

    The capture can hold more than the name: a sentence opener ('As Smith'),
    or a word before an institution ('Evidence from Office for National
    Statistics'). With the reference list at hand, the longest reading that
    matches an entry wins. Otherwise sentence openers are dropped.
    """
    words = author.split()
    if known_keys:
        for i in range(len(words)):
            if words[i][:1].isupper():
                candidate = " ".join(words[i:])
                if normalise_key(candidate, year) in known_keys:
                    return candidate
    while len(words) > 1 and words[0].lower() in SENTENCE_LEADS:
        words = words[1:]
    return " ".join(words)


def extract_inline_citations(text, known_keys=None):
    """Return a list of dicts: {key, raw, has_page, style}.

    Handles parenthetical citations (including several separated by ';') and
    narrative citations, across personal, multi-author and institutional
    author forms. `known_keys` (the reference list's keys) lets a narrative
    citation be read the way the reference list names it.
    """
    found = []

    # 1. Parenthetical: (Author, 2020) / (A and B, 2020, p. 4) / (A, 2020; B, 2021)
    for inner in re.findall(r"\(([^()]*)\)", text):
        for chunk in inner.split(";"):
            chunk = _strip_markdown(chunk).strip()
            if not chunk:
                continue
            parsed = _split_author_year(chunk)
            if not parsed:
                continue
            author, year, has_page = parsed
            if _lead_word(author) in NON_AUTHOR_LEADS:
                continue
            found.append({
                "key": normalise_key(author, year),
                "raw": "(" + chunk + ")",
                "has_page": has_page,
                "style": "parenthetical",
            })

    # 2. Narrative: Author (2020) / Institution Name (2020) / Author et al.
    #    (2020) / Taylor and Nguyen (2017) / Department of Health and Social
    #    Care (2021). Small joining words are allowed between capitalised
    #    words; which reading is the author is settled by _narrative_author.
    narrative = re.compile(
        r"((?:" + NAME_WORD + r"(?:\s+(?:and|&|of|for|the|on|in|y|e|und|si|și|de|del|da|do|dos|di|della|für|der"
        r"|des|al|pentru)\s+|\s+)){0,7}"
        + NAME_WORD + r"(?:\s+et\s+al\.?)?)\s*"
        r"\(\s*(" + YEAR_OR_ND + r")\s*((?:,\s*" + PAGE_REF + r")?)\s*\)"
    )
    for m in narrative.finditer(_strip_markdown(text)):
        author, year, page = m.group(1), m.group(2), m.group(3)
        author = _narrative_author(author, year, known_keys)
        if _lead_word(author) in NON_AUTHOR_LEADS:
            continue
        found.append({
            "key": normalise_key(author, year),
            "raw": "%s (%s%s)" % (author, year, page.rstrip()),
            "has_page": bool(page.strip()),
            "style": "narrative",
        })

    return found


# --------------------------------------------------------------------------
# Reference list extraction
# --------------------------------------------------------------------------

REF_HEADING = (r"(?:References?|Bibliography|Reference\s+List|Works\s+Cited|Referencias(?:\s+bibliogr[áa]ficas)?"
               r"|Bibliograf[íi]a|Refer[êe]ncias(?:\s+bibliogr[áa]ficas)?|Riferimenti(?:\s+bibliografici)?"
               r"|Literaturverzeichnis|Literatur|Quellenverzeichnis|Bibliographie|Literaturangaben|Bibliografie"
               r"|Referin[țt]e(?:\s+bibliografice)?)")


def split_body_and_references(text):
    """Return (body_text, reference_section_text)."""
    m = re.search(r"(?:^|\n)[ \t]*#*[ \t]*" + REF_HEADING + r"[ \t]*:?[ \t]*\n",
                  text, flags=re.IGNORECASE)
    if not m:
        return text, ""
    return text[:m.start()], text[m.end():]


def _group_entries(ref_text):
    """Group the reference section into one string per entry.

    An entry starts at the left margin; hanging-indent continuation lines are
    folded into the entry above them.
    """
    entries = []
    for raw_line in ref_text.splitlines():
        if not raw_line.strip():
            continue
        stripped = _strip_markdown(raw_line).strip()
        indented = bool(re.match(r"^[ \t]{2,}", raw_line))
        starts_entry = (
            not indented
            and re.match(r"^[\-\*\d.\s]*" + NAME_WORD, stripped)
            and re.search(r"\(\s*" + YEAR_OR_ND + r"\s*\)|\b" + YEAR + r"\b",
                          stripped, flags=re.I)
        )
        if starts_entry or not entries:
            entries.append(stripped)
        else:
            entries[-1] = entries[-1] + " " + stripped
    return [e for e in entries if e.strip()]


def extract_reference_list(text):
    """Return (keys, display_lines, legislation_keys) for the reference list."""
    _, ref_text = split_body_and_references(text)
    if not ref_text.strip():
        return [], [], []

    keys, lines, legislation = [], [], []
    leg_alternatives = "|".join(sorted(LEGISLATION_NOUNS))

    for entry in _group_entries(ref_text):
        entry = re.sub(r"^[\-\*•]\s*", "", entry).strip()

        # Preferred form: author(s) followed by a bracketed year.
        m = re.search(r"^(.*?)\(\s*(" + YEAR_OR_ND + r")\s*\)", entry, flags=re.I)
        if m and m.group(1).strip(" ,;&."):
            key = normalise_key(m.group(1), m.group(2))
            keys.append(key)
            lines.append(entry[:120])
            if is_legislation(key):
                legislation.append(key)
            continue

        # Legislation: 'Equality Act 2010. London: HMSO.' (no brackets)
        leg = re.search(r"^(.*?\b(?:" + leg_alternatives + r"))\s+(" + YEAR + r")\b",
                        entry, flags=re.IGNORECASE)
        if leg:
            key = normalise_key(leg.group(1), leg.group(2))
            keys.append(key)
            lines.append(entry[:120])
            legislation.append(key)
            continue

        # Fallback: first bare year in the entry.
        m2 = re.search(r"^(.*?)\b(" + YEAR_OR_ND + r")\b", entry, flags=re.I)
        if m2 and m2.group(1).strip(" ,;&."):
            key = normalise_key(m2.group(1), m2.group(2))
            keys.append(key)
            lines.append(entry[:120])

    return keys, lines, legislation


# --------------------------------------------------------------------------
# Alphabetical ordering
# --------------------------------------------------------------------------

def sort_key(entry):
    """Filing order for a reference entry.

    Matches build_word.py's sort key, so the markdown and the built document
    order the list identically. Markdown emphasis and a leading article are
    ignored, as Harvard files on the first significant word of the author or
    institution name.
    """
    import unicodedata
    clean = re.sub(r"[*_`]", "", entry).strip().lower()
    clean = re.sub(r"^[\-\*•]\s*", "", clean)
    clean = re.sub(r"^(?:" + "|".join(sorted(LEADING_ARTICLES, key=len, reverse=True)) + r")\s+", "", clean)
    # Álvarez files with Alvarez, Ștefan with Stefan: letters with diacritics sort with their base letter
    return "".join(c for c in unicodedata.normalize("NFKD", clean) if not unicodedata.combining(c))


def check_alphabetical_order(text):
    """Report reference entries that are out of alphabetical order.

    Returns (is_ordered, out_of_place, correct_order). Only the entries that
    break the sequence are named, so a single misfiled entry is reported as
    one fault rather than shifting everything after it.
    """
    _, ref_text = split_body_and_references(text)
    if not ref_text.strip():
        return True, [], []

    entries = _group_entries(ref_text)
    if len(entries) < 2:
        return True, [], entries

    keys = [sort_key(e) for e in entries]
    correct = sorted(entries, key=sort_key)

    # Longest non-decreasing run: everything outside it is misfiled.
    n = len(keys)
    best_len = [1] * n
    previous = [-1] * n
    for i in range(1, n):
        for j in range(i):
            if keys[j] <= keys[i] and best_len[j] + 1 > best_len[i]:
                best_len[i] = best_len[j] + 1
                previous[i] = j
    end = best_len.index(max(best_len))
    in_sequence = set()
    while end != -1:
        in_sequence.add(end)
        end = previous[end]

    out_of_place = [(i + 1, entries[i]) for i in range(n)
                    if i not in in_sequence]
    return (not out_of_place), out_of_place, correct


# --------------------------------------------------------------------------
# Direct quotations
# --------------------------------------------------------------------------
#
# Harvard requires every direct quotation to carry its author, year and page
# right beside it. A quotation is found in three forms:
#
#   "double quotes"  /  curly double      - always a quotation boundary
#   'single quotes'  /  curly single      - the UK style Cite Them Right uses;
#                                           an apostrophe inside a word
#                                           (don't, someone's) is never taken
#                                           as a boundary
#   > block quotation lines (markdown)
#
# Quotations of fewer than QUOTE_MIN_WORDS words are left alone: two or three
# quoted words are almost always a term ("person-centred"), not a quotation.
#
# The citation may sit in the same sentence, before the quotation (narrative:
# Smith (2020, p. 4) argues that "...") or after it ("..." (Smith, 2020,
# p. 4)), or in the sentence immediately before. The quotation's own words
# never count as its citation.

QUOTE_MIN_WORDS = 4

LOCATOR = r"(?:" + PAGE_REF + r"|\bparas?\.?\s*\d+|\bparagraph\s+\d+)"

_DOUBLE_QUOTES = [re.compile(r'"([^"\n]+)"'),
                  re.compile("\u201c([^\u201c\u201d\n]+)\u201d")]
_SINGLE_QUOTE = re.compile(
    "(?<![\\w'\u2019])['\u2018](?=[^\\s'\u2018\u2019])"   # opener
    "([^\n]+?)"
    "(?<=\\S)['\u2019](?![A-Za-z0-9])")                  # closer

_ABBREVIATION = re.compile(
    r"(?:^|[\s(])(?:p|pp|al|e\.g|i\.e|cf|vs|ed|eds|no|vol|paras?|n\.d|"
    r"dr|mr|mrs|ms|prof|st|fig|approx|etc|op|cit|ibid)\.$", re.I)


def _quote_spans(paragraph):
    """(start, end, inner_text) for each quotation, outermost first."""
    spans = []
    for rx in _DOUBLE_QUOTES + [_SINGLE_QUOTE]:
        for m in rx.finditer(paragraph):
            s, e = m.start(), m.end()
            if any(s < ee and e > ss for ss, ee, _ in spans):
                continue        # nested or overlapping: the outer one wins
            spans.append((s, e, m.group(1)))
    return sorted(spans)


def _sentence_spans(text):
    """Split a paragraph into sentence spans.

    Full stops inside brackets ('p. 4', 'n.d.') and after common
    abbreviations do not end a sentence, and neither does one followed by a
    lower-case word.
    """
    spans, start, depth = [], 0, 0
    for i, ch in enumerate(text):
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth = max(0, depth - 1)
        elif ch in ".!?" and depth == 0:
            following = text[i + 1:i + 2]
            if following and not following.isspace():
                continue
            if ch == "." and _ABBREVIATION.search(text[max(0, i - 8):i + 1]):
                continue
            rest = text[i + 1:].lstrip()
            if rest and not (rest[0].isupper() or rest[0].isdigit()
                             or rest[0] in "\"'(\u201c\u2018"):
                continue
            spans.append((start, i + 1))
            start = i + 1
    if text[start:].strip():
        spans.append((start, len(text)))
    return spans or [(0, len(text))]


def _attribution(context, narrative_only=False):
    """(cited keys, has locator) for the text around a quotation."""
    cites = extract_inline_citations(context)
    if narrative_only:
        cites = [c for c in cites if c["style"] == "narrative"]
    keys = sorted({c["key"] for c in cites})
    has_page = (any(c["has_page"] for c in cites)
                or bool(re.search(LOCATOR, context, flags=re.I)))
    return keys, has_page


def _judge(quote, context, previous_sentence=""):
    keys, has_page = _attribution(context)
    if not keys and previous_sentence:
        # 'Smith (2020, p. 4) was blunt. "..."' - the speaker introduced by
        # name one sentence earlier. Only a narrative citation counts: a
        # bracketed citation closing the previous sentence belongs to that
        # sentence's own claim, not to the quotation after it.
        keys, prev_page = _attribution(previous_sentence, narrative_only=True)
        has_page = has_page or prev_page
    if not keys:
        state = "no_citation"
    elif not has_page:
        state = "no_page"
    else:
        state = "ok"
    return {"quote": re.sub(r"\s+", " ", quote).strip(),
            "words": len(quote.split()), "status": state, "cited": keys}


def check_quotations(body_text):
    """Every direct quotation in the body, with whether it is attributed.

    Returns a list of dicts: {quote, words, status, cited}, where status is
    'ok', 'no_page' (author and year but no page or paragraph) or
    'no_citation' (nothing beside it at all).
    """
    text = re.sub(r"<!--.*?-->", "", body_text, flags=re.DOTALL)
    lines = text.split("\n")
    results = []

    # Block quotations first, then blank them out of the running text.
    i = 0
    while i < len(lines):
        if not lines[i].lstrip().startswith(">"):
            i += 1
            continue
        j = i
        block = []
        while j < len(lines) and lines[j].lstrip().startswith(">"):
            block.append(lines[j].lstrip()[1:].strip())
            lines[j] = ""
            j += 1
        quote = " ".join(b for b in block if b)
        before = next((lines[k] for k in range(i - 1, -1, -1)
                       if lines[k].strip()), "")
        after = next((lines[k] for k in range(j, len(lines))
                      if lines[k].strip()), "")
        b0, b1 = _sentence_spans(before)[-1] if before.strip() else (0, 0)
        a0, a1 = _sentence_spans(after)[0] if after.strip() else (0, 0)
        context = " ".join([before[b0:b1], quote, after[a0:a1]])
        if len(quote.split()) >= QUOTE_MIN_WORDS:
            results.append(_judge(quote, context))
        i = j

    # Running text, paragraph by paragraph. Headings and table rows are
    # labels, not prose.
    kept = [l for l in lines if not l.lstrip().startswith(("#", "|"))]
    for paragraph in re.split(r"\n\s*\n", "\n".join(kept)):
        paragraph = re.sub(r"\s*\n\s*", " ", paragraph).strip()
        paragraph = re.sub(r"^(?:[\-\*\u2022]|\d+\.)\s+", "", paragraph)
        spans = _quote_spans(paragraph)
        if not spans:
            continue
        # Mask each quotation so its own full stops cannot end a sentence
        # and its own words cannot count as its citation.
        masked = list(paragraph)
        for s, e, _ in spans:
            for k in range(s + 1, e - 1):
                if not paragraph[k].isspace():
                    masked[k] = "x"
        masked = "".join(masked)
        sentences = _sentence_spans(masked)

        for s, e, inner in spans:
            if len(inner.split()) < QUOTE_MIN_WORDS:
                continue
            first = next((n for n, (a, b) in enumerate(sentences) if b > s), 0)
            last = next((n for n, (a, b) in enumerate(sentences) if b >= e),
                        len(sentences) - 1)
            a, b = sentences[first][0], sentences[last][1]
            outside = masked[a:s] + " " + masked[e:b]
            previous = ""
            if first > 0:
                pa, pb = sentences[first - 1]
                previous = masked[pa:pb]
            results.append(_judge(inner, outside, previous))
    return results


# --------------------------------------------------------------------------
# Reading finished documents: Word and PowerPoint as well as markdown
# --------------------------------------------------------------------------

_REF_TITLE = re.compile(r"^\s*" + REF_HEADING
                        + r"\s*(?:\((?:cont\.?|continued)\))?\s*:?\s*$", re.I)


def _shape_paragraphs(shape):
    """Every paragraph of text in a shape, groups and tables included."""
    out = []
    if shape.shape_type == 6:                       # a group
        for inner in shape.shapes:
            out.extend(_shape_paragraphs(inner))
        return out
    if getattr(shape, "has_table", False):
        for row in shape.table.rows:
            out.append(" | ".join(c.text.strip() for c in row.cells))
        return out
    if getattr(shape, "has_text_frame", False):
        for p in shape.text_frame.paragraphs:
            t = "".join(r.text for r in p.runs).strip()
            if t:
                out.append(t)
    return out


def pptx_to_markdown(path):
    """Return (slides_markdown, notes_markdown) for a PowerPoint file.

    Slide titles become headings. A slide titled 'References' (or a text box
    that starts with that word, as on a poster) is gathered into one
    reference list at the end, however many slides it spans. Footer, date
    and slide-number placeholders are furniture and are skipped.
    """
    from pptx import Presentation
    from pptx.enum.shapes import PP_PLACEHOLDER
    furniture = {PP_PLACEHOLDER.FOOTER, PP_PLACEHOLDER.DATE,
                 PP_PLACEHOLDER.SLIDE_NUMBER}

    prs = Presentation(path)
    body, notes, refs = [], [], []
    for number, slide in enumerate(prs.slides, 1):
        title_shape = slide.shapes.title
        title = title_shape.text.strip() if title_shape is not None else ""
        # A reference slide is recognised by its title placeholder, or - in
        # decks built without placeholders, as the harness builds them - by a
        # text box holding nothing but the word 'References'. Everything
        # after it on that slide is the list. On a poster the heading shares
        # its box with the entries, and only that box is the list.
        refs_mode = bool(_REF_TITLE.match(title))
        if not refs_mode:
            body.append("## %s" % (title or "Slide %d" % number))
        for shape in slide.shapes:
            if title_shape is not None and shape.shape_id == title_shape.shape_id:
                continue
            if shape.is_placeholder and \
                    shape.placeholder_format.type in furniture:
                continue
            paragraphs = _shape_paragraphs(shape)
            if not paragraphs:
                continue
            if refs_mode:
                refs.extend(p for p in paragraphs if not _REF_TITLE.match(p))
            elif _REF_TITLE.match(paragraphs[0]):
                if len(paragraphs) == 1:
                    refs_mode = True
                refs.extend(paragraphs[1:])
            else:
                body.append("\n\n".join(paragraphs))
        if slide.has_notes_slide:
            text = slide.notes_slide.notes_text_frame.text.strip()
            if text:
                notes.append(text)

    slides_md = "\n\n".join(body)
    if refs:
        slides_md += "\n\n## References\n\n" + "\n".join(refs) + "\n"
    return slides_md, "\n\n".join(notes)


def load_document(path):
    """Return (text, speaker_notes) for .md, .txt, .docx or .pptx.

    The text always ends with its reference list, so the audit reads a Word
    document or a slide deck exactly as it reads a markdown draft.
    """
    low = path.lower()
    if low.endswith(".docx"):
        from ..office.read import _docx
        return _docx(path), ""
    if low.endswith(".pptx"):
        return pptx_to_markdown(path)
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read(), ""


# --------------------------------------------------------------------------
# Audit
# --------------------------------------------------------------------------

def audit_text(text, notes="", verbose=False):
    """Run the full audit on document text (markdown, or a converted file).

    `notes` is a slide deck's speaker notes. Citations there count, so a
    reference cited only in the notes is not called unused, but it is
    reported: an audience never sees the notes.

    Reference faults (orphans, unused, duplicates, order) are `issues` and
    fail the check. Quotation faults are `quote_warnings`: the work is still
    built and delivered, and the student adds the missing citation by hand.
    """
    body_text, _ = split_body_and_references(text)

    ref_keys, ref_lines, ref_legislation = extract_reference_list(text)
    known = set(ref_keys)
    slide_citations = extract_inline_citations(body_text, known)
    note_citations = extract_inline_citations(notes, known) if notes else []
    citations = slide_citations + note_citations

    cite_set = set(c["key"] for c in citations)
    ref_set = set(ref_keys)

    orphans_all = cite_set - ref_set
    orphans = sorted(k for k in orphans_all if not is_legislation(k))
    legislation_unmatched = sorted(k for k in orphans_all if is_legislation(k))
    unused = sorted(ref_set - cite_set)
    notes_only = sorted((set(c["key"] for c in note_citations)
                         - set(c["key"] for c in slide_citations)) & ref_set)

    ref_counter = Counter(ref_keys)
    duplicates = {k: v for k, v in ref_counter.items() if v > 1}

    ordered, out_of_place, correct_order = check_alphabetical_order(text)

    # Advisory: citation forms carrying no page number (advisory in every author-date style).
    no_page = sorted({c["raw"] for c in citations if not c["has_page"]})

    quotes = check_quotations(body_text) + (check_quotations(notes)
                                            if notes else [])
    quotes_unattributed = [q for q in quotes if q["status"] == "no_citation"]
    quotes_without_page = [q for q in quotes if q["status"] == "no_page"]

    issues = (len(orphans) + len(unused) + len(duplicates)
              + len(out_of_place))
    quote_warnings = len(quotes_unattributed) + len(quotes_without_page)

    result = {
        "total_citations": len(citations),
        "unique_citations": len(cite_set),
        "total_references": len(ref_keys),
        "orphans": orphans,
        "unused": unused,
        "duplicates": dict(duplicates),
        "legislation_unmatched": legislation_unmatched,
        "citations_without_page": no_page,
        "alphabetical": ordered,
        "out_of_order": [entry for _, entry in out_of_place],
        "correct_order": correct_order,
        "cited_only_in_notes": notes_only,
        "quotes": len(quotes),
        "quotes_unattributed": quotes_unattributed,
        "quotes_without_page": quotes_without_page,
        "quote_warnings": quote_warnings,
        "issues": issues,
    }
    return result


def audit_references(filepath, verbose=False):
    """Run the full audit on a .md, .txt, .docx or .pptx file."""
    text, notes = load_document(filepath)
    return audit_text(text, notes, verbose=verbose)


def _short(text, limit=90):
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[:limit - 3].rstrip() + "..."
