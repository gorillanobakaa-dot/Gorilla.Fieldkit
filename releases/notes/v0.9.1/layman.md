# v0.9.1, in plain language

## First: why this is v0.9.1 and not v0.9.0

**v0.9.0 never came out.** Its tag exists, but the release run stopped: the run did not install a small checking program that three tests need. v0.9.1 adds it. Everything below was planned for v0.9.0 and is in v0.9.1.

**The final goal is no longer pushed at you.** Plan row W10 (joining Fieldkit into Gorilla OpenCode) only happens at the very end, when you say so. `fieldkit review` and `fieldkit plan next` used to show it as open work; now they show it as held by you.


## Main goal: learn from past mistakes

**The harness reads its own history.** Every job leaves a record. Fieldkit grouped 3,076 of them by what the job tried to do, found the failures that kept coming back, and wrote down the fix for each one (P18). A later job that hits the same failure gets the known fix instead of starting from zero.

**Refusals explain the way out.** When a safety rule stops a command, the message now carries the correction that worked before (I-20). For two common cases the router fixes the command itself.

**A repeated refusal becomes a defect.** The guard that stops publishing and deleting now writes every refusal to the same list as the other tools. The same reason twice opens one row in the open-problems list, so a wrong rule is found after two hits, not after hours.

**A wrong rule was fixed.** The rule that stops publishing also stopped `fieldkit ship release-ready`, a check that only reads. It blocked this very release twice. It is fixed, and a test now checks every `ship` command against the rule, so a new command cannot slip through unchecked again.

## New tools

- **Local models:** `fieldkit models gguf` says whether a model file is dense or mixture-of-experts and the speed to expect before you load it (P21). `fieldkit models tune` picks settings for a local model on this machine (P22).
- **Everyday jobs test:** `fieldkit accept` runs 13 everyday jobs and records the result (P5).
- **Power:** `fieldkit power investigate` ranks the likely causes of a shutdown nobody explained (P20).
- **GitHub setup:** `fieldkit github` walks through a checked GitHub setup (G1).
- **Pinned downloads:** `fieldkit pins` refuses helper downloads that are not pinned to a fingerprint; `fieldkit image fetch` gets image tools that way (P4, P7).
- **Network questions:** `fieldkit observe` answers which connections and which name lookups a program makes (P10).
- **Repair:** eleven repair tools from Kiro are now `fieldkit repair` actions (I-19).
- **Inbox:** the owner inbox is one readable page, with `fieldkit review --inbox` (P8).

## Everyday problems fixed

- Tests run only for what changed, also during a merge, and are shared across free computers instead of dropped.
- `fieldkit ship prune`, `ship tags` and `ship --all` handle text in any language and report deletes that were refused.
- Gorilla Firefox: the safety checks after install now test the closed doors on Windows and on Linux (P11).

## Not in this release

Not checked on a real run: the everyday jobs test with a local model (only the cloud model was measured), and the learning loop across both computers (refusals are still kept per computer). This page describes each change as its pull request reports it; the release itself did not re-run them.
