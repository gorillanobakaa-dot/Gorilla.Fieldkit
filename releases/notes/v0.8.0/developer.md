# v0.8.0, for developers

## Scope

`v0.7.3..origin/main`, merged PRs #232-#271 (plan items I-01, I-09, I-10, P1-P5, P10, B1, C1, C3, W6).

- Rules as data: rules/rules.yaml generates CORE.md; `fieldkit rules lint` (#264) checks per-block hashes, byte budget, duplicates, enforcer and command names; `fieldkit rules prove` (#265) runs rules/proofs.yaml, one mutation per demand: `54 caught, 0 not caught`. `fieldkit honesty check` runs from `rotate handover`, `task report`, `sitrep check --session`.
- Rules on any device (#267, brief section E): an installed agent with no global rules file is a FAIL and install creates it; Claude hooks (tool router, room) checked and installed the same on both systems.
- Archive guard (#265): never Zero, never a running session, never nested children. Session records are never written by a model (#270, I-10, proof CAUGHT); the guard loads the lifecycle package only for `archive_session`.
- Headless push (#269, B1): a mandated `ship push` whose output is tailed is still the push. Strikes (#268): a red made only of listed timing tests is a flake; the third flake counts; CI prints FAILED ids last for `ci-wait`.
- `fieldkit review` (#244, #253): one list for ideas and inbox, `state/REVIEW.md`, launchers; owner-terminal check (#252).
- `ship push --auto` (#242, #251): PR plus auto-merge only where the base requires a check. `sync` reads CI from the public API without `gh` login and maps Opencode job names.
- `fieldkit web` (#245, #246, #247): stdlib browser drivers (chromium dump-dom, gecko BiDi, safaridriver); NAT64 `64:ff9b::/96` judged by the embedded IPv4. `fieldkit image` (#250). `fieldkit app find|start` and the agent finder (#255, #256). `fieldkit observe` (#271): open-file, port, proc, watch, trace, usage, events; the Linux proc reader imports `pwd` lazily so Windows loads it.
- Zero (#239, #240): any UP station may assume a dead Zero; Zero never archives while it holds the seat; retire only into a new Zero. `fieldkit metrics` (#241). FM-10 busy-spin ignores conhost (#257), `fieldkit size` passes stdio explicitly (#232).

## Not in this release

- Gecko BiDi on a real Firefox, Safari, and live Linux/macOS runs of web, image, app start and observe were not verified; tests use faked systems.
- The fieldkit review page was not run by the owner. P5c (model told what it has) is on an unmerged branch.
- #253's CI replay (C3) and the VAIO install were not verified at the time of the rules rows; the VAIO is installed by Zero after this release.
- Local full-suite run on the Windows laptop: gorilla-size is not built, so test_every_reviewed_card_agrees_with_its_command fails there.
