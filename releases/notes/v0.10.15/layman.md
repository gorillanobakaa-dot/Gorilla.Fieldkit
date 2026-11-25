# v0.10.15, in plain language

This is the complete explanation, not a summary of one. Nothing below is behind a link.

### What changed for you

- **Half-size, portable AI agent rules:** the shared core rules in `rules/CORE.md` and the generated lifecycle/rotation blocks were trimmed roughly in half so every AI turn carries less overhead. Personal and machine-specific notes were removed from the public core rules and moved to an optional, git-ignored file (`~/.fieldkit/rules-local.md`) that `fieldkit rules install` automatically includes if present.
- **Optional rule setup during install:** both no-git installers now accept an optional flag (`install.ps1 -Rules` on Windows, `install.sh --rules` on Linux and macOS) that asks once and runs `fieldkit rules install` and `fieldkit rules check` for installed agent folders. Without the flag, installers keep everything strictly inside the target folder and print the optional command at the end.
- **Compile-once Python hot paths (`fieldkit pycache`):** new `audit`, `warm`, `profile`, and `compare` commands pre-compile Python bytecode once and install a `.pth` helper in `.venv` so hooks and hot commands start faster without runtime recompilation.
- **Safer GitHub shipping and harness checks:** `fieldkit ship` now uses a read-only ref classifier, supports report-only branch pruning, requires both Linux and Windows CI checks before auto-merge, and closes the GitHub harness push/stale-lock steps.
- **Account-wide quota guard and readable errors:** quota protection now tracks limits across the account with a real liveness probe (without flashing a console window on Windows) and stores the readable quota message instead of raw JSON tails.
- **Checked sitrep before archiving:** sessions cannot be archived until a checked situation report (`fieldkit sitrep check`) has been generated and shown.
- **Windows encoding and state paths:** subprocess text decoding now uses UTF-8 cleanly on Windows (fixing a `charmap` crash during `fieldkit ship push`), and rule song reports stay under `state/`.

### What did not change

No tool was removed. Default runs of `install.ps1` and `install.sh` (without `-Rules` / `--rules`) still write nothing outside the chosen Fieldkit folder.

### What was not checked

Full live `install.ps1 -Rules` and `install.sh --rules` runs were tested offline in CI with `FIELDKIT_SKIP_PIP=1` and stubbed installers; an end-to-end live download from PyPI on a clean machine was not run for this release.
