"""A situation report a reader can take in a few seconds at a time.

    fieldkit sitrep template [--lang ro]              the skeleton to fill in (seven headings)
    fieldkit sitrep check FILE                        the rules, applied: exit 3 on a FAIL
    fieldkit sitrep render FILE [--md|--json] [--order classic] [--lang ro]
    fieldkit sitrep example                           a report that passes, to copy from
    fieldkit sitrep doctor                            what this machine can run

# WHY THIS EXISTS

The academic harness is built for a student with ADHD, dyslexia and autism. It had no way to produce a
status report in the shape that student can use, and neither did the sessions that drive it. The owner's
instinct (2026-10-14) was the military SITREP: one line, where we started, what happened, what is left,
what I need from you. That instinct is right; what the research adds is the ORDER and the LIMITS.

# WHAT THE RESEARCH SAYS, AND WHAT THIS TOOL DOES WITH IT

Sources read on 2026-10-14 (secondary where the original page was not reachable):

  - Ako Aotearoa, Dyslexia-Friendly Style Guide (July 2023), adapted from the British Dyslexia Association:
    "Use short, simple sentences and provide clear instructions"; "Be concise - avoid the use of long
    paragraphs"; "Consider using bullet points or numbering"; "Avoid abbreviations where possible. Always use
    the full word when first used"; "Avoid using, or at least overusing, italics and underlines", bold for
    emphasis; "Avoid or limit the use of capital letters for whole words"; "Add extra space around the heading
    and between paragraphs"; "Where tasks are included, clearly indicate the must-do parts"; "Chunk the learning".
  - Scope (citing the National Autistic Society): autistic people "are more likely to take phrases and
    expressions literally"; metaphor and jargon are barriers; headings and bullets over long passages.
  - Workplace neurodiversity guidance (TU Dublin 2026; SDU June 2026; AHRI): be explicit, avoid ambiguous
    instructions, avoid idioms such as "circle back", follow spoken words with written ones, bullet points
    help ADHD readers keep attention.
  - CHADD and the Western Australian working-memory sheet: reduce the material, break tasks into smaller
    pieces, written cues and checklists, because working-memory training does not generalise.
  - The "8-second attention span" figure is a marketing myth (BBC More or Less; Vogel; Posner: attention
    "has been remarkably stable across decades"). This tool does not assume a reader cannot focus. It
    assumes a reader may be interrupted at any point, so the report must survive being read in pieces.

What that means for the SHAPE (stated in source unless marked):

  1. The one line comes first and is a full sentence with the result in it.
  2. "What I need from you" comes SECOND, not last. A reader who stops after the first screen still knows
     what is being asked (model inference from "clearly indicate the must-do parts" and front-loading).
     `--order classic` keeps the owner's original military order for readers who prefer it.
  3. Each section is bullets or numbered steps, not paragraphs. Steps are capped (rules.yaml).
  4. Sentences are capped at 25 words (GOV.UK content design number; the dyslexia guide gives no number).
  5. Idioms (in all six languages, whatever --lang says), abbreviations, em-dashes, italics and
     underline fail the check. Bold is allowed.
  6. Headings exist in six languages; Romanian never alone (grey man, 2026-10-13).
  7. The whole thing is plain text, left aligned, wrapped at 80 columns, with a blank line around every
     heading. Fonts and colours are the reader's terminal's business, not this tool's.

# THE RULE THIS FOLLOWS

The report is a FILE the writer fills in. The tool does not write the content: no model, no tokens, no
variance. It checks the content against rules a person can read (rules.yaml) and renders it the same way
every time. Anything that depends on this machine's state is in `doctor`.
"""
import os
import re
import textwrap

import yaml

HERE = os.path.dirname(__file__)
LANGS = ("en", "es", "pt", "it", "de", "ro")
ORDER_ASK_FIRST = ["One line", "What I need from you", "Where we started", "What happened",
                   "What was achieved", "Where we are now", "What is left"]
ORDER_CLASSIC = ["One line", "Where we started", "What happened", "What was achieved",
                 "Where we are now", "What is left", "What I need from you"]
SECTIONS = ORDER_ASK_FIRST
NUMBERED = {"What happened", "What I need from you"}


def _load(name):
    with open(os.path.join(HERE, name), encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def headings():
    """-> {english: {lang: text}}, English included."""
    raw = _load("headings.yaml").get("headings", {})
    out = {}
    for en, others in raw.items():
        out[en] = {"en": en}
        for lang, text in zip(LANGS[1:], others or []):
            out[en][lang] = text
    return out


def rules():
    return _load("rules.yaml")


def heading(en, lang="en"):
    return headings().get(en, {}).get(lang) or en


def _heading_lookup():
    """any heading text in any language, lower-cased -> the English key."""
    look = {}
    for en, by_lang in headings().items():
        for text in by_lang.values():
            look[text.strip().lower()] = en
    return look


# ---------------------------------------------------------------- parse

def parse(text):
    """A markdown file with the seven headings (# or ##, any language, any order) -> {section: [items]}.
    Items are the bullet or numbered lines under a heading; a bare line is an item too (one per line)."""
    look = _heading_lookup()
    sections = {}
    current = None
    unknown = []
    for raw in text.splitlines():
        line = raw.rstrip()
        m = re.match(r"^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$", line)
        if m:
            key = look.get(m.group(1).strip().lower())
            if key is None:
                unknown.append(m.group(1).strip())
                current = None
            else:
                current = key
                sections.setdefault(current, [])
            continue
        if current is None or not line.strip():
            continue
        item = re.sub(r"^\s*(?:[-*+]|\d+[.)])\s+", "", line).strip()
        if item:
            sections[current].append(item)
    return {"sections": sections, "unknown_headings": unknown}


# ---------------------------------------------------------------- check

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")


def _sentences(item):
    return [s for s in _SENT_SPLIT.split(item.strip()) if s.strip()]


def _words(s):
    return len(re.findall(r"[^\s]+", s))


def check(text):
    """-> {"ok", "problems": [FAIL ...], "warnings": [WARN ...], "counts"}. Pure: the text and the rules."""
    r = rules()
    lim = r.get("limits", {})
    ban = r.get("banned", {})
    parsed = parse(text)
    sec = parsed["sections"]
    problems, warnings = [], []
    for h in parsed["unknown_headings"]:
        warnings.append(f"WARN heading not one of the seven, ignored: '{h}'")
    for name in SECTIONS:
        if name not in sec:
            problems.append(f"FAIL missing section: '{name}'")
        elif not sec[name] and name != "What I need from you":
            problems.append(f"FAIL empty section: '{name}' (write 'Nothing.' if that is the truth)")
    one = sec.get("One line", [])
    if len(one) > 1:
        problems.append(f"FAIL 'One line' has {len(one)} lines; it is one sentence")
    if one:
        if len(_sentences(one[0])) > 1:
            problems.append("FAIL 'One line' is more than one sentence")
        n = _words(one[0])
        if n > lim.get("one_line_words", 25):
            problems.append(f"FAIL 'One line' is {n} words; the limit is {lim.get('one_line_words', 25)}")
        if not re.search(r"[.!?]$", one[0]):
            problems.append("FAIL 'One line' does not end with a full stop: it is a sentence, not a title")
    steps = sec.get("What happened", [])
    if len(steps) > lim.get("steps_max", 7):
        warnings.append(f"WARN 'What happened' has {len(steps)} steps; above {lim.get('steps_max', 7)} the list "
                        f"itself needs chunking")
    asks = sec.get("What I need from you", [])
    if len(asks) > lim.get("asks_max", 3):
        problems.append(f"FAIL {len(asks)} asks; the limit is {lim.get('asks_max', 3)}. More than that is a "
                        f"to-do list, and a to-do list is a different document")
    for i, a in enumerate(asks, 1):
        if len(_sentences(a)) > 1:
            problems.append(f"FAIL ask {i} is more than one sentence: one ask, one sentence")
        # 2026-10-14, the owner, from the Windows harness: a command handed to a person carries its full path, so it
        # runs from whatever folder their terminal opened in. A bare program name is a command that fails for them.
        if re.search(r"(?<![/\\\w.~-])(fieldkit|python3?|bash|git|mach)\s+\S", a):
            warnings.append(f"WARN ask {i} hands over a bare command; give the full path to the program "
                            f"(the owner's rule: it must run from any folder)")
    for name, items in sec.items():
        for item in items:
            low = item.lower()
            for s in _sentences(item):
                n = _words(s)
                if n > lim.get("sentence_words", 25):
                    problems.append(f"FAIL {n}-word sentence under '{name}' (limit {lim.get('sentence_words', 25)}):"
                                    f" '{s[:60]}...'")
            for key in [k for k in ban if k == "idioms" or k.startswith("idioms_")]:
                for idiom in ban.get(key, []):
                    if re.search(r"(?<!\w)" + re.escape(idiom.lower()) + r"(?!\w)", low):
                        problems.append(f"FAIL idiom under '{name}': '{idiom}'. Say the literal thing")
            for ab in ban.get("abbreviations", []):
                if re.search(r"(?<![A-Za-z])" + re.escape(ab.lower()) + r"(?![A-Za-z])", low):
                    problems.append(f"FAIL abbreviation under '{name}': '{ab}'. Use the full words")
            for mk in ban.get("markup", []):
                if mk in item:
                    problems.append(f"FAIL banned mark under '{name}': '{mk}'")
            if re.search(r"(?<!\*)\*(?!\*)[^*\n]+\*(?!\*)|(?<!\w)_[^_\n]+_(?!\w)", item):
                problems.append(f"FAIL italics under '{name}': use bold for the first words, or nothing")
            if name != "What I need from you" and item.rstrip().endswith("?"):
                warnings.append(f"WARN question outside the asks under '{name}': a question reads as a request")
            caps = [w for w in re.findall(r"\b[A-Z]{4,}\b", item) if not re.search(r"\(" + w, text)
                    and not re.search(w + r"\s*\(", text)]
            for w in sorted(set(caps)):
                warnings.append(f"WARN '{w}' in capitals under '{name}' and not expanded anywhere; expand it "
                                f"once or write it as a word")
    ok = not problems
    return {"ok": ok, "problems": problems, "warnings": warnings,
            "counts": {k: len(v) for k, v in sec.items()}}


def exit_code(d):
    return 0 if d.get("ok") else 3


# ---------------------------------------------------------------- render

def _wrap(s, width, indent):
    return textwrap.fill(s, width=width, initial_indent=indent, subsequent_indent=" " * len(indent),
                         break_long_words=False, break_on_hyphens=False)


def render(text, lang="en", order="ask-first", md=False):
    """Checked first: a report that fails the check is not rendered, the problems are. -> {"ok", "text", ...}"""
    d = check(text)
    if not d["ok"]:
        return {"ok": False, "problems": d["problems"], "warnings": d["warnings"], "text": ""}
    if lang not in LANGS:
        return {"ok": False, "problems": [f"FAIL unknown language '{lang}'; one of {', '.join(LANGS)}"],
                "warnings": [], "text": ""}
    width = rules().get("limits", {}).get("line_width", 80)
    sec = parse(text)["sections"]
    seq = ORDER_CLASSIC if order == "classic" else ORDER_ASK_FIRST
    out = []
    for name in seq:
        items = sec.get(name, [])
        h = heading(name, lang)
        out.append(f"## {h}" if md else h)
        if not md:
            out.append("-" * len(h))
        out.append("")
        if not items:
            out.append("Nothing." if lang == "en" else "-")
        elif name == "One line":
            out.append(items[0] if md else _wrap(items[0], width, ""))
        else:
            for i, item in enumerate(items, 1):
                marker = f"{i}. " if name in NUMBERED else "- "
                out.append(f"{marker}{item}" if md else _wrap(item, width, marker))
        out.append("")
    return {"ok": True, "problems": [], "warnings": d["warnings"], "text": "\n".join(out).rstrip() + "\n",
            "lang": lang, "order": order}


def template(lang="en", order="ask-first"):
    seq = ORDER_CLASSIC if order == "classic" else ORDER_ASK_FIRST
    hints = {
        "One line": "The result, in one sentence of 25 words or fewer, with a full stop.",
        "What I need from you": "1. One ask, one sentence. No more than three. Delete this section's items if nothing is needed.",
        "Where we started": "- Two or three bullets. The state before the work.",
        "What happened": "1. Numbered steps, seven at most, in the order they happened.",
        "What was achieved": "- Each bullet something the reader could check.",
        "Where we are now": "- One to three bullets.",
        "What is left": "- Bullets. What is not done, and who it waits on.",
    }
    out = []
    for name in seq:
        out.append(f"# {heading(name, lang)}")
        out.append("")
        out.append(hints[name])
        out.append("")
    return "\n".join(out)


def example():
    with open(os.path.join(HERE, "example.md"), encoding="utf-8") as fh:
        return fh.read()


def doctor():
    """What this machine can run: the files are present, every heading has all six languages, the example
    passes its own check. Nothing here depends on a network or a model."""
    problems = []
    for f in ("headings.yaml", "rules.yaml", "example.md"):
        if not os.path.isfile(os.path.join(HERE, f)):
            problems.append(f"missing {f}")
    hs = headings()
    for en in SECTIONS:
        if en not in hs:
            problems.append(f"heading not in headings.yaml: '{en}'")
        else:
            missing = [l for l in LANGS if l not in hs[en]]
            if missing:
                problems.append(f"'{en}' has no {', '.join(missing)} heading")
    if not problems:
        d = check(example())
        if not d["ok"]:
            problems.extend("example fails its own check: " + p for p in d["problems"])
    return {"ok": not problems, "problems": problems, "languages": list(LANGS)}


def lines(d):
    out = []
    if d.get("text"):
        out.append(d["text"].rstrip())
    for p in d.get("problems", []):
        out.append(f"  {p}")
    for w in d.get("warnings", []):
        out.append(f"  {w}")
    if "ok" in d and not d.get("text"):
        out.append("  OK: the report passes" if d["ok"] else
                   f"  NEXT: fix the {len(d.get('problems', []))} FAIL line(s) above, then run the check again")
    return out
