"""The Gorilla checks: deterministic, fail closed, every reason reported.

dual_track.py's own validation (run by `render`) already checks the DITA
topics are present, the banned marketing and minimiser phrases, and that the
document carries numbers. These checks add what the Gorilla philosophy asks
for and what writers were previously told by hand:

Layman track
  - every required section is present and not trivial (word floors below);
  - "Before You Trust It" has numbered steps; "Key Concepts" gives a real-world
    comparison for EVERY concept row; the how-to section has steps with the
    commands in code blocks and says how to open PowerShell; troubleshooting
    and glossary entries exist; the limits (what it cannot do) are stated.
Both tracks
  - every `fieldkit ...` command shown parses against the real command line
    (subcommand, action and option names, taken from fieldkit.cli's argparse);
  - every number appears in MEASUREMENTS.md or in the group's own source, or
    sits next to "not measured";
  - every web address appears in the group's source or in MEASUREMENTS.md;
  - privacy: fieldkit.core.privacy's scan (secrets, home paths, emails, private
    words) plus banned words: AI assistant names, "the owner".

THE FLOORS were measured on 2026-10-02 (calibration run over the ten documents
rendered by hand before this workflow existed: agent-door, core,
tool-collection, office, builds-releases-thermal, exam, port-engine,
verify-and-build, install-and-proof, leakgate). Each floor sits about 20 per
cent below the smallest value found, so those documents pass and a document
clearly thinner than the thinnest accepted one fails. Words are counted as
runs of letters and digits, tables and code included.

    layman section            smallest found (group)       floor (words)
    Should You Run This?          72 (exam)                    60
    Worst Case, Honestly         132 (tool-collection)        100
    What Data This Touches       147 (office)                 110
    Before You Trust It          229 (tool-collection)        180
    The Big Picture              191 (office)                 150
    Key Concepts                 279 (exam)                   220
    How It Works                 550 (exam)                   440
    Quirky Things                255 (agent-door)             200
    how-to section               309 (exam)                   250
    If Something Goes Wrong      233 (exam)                   180
    Glossary                     111 (tool-collection)         90
    whole document              3612 (exam)                  2900

    layman counts             smallest found               floor
    trust steps                    4                           3
    key concept rows               7                           6
    words per comparison           5                           4
    how-to steps                   5                           4
    how-to code blocks             1 (0 in agent-door, fixed)  1
    troubleshooting entries        5                           4
    glossary terms                 7                           6
    limit statements              30                          15

    developer section         smallest found (group)       floor (words)
    Purpose                      123 (office)                 100
    Architecture                 183 (tool-collection)        140
    Tasks                        363 (tool-collection)        290
    Troubleshooting              191 (exam)                   150
    Impact If Removed             55 (office, tool-collection)  40
    whole document              2743 (tool-collection)       2200

A "limit statement" is a sentence containing cannot, can't, does not, do
not, is not, will not, never, untested, not tested or not measured. The
calibration script is kept as tests/test_gdocs.py's floor test: the committed
documents must keep passing these floors.
"""
import argparse
import re
import shlex

from ..core import privacy

LAYMAN_FLOORS = {
    "should_run": 60, "worst_case": 100, "data": 110, "trust": 180, "big_picture": 150,
    "key_concepts": 220, "how_it_works": 440, "quirky": 200, "usage": 250,
    "troubleshooting": 180, "glossary": 90,
}
LAYMAN_TOTAL_FLOOR = 2900
LAYMAN_COUNTS = {"trust_steps": 3, "concept_rows": 6, "analogy_words": 4, "usage_steps": 4,
                 "usage_code_blocks": 1, "troubleshooting_entries": 4, "glossary_terms": 6,
                 "limit_statements": 15}
DEV_FLOORS = {"purpose": 100, "architecture": 140, "tasks": 290, "troubleshooting": 150, "impact_removed": 40}
DEV_TOTAL_FLOOR = 2200

LAYMAN_HEADINGS = {
    "should_run": r"Should You Run This\??",
    "worst_case": r"Worst Case, Honestly",
    "data": r"What Data This Touches",
    "trust": r"Before You Trust It",
    "big_picture": r"The Big Picture",
    "key_concepts": r"Key Concepts",
    "how_it_works": r"How It Works.*",
    "quirky": r"Quirky Things Worth Knowing",
    "impact": r"What This Means For You",
    "off_switch": r"The Off Switch",
    "troubleshooting": r"If Something Goes Wrong",
    "why_dev": r"Why a Developer Would Do This",
    "open_source": r"Why It Matters That You Can Read This",
    "glossary": r"Glossary",
    "claims": r"Claim Sources",
}
LAYMAN_REQUIRED = ["should_run", "worst_case", "data", "trust", "big_picture", "key_concepts",
                   "how_it_works", "quirky", "troubleshooting", "glossary"]
DEV_HEADINGS = {"purpose": r"Purpose", "architecture": r"Architecture", "tasks": r"Tasks",
                "troubleshooting": r"Troubleshooting", "impact_removed": r"Impact If Removed",
                "claims": r"Claim Sources"}
DEV_REQUIRED = ["purpose", "architecture", "tasks", "troubleshooting", "impact_removed", "claims"]

ASSISTANT_NAMES = ["Claude", "Gemini", "ChatGPT", "Opus", "Sonnet", "Fable", "Luna"]
BANNED_WORDS = [(n, r"\b" + n + r"\b") for n in ASSISTANT_NAMES] + [("the owner", r"\bthe owner\b")]
HOME_PATHS = [("home path", r"%USERPROFILE%\\[^\s`]*\\[A-Za-z]"), ("home path", r"~[/\\][A-Za-z]")]
LIMIT_RX = re.compile(r"\b(cannot|can't|does not|do not|is not|will not|never|untested|not tested|"
                      r"not measured)\b", re.I)
NOT_MEASURED_RX = re.compile(r"not measured", re.I)
OPEN_POWERSHELL_RX = (r"Windows key|Start (menu|button|key)|Win(dows)?\s*\+\s*[XRS]\b|"
                      r"address bar[^.\n]{0,40}powershell")
# A line may name a command that does NOT exist, to say so (a docstring naming an unregistered
# command is a finding worth documenting). It must say so on the same line.
SAYS_NOT_A_COMMAND_RX = re.compile(r"not registered|does not exist|no such (sub)?command|"
                                   r"(real|CLI) subcommand is", re.I)
NUM_RX = re.compile(r"(?<![\d.,_])\d+(?:[,_]\d{3})*(?:\.\d+)*")
URL_RX = re.compile(r"https?://[^\s)`'\"<>|\]]+")
SMALL_NUMBER = 10            # 0..10: step numbers, "1 of 5" style counts, single digits in names
WORD_RX = re.compile(r"[A-Za-z0-9][A-Za-z0-9'’_.-]*")


# -- parsing ---------------------------------------------------------------------
def words(text):
    return len(WORD_RX.findall(text or ""))


def sections(md):
    """-> list of (heading, body) for level-2 headings, in order."""
    out, head, buf = [], None, []
    in_code = False
    for line in md.splitlines():
        if line.lstrip().startswith("```"):
            in_code = not in_code
        if not in_code and line.startswith("## "):
            if head is not None:
                out.append((head, "\n".join(buf)))
            head, buf = line[3:].strip(), []
        elif head is not None:
            buf.append(line)
    if head is not None:
        out.append((head, "\n".join(buf)))
    return out


def find_section(secs, pattern):
    rx = re.compile(r"^" + pattern + r"$", re.I)
    for h, b in secs:
        if rx.match(h):
            return h, b
    return None, None


def usage_section(secs):
    """The how-to section: dual_track titles it from the filled JSON, so it is the heading
    that is not one of the fixed ones (the one with "You should see" if there are several)."""
    known = [re.compile(r"^" + p + r"$", re.I) for p in LAYMAN_HEADINGS.values()]
    rest = [(h, b) for h, b in secs if not any(k.match(h) for k in known)]
    for h, b in rest:
        if "You should see" in b or "Before you start" in b:
            return h, b
    return rest[0] if rest else (None, None)


BLANK_RX = re.compile(r"<[A-Za-z][^<>\n]{1,60}>")          # <your folder>, <name>, <path to file>
COMMAND_RX = re.compile(r"\s*(?:PS [^>]*>\s*)?(?:cd|fieldkit|python3?|py|pip|git|sudo|apt(?:-get)?|dnf|winget|"
                        r"Set-Location|Push-Location|copy|move|del|ls|dir|cat|type|\.\\|\./)\b", re.I)


def code_blocks(text):
    return re.findall(r"^[ \t]*```[^\n]*\n(.*?)^[ \t]*```", text or "", re.M | re.S)


def strip_code(text):
    text = strip_fences(text)
    return re.sub(r"`[^`\n]*`", " ", text)


def table_rows(text):
    rows = []
    for line in (text or "").splitlines():
        s = line.strip()
        if s.startswith("|") and not re.match(r"^\|[\s:|-]+\|$", s):
            cells = [c.strip() for c in re.split(r"(?<!\\)\|", s.strip("|"))]
            rows.append(cells)
    return rows[1:] if rows else []            # drop the header row


def sentences(text):
    return [s for s in re.split(r"(?<=[.!?])\s+|\n+", text or "") if s.strip()]


# -- commands ----------------------------------------------------------------------
def _sub(parser):
    for a in parser._actions:
        if isinstance(a, argparse._SubParsersAction):
            return a
    return None


def _placeholder(tok):
    return bool(re.search(r"[<>\[\]{}|.]|\.\.\.", tok)) or (tok.isupper() and len(tok) > 1)


def commands_in(md):
    """Every `fieldkit ...` command shown: lines in code blocks and inline code spans."""
    found = []
    for block in code_blocks(md):
        for line in block.splitlines():
            s = re.sub(r"^\s*(PS[^>]*>|\$|>)\s*", "", line).strip()
            s = re.sub(r"^python(3)?\s+-m\s+", "", s)
            if s.startswith("fieldkit "):
                found.append(s)
    for line in strip_fences(md).splitlines():
        if SAYS_NOT_A_COMMAND_RX.search(line):
            continue                         # names a non-existent command in order to say so
        for span in re.findall(r"`([^`\n]+)`", line):
            s = re.sub(r"^python(3)?\s+-m\s+", "", span.strip())
            if s.startswith("fieldkit "):
                found.append(s)
    return found


def strip_fences(md):
    return re.sub(r"^[ \t]*```[^\n]*\n.*?^[ \t]*```", " ", md, flags=re.M | re.S)


def _tokens(cmd):
    cmd = re.split(r"\s(?:#|\||;|&&|>|2>)\s?", cmd + " ", maxsplit=1)[0]
    try:
        return shlex.split(cmd, posix=True)
    except ValueError:
        return cmd.split()


def check_command(cmd, parser=None):
    """-> None if `cmd` fits the real fieldkit command line, else the reason."""
    if parser is None:
        from .. import cli
        parser = cli.build_parser()
    toks = _tokens(cmd)[1:]
    if not toks:
        return None
    sp = _sub(parser)
    name = toks[0]
    if name.startswith("-"):
        return None if name in parser._option_string_actions else f"unknown option {name}"
    if _placeholder(name):
        return None
    if name not in sp.choices:
        return f"no subcommand '{name}' (have: {', '.join(sorted(sp.choices))})"
    p = sp.choices[name]
    rest = toks[1:]
    opts = dict(p._option_string_actions)
    words_ = [t for t in rest if not t.startswith("-")]
    nested = _sub(p)
    if nested is not None:
        if words_ and not _placeholder(words_[0]):
            if words_[0] not in nested.choices:
                return f"'{name}' has no action '{words_[0]}' (have: {', '.join(nested.choices)})"
            opts.update(nested.choices[words_[0]]._option_string_actions)
    else:
        pos = [a for a in p._actions if not a.option_strings]
        if pos and pos[0].choices and words_ and not _placeholder(words_[0]):
            # the first bare word fills the first positional only if no option took it
            first = rest.index(words_[0])
            prev = rest[first - 1] if first else None
            took = prev and prev.startswith("-") and prev.split("=")[0] in opts and \
                opts[prev.split("=")[0]].nargs != 0
            if not took and words_[0] not in pos[0].choices:
                return f"'{name}' has no action '{words_[0]}' (have: {', '.join(pos[0].choices)})"
    for t in rest:
        if t.startswith("-") and len(t) > 1 and not re.match(r"^-\d", t):
            o = t.split("=", 1)[0]
            if _placeholder(o):
                continue
            if o not in opts:
                return f"'{name}' has no option {o}"
    return None


# -- numbers and addresses ------------------------------------------------------------
def norm_number(tok):
    """'1,488' / '1_488' -> '1488'; '75.0' -> '75'; '02' -> '2'; versions keep their parts."""
    parts = [str(int(p)) if p.isdigit() else p for p in tok.replace(",", "").replace("_", "").split(".")]
    if len(parts) == 2 and set(parts[1]) <= {"0"}:
        parts = parts[:1]
    return ".".join(parts)


def numbers_in(text):
    return {norm_number(m.group(0)) for m in NUM_RX.finditer(text or "")}


def allowed_numbers(texts):
    out = set()
    for t in texts:
        out |= numbers_in(t)
    return out


def _scaffold_free(md):
    """Remove what the renderer writes itself (header date, step labels, footer)."""
    md = re.sub(r"^> Generated .*$", "", md, flags=re.M)
    md = re.sub(r"\*\*Step \d+:\*\*", "", md)
    md = re.sub(r"^### Step \d+:", "###", md, flags=re.M)
    return md


def number_findings(md, allowed):
    out = []
    # Commands are example input the reader types (a --seconds value is their choice), so the
    # numbers inside code blocks and inside `fieldkit ...` spans are not claims; everything else is.
    text = strip_fences(_scaffold_free(md))
    text = re.sub(r"`(?:python3?\s+-m\s+)?fieldkit [^`\n]*`", " ", text)
    for line in text.splitlines():
        for m in NUM_RX.finditer(line):
            n = norm_number(m.group(0))
            if n in allowed:
                continue
            try:
                if float(n) <= SMALL_NUMBER and "." not in n:
                    continue
            except ValueError:
                pass
            window = line[max(0, m.start() - 80): m.end() + 80]
            if NOT_MEASURED_RX.search(window):
                continue
            out.append(f"number {m.group(0)} is in neither MEASUREMENTS.md nor the group's source "
                       f"(write 'not measured' or cite it): ...{line[max(0, m.start() - 40): m.end() + 30].strip()}...")
    return out


def url_findings(md, corpus):
    out = []
    for u in URL_RX.findall(md):
        u = u.rstrip(".,;:")
        if u not in corpus and u.rstrip("/") not in corpus:
            out.append(f"web address {u} is in neither the group's source nor MEASUREMENTS.md")
    return out


# -- privacy and banned words ------------------------------------------------------------
def prose_lines(md):
    """The document's lines with verbatim quotes blanked, for the banned-word rules:
    code blocks, `code spans` (quoted source: messages, file names) and the Evidence column
    of Claim Sources (an exact phrase from the input, by dual_track's own rule). Line
    numbers are kept. The privacy scan still reads everything."""
    out, in_code, in_claims = [], False, False
    for line in md.splitlines():
        if line.lstrip().startswith("```"):
            in_code = not in_code
            out.append("")
            continue
        if in_code:
            out.append("")
            continue
        if line.startswith("## "):
            in_claims = line[3:].strip().lower() == "claim sources"
        s = re.sub(r"`[^`\n]*`", " ", line)
        if in_claims and s.strip().startswith("|"):
            s = "|".join(re.split(r"(?<!\\)\|", s)[:3])
        out.append(s)
    return out


def _quoted_from_source(line, pos, corpus):
    """True when pos sits inside a "double-quoted" phrase that appears word for word in the source:
    a quotation of the code's own text, not the writer's voice."""
    if not corpus:
        return False
    for q in re.finditer(r"\"([^\"]{8,})\"|“([^”]{8,})”", line):
        if q.start() <= pos < q.end():
            phrase = " ".join((q.group(1) or q.group(2)).split())
            return phrase in corpus
    return False


def privacy_findings(md, terms=None, corpus=""):
    terms = privacy.private_terms() if terms is None else terms
    out = [f"privacy: {h['kind']} on line {h['line']} ({h['excerpt']})"
           for h in privacy.scan_text(md, terms=terms)]
    flat = " ".join(corpus.split()) if corpus else ""
    for lineno, line in enumerate(prose_lines(md), 1):
        for label, rx in BANNED_WORDS:
            for m in re.finditer(rx, line, re.I):
                if _quoted_from_source(line, m.start(), flat):
                    continue
                out.append(f"banned word '{m.group(0)}' on line {lineno}"
                           + (" (say 'the maintainer')" if label == "the owner" else
                              " (no AI assistant names in public docs)"))
        for label, rx in HOME_PATHS:
            if re.search(rx, line):
                out.append(f"{label} on line {lineno}")
    return out


# -- the track checks ---------------------------------------------------------------------
def check_layman(md, allowed_nums, url_corpus, parser=None, terms=None):
    """-> (findings, metrics). Findings empty = pass."""
    f, m = [], {}
    secs = sections(md)
    body = {}
    for key, pat in LAYMAN_HEADINGS.items():
        h, b = find_section(secs, pat)
        body[key] = b
    uh, ub = usage_section(secs)
    body["usage"] = ub
    m["usage_heading"] = uh
    for key in LAYMAN_REQUIRED + ["usage"]:
        if body.get(key) is None:
            f.append(f"layman: section missing: {LAYMAN_HEADINGS.get(key, 'how-to (usage task)')}")
            continue
        n = words(body[key])
        m[f"words_{key}"] = n
        floor = LAYMAN_FLOORS.get(key)
        if floor and n < floor:
            f.append(f"layman: section '{LAYMAN_HEADINGS.get(key, uh)}' has {n} words; the floor is {floor}")
    total = words(md)
    m["words_total"] = total
    if total < LAYMAN_TOTAL_FLOOR:
        f.append(f"layman: document has {total} words; the floor is {LAYMAN_TOTAL_FLOOR}")

    trust = body.get("trust") or ""
    m["trust_steps"] = len(re.findall(r"^\*\*Step \d+:\*\*", trust, re.M))
    rows = table_rows(body.get("key_concepts") or "")
    m["concept_rows"] = len(rows)
    thin = [r[0] for r in rows if len(r) < 3 or words(r[2]) < LAYMAN_COUNTS["analogy_words"]]
    m["analogy_words_min"] = min((words(r[2]) for r in rows if len(r) >= 3), default=0)
    if thin:
        f.append(f"layman: Key Concepts rows without a real-world comparison of at least "
                 f"{LAYMAN_COUNTS['analogy_words']} words: {', '.join(thin)}")
    usage = body.get("usage") or ""
    m["usage_steps"] = len(re.findall(r"^\*\*Step \d+:\*\*", usage, re.M))
    m["usage_code_blocks"] = len(code_blocks(usage))
    ts = body.get("troubleshooting") or ""
    m["troubleshooting_entries"] = len(re.findall(r"^\*\*.+\*\*\s*$", ts, re.M))
    m["glossary_terms"] = len(re.findall(r"^\*\*[^*]+\*\* — ", body.get("glossary") or "", re.M))
    m["limit_statements"] = sum(1 for s in sentences(strip_code(md)) if LIMIT_RX.search(s))
    for key in ("trust_steps", "concept_rows", "usage_steps", "usage_code_blocks",
                "troubleshooting_entries", "glossary_terms", "limit_statements"):
        if m[key] < LAYMAN_COUNTS[key]:
            f.append(f"layman: {key.replace('_', ' ')} = {m[key]}; the floor is {LAYMAN_COUNTS[key]}")
    m["powershell_how"] = bool(re.search(r"PowerShell", md, re.I) and re.search(OPEN_POWERSHELL_RX, md, re.I))
    if not m["powershell_how"]:
        f.append("layman: never says how to open PowerShell (e.g. 'press the Windows key, type "
                 "PowerShell, press Enter')")
    # a command with a blank the reader must fill in is pasted as it is (2026-10-10: `cd "<your Fieldkit folder>"`
    # from C:\\WINDOWS\\system32); the folder comes from `cd (fieldkit where)`
    blanks = [l.strip() for b in code_blocks(md) for l in b.splitlines()
              if BLANK_RX.search(l) and COMMAND_RX.match(l)]               # commands only, not sample output
    m["command_blanks"] = len(blanks)
    if blanks:
        f.append(f"layman: {len(blanks)} command(s) with a blank to fill in, e.g. {blanks[0][:80]!r}: a reader pastes "
                 f"it as it is. Use a command that finds the value itself, e.g. cd (fieldkit where)")
    f += _common(md, "layman", allowed_nums, url_corpus, parser, terms, m)
    return f, m


def check_developer(md, allowed_nums, url_corpus, parser=None, terms=None):
    f, m = [], {}
    secs = sections(md)
    for key in DEV_REQUIRED:
        h, b = find_section(secs, DEV_HEADINGS[key])
        if b is None:
            f.append(f"developer: section missing: {DEV_HEADINGS[key]}")
            continue
        n = words(b)
        m[f"words_{key}"] = n
        if key in DEV_FLOORS and n < DEV_FLOORS[key]:
            f.append(f"developer: section '{h}' has {n} words; the floor is {DEV_FLOORS[key]}")
    m["words_total"] = words(md)
    if m["words_total"] < DEV_TOTAL_FLOOR:
        f.append(f"developer: document has {m['words_total']} words; the floor is {DEV_TOTAL_FLOOR}")
    f += _common(md, "developer", allowed_nums, url_corpus, parser, terms, m)
    return f, m


def _common(md, track, allowed_nums, url_corpus, parser, terms, m):
    f = []
    # A fence that does not start its own line ("**Step 1:** ```bash") is not a code block in
    # Markdown: its closing fence opens one instead, and the rest of the page renders as code.
    for lineno, line in enumerate(md.splitlines(), 1):
        if "```" in line and not line.lstrip().startswith("```"):
            f.append(f"{track}: line {lineno}: a code fence must start its own line (in the filled JSON, "
                     f"put a blank line before ```): {line.strip()[:60]}")
    cmds = commands_in(md)
    m["commands"] = len(cmds)
    for c in cmds:
        why = check_command(c, parser)
        if why:
            f.append(f"{track}: command does not parse: `{c}`: {why}")
    nums = number_findings(md, allowed_nums)
    m["unsourced_numbers"] = len(nums)
    f += [f"{track}: {x}" for x in nums]
    f += [f"{track}: {x}" for x in url_findings(md, url_corpus)]
    f += [f"{track}: {x}" for x in privacy_findings(md, terms, url_corpus)]
    return f
