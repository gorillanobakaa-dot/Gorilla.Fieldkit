## What this is

Fieldkit v0.10.8 fixes CI parallelism and adds a deterministic pre-CI health check tool that prevents the entire class of failures that broke the pipeline today.

## Should you download it?

Yes, if you use Fieldkit on Windows 11 or Linux. CI now runs 40 parallel jobs (20 Windows + 20 Linux) instead of one process, and a new tool catches misconfigurations before they reach CI.

## Why this matters

Before this release, the full 6838-test suite ran in a single process on a single runner, crashing Windows after 47 minutes. The release workflow was also missing shard jobs that the test workflow had. Both are now fixed and enforced automatically.