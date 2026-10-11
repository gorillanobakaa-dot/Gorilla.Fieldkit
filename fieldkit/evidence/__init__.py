"""Evidence that can be checked later: what a research run cited, what was actually fetched, and whether the
page says what the claim says it says.

    fieldkit evidence fetch URL... [--run RUN] [--quote TEXT]    snapshot pages into a content-addressed store
    fieldkit evidence import FINDINGS.md [--out RECORD.jsonl]    a research run's lane reports -> one record per claim
    fieldkit evidence check RECORD.jsonl|FINDINGS.md [--store] [--fetched FILE]   every claim against the rules
    fieldkit evidence show URL                                   the latest snapshot of a URL, and where its text is

Built for Gorilla OpenCode's /research and /osint (docs/METHODS.md #16), usable by any agent: a claim is only as good
as the page behind it, and a model's word that it opened the page is not the page.
"""
