# Brief for a cloud session: make Fieldkit pass on Linux and on a clean machine

Written 2026-10-10 by the session that maintains Fieldkit on the maintainer's Windows laptop. Read this first.

## The job

1. **Make the test suite pass on the GitHub runners** (`.github/workflows/tests.yml`: `ubuntu-latest` and
   `windows-latest`). On 2026-10-09 the last pushes failed there while passing on the maintainer's laptop:
   - Ubuntu, 11 failed: `test_a_file_whose_line_endings_changed_is_replaced_not_patched_and_the_proof_still_holds`,
     `test_tracked_upstream_files_that_look_like_junk_are_source_on_both_sides`,
     `test_ensure_ca_stops_only_the_mitmdump_it_started_on_every_platform`,
     `test_an_older_run_uses_artifacts_or_the_launcher_log_and_says_so`,
     `test_the_workflow_plans_delete_and_replace_steps_and_runs_them`, `test_capture_lays_the_truth_out_in_the_owners_layout`,
     `test_the_proof_fails_when_the_set_is_incomplete`, `test_the_proof_rebuilds_the_live_tree_exactly`,
     `test_the_report_says_ready_or_not`, `test_a_healthy_machine_is_ready`, `test_capture_is_deterministic`.
   - Windows Server, 9 failed: the line-endings, proof, report/healthy-machine tests above, plus
     `test_whole_workflow_script_does_the_easy_parts_model_gets_one_hunk`, `test_an_edit_becomes_done_hand_steps_and_a_checkpoint`,
     `test_the_set_on_pristine_gives_the_built_tree`, `test_a_wrong_port_is_put_back`.
   Likely causes, to verify rather than assume: tests that depend on the laptop's git settings (identity,
   `core.autocrlf`, line endings), on tools installed there (the preflight checks), or on Windows behaviour.
   Fix the test or the code, whichever is wrong; never delete or weaken a test to make it pass, and never mark one
   skip without a written reason in the test.
2. **Make the Linux side real where it is only stubbed**, starting with what the build harness relies on: process
   handling by PID (`fieldkit/core/proc.py`, `fieldkit/buildh/probe.py`), the power check and the spoken herald
   (`fieldkit/buildh/power.py`, `fieldkit/buildh/herald.ps1` is Windows-only: a Linux equivalent, offline, e.g.
   `espeak-ng` if installed, otherwise a printed line), window logs (`fieldkit/buildh/window.py`: on Linux, a
   detached `setsid` process or a `screen`/`tmux` session with the same log and `exit N` line), paths, and the
   Debian kernel build (`fieldkit kernel`, `fieldkit/buildh/` debian parts). Each with tests that run on Linux.
3. Keep the suite green on both runners at the end, and say what you could not test (anything that needs a GPU,
   a real Firefox build, administrator rights or the maintainer's machine).

## The rules (the maintainer's, not negotiable)

- **Fail closed. Evidence over claims.** A check that cannot run is a failure, never a pass. Measure, do not assume.
- **Every tool is tested**; every new module is listed in `docs/groups.yaml` (a test fails on orphans); the commit
  hook (`hooks/pre-commit`, enable with `git config core.hooksPath hooks`) runs the suite and a privacy scan.
- **Nothing private goes in**: no personal names, e-mail addresses, home paths of a real user, tokens or keys
  (`python -m fieldkit privacy scan . --git` before every commit).
- **No new network doors**: Fieldkit sends nothing anywhere unless a command is explicitly about the network.
- **Never change** the maintainer's approval or decision files, and never run anything that approves leak-gate
  entries (decision D-157-10: only the maintainer approves).
- **British English** in all text. Plain words first in documents; a technical part after.
- **Keep line endings** of the files you edit (some are CRLF); write files with explicit newline handling.
- Work on a branch and open a pull request; do not push to `main`. Say in the pull request what was measured and
  what was not.

## Context

Fieldkit is the maintainer's deterministic tool harness: it builds a privacy-hardened Firefox (Gorilla Unleashed)
on Windows, proves it never contacts Mozilla, checks its public claims, and is meant to be driven by small local AI
models as well as by Claude. `README.md` and `docs/THE-BUILD-HARNESS-EXPLAINED.md` explain the whole of it.
Two pieces are in progress on the laptop and not on GitHub yet (an agent-coordination bus, a leak-gate confirmation
mode); do not reimplement them.
