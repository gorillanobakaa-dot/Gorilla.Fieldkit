# v0.9.1, for developers

## Since the v0.9.0 tag

- v0.9.0 (tag at 70729e03) was never published: `release.yml` ran the whole suite without `desktop-file-utils`, so `tests/test_installverify.py` failed 3 tests on `desktop.valid` (run 42727148074). `tests.yml` already installed it; `release.yml` now does the same. The v0.9.0 tag is kept, never moved.
- #410: `fieldkit/plan.py` reads W, Z and G rows; `is_held()` keeps a row whose status is "FINAL GOAL" or "not now" out of `open`; `fieldkit plan next` prints such a phase as `held`, `fieldkit review` lists W10 under "Held by the owner, not work to do". P23 (idea I-01) is phase 7f.

The v0.9.0 notes follow unchanged.


## Scope

`v0.8.1..origin/main`, merged PRs #243 to #408 that landed after v0.8.1 (151 commits).

- Capture and distil (P18, #340 to #375): episodes stored in the journal (3,076), failure-to-fix signatures (`captured.yaml`), refusal table (956 to 40 rules), intent groups, recipes per group with replay counts (build 2 of 25, documents 3 of 9, release 3 of 22), `fieldkit capture workflow list|show|offer`, capture on/off switch, `capture classify` with a final state per open item.
- Tool router (#344, #388, I-20): each refusal carries the measured correction from `refusal_table.yaml`; two refusals are corrected in place (guard prefix, quoted heredoc delimiter).
- Hard lines (#406, #407): the HARD LINE 1 publish pattern ends in `(?![\w-])`, so `ship release-ready` is a read. `fieldkit.method.ship.ACTIONS` is the one subcommand list; a test fails until each new subcommand is classed publishing or reading.
- Refusals (#408): `claude_hook.main` records every deny as tool `guard` in `state/refusals.jsonl`; the same key twice opens one ZERO-OPEN-PROBLEMS row.
- Zero and rotation (#342, #377, #385, #352, #339): epoch fence inside the lock, a live session assumes a MISSING Zero, permanent Zero exempt from rotation, R19 hard rotate, Stop hook only for a really open PR.
- Tests (#349, #369, #371, #372, #386, #378): `testcost` selects by what changed in `cli.py` / `tools.yaml` and by the branch side of a merge; R15 split across devices; a git commit takes no admission slot.
- New commands: `models gguf` (#402), `models tune` (#403), `accept` (#391, #392), `power investigate` (#401), `github` (#243), `pins` (#309), `image fetch` (#368), `observe conns|dns` (#389), `repair ACTION` (#390), readable inbox (#364), `triage` install and release kinds (#323).
- Ship (#398 to #400): UTF-8 throughout, `prune` reports refused deletes, `tags` and `--all` across owner repos.
- Firefox P11 (#393 to #397, #404): doors probes in post-install, firefox-windows stages for the 158 tree, Linux proof on the `.deb`.

## Not in this release

- P5 with a local model was not measured; Linux 13 of 13 is with the cloud model only.
- `refusals.jsonl` is per computer; Windows rows do not reach Linux (idea I-01).
- The listed PRs were not re-run for this page; each carries its own test result.
