---
name: fieldkit-github
description: "Guided, verified GitHub setup with fieldkit github: profiles, plan, apply, read-back, mistake check, undo. Use for branch protection, rulesets, required checks, auto-merge, secret scanning, going public, a first repository, \"where is that setting\"."
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
| many PRs wait on a serial merge (each merge makes the rest BEHIND) | `fieldkit ship batch run` (one branch, one CI, one merge; `batch collect` first shows who joins and why not) |
| a person with no experience | they double-click `launchers/github-setup.cmd` (.sh Linux, .command macOS) |

Rules: a model never runs `apply` or `undo` (they refuse an agent's shell) and never handles a token or password:
the person types `gh auth login` themselves. Required check names are read from checks that really ran, never typed.
Never delete anything on GitHub. Show the person the tool's own text; do not paraphrase risks away.
