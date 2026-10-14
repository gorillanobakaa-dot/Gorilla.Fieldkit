# fieldkit.sitrep: a rule-checked, fixed-layout situation report (parse, check, render, template, doctor)

> Generated 2026-10-14 | Source: `sitrep`

---

## Purpose

fieldkit/sitrep/__init__.py is a pure-Python module (plus two YAML data files and one example) that turns a markdown file with seven known headings into a checked, fixed-layout plain-text or markdown report. It is a linter and a formatter, not a generator: the module states that no model writes the content, so the output has no variance. Trust level: read-only; it reads the report path it is given and its own three data files, writes nothing, and has no network or subprocess calls. It is reached through `fieldkit sitrep` in cli.py and is carded as `sitrep` in fieldkit/desk/tools.yaml with safety read-only and platforms linux, darwin, windows, freebsd. The module is new on 2026-10-14 and has not been run by its intended reader yet: the rules are drawn from published guidance for dyslexic and autistic readers and from the maintainer's own report shape, and none of them has been measured against a real reader. Treat every limit as a first setting to be revised with evidence, which is why each one sits in rules.yaml with its origin written beside it. The parser, the checker and the renderer are covered by tests/test_sitrep.py, one test per rule, each of which breaks the rule on purpose and expects the check to turn red.

## Known Alternatives Considered

The module docstring records one rejected alternative: letting a model write the report. The source says: "The tool does not write the content: no model, no tokens, no variance." It also records the rejected default ordering: the classic military order with the asks last, kept as `--order classic` and not dropped, with the stated reason that the asks-first order lets "a reader who stops after the first screen" know what is asked. No other alternatives are documented in the source material.

## Architecture

- **Pattern:** Functional pipeline over an in-memory dict: parse(text) -> {sections, unknown_headings}; check(text) -> {ok, problems, warnings, counts}; render(text, lang, order, md) calls check() first and refuses on any problem; template(), example() and doctor() are independent helpers. Data (headings, rules) is loaded from YAML on every call, not cached. The CLI layer in cli.py (cmd_sitrep) maps actions to these functions and uses _emit for --json.
- **Trust boundary:** The module trusts its own three data files (headings.yaml, rules.yaml, example.md) as correct and well-formed; a malformed rules.yaml surfaces as a yaml exception, not a refusal. It does not trust the report text: every heading is matched against the table, unknown headings are reported and their items dropped, and every item is checked. The CLI reads the report file with encoding utf-8-sig and converts OSError into a REFUSED exit.
- **Attack surface:** One file path from the command line, read as text; three package-relative YAML/markdown files. No eval, no subprocess, no network, no writes. A hostile report file can at most produce a long output or trip a regular expression on unusual input; the regexes are anchored and bounded by line length. A hostile rules.yaml (an attacker who can write into the installed package) can change what passes, which is the same trust as the package itself.
- **Dependencies:** `yaml (PyYAML, already a Fieldkit dependency)`, `os`, `re`, `textwrap`

## Flags & Configuration

| Name | Type | Default | Effect | Notes |
|------|------|---------|--------|-------|
| `action` | `string` | `none (required)` | template | check | render | example | doctor | positional; check and render require FILE |
| `file` | `string` | `none` | the report to check or render | read with utf-8-sig so a Windows BOM is tolerated; a missing file exits with REFUSED |
| `--lang` | `string` | `en` | heading language for template and render | one of en es pt it de ro; render refuses an unknown code with FAIL unknown language and does not fall back |
| `--order` | `string` | `ask-first` | ask-first puts What I need from you second; classic puts it last | affects template and render only; check is order-independent |
| `--md` | `bool` | `false` | render as markdown (## headings, no wrapping) instead of plain text (dash underlines, 80-column wrap) | the markdown output is for chat windows and files |
| `--json` | `bool` | `false` | machine-readable output from the common parser | check returns {ok, problems, warnings, counts}; render returns {ok, problems, warnings, text, lang, order} |

## API Surface

| Symbol | Description | Side Effects |
|--------|-------------|--------------|
| `headings()` | English heading -> {lang: text} for the six languages, read from headings.yaml | none |
| `rules()` | the parsed rules.yaml | none |
| `heading()` | one heading in one language, English if the language is missing | none |
| `parse()` | markdown with # headings in any language and order -> items per English section key | none |
| `check()` | applies rules.yaml; FAIL strings in problems, WARN strings in warnings | none |
| `exit_code()` | 0 if d["ok"] else 3 | none |
| `render()` | check, then the laid-out text; refuses on problems or an unknown language | none |
| `template()` | the seven headings with one hint line each | none |
| `example()` | the contents of example.md | none |
| `doctor()` | data files present, six languages per heading, example passes its own check | none |
| `lines()` | human lines for the CLI: text, then problems, then warnings, then OK or NEXT | none |

## Kill Switches

### `render(): `if not d["ok"]: return {"ok": False, ... "text": ""}``
- **Condition:** check() reports at least one FAIL
- **Effect:** no report text is produced; the problems are returned and the CLI exits 3
- reversible
- fix the report; there is no --force

### `render(): `if lang not in LANGS``
- **Condition:** an unknown language code
- **Effect:** refusal with FAIL unknown language; no English fallback
- reversible
- deliberate: a silent fallback would hide a typo

### `rules.yaml`
- **Condition:** always
- **Effect:** every numeric limit and banned list lives here; removing a line removes the rule
- reversible
- the module comments mark which numbers are stated in a source and which are model inference

## Dead Code

- **`render(): the `if not md: out.append("-" * len(h))` branch writes the underline only in text mode`** — not dead; listed to record that there is no dead code found in this module on 2026-10-14 (risk: none)

## Performance

- **CPU:** not measured
- **MEMORY:** not measured
- **IO:** four file reads per call (three package files and the report); headings.yaml and rules.yaml are re-read on every call to headings(), rules() and _heading_lookup(), so check() reads them several times. not measured
- **NOTES:** no caching by design; the files are small. No numbers exist in MEASUREMENTS.md for this group.

## Security

- **Remote execution:** none: no subprocess, no eval, no network
- **Data handling:** the report text stays in memory and goes to stdout; nothing is written
- **Attack surface:** one user-supplied path read as text; three package files
- **Notes:** regexes are per-line and bounded; the sentence splitter uses a lookbehind on [.!?] and a lookahead on a capital, digit, quote or bracket

## Error Conditions

| Error | Cause | Remedy |
|-------|-------|--------|
| `FAIL missing section: '<name>'` | a heading was absent or misspelt in every language | copy the heading from the template |
| `FAIL empty section: '<name>'` | a heading other than the asks had no items | write Nothing. if that is the truth |
| `FAIL 'One line' is N words; the limit is 25` | limits.one_line_words exceeded | shorten, or change the limit in rules.yaml with a reason |
| `FAIL N asks; the limit is 3` | limits.asks_max exceeded | cut to three; a longer list is a different document |
| `FAIL idiom under '<name>': '<phrase>'` | a phrase in banned.idioms matched on word boundaries, case-insensitively | say the literal thing |
| `FAIL abbreviation under '<name>': 'e.g.'` | a token in banned.abbreviations matched with non-letter boundaries | use the full words |
| `FAIL banned mark / FAIL italics` | an em-dash, a <u> tag, or single-star or underscore italics | use a comma, colon or brackets; bold (double star) is allowed |
| `FAIL unknown language 'xx'` | render called with a code outside LANGS | use en es pt it de ro |
| `REFUSED: cannot read FILE` | cli.py could not open the path (OSError) | check the path; the CLI exits non-zero |
| `yaml.YAMLError traceback` | a malformed headings.yaml or rules.yaml in the installed package | fix the file; this is not caught on purpose, because a broken rules file must not look like a passing report |

## Tasks

### Run the tests for this module

Before changing a rule or the parser, and after. tests/test_sitrep.py has one test per rule, each breaking the rule on purpose.

**Prerequisites:**
- Fieldkit installed with the test extra (pytest)

**Step 1:** Run:

```powershell
cd (fieldkit where)
```
  - Expected: the Fieldkit folder
**Step 2:** Run:

```powershell
python -m pytest -q tests/test_sitrep.py
```
  - Expected: 12 passed
**Step 3:** Run:

```powershell
fieldkit tools check --run-tests --id sitrep
```
  - Expected: sitrep  present  tested  probe-safe  test PASS, and the result recorded in state/test-results.json

**After this task:** The card's recorded test result matches the current file hash, so `fieldkit sweep` and `fieldkit cards check` see it as tested.

### Change a limit or a banned phrase

The numbers are opinions written down; the module comments say the ones marked model inference are the ones to argue with.

**Prerequisites:**
- an editor

**Step 1:** Edit fieldkit/sitrep/rules.yaml: change the number under limits, or add or remove a line under banned.idioms, and update the comment beside it with the reason and the date.
  - Expected: a saved file
**Step 2:** Run:

```powershell
fieldkit sitrep doctor
```
  - Expected: OK: sitrep can run here (the example still passes under the new rules; if it fails, the example needs the same change)
**Step 3:** Run:

```powershell
python -m pytest -q tests/test_sitrep.py
```
  - Expected: 12 passed, or a named failure telling you which test encodes the old number; update the test with the rule

**After this task:** The check applies the new rule; the test suite documents it.

### Add a heading language

The six languages match the academic kit's LANGS. Adding one is a data change plus one constant.

**Prerequisites:**
- the seven heading texts in the new language

**Step 1:** Add the code to LANGS in fieldkit/sitrep/__init__.py (order matters: it is zipped with the rows in headings.yaml) and append the text to every row in headings.yaml.
  - Expected: seven rows, each one entry longer
**Step 2:** Run:

```powershell
fieldkit sitrep doctor
```
  - Expected: languages: now lists the new code; OK
**Step 3:** Run:

```powershell
fieldkit sitrep render FILE --lang <code>
```
  - Expected: headings in the new language

**After this task:** test_every_heading_exists_in_all_six_languages_and_english_is_the_key fails if any row is short.

## Troubleshooting

**Symptom:** check reports a missing section although the heading is in the file
**Cause:** the heading text does not match headings.yaml in any language after lower-casing and stripping; parse() reported it in unknown_headings and check() emitted a WARN naming it
**Remedy:** fix the heading text or add the variant to headings.yaml
**Verify:** fieldkit sitrep check FILE --json shows the section in counts

**Symptom:** a sentence over 25 words is not flagged
**Cause:** the sentence splitter did not split: the previous sentence ended without a space before a capital, so two sentences were counted as one, or the long text is inside one item that the regex sees as one sentence
**Remedy:** this is the known limit of _SENT_SPLIT; count manually or split the item
**Verify:** fieldkit sitrep check FILE --json, read problems

**Symptom:** a capitalised word is warned about although it is expanded
**Cause:** the expansion is not written as WORD (…) or (WORD …) anywhere in the file; the check only looks for a bracket next to the word
**Remedy:** write the expansion in brackets once, or lower-case the word
**Verify:** the WARN disappears

**Symptom:** render --lang ro prints English
**Cause:** the heading is missing from headings.yaml for that language, so heading() fell back to English
**Remedy:** add the row entry; doctor reports the missing language
**Verify:** fieldkit sitrep doctor

## Technical Debt

🟡 **LOW** — headings.yaml and rules.yaml are re-read on every call, including several times inside one check() → cache per process with functools.lru_cache keyed on file mtime if a profile ever shows it; not measured, and the files are small
🟡 **LOW** — the sentence splitter and the word counter are regex approximations (Dr. X splits; URLs count as one word) → keep, and document the limit (done in the layman track); a real tokeniser would add a dependency the maintainer's audience cannot afford
🟠 **MEDIUM** — the idiom list is English-only → add per-language lists under banned.idioms_<lang> and apply them when --lang is given, with the academic kit's language detection
🟡 **LOW** — the expansion check for capitals is a bracket heuristic → accept a glossary section as an alternative expansion, matching the dyslexia guide's glossary advice
🟡 **LOW** — check() has 26 branches and 27 local variables by pylint's count → split into one function per rule family (structure, length, banned, style) when the next rule is added

## Impact If Removed

The `fieldkit sitrep` command, its card, the fieldkit-sitrep skill and tests/test_sitrep.py stop working. Nothing else in Fieldkit imports this module. The academic harness loses the only report format written for its reader, and sessions fall back to free-form status text with no check.

## Claim Sources

| Claim | Basis | Evidence |
|-------|-------|----------|
| No model writes the content | 📄 stated in input | no model, no tokens, no variance |
| The asks-first order exists so a reader who stops early still knows the ask | 📄 stated in input | a reader who stops after the first screen |
| The module has no subprocess, eval or network calls | 🤖 model inference | *(none — model judgment)* |
| A malformed rules.yaml raises and is not caught | 🤖 model inference | *(none — model judgment)* |
| The data files are re-read on every call | 🤖 model inference | *(none — model judgment)* |
| check() has 26 branches and 27 locals | 📄 stated in input | Too many branches |
| Nothing else in Fieldkit imports this module | 🤖 model inference | *(none — model judgment)* |
| Performance is not measured | 🤖 model inference | *(none — model judgment)* |


---
**How to verify this document:**
`📄 stated in input` — the model's phrasing of something your source text said.
Find the matching line in the original to verify.
`🤖 model inference` — the model's own judgment or synthesis. Treat as opinion,
not measurement. Re-run on the same input and check whether specific numbers
stay consistent between runs.

*Auto-generated DITA-structured developer documentation.*