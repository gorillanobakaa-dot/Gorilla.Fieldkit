## What this is

Fieldkit v0.7.1: the harness spends less and stops sooner. Cheap workers do the jobs and a stronger model only reviews, a change that contains no code runs no tests, and a change that fails CI twice is parked with a note instead of being retried for days. (v0.7.0 was tagged without its release notes and never published; v0.7.1 is the same code with the notes and the new version.)

## Should you download it?

Yes, if you already run Fieldkit: it removes the loops and the spending that made v0.6.x slow and costly. Installing is unchanged (the install script, or `fieldkit update`). There is no separate program to download.

## Why this matters

Before, one small change could start days of retries and long sessions re-reading the same conversation. Now the rules are written in one place and enforced by checks, and the two computers carry the same rules.

---
# In plain language: everything in this release

This is the complete explanation, not a summary of one. Nothing below is behind a link.

<!-- plain-language track: in full, on this page -->



---

# For developers: how it works, and how to check it

Written for someone who will audit, fork or change the code. It covers the same release as the plain-language part above; neither is a summary of the other.

<!-- developer track: in full, on this page -->

### Scope

`v0.6.0..origin/main`, plan phases 1-6 and I5, I7, I8 of docs/HARNESS-PLAN.md.

- Roles: roles.yaml and lifecycle/roles.py, `task start --model/--accept`, reviewer verdict pass / send_back / fix_itself (I5, I7); I8 triage-learn; C3b review-verdict stub; C4 off-not-lost, no cable revive, watchdog no restart.
- Test selection: a diff with no code selects zero tests (R7 follow-up); `ci_tests --merged` skips the main run when the merge tree equals the PR tree; `testcost run --fast` skips an identical pass.
- Rules: TWO STRIKES in rules/CORE.md; duplicated text removed from the lifecycle and rotation blocks so every global file fits the 8 KB limit.
- Windows: windowless hooks (pythonw), hog registry skips a registered process's conhost, children of a console-less fieldkit keep our stdout (prefix-diff, webtest), liveness close test accepts the venv launcher pid.
- R6: a recipe for all 23 projects; C1-C7 household offload; I1-I4 cost lines.

### Not in this release

- O3, L3 (#208/#213) and the TWO STRIKES code (#212): parked after repeated Windows CI failures, branches kept.
- Local full-suite run on the Windows laptop: 4942 passed, 2 failed (names not captured); CI green on windows-latest and ubuntu-latest for every merged PR.
