# v0.7.3, for developers

## Scope

`v0.7.2..origin/main`, plan items O3, L3, TWO STRIKES, the VAIO proof and the map page.

- TWO STRIKES (#212, METHODS 31): strikes registry; `fieldkit strikes list|park|unpark|check`; `ship ci-wait` parks on the 2nd failed round (exit 4); a parked branch or PR is refused by `ship` and `task start`; `plan next` shows parked items.
- O3, L3 (#208, #213): `task start` refuses a brief with no plan id and lints its length; the goal loop judges by command first, stops a stalled goal and counts verdicts per goal.
- orchestrate-session card: live proof on the VAIO, 27 Oct (#228).
- docs/WHERE-THINGS-LIVE.md and the AGENTS.md link (#229); HARNESS-PLAN hygiene (#230).

## Not in this release

- Local full-suite run on the Windows laptop: 2 failures not identified; test_every_reviewed_card_agrees_with_its_command fails there because gorilla-size is not built (inbox 241).
- Z1's timer and hook were not exercised on a long real run.
- The strikes code and the O3 lint ran green on CI for the merged PRs; they were not exercised on a long real unattended job.
