---
name: fieldkit-evidence
description: Research evidence that can be checked later - find sources in a 985-source registry, search free scholarly and data APIs as JSON, fetch pages safely into a store kept by hash with their words, render a checked run as a report, import a research run's findings (Gorilla OpenCode findings-*.md or one lane's reply) as one record per claim, and check every claim against the output contract, the run's own fetches, its snapshot and its quote. Use after a /research or /osint run, before writing up a dossier, when a claim cites a URL, or when asked whether a source really says something.
license: AGPL-3.0-or-later
compatibility: "Needs the fieldkit command (Gorilla Fieldkit, github.com/gorillanobakaa-dot/Gorilla.Fieldkit; Python 3.11+ with PyYAML). Works offline; no network access unless a step says so."
---

# Evidence (fieldkit evidence)

A model's word that it read a page is not the page. Keep the page; check the claim against it.

## Keep what you read
```
fieldkit evidence fetch URL... --run RUN [--quote "the passage"]      goes online: over MCP, network=true
fieldkit evidence show URL                                             when, status, hash, where the words are
```
- Refused, never fetched: loopback, private networks, link-local (cloud metadata), file:, ftp:.
- robots.txt is obeyed (a 4xx robots.txt allows all, 5xx allows nothing); one request per host per second.
- Every answer is kept, a 404 too: "the page was gone" is evidence.
- `BOT CHALLENGE, not the page` = the site answered with a bot check. That is not a source: cite another.
- The store: FIELDKIT_EVIDENCE, else ~/.local/share/fieldkit/evidence (%LOCALAPPDATA%\fieldkit\evidence). Owner-only.

## Find sources, search them
```
fieldkit evidence sources find sanctions --free --grade B          985 sources, best grade and freest reach first
fieldkit evidence search list                                      the free APIs
fieldkit evidence search openalex "query" --limit 10 --run RUN     JSON hits; --run keeps the raw answer as evidence
fieldkit evidence search preprints "query"                         peer_reviewed: false - say so when you cite one
```
A search that failed searched nothing: that is not evidence that nothing exists. SEC and Unpaywall need
FIELDKIT_CONTACT_EMAIL; searxng needs FIELDKIT_SEARXNG (your own instance).

## Check a run
```
fieldkit evidence import findings-....md --out run.jsonl
fieldkit evidence check run.jsonl                          the output contract, line by line
fieldkit evidence check run.jsonl --store --run RUN        + every cited URL has a 2xx snapshot that holds its quote
fieldkit evidence check run.jsonl --fetched urls.txt       + every cited URL was opened in the run
```
exit 0 = every claim holds; 3 = findings, each naming the lane, the claim and the rule.
A Gorilla OpenCode run saves `findings-*.jsonl` beside `findings-*.md`: check the .jsonl, it holds what each
lane fetched, so each claim is held to its own lane's pages.

## Write it up
```
fieldkit evidence render run.jsonl --out report.docx --store      unsupported claims first, every claim with its sources
```
The report is the record laid out, not a summary: nothing is merged or reworded.

| rule | means | do |
|---|---|---|
| contract-* | a heading, the BASIS line, CONFIDENCE or SOURCES TRIED is missing or malformed | re-run the lane, or say the lane is malformed |
| claim-tier / claim-grade / claim-scale | not one of the five tiers, not A-F + 1-6, or the wrong scale for the run | correct the grade, or drop the claim |
| cited-not-opened | the run never fetched the URL it cites | drop the claim, or fetch and re-check |
| no-snapshot / snapshot-status / snapshot-challenge | no kept page, an error page, a bot check | fetch it; if it fails, the claim has no source |
| quote-not-found | the page does not contain the quoted passage | the claim is not supported by that page |
| note same-source / one-origin | one page or one origin behind several lanes | count it once: one voice, not many confirmations |
