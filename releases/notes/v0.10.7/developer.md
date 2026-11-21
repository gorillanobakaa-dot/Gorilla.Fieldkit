# v0.10.7, for developers

## Scope
v0.10.6..main:

- **P48 - Maximum CI parallelism (fieldkit/method/batch.yaml, .github/workflows/tests.yml)**  
  - Increased batch.shards.default from 4 to 20 shards per OS to maximize parallel execution.
  - 20 shards x 2 OS = 40 concurrent jobs (GitHub Actions limit for public repos).
  - For a 6838-test suite: each shard processes ~342 tests instead of ~1710 tests before.
  - The balance() function in fieldkit/method/shard.py uses longest-processing-time-first algorithm with duration records from .github/test_durations.json.

- **Duration recording**  
  - Run python -m fieldkit.method.shard record JUNIT.xml --out .github/test_durations.json to record actual test execution times.
  - Files without duration records use the median runtime as a fallback for load balancing.

## Verification
- ieldkit release-page check releases/notes/v0.10.7/page.md --layman releases/notes/v0.10.7/layman.md --developer releases/notes/v0.10.7/developer.md
- ieldkit version-sync . --expect 0.10.7
- ieldkit parity os --check
- Full CI run on Windows and Linux with 20 shards per OS