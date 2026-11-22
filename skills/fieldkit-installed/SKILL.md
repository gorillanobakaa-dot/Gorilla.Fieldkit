---
name: fieldkit-installed
description: Keep the copy of Fieldkit that the `fieldkit` command runs (the Fieldkit-installed worktree) level with origin/main, with fieldkit installed check, sync and where - PASS or FAIL against origin/main, a fast-forward only move that refuses a dirty tree and never resets or deletes, pip install -e re-run only when pyproject.toml changed, verified by HEAD = origin/main and a loading `fieldkit --version`. Use when a merged fix does not show on this machine, a subcommand answers "invalid choice" that main has, the SessionStart line says FIELDKIT INSTALLED COPY, or after a merge to main.
license: AGPL-3.0-or-later
compatibility: "Needs the fieldkit command (Gorilla Fieldkit, github.com/gorillanobakaa-dot/Gorilla.Fieldkit; Python 3.11+ with PyYAML) on Windows, Linux, macOS. Network only for sync (git fetch origin main)."
---

# The installed Fieldkit follows main

**Never `git pull` or `reset` the installed folder by hand.** Source: `fieldkit/core/installed.py`, card `installed`.

| The question | Run |
|---|---|
| is the command I run level with main? | `fieldkit installed check` (PASS / FAIL, uses the last fetched ref and says how old) |
| bring it level | `fieldkit installed sync` (one PASS / FAIL line) |
| which folder is it? | `fieldkit installed where` |

Sync refuses a dirty tree, a tree not on main's history, and a tree a running build holds. It moves with
`git checkout --detach` (fast-forward only), re-runs `pip install --no-deps -e` only when `pyproject.toml` changed,
then checks HEAD = origin/main and that `fieldkit --version` loads. `fieldkit sync` also shows the row, and
`fieldkit ship merge` runs the sync after a merge into main lands. The SessionStart hook only reports.
`FIELDKIT_INSTALLED_DIR` overrides where the tree is looked for. A machine that runs from a checkout has none: NOT-FOUND, silent.
