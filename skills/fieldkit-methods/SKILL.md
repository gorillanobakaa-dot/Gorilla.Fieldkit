---
name: fieldkit-methods
description: >-
  How work gets done in Fieldkit, as tools - the cheapest test set that is enough and what the checks cost (testcost: a passed tree is never re-tested), the same pinned versions and capabilities on every machine before a test (parity), wait on a condition, prove a test can fail (mutate), compare test failures by name before and after a change (regress), check a generated file against its generator (drift), speak MCP to a server like a client (mcp-probe), survey a repository's facts, check translation tables (i18n), check tool cards against their commands, render Office files to look at them, ship a branch without force-pushing (ship), and hand fixes to someone else's tool (handover). Use before changing code, before claiming a test proves something, before pushing, when connecting an MCP server, or when reporting a bug in another tool.
license: AGPL-3.0-or-later
compatibility: "Needs the fieldkit command (Gorilla Fieldkit, github.com/gorillanobakaa-dot/Gorilla.Fieldkit; Python 3.11+ with PyYAML) on Windows, Linux. Works offline; no network access unless a step says so."
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

## Before a test, a measurement or a benchmark: the same ground on every machine
```
fieldkit parity manifest [--hash]                  this machine: versions + sha256, capabilities, OS (stable JSON)
fieldkit parity compare                            against every household device over the link: SAME / PARTIAL / DRIFT
fieldkit parity align [--apply]                    to releases/parity-pin.yaml FROM THE RELEASE (sha256 + SHA256SUMS);
                                                   Windows installs (old exe kept); Linux/macOS get the root line to run
fieldkit parity preflight --kind bench             the run guard's check alone: pin, other sessions' processes in this
                                                   checkout or on the binary, a core pinned, a model this run did not load
```
Every guarded run already runs the check (WARN for tests, REFUSED for kinds bench and measure) and stamps the
manifest hash into testcost's result rows: quote that hash with a measurement. The Mac is usually off: it shows as
ABSENT and never fails a compare. Change the pin only by a commit; nothing updates in the background.

## Waiting
`fieldkit wait --until "CMD" --matches RE --timeout 600` - never a fixed sleep.

## Generated files
`fieldkit drift FILE --gen "GENERATOR"` - if it drifted, edit only your entry and say so; regenerate in a commit of
its own. Found: a generator that was random (Go map order).
`fieldkit merge-driver check` - two branches adding cards merge with no conflict in tools.yaml, platform_index.json
and docs/groups.yaml (entry by entry; `install` if it says NEEDS). A conflict left in one of them is two edits of
the same entry: resolve that entry only, then `python -m fieldkit.core.platform_gate --write`.

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
fieldkit ship push --tag vX a NEW tag on a commit GitHub has (or that this push carries); never moves a tag
fieldkit ship gh-release vX -- ARGS   gh release create --verify-tag, the repository from git, CI green
fieldkit mandate doctor     the owner-wide mandate: in force, days left, keys, every covered hook honoring the gate
```
Under the owner-wide mandate (owner, 2026-10-26) these ARE the owner's yes: every covered repository's pre-push hook
asks `fieldkit mandate gate`, which lets a push go without the owner flag only when it came through `fieldkit ship
push`, the mandate is in force and names the repository, every branch matches feat/ fix/ release- night/ and
fast-forwards under its own name, and every tag is new. A hand-typed `git push` in a covered clone is refused by the
tool router. Never hand the owner a GORILLA_OWNER_APPROVED line per push: that flag stays the owner's own manual path.
Then the pull request with the host's tools: open it, wait for CI (`fieldkit wait` or the host's events), merge only
when green (the mandate's merge_to_main: ci_green, by pull request). A repository the mandate does not name needs the
owner's yes again. A clone whose hook does not ask the gate: `fieldkit mandate install-hooks` (preview), `--yes`.
Expiry: 3 days before, one line in `mandate status` / `mandate start` / the prompt hook; the owner runs `fieldkit
mandate renew FILE.yaml` (the approve screen, typed yes + passphrase).
The renewed pair reaches the other computer by itself: the renewal runs `fieldkit mandate share`, whose files land
there in the scratch root (`link-*/NAME-N.yaml`, `NAME-M.seal`; link.log says where) and whose link verb
mandate-receive (cable only) runs `fieldkit mandate receive FILE.yaml FILE.seal` there. It installs ONLY a pair the
owner key already pinned on that computer verifies (any changed byte, other key, stop or expiry is refused), never an
older approval over a newer one; the old pair goes to ~/Agents.Work.Trash. A far listener without that verb gets a
note with the command.
Windows, Linux and macOS alike (2026-10-26). The protected trust store: %ProgramData%\Fieldkit\mandate-trust (UAC),
/etc/fieldkit/mandate-trust (pkexec), /Library/Application Support/Fieldkit/mandate-trust (sudo). On a Mac without
Apple's developer tools /usr/bin/git is a stub and is never run (the doctor says "no clone here"); the lean set has no
`cryptography`, so a mandate there is NOT in force until the owner installs it.

### The merge train (parallel PRs, 2026-10-26)
```
fieldkit ship overlap             before work starts and at PR open: open PRs / live stations on the same files
fieldkit ship update              main moved: merge it the train's way (generated files regenerated, cards entry by
                                  entry, proven only a merge); the hook then runs only the affected tests
fieldkit ship ready PR            the SESSION says the PR is final: refused while anything is unpushed, uncommitted
                                  or still in the pre-commit hook. One more commit coming: `ready PR --pending TEXT`
fieldkit ship train --dry-run     Zero: the order it would merge in, what each PR needs, stale / orphan flags
fieldkit ship train               Zero: merge the declared PRs one at a time (gh pr merge --merge
                                  --match-head-commit), never red, never a head nobody declared; resumable
fieldkit ship freeze --reason T   a checkout that must not move; `unfreeze` = ff + `fieldkit parity baseline --set`
fieldkit ship train --modes       every failure mode this closes, with its check
fieldkit ship ci-wait PR [--merge]   one PR: run it in the BACKGROUND, one wake when CI ends (green, or red with
                                  the failed log tail); --merge merges on green and checks the PR reads MERGED
```
Never `gh pr checks --watch && gh pr merge` or an `until gh pr checks ...; sleep` loop by hand (the tool router
refuses both): the exit code of a watch is not the CI of the head (a re-run still running read as green).
Never `git merge origin/main` by hand on a PR branch when `ship update` can do it.

## Someone else's tool
Reproduce the bug on THEIR untouched copy; fix a scratch copy; `fieldkit regress` their own suite before and after;
then `fieldkit handover FIXES.yaml --target THEIR_COPY --out handover.md`. Never change their copy yourself.
