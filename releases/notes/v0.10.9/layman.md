# v0.10.9, in plain language

This update fixes the CI pipeline and adds a tool that checks for common mistakes before tests run.

## What changed for you

- **Tests now run on 40 machines at once:** The test suite splits across 20 parallel jobs on Windows and 20 on Linux. Before this release, all 6838 tests ran in a single process on one machine, which crashed the Windows runner every time.

- **The release pipeline now matches the test pipeline:** The workflow that publishes a new version was running tests differently from the workflow that checks pull requests. Both now use the same 20-shard setup.

- **A new check runs before every commit:** Whenever you change a PowerShell script, a workflow file, or add a new tool, Fieldkit now automatically checks that nothing is misconfigured before the change is pushed. It catches stale generated files, missing tool registrations, and workflow shard mismatches on the spot.

- **Windows installer fixed:** The installer script broke on newer Windows runners because a zip extraction command was removed from PowerShell 7. It now uses a compatible method that works on both PowerShell 5.1 and 7.

## What did not change

No existing tools were removed or changed in behaviour.