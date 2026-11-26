# v0.10.17, in plain language

This is the complete explanation, not a summary of one. Nothing below is behind a link.

### What changed for you

- **One network command:** `fieldkit network` brings together Wi-Fi radios, nearby networks, saved profiles, routes, router and LAN-device views, signal/watchdog information, and diagnosis. It accepts everyday aliases such as `status`, `diag`, `wlan`, `nearby`, `saved`, and `gateway`; it does not read Wi-Fi passwords or keys.
- **A guarded way to resume saved coding work:** `fieldkit handoff` can find a saved session by its UUID, title text, or project from supported local Claude, Codex, Kiro, Antigravity, and LM Studio session stores. It records the session's last activity and produces a redacted handoff card for the selected target provider.
- **Remote handoff remains deliberately explicit:** a handoff to a remote provider is preview-only unless approval is supplied at a keyboard. Before dispatch, Fieldkit runs its privacy scan; a scan finding, or an unavailable scan, prevents the remote launch. Local LM Studio handoffs do not send the card over the network.

### What did not change

The handoff command is registered as a draft, irreversible tool card. It writes cards under `~/.agent-handoff/cards` and can launch another agent only after the privacy gate and explicit approval. The network command continues to report an unsupported operation honestly on platforms where the underlying facility is unavailable.

### What was checked

GitHub Actions tested both additions on Linux and Windows. The dedicated `e2e-network-*` and `e2e-handoff-*` checks passed on both systems. The post-merge `main` workflow for commit `a240b927` also completed successfully.
