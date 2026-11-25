---
name: fieldkit-methods
description: >-
  How work gets done in Fieldkit, as tools - the cheapest test set that is enough and what the checks cost (testcost: a passed tree is never re-tested), the same pinned versions and capabilities on every machine before a test (parity), wait on a condition, prove a test can fail (mutate), compare test failures by name before and after a change (regress), check a generated file against its generator (drift), speak MCP to a server like a client (mcp-probe), survey a repository's facts, check translation tables (i18n), check tool cards against their commands, render Office files to look at them, check that a new function or feature is built in - carried, known to the AI, visible, on the release page, on every system, tested (integration), and hand fixes to someone else's tool (handover). Use before changing code, before claiming a test proves something, when connecting an MCP server, or when reporting a bug in another tool.
license: AGPL-3.0-or-later
compatibility: "Needs the fieldkit command (Gorilla Fieldkit, github.com/gorillanobakaa-dot/Gorilla.Fieldkit; Python 3.11+ with PyYAML) on Windows, Linux. Works offline; no network access unless a step says so."
---

# Methods as tools (docs/METHODS.md)

Look in docs/METHODS.md before inventing a procedure. When you invent one, add a row there, and turn it into a
tool with a test. Every command below takes `--json`; text output ends with `NEXT:` when there is a next step.

## A clean capture lesson

`fieldkit capture clean --keep-last 2w` reports per-user and system temp, native session-store file ages,
and scratch eligible for Trash. Add `--apply` for scratch and `--retire-sessions` for native transcripts.
Use `fieldkit capture start --project kernel --lesson "Debian kernel build" --workspace PATH --agents claude --keep-last 3d`
to run the same eligibility checks before starting a lesson. With `--dry-run`, only metadata is inspected.
A month is 30 elapsed days and `--all` admits every age to the same closure checks.
Trash requires a completed native session, an inactive engine, an event window wholly before the cutoff,
and a local read-back of the original bytes and identity manifest. Scratch still needs its own disposition.
Standalone Claude Code, Codex rollouts, Gemini CLI JSON and Kiro V3 messages have adapters. Protected and
unsupported stores, including shared databases, Kiro Crew transcripts and Antigravity, stay in place.
Use `--lookback 30m --session claude=ID` with `--agents claude` to include recent completed work from one
existing Claude Code session. For Codex, use `--agents codex --session codex=ID`. Fieldkit reads only the named
rollout under `CODEX_HOME/sessions` (default `~/.codex/sessions`). Use `--session` once per selected session.
Fresh Claude lessons use live hooks. Fresh Codex lessons read only the bytes appended to their selected rollout.
`fieldkit capture scan` collects from selected lesson sources and `fieldkit capture off` closes that window.
`fieldkit capture scan --legacy` is a separate, explicit Claude history backfill while capture is off.
Use `--agents agy` before starting an Antigravity CLI session. It records new tool events from the CLI's
[PostToolUse hook](https://antigravity.google/docs/hooks?tab=cli) in the selected workspace. `capture off`
removes Fieldkit's named hook from the shared Antigravity hook settings.
Kiro Crew coverage is limited to default `~/.kiro/crew/scratch` allocations with verified, inactive owners.
Gorilla OpenCode coverage includes `opencode-tools-test<digits>` directories under default OS temp.
Both require P40 disposition evidence. Kiro lesson capture still requires a verified adapter.
`fieldkit capture off --distill` closes the window and distils that lesson.

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
fieldkit testcost staged                                       names the covering tests; GitHub CI (Linux and Windows) runs them, never this computer (--local only when the owner asks)
fieldkit testcost unmapped --all                               tracked files no test covers (must be 0: each one runs the whole suite in CI)
fieldkit ciaudit --since DATE                                  what CI really ran: whole suite vs targeted per PR run, minutes, trigger files
fieldkit ciaudit --run RUN                                    queue, active time and gaps for all attempts of one Actions run
fieldkit ci plan --selection F [--check|--explain] | ci durations merge|fetch | ci quarantine --check | ci covmap build|merge|show|key | ci runner | agentperms audit|fix   CI runners N, durations, hung tests, covmap, runner CPU/RAM (xdist workers), allow-everything agent settings
fieldkit testcost report                                       what the suite cost, re-runs avoided, what refusals caught
fieldkit testcost flags                                        gate tiers the owner decides (all off until his yes)
fieldkit mutate FILE --swap "the rule" "the rule, broken" -- TEST COMMAND
fieldkit mutate --plan mutations.yaml -- TEST COMMAND          [{file, swap: [OLD, NEW], why}]
```
In Git Bash, a swap argument that starts with `/` reaches Python already rewritten: `/usr/share/x` arrives as
`C:/Program Files/Git/usr/share/x`. mutate refuses such a swap and runs nothing. Put `MSYS_NO_PATHCONV=1` in
front of the command, or use a `--plan` file (2026-10-25).
CAUGHT = the test holds the rule. SURVIVED = it does not: write the test that fails. The file is always restored.

## An xz archive or program you made or ship (.xz, .tar.xz, a .deb, an unpacked app)
```
fieldkit once run|check [--key K] [--state PATH] -- CMD   skip a command the journal records as a success on the same inputs; watch --key K < OUTPUT says done:true (stop) when nothing changed; never re-run or re-ask the owner what already succeeded
fieldkit capture audit --json   read-only clean-slate audit of configured Windows or Linux scratch roots
fieldkit capture audit --device PEER --via cable --json   audit a peer through the selected transport
fieldkit ownership take PATH   Windows UAC action on one file or directory, with owner and DACL verification
fieldkit ownership take-drive DRIVE   take ownership recursively on a selected local drive
fieldkit ownership install-explorer   register the current user's file, folder and drive actions
fieldkit compileonce build [--force] | check   pre-compile all Python modules in fieldkit/ and kits/ to .pyc ahead of time; check verifies 0 stale/missing
fieldkit install app SRC --name APP [--replace] [--apply] [--peer]   install a program on Linux or Windows (or on the peer over the cable) with icons, .desktop / .lnk, caches, and verification
fieldkit install purge APP [--dir PATH] [--apply] [--peer]           completely remove/quarantine a program on Linux or Windows (processes, folders, shortcuts, registry keys, icon caches)
fieldkit install verify PRODUCT [--peer|--host macair] | self-test [--os all]   installed as its release says on Linux (GNOME), Windows (shortcuts, .ico, exe icon) and macOS (bundle, .icns): binary, every icon size, caches, nothing older shadowing; parity align and the release cycle call it
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

## Before you call a feature done: is it built in? (docs/INTEGRATION.md)
```
fieldkit integration check --repo PATH [--changed BASE]   one line per missing surface, with the file to fix; exit 3 = gaps
fieldkit integration self-test --kind opencode|fieldkit   seeds one omission per surface; exit 0 = every one caught
fieldkit pins check --repo OPENCODE   refuses rolling (latest, nightly, master) or sha256-unpinned helper URLs; exit 3 = findings
fieldkit pins self-test               seeds each break kind in a fixture; exit 0 = every one refused
```
Rules per surface are data: `fieldkit/integration/surfaces.yaml`. Run `--changed origin/main` before committing a new
helper, tool or command.

## Before a test, a measurement or a benchmark: the same ground on every machine
```
fieldkit parity manifest [--hash]                  this machine: versions + sha256, capabilities, OS (stable JSON)
fieldkit parity compare                            against every household device over the link: SAME / PARTIAL / DRIFT
fieldkit parity os [--write|--briefs DIR|--since REF|--live]  Windows-Linux gaps: one record each (action, target, mechanism, effort, proof), exit 3 while gaps exist
fieldkit parity os --check [--json]  read-only PR and release gate: valid parity records and current docs/PLATFORMS.md
fieldkit ship release TAG --hotfix WHY  logged security or broken-release fix, up to 10 per repository and operating system in 24 hours
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
Commits, pushes, pull requests, merges, tags and releases: the `fieldkit-ship` skill, the one GitHub cookbook.

## Someone else's tool
Reproduce the bug on THEIR untouched copy; fix a scratch copy; `fieldkit regress` their own suite before and after;
then `fieldkit handover FIXES.yaml --target THEIR_COPY --out handover.md`. Never change their copy yourself.

## The project brief (P41a)

`fieldkit cookbook load ID|--cwd PATH` prints a bounded brief of a registered project (Firefox, Gorilla OpenCode, the
kernel, Fieldkit): purpose, where its cookbook and recipes are, current state, the next command. The SessionStart hook
runs it by cwd, so do not search the disk to learn where things are: read the brief. `cookbook list|show ID|check`;
the registry is `projects/<id>/project.yaml` (projects/README.md).

## Installer preflight (P47)

`fieldkit install preflight [--lean|--full] [--allow-missing-terminal] [--json]` (`fieldkit.desk.install_preflight`) runs deterministic, read-only installer preflight before install or update changes on Windows and Linux. It reuses the existing installer checks, resolves `gorilla-size --version` and `gorilla-trash --help` from a fresh user terminal outside the install folder first, and then checks consuming background services (starting with Gorilla Size in the VAIO cable listener). When the terminal works and the service cannot resolve the tool, it stops before install or replacement and reports service discovery as the failure; when a terminal tool is missing, it routes through `fieldkit size integrate`.
