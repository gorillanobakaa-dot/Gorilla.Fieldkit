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

## Stage 6 and 7 result (2026-11-03)

- `fieldkit capture weaktest`: sitrep reader rules pass on 17 of 17 steps (release 12, documents 5). Local model
  `google/gemma-4-12b-qat` (LM Studio, loaded for the test, unloaded after) gave the right PASS/FAIL verdict on 33 of 34
  cases (release 24/24, documents 9/10; the miss was an answer cut off at the 400-token limit, no verdict word). Not
  verified: recipes merged after this run (sync, test), and whether the model can run the commands itself.
- `fieldkit capture workflow list|show|offer` serves a recipe one step at a time; the prompt hook prints the offer line
  when a request matches an accepted recipe.

## Kiro's extraction, integrated (2026-11-08)

Kiro (2026-11-05) mined the capture into 57 carded tools, a router, 10 signatures and 23 workflow candidates in
`Desktop/50000.scripts.experience/out/` (outside the repository). `fieldkit tools external out/tools` read the
catalog: 6 compatible-untrusted, 51 incompatible (Kiro's cards use effects and safety values outside Fieldkit's schema), 0 runnable. Every tool has
one disposition below.

**Signatures.** All 10 are the triage set `agent-shell` (`fieldkit triage LOG --set agent-shell`), each fix naming
a Fieldkit command.

**Router.** Kiro's `route.py` / `dispatch-table.json` compiled signatures + the refusal table + workflow YAMLs into
one lookup. Not ported: it is a second router beside the real ones. Errors go to `fieldkit triage --set auto`
(the 10 signatures are there now), commands are refused by `fieldkit/autopilot/toolrouter.py` (rules in code,
each naming the tool), intents get `fieldkit capture workflow offer`. Checked 2026-11-08: the tool router does
NOT read `fieldkit/capture/refusal_table.yaml`; that file is a measured report (refusal -> what the agent did
next), regenerated by `capture distill refusals`, not a rule source. Its top rows show where a refusal message
did not lead to a success within 5 steps (e.g. code-with-backslashes-in-a-heredoc: 39 of 130); that is parked as
idea I-20, not a change made here.

**Workflows.** None of the 23 candidates has a PASS check per step (all `not-promotable-without-human-
implementation-review`), so none is a recipe. Each is a procedure file `fieldkit/method/workflows/<id>.procedure.yaml`
with its exact blocker; `fieldkit capture workflow list` shows them. Replay-backed first: subgroup-other-python (24/99), subgroup-test-python (15/106), subgroup-test-go (6/32), subgroup-other-go (5/9), subgroup-release-other (4/10), subgroup-sync-other (3/7), subgroup-debug-go (2/10), subgroup-sync-python (2/11), subgroup-other-other (1/93), subgroup-test-cargo (1/23).

| Kiro tool | Catalog | Disposition | Where / why |
|---|---|---|---|
| bash-calls-wallclock | incompatible | native in Fieldkit now | `fieldkit overhead bash-calls` (batch 2) |
| invalid-escape-fix | incompatible | native in Fieldkit now | `fieldkit pyliterals` (batch 2): value-preserving, Kiro's regex rewriter changed values |
| pdf-bbox-check | incompatible | native in Fieldkit now | `fieldkit office form-bbox-check` (batch 1) |
| pdf-fillable-check | incompatible | native in Fieldkit now | `fieldkit office fillable-check` (batch 1) |
| pptx-chart-check | incompatible | native in Fieldkit now | `fieldkit office pptx-chart-check` (batch 1) |
| pptx-master-check | incompatible | native in Fieldkit now | `fieldkit office pptx-master-check` (batch 1) |
| pptx-schema-verdict | incompatible | native in Fieldkit now | `fieldkit office pptx-schema-verdict` (batch 1) |
| skill-frontmatter-check | incompatible | native in Fieldkit now | `fieldkit skills validate --strict` (batch 1) |
| skill-package | incompatible | native in Fieldkit now | `fieldkit skills package` (batch 1) |
| unicode-escape-fix | incompatible | native in Fieldkit now | `fieldkit pyliterals` (batch 2): only drive paths, file must parse |
| advanced-distill | incompatible | Fieldkit already has it | `fieldkit capture distill` (stages 1-7) |
| assessed-word-count | incompatible | Fieldkit already has it | the academic harness counts words (`fieldkit academic`, skill fieldkit-academic) |
| benchmark-aggregate | incompatible | Fieldkit already has it | `fieldkit model-eval` / `fieldkit mcp-eval` report their runs |
| build-phases-split | incompatible | Fieldkit already has it | `fieldkit overhead` (bare parts: migrate, compile, package) |
| capture-gap-reconciler | incompatible | Fieldkit already has it | `fieldkit capture distill`; capture is OFF by default now (PR #374) |
| capture-pair-audit | incompatible | Fieldkit already has it | `fieldkit capture distill pairs` |
| capture-source-inventory | incompatible | Fieldkit already has it | `fieldkit sweep` (scripts not adopted) and `fieldkit size hash` |
| capture-workflow-miner | incompatible | Fieldkit already has it | `fieldkit capture distill recipes`; its 23 candidates are the procedures below |
| cost-since | incompatible | Fieldkit already has it | `fieldkit cost` / `fieldkit session-cost` |
| first-user-msg | incompatible | Fieldkit already has it | `fieldkit teach` / `fieldkit board` read transcripts |
| mandate-trust-pin-verify | incompatible | Fieldkit already has it | `fieldkit mandate-doctor` (fail-closed mandate check) |
| pr-green-ready | incompatible | Fieldkit already has it | `fieldkit ship ci-wait` and the merge train (`fieldkit merge-train`) |
| pytest-suite-stats | incompatible | Fieldkit already has it | `fieldkit overhead bash-calls` (pytest targeted / full suite rows) and `fieldkit testcost` |
| rules-assemble-check | incompatible | Fieldkit already has it | `fieldkit rules` / laptop-rules (one source, copied between markers) |
| session-active-span | incompatible | Fieldkit already has it | `fieldkit overhead` (wall clock split by bucket) |
| tool-honesty-verify | incompatible | Fieldkit already has it | `fieldkit honesty check` and `fieldkit mutate` |
| toolbox-drop-verify | incompatible | Fieldkit already has it | `fieldkit release verify` (SHA256SUMS of a release) |
| brace-parity-check | incompatible | port queued (idea I-19) | C/C++ delimiter balance before a two-hour Firefox build; belongs in the emerge preflight |
| cp1252-stdout-fix | incompatible | port queued (idea I-19) | rewrites print/open calls; needs the same value-preserving proof as pyliterals. Until then triage `py-cp1252-charmap` |
| evidence-macho-extract | incompatible | port queued (idea I-19) | Mach-O minimum OS for the Mac 10.13 release check; no Fieldkit equivalent |
| keep-both | incompatible | port queued (idea I-19) | keeps both sides of append-only logs; `fieldkit merge-driver` covers tools.yaml/platform_index/groups.yaml only |
| missing-import-fix | incompatible | port queued (idea I-19) | AST detector is sound; the fixer edits imports in place - port with a parse-after check. Until then triage `py-missing-stdlib-import` |
| pid-guarded-stop | incompatible | port queued (idea I-19) | stop by PID only after name + start time match; `fieldkit liveness` tracks runs but has no such stop |
| plan-row-insert | incompatible | port queued (idea I-19) | Board rows; `fieldkit board` checks them but does not insert one |
| pwsh-parent-chains | incompatible | port queued (idea I-19) | who opened dozens of consoles; `fieldkit hog` reports the hoarding, not the parent chain |
| pwsh-syntax-check | incompatible | port queued (idea I-19) | bash heredoc/export in .ps1 files; triage `pwsh-bash-syntax` names the failure after the fact |
| tpfan-config-verify | incompatible | port queued (idea I-19) | belongs to the fan-control kit (skill fieldkit-fan-control-test); no equivalent |
| worktree-classify-recycle | incompatible | port queued (idea I-19) | worktrees vs PR merge state before recycling; done by hand in every DONE PROTOCOL step 4 |
| benchmark-html-report | incompatible | not ported | an HTML view of the same results; reports here are text and JSON |
| build-dispatch-table | incompatible | not ported | a second router; folded into triage signatures, the refusal table and workflow offer (see Router) |
| build-multiturn | incompatible | not ported | training-data export for a local model: W10, the owner's final goal, not now |
| claude-gap-importer | incompatible | not ported | recovers records into the capture store; capture is OFF and switched on per lesson |
| deid-build-fixture | incompatible | not ported | made the overhead test fixtures once; tests/data now holds them |
| filter-training | incompatible | not ported | training-data export: W10, not now |
| gemini-provider-check | incompatible | not ported | Gorilla OpenCode's own check belongs in that repository, not Fieldkit |
| generate-diagnoses | incompatible | not ported | calls a model per error fingerprint; triage signatures do it without a model |
| git-auth-check | incompatible | not ported | pushes go only through `fieldkit ship push`; triage `git-terminal-auth` says so |
| heredoc-eof-fix | incompatible | not ported | guesses where a heredoc body ends and cannot recover truncated input; triage agent-shell `shell-heredoc-eof` says write a file |
| missing-script-fix | incompatible | not ported | unbounded os.walk and suggests running a same-named file found elsewhere (unsafe); triage `py-script-not-found` |
| path-slash-fix | incompatible | not ported | duplicate of `fieldkit/method/mutate.py` msys_rewritten; triage `shell-msys-path-to-native` |
| pdf-coordinate-transform | incompatible | not ported | a one-off helper of the form check; form-bbox-check covers the failure it served |
| prices-from-md | incompatible | not ported | parses a vendor price page by hand; prices are data Fieldkit keeps in its own table |
| procedure-to-training | incompatible | not ported | training-data export: W10, not now |
| route | incompatible | not ported | a second router; folded in (see Router) |
| transcript-gap-audit | incompatible | not ported | audits capture coverage; capture is OFF and switched on per lesson |
| untriaged-source-review | incompatible | not ported | one-time review of Kiro's 104 source groups, done (out/RECONCILIATION.md) |
| workflow-readiness-review | incompatible | not ported | one-time screen; its verdicts are the `blocker` lines of the procedures below |

Totals: 10 native in Fieldkit now, 17 Fieldkit already has it, 11 port queued (idea I-19), 19 not ported.
