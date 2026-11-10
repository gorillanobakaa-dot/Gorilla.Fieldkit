## What this is

Fieldkit v0.9.0: the harness now learns from its own history. It turned 3,076 recorded work episodes into failure-to-fix signatures, a refusal table (956 refusals folded into 40 rules) and replayable recipes, and the tool router now corrects some refused commands by itself. This release also adds new tools for local models, power incidents, GitHub setup and pinned helper downloads, and it fixes a guard rule that blocked a read-only release check.

## Should you download it?

Yes, if you run Fieldkit: fewer jobs stop and wait for you, and a repeated refusal now opens a defect row by itself. Installing is unchanged (the install script, or `fieldkit update`). There is no separate program to download.

## Why this matters

Long unattended work fails on the same few mistakes again and again. Each one costs hours and paid model time. This release records those mistakes and turns them into rules and tests, so the next run does not repeat them.
