# v0.10.14, for developers

Written for someone who will audit, fork or change the code. It covers the same release as the plain-language part above; neither is a summary of the other.

## Scope

`v0.10.13..main`: PRs 690, 693, 694, 695, 696, 697.

- **PR 690, Linux parity and cable first.** Linux paths in leakgate, buildh and thermal; `proc.kill_tree` for POSIX process groups; catchup test isolation; a gate that refuses Windows-only calls on other OSes; PLATFORMS.md regenerated; OS-aware power-scheme test. Capture audit uses the auto route (cable, then SSH); purge and install-app run over `peers link`; toolrouter warns on a hand-run `ssh` to a cable peer.
- **PR 693.** A budget pause (exit 75) is no longer classified as a quota error by the guard.
- **PR 694.** A cable job's return note carries Claude's exit code and the log tail.
- **PR 695.** `claude-health`, `claude-settings` and `claude-settings-set` cable probes: one real `claude` run on the peer with its exit and error; read and write the five cache TTL keys (backup first, read back, no model, no login).
- **PR 696.** The agent quota patterns lost their bare words: `overloaded`, `rate limit`, `credit balance` and `429` need the error's verb or noun next to them (a PID, a count or a test name no longer matches).
- **PR 697.** docs: plan rows R14 and R18 closed with code and test proof.

### Verification

- CI green on each PR (Linux and Windows) before merge.
- `claude-health` over the cable: exit 0, 21.9 s on the Linux laptop.
- Not verified: cable-first fallback to SSH on a pair where the cable is down; long Claude jobs on the Linux laptop.
