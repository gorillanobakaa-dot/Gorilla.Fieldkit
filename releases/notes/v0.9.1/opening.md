## What this is

Fieldkit v0.9.1 (it carries everything planned for v0.9.0, which was tagged but never published): the harness now learns from its own history. It turned 3,076 recorded work episodes into failure-to-fix signatures, a refusal table (956 refusals folded into 40 rules) and replayable recipes, and the tool router now corrects some refused commands by itself. This release also adds new tools for local models, power incidents, GitHub setup and pinned helper downloads, and it fixes a guard rule that blocked a read-only release check.

## What changed since the v0.9.0 tag

v0.9.0 was tagged but its release run failed: the release workflow did not install one checking program the tests need, so nothing was published. v0.9.1 fixes the release workflow, and it also stops the final goal (plan row W10, joining Fieldkit into Gorilla OpenCode) from being shown as work to do: `fieldkit review` and `fieldkit plan next` now list it as held by the owner.

## Should you download it?

Yes, if you run Fieldkit: fewer jobs stop and wait for you, and a repeated refusal now opens a defect row by itself. Installing is unchanged (the install script, or `fieldkit update`). There is no separate program to download.

## Why this matters

Long unattended work fails on the same few mistakes again and again. Each one costs hours and paid model time. This release records those mistakes and turns them into rules and tests, so the next run does not repeat them.
