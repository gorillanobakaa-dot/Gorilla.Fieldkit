# v0.9.3, for developers

## Scope

`v0.9.2..origin/main`: PRs #417 and #419.

- `inbox.migrate_legacy()` moves open owner-inbox items whose kind is not in `OWNER_KINDS` (only `hardline` since 2026-11-01) to the agent ledger with `moved_from`; the file keeps them as state `moved`. `review.entries()` runs it and lists only `OWNER_KINDS`. On the VAIO it moved #27 to #39 (13 items).
- `inbox.words_for(item)` builds what / if you approve / if you leave from the item's own title, why and command when it has none; `inbox.add` stores them for owner items. The test that asserted the placeholder now asserts its absence.
- `review.ideas_in_plan()` + `idea_row()`: a plan row counts for an idea only when it names the idea id and its date (ids were reused: the October I-01 is P2, the 2026-11-10 I-01 is P23). A built idea says "ALREADY BUILT as plan row P23 (#412)"; `ideas.set_status(accepted)` on it records the existing row and writes no brief.
- `plan.HEAD_IDS`: a merge whose PR title starts with the row id ("P24: ...") counts for that row; P23 and P24 are DONE (#412, #415).

- #419: plan status cells written from their merges: R10, R14, R15, R16, R19, P19, P20, P21 DONE; P4 and P7 DONE for `fieldkit image` with the Gorilla OpenCode toolbox part not verified; P5 PARTIAL (Linux 13/13 with the cloud model; the local-model run is missing: no local model runtime on the VAIO, LM Studio is on the ThinkPad). `plan next` now says P5 is the open row. A test string with an invalid escape (`"\\|"`) no longer warns on every commit.

## Not verified

The review page was read as rendered on the VAIO; the owner's own terminal run of `fieldkit review` after this release is not yet seen.
