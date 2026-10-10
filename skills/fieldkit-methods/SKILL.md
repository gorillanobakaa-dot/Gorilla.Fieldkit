---
name: fieldkit-methods
description: How work gets done in Fieldkit, as tools - wait on a condition, prove a test can fail (mutate), compare test failures by name before and after a change (regress), check a generated file against its generator (drift), speak MCP to a server like a client (mcp-probe), survey a repository's facts, check translation tables (i18n), check tool cards against their commands, render Office files to look at them, ship a branch without force-pushing (ship), and hand fixes to someone else's tool (handover). Use before changing code, before claiming a test proves something, before pushing, when connecting an MCP server, or when reporting a bug in another tool.
---

# Methods as tools (docs/METHODS.md)

Look in docs/METHODS.md before inventing a procedure. When you invent one, add a row there, and turn it into a
tool with a test. Every command below takes `--json`; text output ends with `NEXT:` when there is a next step.

## Before a change: the baseline
```
fieldkit regress record before -- python -m pytest -q -rA      (or go test -v ./..., or their own suite)
... change ...
fieldkit regress record after  -- python -m pytest -q -rA
fieldkit regress compare before after                          exit 3 = something that passed now fails
```
Compare failures BY NAME. "Still failing" before and after is not yours; "new" is; "vanished" is not a pass.

## A test you wrote: prove it can fail
```
fieldkit mutate FILE --swap "the rule" "the rule, broken" -- TEST COMMAND
fieldkit mutate --plan mutations.yaml -- TEST COMMAND          [{file, swap: [OLD, NEW], why}]
```
CAUGHT = the test holds the rule. SURVIVED = it does not: write the test that fails. The file is always restored.

## Waiting
`fieldkit wait --until "CMD" --matches RE --timeout 600` - never a fixed sleep.

## Generated files
`fieldkit drift FILE --gen "GENERATOR"` - if it drifted, edit only your entry and say so; regenerate in a commit of
its own. Found: a generator that was random (Go map order).

## MCP servers
`fieldkit mcp-probe [--call NAME --args JSON] -- SERVER COMMAND` - handshake as mcp-go does (2024-11-05), the tool
list and what it costs per turn, one call with its _meta labels, stray stdout named.

## A repository you have not read
`fieldkit survey PATH ...` - languages by lines, build files, test counts, licence, CI, big files, privacy findings,
git. Check every claim (yours or a helper's) against these facts before repeating it.

## Data tables
`fieldkit i18n check FILE --table KEY --langs a,b --keys-from "CMD"` / `--disjoint FIELD`.

## Tool cards (what an agent can reach)
`fieldkit cards check` after changing a command or a card; `fieldkit cards draft FILE.py` to start a card (it stays
a draft until a person reviews it). Name exit codes that are answers (`exits: {2: question, 3: findings}`) and what
the answer carries (`output: own | third-party`).

## Looking at documents
`fieldkit office render FILE --sheet` - then look at contact-sheet.png. Checks pass on layouts that look wrong.

## Shipping
```
fieldkit ship status        where the branch stands
fieldkit ship push          sync a squash-merged branch (merge -s ours, tree unchanged) and push; never forces
```
Then the pull request with the host's tools: open it, wait for CI (`fieldkit wait` or the host's events), merge only
when green and only where the owner has said so. A new repository needs the owner's yes again.

## Someone else's tool
Reproduce the bug on THEIR untouched copy; fix a scratch copy; `fieldkit regress` their own suite before and after;
then `fieldkit handover FIXES.yaml --target THEIR_COPY --out handover.md`. Never change their copy yourself.
