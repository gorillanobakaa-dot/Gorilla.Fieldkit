# v0.10.7, in plain language

This update maximizes CI performance by running up to 40 concurrent test jobs across Windows and Linux, reducing test suite execution from hours to minutes.

## What changed for you

- **20 shards per OS instead of 4:** Your test suite now splits across 20 parallel jobs per OS instead of 4. Tests are automatically balanced by recorded execution time, ensuring each shard finishes in roughly the same time.

- **40 concurrent runners:** Windows and Linux tests run in parallel, up to 20 jobs per OS (40 total). This matches GitHub Actions' limit for public repositories.

- **Automatic load balancing:** The shard algorithm uses recorded test durations to place longer-running tests in lighter shards. Files without duration records use the median runtime as a fallback.

## What did not change

Nothing is deleted or emptied automatically. The CI configuration change only affects how tests are distributed across parallel jobs.