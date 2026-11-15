# v0.9.2, for developers

## Scope

`v0.9.1..origin/main`: PRs #412, #413, #414, #415.

- #412 (P23): `fieldkit/refusals.py` ledger rows tagged with their machine and exchanged as LEDGER1 notes over `fieldkit peers link` (deduplicated by row id); a repeated key opens a defect (`refusals_policy.yaml`); `tasks.start` refuses a brief with an open read-only defect (`DefectOpen`); `refusals close KEY --test NAME` runs the test first; `overhead` shows hours lost per cause (minutes per refusal are an estimate from the policy file).
- #413: `rotate_stop.zero_session()` reads `NET.yaml` `zero.session` with a regex (the hook loads no YAML) and `permanent_zero()` exempts it; `budget.lines()` writes no handover for Zero. `net.handover(assume=True)` refuses when Zero is on another machine that answers `hello` over the cable (`far_alive`). The guard hook passes the refused command to `refusals.record` so a read-only refusal is marked. `net.check()` runs `refusals.sync_quiet()` (every comms check) instead of the prompt hook.
- #415 (P24): `net.SITREP4` (started, done, now, left) on checkin (`--started --done-so-far --now --left`); a roll row missing one is `INCOMPLETE` (WARN). `rotate.handover` refuses an empty Done or Next (ValueError, CLI exit 2) and sends the four-part sitrep to Zero (`net.report_rotation`, never raises). `zero task --done-cmd CMD`; `zero release --finished` runs it (600 s timeout) and refuses unless exit 0, proof in `done_proof`; orders without it show "done not proven". `roll()`/`check()` return `unanswered` with session, host session and one exact line (`send_message <host>`, `checkin --relieve`, or `zero send`). `command.orphans()` in the sweep: an open order whose station rotated with no relief after `orphan_min` (default 20) wakes Zero once with the `fieldkit task start` line; nothing auto-starts.
- #414: `tests/conftest.py` sets `FIELDKIT_LIFECYCLE_DIR` to a temp folder for every test.

## Not verified

P24 ran on Linux only (CI runs Windows); `--done-cmd` runs through the shell, so its syntax is the shell's; whether the Claude hook payload carries a host session is not checked (falls back to an environment variable). The cable exchange and the far-Zero refusal against the real ThinkPad (it is quiet for a fan test); the ThinkPad's own keeper task is not in this repository.
