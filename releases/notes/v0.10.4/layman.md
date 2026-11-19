# v0.10.4, in plain language

## Fewer repeated checks

Related changes can share one pull request. Fieldkit can reuse checks when GitHub merges the same code those checks already covered. It starts fewer end-to-end jobs for changes with known dependencies. The Windows and Linux checks still have to pass.

## Recover progress after an interruption

A build can reuse a completed step when its inputs and output files match. The privacy test can resume from saved results when Fieldkit can verify the earlier work and confirm the test environment is ready. If it cannot verify that evidence, it runs the checks again. On Windows, resuming requires an elevated run and a readable, matching Windows state.

## Hand a job to another session

The next session gets the complete plan, the recorded commit when available, and the IDs of any checks still running. One successor can claim the handoff. CI also shows where a test stalled and what failed.

## Publish with the existing signed approval

Fieldkit uses the existing signed approval to publish a checked release. The approval can name a particular computer. If an upload stops midway, the same release files can supply the remaining files.
