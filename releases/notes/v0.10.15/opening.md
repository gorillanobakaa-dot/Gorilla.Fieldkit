## What this is

Fieldkit v0.10.15 cuts shared AI agent rules roughly in half, separates personal machine notes out of the public core rules into an optional local file, lets both installers apply agent rules on request, adds hot-path bytecode warming (`fieldkit pycache`), and hardens GitHub shipping, quota guards, and session handoffs.

## Should you download it?

Yes. Every user gets leaner, portable AI rules with optional one-flag installation (`install.ps1 -Rules` / `install.sh --rules`), faster Python hook startup via `fieldkit pycache`, and safer GitHub push/merge checks.

## Why this matters

Before this release, every AI turn sent a much larger global rules file containing machine-specific notes, fresh users had to run a separate command to install agent rules, and hook scripts recompiled bytecode on hot paths. This release trims the shared rules, makes them clean for any computer out of the box, and enforces stricter checks before shipping or archiving sessions.
