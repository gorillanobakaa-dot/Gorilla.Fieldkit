## What this is

Fieldkit v0.7.2: the harness no longer sits idle on a job, and it guards what it spends and what it forgets. A deterministic watch finds a job that ended, stalled or died and says what to do next; no expensive model starts without a deterministic yes; a suggestion made mid-job is parked instead of moving the plan; and only the coordinating session may change a repository's main checkout.

## Should you download it?

Yes, if you already run Fieldkit: it removes the hours the coordinator and the helper computer used to lose waiting on each other. Installing is unchanged (the install script, or `fieldkit update`). There is no separate program to download.

## Why this matters

On 2026-10-29 a finished job sat unnoticed for about an hour because nobody was watching it. Waiting costs money, so the watching is now a check, not a habit.
