---
name: fieldkit-overhead
description: How much longer than the bare pre-AI build script a job took, and where the extra time went, with fieldkit overhead - one line per job (a Firefox or kernel build-harness task, a fieldkit goal) with the bare build time from the build pipeline's own stage logs, the wall clock, the ratio, and the split into rework, tests and gates, LLM time, waiting for the owner, agent tools and idle; a budget (overhead.max_ratio, default 1.5x) shown as WARN in goal status, the goal report and the Board. Read-only, offline, no model. Use when a job "took days", to see where a migration or build's time went, before and after changing how jobs are run, when a goal or the Board shows OVERHEAD WARN, or when the owner asks why a 90-minute build took longer.
license: AGPL-3.0-or-later
compatibility: "Needs the fieldkit command (Gorilla Fieldkit, github.com/gorillanobakaa-dot/Gorilla.Fieldkit; Python 3.11+) on Windows, Linux, macOS. Works offline; no network access."
---

# fieldkit overhead - the job against the bare build script

Owner, 2026-10-26: the bar is Gentoo Portage and his own pre-AI build script - a 90-minute compile + package,
unattended, no clicks. Measured the same day: Firefox 158 had 88 minutes of migration and took 61 hours. **Never
estimate this by hand: run the meter.** Source `fieldkit/overhead.py`, card `overhead`.

| The question | Run |
|---|---|
| every known job, one line each | `fieldkit overhead` (exit 1 = a job is over budget) |
| one build job with its session split | `fieldkit overhead firefox-158.0 --session 083352b5` |
| a follow-up after a silent day | `fieldkit overhead "firefox-158.0 #2"` |
| a goal | `fieldkit overhead goal:<id>` (sessions come from its jobs' CHECKPOINT.json) |
| no stage log for this job | `fieldkit overhead JOB --bare 90` or `"overhead": {"baselines": {"JOB": 90}}` |
| the bare times the logs give | `fieldkit overhead baselines` |
| machine-readable | add `--json` |

## Reading the line

`firefox-158.0: bare 2 h 49 min (migrate 1 h 28 min, compile 1 h 16 min, package 5 min), took 62 h 55 min = 22.4x
WARN (max 1.5x); waiting-for-owner 24 h 31 min, rework 15 h 23 min, idle 8 h 05 min, tests+gates 7 h 15 min, ...`

- **bare**: the one run of each stage the bare script does: the first migration burst, the longest successful
  compile, the longest successful package. The sum is the baseline; ratio = wall clock / bare.
- **rework**: every other run of a stage (a failed compile, the leak gate again, post-install again, a testcost
  check re-run on a tree that already passed, a goal job attempt after a failed one). The detail lines name them.
- **tests+gates**: the one kept run of each gate, testcost rows, pytest/mutate/telemetry calls in the transcript.
- **LLM**, **agent tools**, **waiting-for-owner** come from the Claude Code transcript: without `--session` (or
  `"overhead": {"sessions": {"JOB": ["ID"]}}`) they are not split and count as idle. Sessions are never guessed.
- **waiting-for-owner**: a turn ended until the owner's next message, open inbox items naming the job, the owner's
  approve of the plan or the pre-approvals, a file tool that waited over 60 s (an approval prompt), a question to
  the owner, a denied call.
- Every second is in exactly one bucket: bare > rework > tests > tools > LLM > owner > idle where they overlap.

## Budget and where WARN shows

`"overhead": {"max_ratio": 1.5}` in `fieldkit.local.json` (default 1.5). Over it = WARN: in `fieldkit goal status`,
the goal's REPORT.md, and `overhead.board_line()` (the zero-net Board). A job without a baseline is NOBASE and never
warns. The meter only reads; it never stops a job or asks anyone anything.

## Known limits

- post-install has no start event: from the install, capped at 30 min (the test cost audit's rule).
- A leak gate run without a journal mark ends at its results file's time, never after the next run starts.
- Background Bash work shows only through the stage logs (the transcript sees its start).
- Claude Code transcripts only (no Antigravity / Codex split yet).
