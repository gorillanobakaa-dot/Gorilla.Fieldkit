## What this is

Fieldkit v0.7.2: the harness no longer sits idle on a job, and it guards what it spends and what it forgets. A deterministic watch finds a job that ended, stalled or died and says what to do next; no expensive model starts without a deterministic yes; a suggestion made mid-job is parked instead of moving the plan; and only the coordinating session may change a repository's main checkout.

## Should you download it?

Yes, if you already run Fieldkit: it removes the hours the coordinator and the helper computer used to lose waiting on each other. Installing is unchanged (the install script, or `fieldkit update`). There is no separate program to download.

## Why this matters

On 2026-10-29 a finished job sat unnoticed for about an hour because nobody was watching it. Waiting costs money, so the watching is now a check, not a habit.

---
# In plain language: everything in this release

This is the complete explanation, not a summary of one. Nothing below is behind a link.

<!-- plain-language track: in full, on this page -->



---

# For developers: how it works, and how to check it

Written for someone who will audit, fork or change the code. It covers the same release as the plain-language part above; neither is a summary of the other.

<!-- developer track: in full, on this page -->

### Scope

`v0.7.1..origin/main`, plan items Z1, P1, I5 follow-up and L3 guards.

- Z1: `fieldkit task watch [--once] [--act]` (fieldkit/lifecycle/watch.py) flags UNHANDLED, STALLED and DIED tasks; a 5-minute timer entry (schedule.py WATCH); a line for Zero in the room hook; `peers link relay listen --follow` and `relay doctor --restart`; CORE rule NO PASSIVE WAITING (#224).
- Model spend guard: models.yaml, lifecycle/models.py; guards in the listener, `task start` and autopilot spawn; the premium refusal runs before the claude executable lookup; ledger and report (#223, METHODS 30).
- P1: `fieldkit ideas add|list|brief|review` (state/ideas/ideas.json, TODO.Features.Plan.md rebuilt from it), room hook text, backup carries state/ideas (#220, #225).
- Guards: main-checkout guard (#222), tag guard and `ship release-ready` dry run (#219), emerge recipe uses the PATH python (#221).
- Plan status and CORE model line (#226).

### Not in this release

- O3, L3 (#208, #213) and the TWO STRIKES code (#212): parked, branches kept on GitHub, for a stronger model. #212's prefix-diff failure was already fixed on main by b1898a9.
- Local full-suite run on the Windows laptop: 2 failures not identified; test_every_reviewed_card_agrees_with_its_command fails there because gorilla-size is not built; CI green on windows-latest and ubuntu-latest for every merged PR.
- Z1's timer and hook were not exercised on a long real run.
