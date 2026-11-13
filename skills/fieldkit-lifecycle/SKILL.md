---
name: fieldkit-lifecycle
description: Zero friction for unattended work with fieldkit friction, fieldkit task, fieldkit sessions and fieldkit machine - find everything that can wait on the owner's click or approval on every machine (desktop session cards, permission prompts, sessions not in bypass mode, Remote Control, UAC needs outside the mandate, [NEEDS YOU] notes, inbox items, sessions whose title lacks the machine tag such as [VAIO-LNX]), start relief and side jobs as visible desktop sessions (headless from a brief for jobs no human talks to, or when the app refuses) instead of a session card (here or on the other computer over the cable), and list finished or idle sessions with the RAM and CPU they hold so they get archived. Use when the owner says friction, approval, "it asked for my approval", archive sessions, idle sessions, machine tag, which computer, session title, too many claude.exe, the laptop is slow from sessions, or when a job must start another job.
license: AGPL-3.0-or-later
compatibility: "Needs the fieldkit command (Gorilla Fieldkit, github.com/gorillanobakaa-dot/Gorilla.Fieldkit; Python 3.11+ with PyYAML and psutil) on Windows, Linux, macOS. Works offline; the only network is the household link (cable) to paired peers."
---

# fieldkit friction / task / lifecycle - nothing waits on the owner

Owner, 2026-10-26: "Long horizon tasks run unattended and will typically not have anyone waiting 5 days in front
of the computer for those moments when an approval might be needed."

## Starting another job: a visible session, never a card

```
fieldkit task start BRIEF.md                 headless here: a one-step autopilot runbook in its own window
fieldkit task start BRIEF.md --on peer       on the other computer: its link listener starts `fieldkit autopilot claude`
fieldkit task status                         every task + the reports that came back (over the cable too)
fieldkit task report ID --status done --branch B [--pr URL] --note "..."   the job's own last step
fieldkit autoaccept status|list              I-04: work the HARNESS accepted by itself (every check passed AND an independent higher-rung reviewer said yes), the policy switch, the undo lines; the owner reviews after the fact in `fieldkit review` (gates: docs/AUTONOMY-GATES.md)
fieldkit refusals list                        refusals repeated by train/ship/guard/goal/task: each repeated reason is ONE open defect row (never an inbox item)
```

A side job, relief or successor starts as a VISIBLE desktop session (Claude desktop `start_session`, initiation
`user_asked`, context `fresh`, the brief as its prompt): it shows in the sidebar and the owner can talk to it, with no
click to start. Solo = one session or job at a time, multi = up to the cap in `fieldkit/lifecycle/routing.yaml`.
The commands below are headless: for jobs no human talks to (build and test runs, `--run`, goal workers) and the
fallback when the app refuses the session start (say so plainly). A card (start_session "own_initiative") waits for
the owner's click: never propose one. A relayed chat message to another session is not an order either (that session may refuse it or ask
its owner): `task start --on` goes to the peer's link listener, signed, and needs no session's consent. The peer's
approved link.yaml must allow `claude` (`fieldkit friction` says when it does not).

## Solo or Multiplayer, and the model router (owner, 2026-11-02)

One hour of parallel jobs burned the 5-hour quota. `fieldkit route status` shows the mode; the owner accepts it once
a day with `fieldkit route choose --mode solo|multi --model haiku|sonnet|opus|fable`. Without a fresh accept the
default is solo + sonnet. **Solo**: one job at a time, nothing `--on` another device. **Multi**: up to 3 jobs.
`fieldkit task start` then scores the brief's risk and runs it on the cheapest model that needs, never above the
accepted one; a failed round climbs one rung (never above it); a job that needs Fable is parked in the owner inbox.
`--model M --pin` keeps M. `fieldkit route assess BRIEF.md` shows what a brief would get. Never start jobs around it.

## What can wait on a person

```
fieldkit friction            here + every peer; one line each with its fix; exit 0 = zero friction
fieldkit friction --here     this computer only
fieldkit friction ack KEY    a line that is handled
```

Fix each line, or put what truly needs the owner in `fieldkit inbox` (the ONE morning list), never a dialog.
Kind `untagged` is a live session whose title does not start with its machine's tag (below).

## Machine tags: every session title says which computer it is on

```
fieldkit machine name                 this machine's tag, e.g. [VAIO-LNX], [L15-WIN], [AIR-MAC]
fieldkit machine list [--offline]     every device: tag, OS, link address, last seen (--json)
fieldkit sessions tag --check         every live session, here and on each peer, whose title lacks its tag; exit 1
```

Title yourself `[TAG] <job>` at the start of every session (the hook says the tag at SessionStart and at the
first prompt). The tag is made ONCE from the hardware model and the OS by fixed rules
(`fieldkit/peers/machine_tags.yaml`) and stored in the household registry (`~/.fieldkit/link/household.json`); never
type it yourself. `task start` titles, rotate successors' briefs (SESSION TITLE line), roll call, sitreps, the
net's Board row and the ROOM lines tasks write carry it by themselves. A peer that cannot be asked is reported NOT
CHECKED and the check exits 1: silence is not a pass. `fieldkit doctor` carries the same check for this computer.

## Idle sessions: archive them

```
fieldkit sessions sweep [--idle-minutes 30] [--keep ID]   finished / idle sessions, RAM + CPU measured
```

FINISHED (no open PR, idle) -> `archive_session ID` (Remote Control on: `set_remote_control` off first).
IDLE (open PR) -> kept until the PR closes. The command post (Zero in NET.yaml) is never listed.
`fieldkit hog check` shows one row: "idle agent sessions holding N GB".

## When your own job is done

`fieldkit sessions protocol` prints the done protocol every brief ends with: tests + mutate + telemetry scan,
`fieldkit ship push`, PR, report (Board row / `task report`), recycle your leftovers, stop your background jobs,
then archive yourself (`archive_session "self"`) or exit (headless). Zero archives only into a new Zero:
hand the seat over (`fieldkit zero handover --to ST`, or `fieldkit rotate handover` + the relief's `--take`), the new
Zero UP, HANDOFF.md written, then `fieldkit zero retire` exits 0. The tool router refuses `archive_session` on Zero
until it does.

The standing rule block: `fieldkit sessions rule --all` (CLAUDE.md, GEMINI.md, Codex AGENTS.md); check with
`fieldkit sessions doctor`.

`fieldkit keeper tick` (timer-run every 5 min while work is open; installs itself on `task start`) keeps work moving when no model turn runs: BEHIND PR update, one fix job, one restart, queued briefs, wake Zero. `fieldkit idle report --since 24h` lists the gaps where work was open and nothing acted.
