## What this is

Fieldkit v0.7.1: the harness spends less and stops sooner. Cheap workers do the jobs and a stronger model only reviews, a change that contains no code runs no tests, and a change that fails CI twice is parked with a note instead of being retried for days. (v0.7.0 was tagged without its release notes and never published; v0.7.1 is the same code with the notes and the new version.)

## Should you download it?

Yes, if you already run Fieldkit: it removes the loops and the spending that made v0.6.x slow and costly. Installing is unchanged (the install script, or `fieldkit update`). There is no separate program to download.

## Why this matters

Before, one small change could start days of retries and long sessions re-reading the same conversation. Now the rules are written in one place and enforced by checks, and the two computers carry the same rules.
