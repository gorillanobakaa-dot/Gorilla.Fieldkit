# v0.7.1, for developers

## Scope

`v0.6.0..origin/main`, plan phases 1-6 and I5, I7, I8 of docs/HARNESS-PLAN.md.

- Roles: roles.yaml and lifecycle/roles.py, `task start --model/--accept`, reviewer verdict pass / send_back / fix_itself (I5, I7); I8 triage-learn; C3b review-verdict stub; C4 off-not-lost, no cable revive, watchdog no restart.
- Test selection: a diff with no code selects zero tests (R7 follow-up); `ci_tests --merged` skips the main run when the merge tree equals the PR tree; `testcost run --fast` skips an identical pass.
- Rules: TWO STRIKES in rules/CORE.md; duplicated text removed from the lifecycle and rotation blocks so every global file fits the 8 KB limit.
- Windows: windowless hooks (pythonw), hog registry skips a registered process's conhost, children of a console-less fieldkit keep our stdout (prefix-diff, webtest), liveness close test accepts the venv launcher pid.
- R6: a recipe for all 23 projects; C1-C7 household offload; I1-I4 cost lines.

## Not in this release

- O3, L3 (#208/#213) and the TWO STRIKES code (#212): parked after repeated Windows CI failures, branches kept.
- Local full-suite run on the Windows laptop: 4942 passed, 2 failed (names not captured); CI green on windows-latest and ubuntu-latest for every merged PR.
