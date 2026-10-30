# v0.7.2, for developers

## Scope

`v0.7.1..origin/main`, plan items Z1, P1, I5 follow-up and L3 guards.

- Z1: `fieldkit task watch [--once] [--act]` (fieldkit/lifecycle/watch.py) flags UNHANDLED, STALLED and DIED tasks; a 5-minute timer entry (schedule.py WATCH); a line for Zero in the room hook; `peers link relay listen --follow` and `relay doctor --restart`; CORE rule NO PASSIVE WAITING (#224).
- Model spend guard: models.yaml, lifecycle/models.py; guards in the listener, `task start` and autopilot spawn; the premium refusal runs before the claude executable lookup; ledger and report (#223, METHODS 30).
- P1: `fieldkit ideas add|list|brief|review` (state/ideas/ideas.json, TODO.Features.Plan.md rebuilt from it), room hook text, backup carries state/ideas (#220, #225).
- Guards: main-checkout guard (#222), tag guard and `ship release-ready` dry run (#219), emerge recipe uses the PATH python (#221).
- Plan status and CORE model line (#226).

## Not in this release

- O3, L3 (#208, #213) and the TWO STRIKES code (#212): parked, branches kept on GitHub, for a stronger model. #212's prefix-diff failure was already fixed on main by b1898a9.
- Local full-suite run on the Windows laptop: 2 failures not identified; test_every_reviewed_card_agrees_with_its_command fails there because gorilla-size is not built; CI green on windows-latest and ubuntu-latest for every merged PR.
- Z1's timer and hook were not exercised on a long real run.
