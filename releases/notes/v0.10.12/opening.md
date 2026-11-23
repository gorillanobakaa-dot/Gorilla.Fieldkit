## What this is

Fieldkit v0.10.12 adds `fieldkit escalate`: it runs a job as a cheap, low-effort AI session, checks the result with a plain pass/fail signal, and only on a failure starts a fresh, stronger session. It also adds a check that fails when the one-hour prompt cache is not switched on.

## Should you download it?

Yes, if you use Fieldkit on Windows 11 or Linux and run AI jobs. It works the same on both systems.

## Why this matters

Anthropic's cost guide reports that low effort with only the failures re-run at high effort solved about 97% of tasks at about $0.17 each, against 95.3% at $0.29 when everything runs at high effort. When the first try passes, no extra model call is made.

v0.10.12 also carries everything v0.10.11 was meant to ship: the v0.10.11 tag was never published, because its release run failed the full test suite.
