# P18: distil the captured experience into deterministic workflows, tools and checks

Owner, 2026-11-03 (idea I-15, accepted in `fieldkit review`): every command and script in the capture was run for a
reason. The reason and the method are the experience. Turn it into workflows, hooks, loops, tools and checks that
are deterministic, so that an old or weak local model, and a person with ADHD or other special needs, can follow
one safe next step and see a check say whether it worked.

This page is the plan. It starts after phase 0 (repairs) in `docs/HARNESS-PLAN.md`.

## What is in the capture (measured 2026-11-03, `capture_episodes.py`)

| Measure | Count |
|---|---|
| Captured items | 48,746 (27,114 commands, 16,260 scripts, 4,614 edits, 718 files, 40 sessions) |
| Items whose agent wrote down the purpose (`description`) | 18,187 |
| Distinct requests (first line of the request that caused the work) | 2,880 |
| Sessions | 343 |
| Episodes (one session working on one request) | 3,059, of which 1,103 have 3 or more steps |
| Episodes with at least one failure | 603 |
| Failed commands | 1,727, of which 1,604 were followed by a success in the same episode |
| Hook or tool-router refusals (the harness already said "do it this way") | 956 |
| Labels given by the capture | workflow 15,139; build step 12,246; test 8,771; debugging procedure 8,425; workaround 2,072 |

Not measured yet: how many episodes reach the same goal by the same steps (stage 4 answers it); the capture covers
this computer's Claude sessions only. Gemini, Gorilla OpenCode and the Linux laptop's sessions are not in it.

## Principles

1. Deterministic first. Stages 0 to 4 use no model. A model is called only to name or generalise a group the
   deterministic stages already formed, and what it returns is data (YAML), accepted only when a check passes.
2. Nothing is discarded. Every item ends in a state: `tool`, `procedure` or `promoted`, with evidence; an item that
   cannot become a tool becomes a searchable procedure with the reason (rules/CORE.md, zero-loss capture).
3. One step, one check. Every workflow is served as: one plain sentence, one command, one check with a clear
   PASS or FAIL, then the next step. No plan for the reader to hold in their head.
4. Reuse, do not duplicate. The goal `harvest-automatic` already has the jobs `adopt-auto`, `failure-knowledge`,
   `languages-workflows` and `ledger-strict`. P18 widens their scope to episodes and intent; it does not start a
   second pipeline.
5. Spend is braked. Each stage has a token budget; stages 0 to 4 have a budget of zero.

## Stages

| # | Stage | What it does | Output | Done when |
|---|---|---|---|---|
| 0 | Coverage and clean requests | Scan every agent (Claude incl. subagents, Gemini, Gorilla OpenCode) on both computers; drop noise from the request field (cross-session messages, "Tool loaded") into a separate field, never delete it | coverage report per agent and machine | every transcript on disk is read or listed with a reason; requests without noise measured |
| 1 | Episodes | Cut each session into episodes (request -> steps -> result); keep the order, purpose, exit code and error of every step | `fieldkit capture episodes` (records in the capture store) | 3,059+ episodes stored, each step linked to its item |
| 2 | Failure -> fix | For each failure followed by a success, store the error text, what changed and the command that worked | triage signatures + a check per signature | every one of the 1,604 pairs is a signature, a duplicate of one (counted), or a procedure with the reason; replay of a sample matches |
| 3 | Refusals -> defaults | Count which router and hook rules fired (956) and what the agent did next; the common correction becomes the default path (R5: the router does instead of refusing) | a table rule -> correction -> count | the top 10 corrections are automatic |
| 4 | Intent groups | Group the 2,880 requests and 18,187 stated purposes by words (deterministic, no model) into task kinds: build, test, debug, release, sync, clean up, research, documents | groups with their episodes | every episode is in a group or in "other" with a count |
| 5 | Workflow recipes | For each group, align the step sequences of its episodes into one recipe: steps, a check after each step, the known failures and their fixes from stage 2. A model names and generalises once per group; the recipe is YAML | workflow YAML under `fieldkit/method/` + a card | the recipe replays (dry run) on at least 3 of its own episodes |
| 6 | Weak-model test | Give each recipe to a small local model and to the plain-language reader shape (fieldkit-sitrep rules) | measured success per recipe | a small local model completes the recipe's checks on a test tree; the sitrep checker passes the text |
| 7 | Built in | Register every tool and workflow: tools.yaml, cards, `fieldkit tools find`, a prompt hook that offers the matching workflow for a matching request, release page | `fieldkit integration check` passes | all six conditions of docs/INTEGRATION.md |
| 8 | Strict ledger | Every item in a final state with evidence | `fieldkit capture ledger --strict` exits 0 | 0 outstanding, 0 lost |

## Order and cost

- Stages 0 to 4 are scripts: no model, minutes to hours of CPU, run under `fieldkit guard --`.
- Stage 5 is the only model stage: Sonnet, one call per group, a dollar budget per group, through the goal
  supervisor. Opus only to review a recipe that failed its replay twice.
- Stage 6 uses a local model (LM Studio), unloaded after the test.
- First proof: one group end to end (for example "release a Gorilla OpenCode build") before the rest.

## Measures (quoted in the HARNESS-PLAN row when done)

- episodes stored / episodes found; signatures from failure pairs; recipes that replay; recipes a small local
  model completes; `capture ledger --strict` outstanding = 0.

## Tools used to size this plan

`Documents/Scripts/CaptureFamilies/` (outside this repo): `capture_families.py` (command families, script
languages), `capture_context.py` (how much context each item carries), `capture_episodes.py` (the table above).
Stage 1 adopts them into `fieldkit capture`.
