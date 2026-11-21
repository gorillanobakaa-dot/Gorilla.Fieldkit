## What this is

Fieldkit v0.10.7 maximizes CI parallelism by increasing test shards from 4 to 20 per OS, dramatically reducing test execution time from hours to minutes on both Windows and Linux. This release builds on v0.10.6's desktop tools and installer improvements.

## Should you download it?

Yes, if you use Fieldkit on Windows 11 or Linux. This update significantly speeds up CI pipelines by running up to 40 concurrent test jobs (20 Windows + 20 Linux).

## Why this matters

With 20 shards per OS instead of 4, your test suite can now run in parallel across 40 GitHub Actions runners. For a 6838-test suite, this reduces CI time from hours to minutes. The shard algorithm balances longer-running tests across lighter shards to ensure even completion times.

---
# In plain language: everything in this release

This is the complete explanation, not a summary of one. Nothing below is behind a link.

<!-- plain-language track: in full, on this page -->

### What changed for you

- **20 shards per OS instead of 4:** Your test suite now splits across 20 parallel jobs per OS instead of 4. Tests are automatically balanced by recorded execution time, ensuring each shard finishes in roughly the same time.

- **40 concurrent runners:** Windows and Linux tests run in parallel, up to 20 jobs per OS (40 total). This matches GitHub Actions' limit for public repositories.

- **Automatic load balancing:** The shard algorithm uses recorded test durations to place longer-running tests in lighter shards. Files without duration records use the median runtime as a fallback.

### What did not change

Nothing is deleted or emptied automatically. The CI configuration change only affects how tests are distributed across parallel jobs.

---

# For developers: how it works, and how to check it

Written for someone who will audit, fork or change the code. It covers the same release as the plain-language part above; neither is a summary of the other.

<!-- developer track: in full, on this page -->

﻿# v0.10.7, for developers

### Scope
v0.10.6..main:

- **P48 - Maximum CI parallelism (fieldkit/method/batch.yaml, .github/workflows/tests.yml)**  
  - Increased batch.shards.default from 4 to 20 shards per OS to maximize parallel execution.
  - 20 shards x 2 OS = 40 concurrent jobs (GitHub Actions limit for public repos).
  - For a 6838-test suite: each shard processes ~342 tests instead of ~1710 tests before.
  - The balance() function in fieldkit/method/shard.py uses longest-processing-time-first algorithm with duration records from .github/test_durations.json.

- **Duration recording**  
  - Run python -m fieldkit.method.shard record JUNIT.xml --out .github/test_durations.json to record actual test execution times.
  - Files without duration records use the median runtime as a fallback for load balancing.

### Verification
- ieldkit release-page check releases/notes/v0.10.7/page.md --layman releases/notes/v0.10.7/layman.md --developer releases/notes/v0.10.7/developer.md
- ieldkit version-sync . --expect 0.10.7
- ieldkit parity os --check
- Full CI run on Windows and Linux with 20 shards per OS
