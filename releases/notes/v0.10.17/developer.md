# v0.10.17, for developers

Written for someone who will audit, fork or change the code. It covers the same release as the plain-language part above; neither is a summary of the other.

## Scope

`v0.10.16..main`: PRs 716 and 717.

- **PR 716, unified network CLI.** Added `fieldkit/network/cli.py` as the `fieldkit network` command and registered its cards, command tree, OS-parity inventory, documentation group, Linux/Windows E2E checks, and `tests/test_network.py`. The command consolidates network state, diagnosis, Wi-Fi radio/profile/channel work, routes, router and LAN-device inspection, and watchdog integration. Platform-dependent calls are guarded; JSON help and live-diagnosis coverage were corrected before merge.
- **PR 717, cross-provider session discovery and guarded handoff.** Added `fieldkit/handoff.py`, `tests/test_handoff.py`, `docs/HANDOFF.md`, Linux/Windows E2E checks, CLI and command-tree registration, platform inventory, documentation group, and the `handoff` tool card. Session discovery covers local Claude, Codex, Kiro, Antigravity, and LM Studio stores; cards carry source, title, project, recent messages/tool calls, artifacts, and `last_activity`. Cards are redacted through Fieldkit privacy helpers and remotely dispatched only after Fieldkit's privacy scan, model guard, and explicit approval. The launcher uses `fieldkit.proc` rather than raw subprocess invocation.

### Verification

- PR #716 merged as `cdf9080c`; PR #717 merged as `a240b927`.
- PR #717's dedicated `e2e-handoff-linux` and `e2e-handoff-windows` checks succeeded. Its Windows shard 3 initially stalled while the test step was in progress; attempt 2 completed successfully with 235 passed and 2 platform-specific skips.
- The `tests` workflow for `main` at `a240b927` completed successfully. `fieldkit ship release-ready v0.10.17 --repo <checkout>` confirmed CI green and OS parity current before this release preparation.

### Known limits

- ChatGPT share pages are client-rendered and may not expose their transcript to a non-browser fetcher. A user can instead provide the conversation text or a local file.
- A remote provider launch has not been exercised against every provider CLI in this release process. The preview, privacy-gate, and Linux/Windows CI coverage are not evidence that every third-party CLI/model spelling will work on a given machine.
