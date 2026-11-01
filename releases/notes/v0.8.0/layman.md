# v0.8.0, in plain language

## Main goal: work that runs without you

**Every rule is proven.** The rules are now a list the tools read. For each rule there is a test that breaks it on purpose and shows the tool catching it (`fieldkit rules prove`: 54 caught, 0 not caught). A rule that no tool enforces is flagged for deletion or enforcement. The rules are also shorter: duplicates are said once.

**Rules on any device.** A computer with an agent installed but no rules file now fails the check, and the install creates the file. Windows and Linux are set up the same way.

**Archiving is guarded for every agent.** The guard refuses to archive the coordinating session (Zero), a session that is still running, or one with sessions nested inside it. The desktop app's session records can no longer be edited by a model, which would have archived a session past the guard (idea I-10). Claude, Antigravity and OpenCode are covered; Codex and Gemini have no session records of this kind, so there is nothing to edit.

**Unattended pushes work.** A push whose output was piped through `tail` was refused as if it were forbidden. It is now recognised as the same allowed push (B1).

**Timing flukes are not strikes.** If a change fails CI only on tests known to be sensitive to timing, it counts as a flake, not one of the two strikes. The third flake does count.

## Everyday problems fixed

**One review.** `fieldkit review` shows every parked idea and inbox item in one list, in plain words, answered once (P1). The idea I-09 (rules kept lean by a tool) and the idea I-10 are marked built.

**Find and start any program** on Windows, Linux and macOS (`fieldkit app`). **Syncing** reads CI from the public GitHub API when no login exists, so a headless job can update itself. **Opencode jobs** in CI are mapped by name per repository, and a push no longer auto-merges where no CI check is required.

**Zero** can be taken over by any live station when it dies, never archives while it holds the seat, and retires only into a new Zero.

**Long-term proof.** `fieldkit metrics` reads the harness records to show whether the harness is getting better over weeks.

## New tools (ideas you approved)

**Images** (`fieldkit image`, P4): check, shrink, compare, read text, and strip personal data. **Web** (`fieldkit web`, P3): fetch a page, falling back to a real browser with a throwaway profile, no telemetry and no downloads. A private address hidden inside an IPv6 address can no longer slip past the safety check. **Observe** (P10, one toolkit for Windows, Linux and macOS): open files, ports, processes, a live watch, trace, usage and events.

## Not in this release

Not checked on a real run: a real Firefox driven by the web fallback (Gorilla Unleashed never starts its remote agent here), Safari, and live Linux and macOS runs of the web, image and program-start tools. The review page was not tried by you. The coordinating-session rules were checked on the Windows laptop; the install on the Linux laptop follows this release.
