# v0.9.4, for developers

## Scope

`v0.9.3..origin/main`: PR #420 (plan row C8) and PR #421 (idea I-27 built as C8).

- `fieldkit/peers/link_relay.py`: `relay listen --as STATION` is the per-session ear; it takes the relay's duty only while no relay runs, refuses a second ear for the same station, and wakes only for traffic addressed to its station or to everyone.
- `fieldkit/autopilot/room.py`: `_ear()` nags with the exact line on every UserPromptSubmit and at most every `EAR_NAG_S` on tool calls, never on the call that starts the ear, never headless, not deduplicated (an ear that ended must be restarted every time).
- `fieldkit/zero/net.py`: a deaf station is a finding of the comms check; a cable note for an OpenCode station on this computer is pushed to it on arrival.
- Tests: `tests/test_peers_link_ear.py`, additions to `test_zero_net.py`, `test_autopilot_room.py`; `tests/ear_mutations.yaml`: 7 mutations, 7 caught, 0 survived.

## Not verified

A real note crossing the cable between the two computers waking an idle session end to end was not measured by this job. Run `test_autopilot_room.py` before `test_zero_net.py` in one pytest process: in the reverse order a state left by `test_zero_net.py` makes `test_the_hook_writes_shows_and_never_fails` fail (it passes alone and in the suite's own order).
