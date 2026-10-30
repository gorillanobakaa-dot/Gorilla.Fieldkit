## What this is

Fieldkit v0.7.3: the three changes set aside in v0.7.2 are now in. A job brief must name its plan item, a stalled goal is stopped, and a pull request that fails CI twice is parked by the tools instead of retried. The Windows laptop also has a live proof of the orchestrate-session job on the second computer, and one page now says where plans, ideas, hand-overs and briefs live.

## Should you download it?

Yes, if you already run Fieldkit: it stops two ways money was wasted, endless CI retries and goals that stalled without anyone noticing. Installing is unchanged (the install script, or `fieldkit update`). There is no separate program to download.

## Why this matters

A fix that fails CI twice rarely passes on a third blind try. The rule was written down on 2026-10-29; from this release the tools enforce it.
