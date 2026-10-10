---
name: fieldkit-career
description: A CV and a UK job hunt for one candidate, in English and Romanian, any trade - check the CV against fixed rules and ask what is missing, write UK-style CVs (.docx and .pdf) from verified facts only, search official job-board APIs (Reed, Adzuna), score vacancies, build application packs with cover letters made only of approved sentences, track every application, and back the harness up. Use when asked to check or rewrite a CV, find jobs, refresh the shortlist, prepare an application, or "cv si joburi" / "CV și căutare joburi". Never submits an application.
---

# CV and job hunt (fieldkit cv, fieldkit jobs)

The AI judges the words; Fieldkit does everything else the same way every time. Every command takes `--json`; every
output ends with `NEXT:`. Answer the candidate in their language (Romanian if they write Romanian).

## Hard boundaries (enforced in code and tests; do not work around them)

- **Never invent a fact.** A CV, a score or a letter uses only `profile.yaml`. A missing fact is a question to the
  person, never a guess. Every `evidence` entry names its `source`.
- **Official APIs only** (Reed, Adzuna). No Indeed or LinkedIn automation or scraping: their terms forbid it and the
  candidate's account is at stake. Sites without an API are read in a browser by the person (or the AI's browser),
  and their adverts are pasted in (`jobs add`, `ingest-advert`).
- No accounts, no passwords, no CAPTCHAs or bot checks.
- **Never press Submit.** The candidate submits; then `fieldkit jobs status KEY submitted_by_user`.
- **Never change or delete a file without a copy first.** `cv import` copies the original read-only; `cv render`
  never overwrites (dated names, `_2`, `_3` ...).
- **Personal data never leaves the machine**: it lives in `local/career/` (git-ignored); the candidate's name is
  added to the private word list, so the pre-commit privacy scan blocks it.
- **Stop at every confirmation point below** and wait for the person's answer (DA / NU / corrections).

## Stage 0 - whose CV, and a backup

1. `fieldkit backup` - before any change to the harness. With no folder configured it lists the folders that look
   like backup folders and stops: ask which one, never guess; then `fieldkit backup --to "FOLDER"`.
2. `fieldkit cv init` - it asks **"Pe ce nume vrei să creez acest CV?"**. Ask the person, then
   `fieldkit cv init --name "FULL NAME"`. Never use a name from an example, a test or another person's files.

## Stage 1 - read and judge the CV (STOP at the end)

1. `fieldkit cv import "PATH TO CV"` (a read-only copy, with its sha256), then `fieldkit cv check "COPY"`.
   Add `--trade driver|security|warehouse` if the trade is not detected; a new trade is a file in
   `fieldkit/career/trades/`.
2. Say honestly whether it is well written (the `verdict`), list the findings in the order given (most important
   first), and add what you judge from the text (unclear sentences, weak wording).
3. Ask **every** question `check` lists, showing what the CV already says (`cv_says`). Wait for the answers.

## Stage 2 - rewrite (only after DA; STOP to show the result)

1. Write the facts and answers into `local/career/profile.yaml` (format: `fieldkit/career/profile.py`):
   experience newest first, action verbs, results in figures only where the person gave them, one or more
   `cv_variants` (one per kind of job, each with its own headline, profile, keywords, skills), text as
   `{en: ..., ro: ...}` for a CV in both languages. Show what you wrote; the person confirms it.
2. `fieldkit cv render --variant NAME --lang en` (and `--lang ro`): .docx and .pdf, UK style (at most 2 pages, no
   photo, no date of birth, ATS-friendly), re-checked as an applicant-tracking system would read it.
3. Show both files; apply every correction in `profile.yaml`, then render again.

## Stage 3 - find jobs (STOP before writing letters)

1. Keys (free; the person registers): Reed (reed.co.uk/developers) and Adzuna (developer.adzuna.com), in
   `fieldkit.local.json` as `{"career": {"reed_key": "...", "adzuna_app_id": "...", "adzuna_app_key": "..."}}`.
   Search terms go in `profile.yaml` `queries: [{what, where}]`.
2. `fieldkit jobs run --top 8` - search, score (0-100), shortlist, packs, `reports/latest.json` and `report.md`.
   Exit: 0 clean, 3 no source could search, 4 no key, 5 some searches failed. Hand `human_actions` to the person.
   Jobs from sites without an API: write them in a YAML list (title, employer, location, url, salary_text,
   description) and `fieldkit jobs add FILE`.
3. **Verify before applying.** API descriptions are shortened and Adzuna pay is often an estimate. Open each
   shortlisted advert, save its full text, then `fieldkit jobs ingest-advert KEY --text-file FILE [--apply-url URL]`.
   Pay becomes "verified" only if the advert states it. Confirmed another way:
   `fieldkit jobs verify KEY --salary-min N --salary-max N --closes DATE --note "SOURCE"`. Never verify an estimate.
4. Show the shortlist: title, employer, place, pay (and whether verified), score with reasons, link; recommend the
   top 5. `fieldkit jobs takehome --gross N` gives take-home pay. The dated table of every job:
   `fieldkit jobs export` (jobs_YYYY-MM-DD.csv in the data folder).
5. For the jobs the person chooses: `fieldkit jobs pack KEY` (job.md, cover_letter.md, checklist.md). You may cut or
   reorder the letter; after any edit `fieldkit jobs check-letter KEY` (exit 6 = a sentence that is not approved:
   remove it, or ask the person whether it is true and only then add it to `letter.extra_allowed`).
6. `fieldkit jobs apply KEY` shows the link, CV, letter and what to check. The person submits; record it:
   `fieldkit jobs status KEY submitted_by_user` (later: interview, offer, rejected; or skipped with a reason).

## Stage 5 - backup after the work

`fieldkit backup --to "THE SAME FOLDER"` - dated `harness_backup_YYYY-MM-DD_HHMM.zip`, re-opened and checked
(every file, every CRC, the byte total). Report the exact path and size.

## Stage 6 - reminder

End with, on its own line: **⚠️ NU UITA: fă backup la harness și pe USB-ul ROȘU!** (put it in
`fieldkit.local.json` as `{"backup": {"reminder": "..."}}` and `fieldkit backup` prints it every time).

## Final report

Files created or changed and where; how to start next time (`fieldkit jobs run --top 8`); where both backups are;
the USB reminder.

## Tuning (when the shortlist is wrong)

The candidate's rules are in `profile.yaml` (`salary_floor`, `locations`, `exclude_title_patterns`,
`cv_variants.*.keywords`, `shortlist_min_score`, `languages_spoken`, `clearances`). The general rules are data:
`fieldkit/career/scoring.yaml`, `advert.yaml`, `rules.yaml`, `trades/*.yaml`. For every wrong ranking, add the real
title and pay as a test in `tests/test_career.py` first, then change the rule, then `fieldkit jobs rescore`.
