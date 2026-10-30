# Fieldkit - instructions for coding agents

The rules for every project are in `rules/CORE.md` (the one source; your global CLAUDE.md, GEMINI.md or
~/.codex/AGENTS.md carries a copy). This file holds only what is Fieldkit's own. The previous long version, with
every command and its history, is `docs/AGENTS-REFERENCE.md`: read it only when you need a detail. An agent with no
global rules file (Gorilla OpenCode) reads `rules/CORE.md` first.
Where the harness is going: `docs/VISION.md`; the work that gets it there, item by item: `docs/HARNESS-PLAN.md`.
Where a plan, idea, hand-over or brief is kept, and where a new one goes: `docs/WHERE-THINGS-LIVE.md`.

Fieldkit is a set of deterministic tools. The model decides what to do; Fieldkit does it the same way every time and
reports the result. Prefer a Fieldkit command to a one-off script for the same job.

## Commands

Every command accepts `--json`. Exit codes: 0 fine, 1 error, 2 bad usage, 3 findings (problems, secrets or a failed
stage). Each output ends with `NEXT:`, the step to take. Find a tool: `fieldkit tools find WORDS`,
`fieldkit tools list`, `fieldkit cards show NAME`, or the skills (fieldkit-*). The ones every job uses:

```
fieldkit emerge list|plan|run|status RECIPE      every build, compile and package (rules/CORE.md BUILDS)
fieldkit testcost staged                          the tests covering what you changed (the pre-commit hook runs it)
fieldkit guard -- CMD ...                         every heavy command on the Windows laptop
fieldkit ship push [--tag vX] / ship gh-release   pushes and releases, through the owner-wide mandate
fieldkit inbox add TITLE WHY [COMMAND]            what only the owner may do, for the one morning review
fieldkit size find|recycle|hash ...               search, recycle (never a permanent delete), hashes
fieldkit privacy scan PATH --git                  before any commit, push or release
fieldkit task start BRIEF.md [--on peer]          a side job, headless, here or on the VAIO
fieldkit rotate check --source AGENT --cwd . --newest    session length (Claude Code: the hook does it)
fieldkit sync [--apply]                           both machines against GitHub, at session start and end
```

Machine-specific paths live in `fieldkit.local.json` (git-ignored, never published). The owner's own tools, sources
and tests live in `local/` (git-ignored). `state/` is git-ignored: plans and reports there are not shared until the
owner says "add it to git" (rules/CORE.md DONE PROTOCOL).

## Changing Fieldkit

- Run the tests covering your change (`fieldkit testcost staged`); the full suite runs in CI. A new check needs a test
  that proves it can fail (`fieldkit mutate`).
- Pipelines, signatures, recipes and the registry are data (YAML). Change the data before the code.
- A tool is written for Windows and Linux; CI runs both. A card naming one system needs `platforms_why`. After
  changing a card's `platforms` or entry, run `python -m fieldkit.core.platform_gate --write`. Never hand-resolve
  `tools.yaml`, `platform_index.json` or `docs/groups.yaml`: `fieldkit merge-driver` merges them.
- Inside a tool, a reader that exists on one system checks `sys.platform` and returns nothing elsewhere.
- How work gets done, as tools: `docs/METHODS.md`. A new method is added there and becomes a tool with a test.
- The core rules: edit `rules/CORE.md` only, then copy the block between its markers into the global files.

## Rules this code enforces

0. The explanation is the product: a release page carries the plain-language document in full, before the developer
   one (`fieldkit release-page compose`).
1. Verify the artefact, not the exit code.
2. Stop only what you started (by PID). Clean up what your job left with `fieldkit size recycle`. Exception: the
   scratch root (`fieldkit scratch new`, `scratch clean --mine` deletes directly); nothing is executed from it.
3. Do not probe a script that runs on load: `fieldkit tools check` first.
4. Nothing personal is published: `fieldkit privacy scan REPO --git` before any commit, push or release.
5. The same failure is never diagnosed twice: when `triage` says unrecognized, add the signature.
6. Report the platform honestly: a Debian-only stage on Windows reports `not-this-platform`.

## Decisions

A model never asks a bare question: `fieldkit build-harness briefs TASK` shows each open decision as a full brief.
Show the brief as it is. Only the owner records an answer (`decide`, at a real terminal). An unproven privacy or
security claim is made true and proven, never deleted.

## Two machines and the cable

- `fieldkit sync` at session start and end; `--apply` only fast-forwards a clean tree on green CI.
- Notes between the computers: `fieldkit peers link send`. The tool numbers and signs them; `--needs-you` and
  `--done --evidence ...` set the tags. A `!! LINK CHECK` line is a fact to resolve, never noise.
- The agent net: `fieldkit zero` says who Zero (the coordinating session) is. Claude Code sessions are checked in by
  the hook; other agents run `fieldkit zero checkin --as AGENT:NAME` at start. Owner words "zero", "sitrep",
  "comms check", "roll call" mean `fieldkit zero` (skill fieldkit-net).

<!-- fieldkit-lifecycle-rule v1 -->
## Zero friction: headless side jobs, archive when done (fieldkit lifecycle)

SIDE JOBS: start a relief or side job with `fieldkit task start BRIEF.md [--on DEVICE]` (a headless autopilot run here, or the other computer's link listener over the cable). Never propose a desktop session card or ask a person to click: a card waits for the owner, who is asleep.

Nothing may wait on the owner (no cards, prompts or [NEEDS YOU] for what the mandate covers): `fieldkit friction` lists what could, exit 0 = none.

Idle sessions eat RAM and CPU. The command post runs `fieldkit sessions sweep` on every watchdog wake and archives what it lists.

DONE PROTOCOL (fieldkit lifecycle; do every step, in this order, without asking anyone):
1. the tests covering your changes pass (the full suite runs once, when the project is finished); commit on your own branch (never main).
2. push with `fieldkit ship push` (the owner-wide mandate covers it; never a GORILLA_OWNER_APPROVED line).
3. open the pull request where your agent can (a headless run reports the branch; the command post opens it).
4. report: a Board row in state/coordination/PLAN.md (what was done, what was NOT verified).
5. recycle what YOUR job left (test copies, scratch, your own worktrees) with `fieldkit size recycle`; unload any local model you loaded.
6. stop your own background jobs and listeners (by PID, never by name).
7. archive yourself: Claude desktop `archive_session "self"` (turn Remote Control off first with `set_remote_control` if it is on: it blocks the archive); any other agent ends its session. Zero archives only into a new Zero: hand the seat over (`fieldkit zero handover --to ST`, or `fieldkit rotate handover` and the relief's `--take`), wait until the new Zero is UP, write state/coordination/HANDOFF.md, and `fieldkit zero retire` must exit 0 (the tool router refuses the archive until it does).
<!-- /fieldkit-lifecycle-rule -->
