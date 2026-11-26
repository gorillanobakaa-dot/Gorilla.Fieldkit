## What this is

Fieldkit v0.10.17 adds a unified cross-platform network command and a guarded session-handoff command for continuing saved coding work with another provider.

## Should you download it?

Yes, if you use Fieldkit to diagnose Wi-Fi or routes, or if a coding session needs to be resumed by another supported provider. The new commands run on Windows, Linux, and macOS; their available actions still depend on the operating system and the installed provider CLIs.

## Why this matters

Network diagnosis previously lived behind separate command surfaces. Saved coding conversations could be located manually but not handed over through one Fieldkit command with a redacted, privacy-gated card. This release makes both workflows discoverable through the command registry while retaining explicit approval before a remote provider receives a handoff card.
