# v0.10.15, for developers

Written for someone who will audit, fork or change the code. It covers the same release as the plain-language part above; neither is a summary of the other.

## Scope

`v0.10.14..main`: PRs 496, 703, 704, 705, 706, 707, 708, 709, 710, 711.

- **PR 496, `fieldkit pycache` (`audit`, `warm`, `profile`, `compare`).** Compiles hot-path Python modules once, installs a `.venv` `.pth` bytecode hook, adds the `hotpath_compiled` integration check row and `e2e-pycache` Linux/Windows CI jobs, regenerates `cmdtree.json`, and closes the `git cat-file` child in `audit_patches`.
- **PR 703, UTF-8 subprocess text mode and `state/` paths.** `fieldkit.proc` text mode decodes UTF-8 explicitly (preventing Windows cp1252/charmap `0x81` crashes in `ship push`); `rules_song` writes under `state/`.
- **PR 704, readable quota guard reason.** Stores the human-readable quota line instead of the trailing fragment of the status JSON.
- **PR 705, account-wide quota guard.** Tracks quota across the account with a proof-of-life probe launched via `fieldkit.proc` (no raw `subprocess`, no Windows console popup).
- **PR 706, ship ref classifier and dual-OS auto-merge.** Read-only ref classifier (same repo, base, head commit); report-only `fieldkit ship prune`; `fieldkit ship push --auto` requires both Linux and Windows CI contexts; unified model and effort strategy.
- **PR 707, sitrep archive gate.** Blocks archiving a session until a fresh `fieldkit sitrep check` passes and is recorded with the real clock.
- **PR 708, leaner project rules and skill descriptions.** Shortened skill descriptions, resolved four conflicting lines in the repository's project rules file, and brought it under the 1400-token project budget.
- **PR 709, GitHub harness steps 2+3 (P50).** `stale_lock` reads the git directory directly from files rather than spawning `git`; completes the GitHub harness push-guard steps.
- **PR 710, halved shared core rules.** Compacted `rules/rules.yaml` and regenerated `rules/CORE.md` plus generated lifecycle/rotation blocks while preserving all proof anchors and required rotation phrases.
- **PR 711, portable core rules and optional installer wiring.** Removed personal/machine-specific entries from `rules/rules.yaml` / `rules/CORE.md` in favor of optional `~/.fieldkit/rules-local.md` (`<!-- fieldkit-local-rules v1 -->`), forwarded `--home` in `fieldkit rules install|check`, and added opt-in `-Rules` (`install.ps1`) and `--rules` (`install.sh`) flags with offline CI tests.

### Verification

- CI green on every merged PR (Linux and Windows) prior to merge, including 53 check runs on PR #711.
- Static gates passed locally before tagging: `fieldkit rules check --repo-only`, `fieldkit repair ps-compat-check`, `fieldkit integration check --changed`, `fieldkit release-page check`, and `fieldkit privacy scan . --git`.
- Not verified: live PyPI network install with `-Rules` / `--rules` on a brand-new host (CI covers both flags offline under `FIELDKIT_SKIP_PIP=1`).
