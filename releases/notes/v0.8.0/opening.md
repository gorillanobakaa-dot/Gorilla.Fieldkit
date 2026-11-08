## What this is

Fieldkit v0.8.0: every written rule is now checked by a tool that is itself tested, the rules work on any device and any agent, and the harness gained image, web-browser and system-observation tools. Two blocks that stopped unattended runs are gone: a headless push that was wrongly refused, and timing-sensitive tests that counted as real failures.

## Should you download it?

Yes, if you already run Fieldkit: it removes the main reasons a job stopped and waited for you, and it proves each rule can actually catch a breach. Installing is unchanged (the install script, or `fieldkit update`). There is no separate program to download.

## Why this matters

A rule nobody checks is a wish. In this release each rule has a test that breaks it on purpose and shows the tool catching it: 54 caught, 0 not caught.
