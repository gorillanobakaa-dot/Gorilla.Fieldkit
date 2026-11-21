## What this is

Fieldkit v0.10.7 maximizes CI parallelism by increasing test shards from 4 to 20 per OS, dramatically reducing test execution time from hours to minutes on both Windows and Linux. This release builds on v0.10.6's desktop tools and installer improvements.

## Should you download it?

Yes, if you use Fieldkit on Windows 11 or Linux. This update significantly speeds up CI pipelines by running up to 40 concurrent test jobs (20 Windows + 20 Linux).

## Why this matters

With 20 shards per OS instead of 4, your test suite can now run in parallel across 40 GitHub Actions runners. For a 6838-test suite, this reduces CI time from hours to minutes. The shard algorithm balances longer-running tests across lighter shards to ensure even completion times.