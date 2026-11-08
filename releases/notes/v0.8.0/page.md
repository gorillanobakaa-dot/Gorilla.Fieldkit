## What this is

Fieldkit v0.8.0: every written rule is now checked by a tool that is itself tested, the rules work on any device and any agent, and the harness gained image, web-browser and system-observation tools. Two blocks that stopped unattended runs are gone: a headless push that was wrongly refused, and timing-sensitive tests that counted as real failures.

## Should you download it?

Yes, if you already run Fieldkit: it removes the main reasons a job stopped and waited for you, and it proves each rule can actually catch a breach. Installing is unchanged (the install script, or `fieldkit update`). There is no separate program to download.

## Why this matters

A rule nobody checks is a wish. In this release each rule has a test that breaks it on purpose and shows the tool catching it: 54 caught, 0 not caught.

---
# In plain language: everything in this release

This is the complete explanation, not a summary of one. Nothing below is behind a link.

<!-- plain-language track: in full, on this page -->

### Main goal: work that runs without you

**Every rule is proven.** The rules are now a list the tools read. For each rule there is a test that breaks it on purpose and shows the tool catching it (`fieldkit rules prove`: 54 caught, 0 not caught). A rule that no tool enforces is flagged for deletion or enforcement. The rules are also shorter: duplicates are said once.

**Rules on any device.** A computer with an agent installed but no rules file now fails the check, and the install creates the file. Windows and Linux are set up the same way.

**Archiving is guarded for every agent.** The guard refuses to archive the coordinating session (Zero), a session that is still running, or one with sessions nested inside it. The desktop app's session records can no longer be edited by a model, which would have archived a session past the guard (idea I-10). Claude, Antigravity and OpenCode are covered; Codex and Gemini have no session records of this kind, so there is nothing to edit.

**Unattended pushes work.** A push whose output was piped through `tail` was refused as if it were forbidden. It is now recognised as the same allowed push (B1).

**Timing flukes are not strikes.** If a change fails CI only on tests known to be sensitive to timing, it counts as a flake, not one of the two strikes. The third flake does count.

### Everyday problems fixed

**One review.** `fieldkit review` shows every parked idea and inbox item in one list, in plain words, answered once (P1). The idea I-09 (rules kept lean by a tool) and the idea I-10 are marked built.

**Find and start any program** on Windows, Linux and macOS (`fieldkit app`). **Syncing** reads CI from the public GitHub API when no login exists, so a headless job can update itself. **Opencode jobs** in CI are mapped by name per repository, and a push no longer auto-merges where no CI check is required.

**Zero** can be taken over by any live station when it dies, never archives while it holds the seat, and retires only into a new Zero.

**Long-term proof.** `fieldkit metrics` reads the harness records to show whether the harness is getting better over weeks.

### New tools (ideas you approved)

**Images** (`fieldkit image`, P4): check, shrink, compare, read text, and strip personal data. **Web** (`fieldkit web`, P3): fetch a page, falling back to a real browser with a throwaway profile, no telemetry and no downloads. A private address hidden inside an IPv6 address can no longer slip past the safety check. **Observe** (P10, one toolkit for Windows, Linux and macOS): open files, ports, processes, a live watch, trace, usage and events.

### Not in this release

Not checked on a real run: a real Firefox driven by the web fallback (Gorilla Unleashed never starts its remote agent here), Safari, and live Linux and macOS runs of the web, image and program-start tools. The review page was not tried by you. The coordinating-session rules were checked on the Windows laptop; the install on the Linux laptop follows this release.

---

# For developers: how it works, and how to check it

Written for someone who will audit, fork or change the code. It covers the same release as the plain-language part above; neither is a summary of the other.

<!-- developer track: in full, on this page -->

### Scope

`v0.7.3..origin/main`, merged PRs #232-#271 (plan items I-01, I-09, I-10, P1-P5, P10, B1, C1, C3, W6).

- Rules as data: rules/rules.yaml generates CORE.md; `fieldkit rules lint` (#264) checks per-block hashes, byte budget, duplicates, enforcer and command names; `fieldkit rules prove` (#265) runs rules/proofs.yaml, one mutation per demand: `54 caught, 0 not caught`. `fieldkit honesty check` runs from `rotate handover`, `task report`, `sitrep check --session`.
- Rules on any device (#267, brief section E): an installed agent with no global rules file is a FAIL and install creates it; Claude hooks (tool router, room) checked and installed the same on both systems.
- Archive guard (#265): never Zero, never a running session, never nested children. Session records are never written by a model (#270, I-10, proof CAUGHT); the guard loads the lifecycle package only for `archive_session`.
- Headless push (#269, B1): a mandated `ship push` whose output is tailed is still the push. Strikes (#268): a red made only of listed timing tests is a flake; the third flake counts; CI prints FAILED ids last for `ci-wait`.
- `fieldkit review` (#244, #253): one list for ideas and inbox, `state/REVIEW.md`, launchers; owner-terminal check (#252).
- `ship push --auto` (#242, #251): PR plus auto-merge only where the base requires a check. `sync` reads CI from the public API without `gh` login and maps Opencode job names.
- `fieldkit web` (#245, #246, #247): stdlib browser drivers (chromium dump-dom, gecko BiDi, safaridriver); NAT64 `64:ff9b::/96` judged by the embedded IPv4. `fieldkit image` (#250). `fieldkit app find|start` and the agent finder (#255, #256). `fieldkit observe` (#271): open-file, port, proc, watch, trace, usage, events; the Linux proc reader imports `pwd` lazily so Windows loads it.
- Zero (#239, #240): any UP station may assume a dead Zero; Zero never archives while it holds the seat; retire only into a new Zero. `fieldkit metrics` (#241). FM-10 busy-spin ignores conhost (#257), `fieldkit size` passes stdio explicitly (#232).

### Not in this release

- Gecko BiDi on a real Firefox, Safari, and live Linux/macOS runs of web, image, app start and observe were not verified; tests use faked systems.
- The fieldkit review page was not run by the owner. P5c (model told what it has) is on an unmerged branch.
- #253's CI replay (C3) and the VAIO install were not verified at the time of the rules rows; the VAIO is installed by Zero after this release.
- Local full-suite run on the Windows laptop: gorilla-size is not built, so test_every_reviewed_card_agrees_with_its_command fails there.
