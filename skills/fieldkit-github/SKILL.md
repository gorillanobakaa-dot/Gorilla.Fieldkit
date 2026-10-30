---
name: fieldkit-github
description: Guided, verified GitHub setup for people with little GitHub experience, with fieldkit github - profiles (Full Speed Ahead, Long Horizon Tasks, AI Assisted Auditing, Check My Work, First Repository, Team, Public Release, Private Backup, Freeze), plain-words explanations, a plan of now-to-wanted per setting, apply with a yes before every change and a read-back after, a check against known mistakes (ruleset aimed at no branch, a mistyped required check, auto-merge with no checks, extra-approval trap, branches not deleted), and undo from a snapshot. Use for any GitHub setup question - branch protection, rulesets, required checks, auto-merge, secret scanning, making a repository public, a first repository, "where is that setting".
license: AGPL-3.0-or-later
compatibility: "Needs the fieldkit command (Gorilla Fieldkit, github.com/gorillanobakaa-dot/Gorilla.Fieldkit; Python 3.11+) and gh (GitHub CLI), logged in by the person. Windows, Linux, macOS."
---

# fieldkit github - do not explain GitHub setup from memory

For GitHub setup run `fieldkit github`; the explanations, menu paths, risks and decision logic are in the repository
(`fieldkit/github/profiles.yaml`, `glossary.yaml`, `doctor.yaml`), tested, and the same every time. Do not improvise
a setup procedure.

| The question | Run |
|---|---|
| is this computer ready | `fieldkit github doctor` |
| which setups exist | `fieldkit github profiles` |
| what does a profile do, in plain words | `fieldkit github explain PROFILE` |
| what would change here | `fieldkit github plan PROFILE` (read only, exit 3 = changes) |
| is my setup right / did I make a mistake | `fieldkit github check [PROFILE]` (read only, exit 3 = findings) |
| make it so | `fieldkit github apply PROFILE` - the person's terminal only |
| put it back | `fieldkit github undo SNAPSHOT` or `undo latest` |
| a person with no experience | they double-click `launchers/github-setup.cmd` (.sh Linux, .command macOS) |

Rules: a model never runs `apply` or `undo` (they refuse an agent's shell) and never handles a token or password:
the person types `gh auth login` themselves. Required check names are read from checks that really ran, never typed.
Never delete anything on GitHub. Show the person the tool's own text; do not paraphrase risks away.
