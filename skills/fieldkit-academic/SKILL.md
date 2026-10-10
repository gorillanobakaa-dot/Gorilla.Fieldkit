---
name: fieldkit-academic
description: Academic work for a student in the UK, US, Spain, Portugal, Italy, Germany or Romania - set up an assignment (ask the deadline and the plagiarism-checker upload date), read the study materials into a searchable library, check citations against the reference list (any of six languages), the shape APA 7, Harvard (Cite Them Right) or ISO 690 gives each entry, that the work is all in the language the course asks for (and British or American spelling), the word count, long dashes and copied runs of words, and say the one next step. Use when a student asks "when is it due", "check my references", "is this plagiarism", "what do I do next", or "tema", "bibliografie", "referinte".
---

# Academic work (fieldkit academic)

Fieldkit checks; the student writes. Every command takes `--json`; text output ends with `NEXT:`.
Exit codes: 0 ok, 2 a question to ask (or bad input), 3 a check found something to fix.

Designed first for a student with ADHD and dyslexia; that design is the default for everyone:
- **One thing at a time.** Relay `next`'s one step, not the whole checklist.
- **Short sentences.** Say what to do, not why it is wrong.
- **Ask, never guess.** No profile, no date, no style: ask the question the command returns (exit 2), in the
  student's language, then run the command again with the answer.

## Start

```
fieldkit academic init                                        # exit 2: the questions (name, country)
fieldkit academic init --name "FULL NAME" --country es        # country sets style, page, dates, language
fieldkit academic setup FOLDER                                # exit 2: asks the deadline AND the upload date
fieldkit academic setup FOLDER --deadline "28 October 2026" --upload "21 October 2026" --type reflective-essay
fieldkit academic next FOLDER                                 # days left to the earlier date + the next step
```

Dates are read in any of the seven countries' forms and written the country's way. A date found in the brief is
only offered inside the question; the student confirms it.

## Check a draft (.md, .txt, .docx, .pptx)

```
fieldkit academic refs FILE          every citation listed, every entry cited, order, duplicates, quotation pages
fieldkit academic style FILE         entry/citation shape of the profile's style (warnings, never failures)
fieldkit academic language FILE      sentences in another language (FAIL), spelling variant (WARN)
fieldkit academic words FILE --target 2000
fieldkit academic dashes FILE        counted only where the country counts them (uk)
fieldkit academic plagiarism FILE --sources "FOLDER/1 - DROP YOUR STUDY MATERIALS HERE"
fieldkit academic structure FILE --type report
```

## Sources

```
fieldkit academic extract FOLDER                 read every study file, page by page, into the library
fieldkit academic search FOLDER '"social model" disability'    with the page/slide to cite
fieldkit academic accessed FOLDER URL            today's date stamped once; the line in the profile's style
```

## Boundaries

- The profile and the work stay on the student's machine (`local/academic`, the work folder). The name goes on the
  private word list; never write it into the repository.
- The checks test links and shapes, not whether a source says what the work claims. Say so when relaying `refs`.
- Supported styles now: apa7, harvard-ctr, iso690. MLA, Chicago, Vancouver, IEEE and footnote styles are not built
  yet: say that, rather than checking with the wrong style.
