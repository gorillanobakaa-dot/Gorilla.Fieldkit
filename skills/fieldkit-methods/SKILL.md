---
name: fieldkit-methods
description: How work gets done in Fieldkit, as tools - the cheapest test set that is enough and what the checks cost (testcost: a passed tree is never re-tested), wait on a condition, prove a test can fail (mutate), compare test failures by name before and after a change (regress), check a generated file against its generator (drift), speak MCP to a server like a client (mcp-probe), survey a repository's facts, check translation tables (i18n), check tool cards against their commands, render Office files to look at them, ship a branch without force-pushing (ship), and hand fixes to someone else's tool (handover). Use before changing code, before claiming a test proves something, before pushing, when connecting an MCP server, or when reporting a bug in another tool.
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
fieldkit testcost plan                                         changed files -> only the tests that cover them
fieldkit testcost run --fast                                   run that set while iterating; the FULL suite once, before push
fieldkit testcost report                                       what the suite cost, re-runs avoided, what refusals caught
fieldkit testcost flags                                        gate tiers the owner decides (all off until his yes)
fieldkit mutate FILE --swap "the rule" "the rule, broken" -- TEST COMMAND
fieldkit mutate --plan mutations.yaml -- TEST COMMAND          [{file, swap: [OLD, NEW], why}]
```
In Git Bash, a swap argument that starts with `/` reaches Python already rewritten: `/usr/share/x` arrives as
`C:/Program Files/Git/usr/share/x`. mutate refuses such a swap and runs nothing. Put `MSYS_NO_PATHCONV=1` in
front of the command, or use a `--plan` file (2026-10-25).
CAUGHT = the test holds the rule. SURVIVED = it does not: write the test that fails. The file is always restored.

## An xz archive you made or ship (.xz, .tar.xz, a .deb)
```
fieldkit xzcheck FILE... [--cap-mib 128]     dictionary = what the content needs (not more, not less); unpacks within the cap
```
Never "improve" compression with a bigger `dict=`: past the content's size it adds no bytes saved, only memory every
installing machine must have. `xz -9e` (64 MiB) is the most for anything up to 64 MiB of content.

## A check (a scanner, a gate row, an audit): prove it can fail, and get a second witness
```
fieldkit canary run [ID ...]        each check on a planted input it must flag and a clean control it must pass
fieldkit canary witness [ID ...]    the source reading against an independent reading of the shipped artifact
fieldkit canary list
```
OK = it flags the plant and passes the control. BLIND = it passed the plant: it cannot see what it exists to see.
FALSE-ALARM = it flagged the clean control. UNAVAILABLE = it did not run (never counted as OK). DISAGREE (witness) =
source and artifact tell different stories: believe neither until you know why. A new check gets a canary in
fieldkit/method/canaries.yaml (contents as a list of parts, so the registry does not trip the scanners it tests;
machine paths in local/canaries.yaml). Release checks call `canary.gate(ids)`: a blind check cannot sign a release.
Learned 2026-10-18/19: a telemetry scan that read only the source passed a program carrying a tracing library 1,036
times; the static theme check cannot see a white popup background (white is a theme colour; only the rendered
contrast check sees that failure).

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
