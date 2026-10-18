---
name: fieldkit-autopilot
description: Run long work unattended (builds, leak gates, release runbooks) with Claude Code or Antigravity without the owner pressing Yes, using fieldkit autopilot - no approval prompts, only hard lines (no push or publish, no deletes outside scratch, no changes to guards, rules, the build harness, leak-gate allowlists or the decision register, no owner approvals, no credential files), no GitHub credential in the run, an owner inbox for everything that needs the owner, and fieldkit publish afterwards. Use when the owner says unattended, overnight, autopilot, YOLO, "I want to sleep", "stop asking me", "run it without me", or when a run keeps stopping for approvals; also to stop such a run (fieldkit autopilot stop) or to quiet the machine before a measuring run.
---

# Unattended work (fieldkit autopilot)

Owner, 2026-10-18, after 40 hours of pressing Yes through a night: **safety comes from the harness, not from
approval prompts.** An AI is for delegating; a run that needs a person every 25 commands is not a harness.

## Start
- Claude Code, headless: `fieldkit autopilot claude "PROMPT" --cwd DIR` (bypass mode + the hard-line hook).
  The CLI needs one `/login` by the owner first: `fieldkit autopilot login check` says whether it has one;
  `fieldkit autopilot login` opens Claude Code in its own window from its REAL path (the desktop app is MSIX:
  its files live under $env:LOCALAPPDATA\Packages\Claude_*\LocalCache, not where the app itself sees them)
  and the owner types /login inside that window. Never hand the owner a path you only saw from inside the app.
- Claude Code desktop: open the session in `Documents\Autopilot` (`fieldkit autopilot workspace` creates it).
- Antigravity: close it, then `fieldkit autopilot antigravity` (its guard switches to autopilot mode).
- `fieldkit autopilot status` says what is on.

## Long jobs: let the harness run them
- Put the steps in a runbook (a ```runbook block: id, run, cwd, retries, needs, done_if, timeout, owner,
  fix) and start it with `fieldkit runbook start FILE` (its own window, survives the session). The harness runs
  the steps, waits, retries, prints the ETA and writes the report from exit codes; it calls a model only to fix a
  failed step marked `fix: true`. Do not poll a running job yourself: `fieldkit runbook status FILE`.

## While running
- Never ask for approval. What only the owner may do goes to `fieldkit inbox add TITLE WHY [COMMAND]`; carry on
  with whatever does not depend on it.
- A hard-line DENY is final: report it in the inbox; never look for another route (2026-10-18: a blocked
  `git push` was followed by `gh release create` - that is exactly what the hard lines and the missing credential
  now stop).
- Measuring runs: the leak gate quiets the machine by itself for its whole run (quiet.log in its run folder).
  By hand: `fieldkit autopilot quiet` lists, `--apply --hold PID` or `--apply --minutes N` keeps it quiet.
  Every app outside Windows that holds an internet socket is stopped by PID (the netstat -ano + kill the owner
  asked for), plus the quiet list (fieldkit.local.json autopilot.quiet); NEW processes are judged every 2 s, so a
  respawned app dies within about 2 s, and all of them every 30 s. WebView2 is judged by its HOST only (WhatsApp's
  dies, Windows Search's and the agent's are spared; an unreadable host is spared). Windows, the gate, the browser
  under test and this Claude session are never stopped. The STOP file ends it.
- Time estimates come from `fieldkit eta`, never from a guess.
- The verdict is the line the tool printed; a PASS you did not see printed is not a PASS.

## The owner
- `fieldkit autopilot stop [why]` - only reads from then on; `resume` lifts it. Never argue with a stop.
- In the morning: `fieldkit inbox`, then `fieldkit publish --repo DIR [--release TAG --notes FILE --asset FILE]`.
