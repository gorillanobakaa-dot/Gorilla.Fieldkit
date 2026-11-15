# v0.9.2, in plain language

## Zero stays in command

**Zero never rotates.** Before, only one fixed session was spared from rotation, so when Zero moved to a new session the new one was told to hand over. Now whichever session is Zero is spared, and the 90% budget line no longer writes a handover for it.

**No second Zero.** A scheduled task on the ThinkPad took over Zero while Zero was alive on the VAIO. Now a session on one computer cannot take over Zero from a Zero on the other computer that still answers over the cable.

## Zero commands, and every job gets finished (P24)

**Reports in four parts.** When Zero calls a roll call, each session answers: where it started, what it did, what it is doing now, what is left. An answer missing one of the four is marked incomplete.

**No rotation without a report.** A session that hands over must say what it did and what is left, and that report goes to Zero before the next session starts. An empty report is refused.

**"Done" must be proven.** Zero can give an order with a command that proves the work is finished. The order closes only when that command succeeds.

**Nobody goes missing quietly.** The roll call lists every session that did not answer, with the exact step Zero takes to reach it. A session that handed over and whose job nobody took over within 20 minutes is flagged to Zero with the command that starts its successor.

## One record of mistakes for both computers (P23)

Every refusal, failed CI run and blocked hook is kept in one record that both computers share over the cable. The same reason twice opens a defect by itself, never an item for you. A job that was refused a read-only command is reported as a defect and is not run again until the cause is known. A false alarm is closed only with a test that proves the read passes. `fieldkit overhead` shows the hours lost per cause.

## Tests stay in their own room

A test wrote two fake jobs into the real list of jobs, where they showed as started work. Tests now always use a temporary folder.
