---
name: fieldkit-sitrep
description: Write the end-of-session status report in the shape a reader with ADHD, dyslexia or autism can take in a few seconds at a time, and have it checked against written rules before it is shown. Use at the end of any session, after any long run, when asked for a sitrep, a status, "where are we", "what happened", or "what do you need from me", and whenever the reader is a student working with the academic harness. Works the same on Windows and Linux.
license: AGPL-3.0-or-later
compatibility: "Needs the fieldkit command (Gorilla Fieldkit, github.com/gorillanobakaa-dot/Gorilla.Fieldkit; Python 3.11+ with PyYAML) on Windows, Linux, macOS, freebsd. Works offline; no network access unless a step says so."
---

# The sitrep (fieldkit/sitrep)

A status report is a FILE you fill in. The tool checks it and renders it. No model writes the content, so it
says the same thing every time and can be corrected once for everybody.

## The shape, and why it is in this order

```
One line               the result, one sentence, 25 words or fewer, with a full stop
What I need from you   the asks, numbered, three at most, one sentence each   <- SECOND, not last
Where we started       two or three bullets
What happened          numbered steps, seven at most
What was achieved      bullets the reader could check
Where we are now       one to three bullets
What is left           bullets: what is not done, and who it waits on
```

The asks come second because a reader who stops after the first screen must still know what is being asked
of them. That is the one change from the classic military order; `--order classic` gives the classic one.

## Do this at the end of a session

```
fieldkit sitrep template > sitrep.md        (or --lang ro, es, pt, it, de for the headings)
... fill it in ...
fieldkit sitrep check sitrep.md             exit 0 passes; exit 3 lists every FAIL line, fix them all
fieldkit sitrep render sitrep.md            the report, plain text, wrapped at 80 columns
fieldkit sitrep render sitrep.md --md       the same as markdown, for a chat window or a file
```

`fieldkit sitrep example` prints a report that passes, to copy the tone from. `fieldkit sitrep doctor` says
whether the tool can run here (it has no machine dependencies beyond Python and PyYAML).

## What the check refuses, and what it only warns about

FAIL (the report is not rendered until these are fixed):
- a missing or empty section; a one-liner that is two sentences, over 25 words, or has no full stop
- more than three asks, or an ask that is more than one sentence
- any sentence over 25 words
- an idiom from the list in `fieldkit/sitrep/rules.yaml` ("circle back", "at the end of the day", ...)
- an abbreviation ("e.g.", "i.e.", "etc.")
- an em-dash, italics or underline (bold is allowed, on the first words of an item)

WARN (rendered, but said):
- more than seven steps under What happened
- a question outside the asks
- a word in capitals that is never expanded

## Rules for the writer that the check cannot see

- Say the literal thing. "Can you check the log?" is a question; "Check the log." is an ask.
- One idea per sentence. Numbers in a table or on their own line, not inside a sentence.
- Name a file only when the reader has to go there.
- British English. No em-dashes anywhere.
- If nothing is needed from the reader, leave the asks section empty: the render prints "Nothing."

## Where the rules come from

`fieldkit/sitrep/__init__.py` lists the sources read on 2026-10-14 (the Ako Aotearoa / British Dyslexia
Association style guide, Scope citing the National Autistic Society, workplace neurodiversity guidance,
CHADD on working memory) and marks every number as "stated in source" or "model inference". The ones marked
inference are the ones to argue with, in `rules.yaml`, not in the code.
