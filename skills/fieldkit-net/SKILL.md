---
name: fieldkit-net
description: The agent net, the military way, with fieldkit zero (also fieldkit net) - is Zero established (who is net control, its epoch, its 2IC), establish Zero, roll call every session for a fixed sitrep, comms check / radio check (leases, SILENT and MISSING stations, a dead cable revived), compare the clocks of both computers, hand Zero over, send a message that wants an ack, read and verify the net log. Use when the owner says "zero", "zero established", "has zero been established", "establish zero", "comms check", "radio check", "roll call", "net", "who is out there", "is everyone alive", "2IC", "second in command", "who is 2IC", or when a session starts and must learn who Zero is, or the sessions went quiet; and for command - "give X its orders", "task X", "who is doing what", "orders", "X is done", a write refused with GROUND CLAIM, "why idle", or "--relieve" in a rotation brief.
license: AGPL-3.0-or-later
compatibility: "Needs the fieldkit command (Gorilla Fieldkit, github.com/gorillanobakaa-dot/Gorilla.Fieldkit; Python 3.11+ with PyYAML) on Windows, Linux, macOS. Works offline; the only network is the paired cable peer through fieldkit peers link."
---

# fieldkit zero - the agent net

The owner is a former soldier and speaks radio procedure. Read the words as below; never ask what they mean.

| The owner says | It means | Run |
|---|---|---|
| "has Zero been established?", "zero?", "net?" | is there a net control station, and who | `fieldkit zero` |
| "establish Zero", "you are Zero", "open the net" | this session becomes net control | `fieldkit zero open --name "command post" [--2ic STATION]` |
| "roll call", "who is out there" | every station reports | `fieldkit zero rollcall --wait 10` |
| "sitrep", "sit rep", "SITREP" (owner, 2026-10-31) | NOT the roll call: the owner wants four answers - where we started, what we have done, what is going on now, what is left. Write them with the fieldkit-sitrep skill (`fieldkit sitrep template`, `check`, `render --md`); read the running jobs first (`fieldkit task status`) | `fieldkit sitrep template` |
| "comms check", "radio check", "is everyone alive" | liveness of every station and the cable | `fieldkit zero check` |
| "time sync", "check the clocks" | clocks of this computer and the cable peer | `fieldkit zero time` |
| "hand over Zero", "X is Zero now" | change of Zero, epoch +1 | `fieldkit zero handover --to STATION` |
| "2IC", "second in command", "who is 2IC" | the standby station that assumes Zero when Zero is MISSING | `--2ic STATION` / `handover --assume` |

`fieldkit net VERB` is the same command (`fieldkit net route|compare|kind|...` stay the network tool).

## Military -> IT

Zero = net control station = coordinator / leader / primary. 2IC (second in command) = standby / deputy for
failover. Station / outstation = a worker session. Epoch = fencing token: +1 on each change of Zero; an older epoch is
refused, so two Zeros never act (split-brain refused). Comms check = heartbeat; each check-in renews a lease (TTL =
2 x interval). SILENT = lease expired once (PLAN.md Board), MISSING = twice (`fieldkit inbox`). Net log = append-only,
hash-chained audit log (`state/coordination/net-log.jsonl`), signed with the cable key when there is one. Ack =
delivery receipt; unacked messages are retried, then listed (store-and-forward). Net discipline = 6 messages a minute
a station + quiet hours; FLASH = sent at once.

## Every station

- A Claude Code session is checked in by the room hook at every prompt and tool call; it sees its traffic there.
- Gemini, Antigravity, Codex and any agent with no hook: run `fieldkit zero checkin --as gemini:NAME` at start and
  every 15 minutes (AGENTS.md / GEMINI.md rule).
- Answering a roll call (all six fields, one plain line each, `-` for none):

```
fieldkit zero checkin --ref R-xxxxxxxx --job "what you do" --state WORKING|BLOCKED|WAITING|DONE|IDLE \
  --branch "feat/x, PR #n" --waiting "who or what" --not-verified "what is not checked" --shared "shared ground touched"
```
- Leaving the net: `fieldkit zero checkin --off`.

## Zero

- `fieldkit zero rollcall --wait 10`: ANSWERED / PENDING / LATE / MISSING per station, plus UNREGISTERED sessions found
  from their transcripts and OpenCode register, and NO STATION for a cable peer nobody answered from. An idle Claude
  Desktop session runs no hook: nudge it with your own send_message tool (the line names its local_ id).
- Zero is told by the hook, once per change, which stations went SILENT or MISSING.
- Rotating Zero: `fieldkit rotate handover` writes the relief's first command into the brief
  (`fieldkit zero handover --take`).
- `fieldkit zero schedule --apply` once per computer: the comms check every 15 minutes with nobody awake (revives the
  cable through `fieldkit peers link heal --revive`, retries unacked messages, writes the Board row).

## Command: orders, ground, drift, idle

| The owner says | It means | Run |
|---|---|---|
| "give X its orders", "X works on Y", "task X" | Zero gives a station its order: objective, ground (folders), what done looks like | `fieldkit zero task STATION --objective ".." --area PATH[,PATH] --done ".."` |
| "who is doing what", "orders" | every order with its sitrep, quiet minutes, idle query | `fieldkit zero orders` |
| "X is done", "free X's ground" | Zero closes the order | `fieldkit zero release STATION [--finished]` |

- Ground is a folder (give each station its own worktree: a worktree is a branch). Two live orders never overlap.
- A write (Edit / Write / MultiEdit / NotebookEdit, or git commit / reset / checkout / stash / merge / rebase ...) on
  ground another station holds, or on shared ground outside your own order, is REFUSED by the PreToolUse hook. Never
  work around it: `fieldkit zero send --to ZERO "need X"` and carry on inside your own ground. PLAN.md, temp,
  scratchpads, memory and notes are always free.
- The scheduled comms check flags DRIFT (changes outside the ground; WORKING with nothing moving for 45 min), asks an
  IDLE station once "why idle, what is blocking you" (answer with `fieldkit zero checkin` and the six fields), lists
  DONE stations whose session ended with no open PR for archiving, and wakes Zero with ONE message per NEW finding.
  Zero silent: an inbox item names the 2IC (`handover --assume`). A quiet net sends nothing.
- Rotating with an order: `fieldkit rotate handover` writes the relief's first command into the brief:
  `fieldkit zero checkin --relieve OLD_STATION` (order and ground move to the relief).
- `fieldkit zero doctor` says whether the hook's matcher covers edits; the settings file is the owner's.

Exit codes: 0 OK, 1 WARN, 2 ALARM, 3 no net, 4 REFUSED. Never edit NET.yaml or the net log by hand: `fieldkit zero log
--verify` finds a changed line.
