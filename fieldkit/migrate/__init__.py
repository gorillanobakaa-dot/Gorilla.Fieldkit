"""Migration control: carrying the Gorilla patch set to a new Firefox release as a plan followed step by step.

The plan itself is the owner's document, Gorilla.firefox/MIGRATION-PLAN.md. This package is how the harness enforces
it, so that a small local model (driven through Gorilla OpenCode) can run a week-long migration without improvising:

    ledger.py       the INTENT LEDGER, intents/INTENTS.yaml in the owner repository: one intent per hunk cluster, with
                    its quoted purpose, the anchors that survive a refactor, and a deterministic behaviour check
    status.py       where every intent stands in one migration: applied, ported, verified in the tree, proven in the
                    installed build (the hunk judge of the claims audit and the decision check kinds, reused)
    intake.py       S1 upstream intake: what the new Firefox release adds (prefs, hosts, modules, actors, about:
                    pages, background tasks, AI/ML), each an INTAKE item that needs a disposition
    consistency.py  S8 documents and owner tools against the decision register and the shipped build
    plan.py         the stages S0-S9, their exit gates and the state file; `advance` refuses while a gate is red
    measure.py      `migrate seed` (writes the ledger) and `migrate check` (the cached measurements the gates read)
    sitrep.py       the SITREP: stage, gate, progress per group, briefs, parked tickets, journal, ONE next action
    guard.py        the drift guard: work items, parked tickets, refusals outside the current stage
    briefs.py       decision brief producers for intake, consistency and lost layers (fieldkit/briefs is the only way
                    to ask the maintainer anything)
    cli.py          `fieldkit build-harness migrate ...`

Nothing here edits the ported tree or the installed build. In the owner repository it writes only intents/.
"""
