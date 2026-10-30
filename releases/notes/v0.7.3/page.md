## What this is

Fieldkit v0.7.3: the three changes set aside in v0.7.2 are now in. A job brief must name its plan item, a stalled goal is stopped, and a pull request that fails CI twice is parked by the tools instead of retried. The Windows laptop also has a live proof of the orchestrate-session job on the second computer, and one page now says where plans, ideas, hand-overs and briefs live.

## Should you download it?

Yes, if you already run Fieldkit: it stops two ways money was wasted, endless CI retries and goals that stalled without anyone noticing. Installing is unchanged (the install script, or `fieldkit update`). There is no separate program to download.

## Why this matters

A fix that fails CI twice rarely passes on a third blind try. The rule was written down on 2026-10-29; from this release the tools enforce it.

---
# In plain language: everything in this release

This is the complete explanation, not a summary of one. Nothing below is behind a link.

<!-- plain-language track: in full, on this page -->



---

# For developers: how it works, and how to check it

Written for someone who will audit, fork or change the code. It covers the same release as the plain-language part above; neither is a summary of the other.

<!-- developer track: in full, on this page -->

### Scope

`v0.7.2..origin/main`, plan items O3, L3, TWO STRIKES, the VAIO proof and the map page.

- TWO STRIKES (#212, METHODS 31): strikes registry; `fieldkit strikes list|park|unpark|check`; `ship ci-wait` parks on the 2nd failed round (exit 4); a parked branch or PR is refused by `ship` and `task start`; `plan next` shows parked items.
- O3, L3 (#208, #213): `task start` refuses a brief with no plan id and lints its length; the goal loop judges by command first, stops a stalled goal and counts verdicts per goal.
- orchestrate-session card: live proof on the VAIO, 27 Oct (#228).
- docs/WHERE-THINGS-LIVE.md and the AGENTS.md link (#229); HARNESS-PLAN hygiene (#230).

### Not in this release

- Local full-suite run on the Windows laptop: 2 failures not identified; test_every_reviewed_card_agrees_with_its_command fails there because gorilla-size is not built (inbox 241).
- Z1's timer and hook were not exercised on a long real run.
- The strikes code and the O3 lint ran green on CI for the merged PRs; they were not exercised on a long real unattended job.
