# Developer notes for v0.10.17

PR #716 integrates network diagnosis, radio inspection and triage into the Fieldkit CLI. PR #717 integrates the handoff command with the dispatcher, tool registry, platform inventory and documentation. Its Windows and Linux CI jobs exercise discovery and preview using synthetic session data.

The handoff pipeline extracts saved context, redacts the card, applies provider settings and checks remote cards before launch. Provider CLI availability and model access depend on the installation. Live launch across all providers and physical macOS operation remain unverified.

The generated lifecycle instructions require a release URL for Fieldkit work and explicit owner intent before archiving an owner conversation.
