---
name: fieldkit-autopilot
description: Run long work unattended (builds, leak gates, release runbooks, multi-day goals) with Claude Code or Antigravity without the owner pressing Yes, using fieldkit autopilot and fieldkit goal - a goal with checkable acceptance criteria worked toward for days by a planner, fresh workers with budgets and checkpoints, and a separate verifier; owner decisions pre-approved once; a supervisor that resumes after reboots, sleep and usage limits; no approval prompts, only hard lines (no push or publish, no deletes outside scratch, no changes to guards, rules, the build harness, leak-gate allowlists or the decision register, no owner approvals, no credential files), no GitHub credential in the run, an owner inbox for everything that needs the owner, and fieldkit publish afterwards. Use when the owner says unattended, overnight, autopilot, YOLO, "I want to sleep", "stop asking me", "run it without me", or when a run keeps stopping for approvals; also to stop such a run or to quiet the machine before a measuring run.
license: AGPL-3.0-or-later
compatibility: "Needs the fieldkit command (Gorilla Fieldkit, github.com/gorillanobakaa-dot/Gorilla.Fieldkit; Python 3.11+ with PyYAML). Works offline; no network access unless a step says so."
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

## Long horizon: hand over a GOAL, not a queue (fieldkit goal)
Owner, 2026-10-22: "eliminate THE MAIN BOTTLENECK, THE HUMAN IN THE LOOP ... my 15 years old scripts for gentoo and
debian could run for 24 to 48 hours ... without me ever having to be present". Work longer than one session is a goal:
1. Write the spec (goal.json shape): `objective`, `criteria` (commands whose exit code or printed line decides, e.g.
   `{"id": "tests", "run": "go test ./...", "cwd": "...", "expect": {"exit": 0}}`; `{"judge": "agent", "question": ...}`
   only for what no command can check), `milestones`, `constraints` (the owner's rules), `budget`
   (`days`, `cost_usd`, `window_usd` per `window_hours`), `owner_decisions`, `cwd`, optional `seed_jobs`, `on_done`
   (`[{kind: push_branch|open_pr, repo, branch}]`). `fieldkit goal new ID --from SPEC.json`.
2. Ask the owner ALL decisions up front, once: fill `state/goals/<id>/PREAPPROVALS.yaml` (answers by category,
   take_default or a fixed answer; actions push_branch/open_pr for named repos and branch globs, never main), show
   the whole file, and have the owner run `fieldkit goal approve ID` at a real terminal (typed yes; sha256-pinned;
   any later change voids it). Never approve it yourself; never edit it after approval.
3. Start: `fieldkit goal run` (the supervisor). The owner registers the logon/resume task ONCE:
   `fieldkit goal supervisor-script` writes `state/goals/register-goal-supervisor.ps1` for the owner to run.
4. Watch, never poll in a loop: `fieldkit goal status [ID]`, `state/goals/<id>/events.log`, `REPORT.md`.
   Stop: `fieldkit goal stop ID|--all`.
How it decides: the planner (a headless run with its own small budget) writes bounded jobs with `verify` commands;
a fresh worker does one (job.py); the verifier runs the job's checks and every criterion; only a PASS it printed
moves a criterion. The planner saying "done" is not done: the verifier decides (logged "verifier wins"). A question
no pre-approval covers goes to the inbox WITH a default, and every job that does not wait on it goes on.
Parallel workers (2026-10-26: one job ran alone for two hours while a disjoint one sat queued): up to `max_workers`
jobs of a goal run at once (goal.json, default 2). A job starts only when every job in its `needs` is verified and
its `touches` are disjoint from every running job's (paths resolved against the job's `cwd`; a folder covers what is
under it). A job with NO `touches` runs alone, so list every path each job changes. Each finished job is verified
and re-planned at once while the others keep running; the planner saying done waits for running workers. The
supervisor heartbeat comes from its own thread (fresh while workers run; `status` shows the running jobs in the
lock), and a job a dead supervisor left `running` resumes from its checkpoint.

## Process liveness: nothing orphaned, nothing silent (fieldkit liveness)
2026-10-26: an orphaned go.exe held the verifier's pipes for 80 min, the supervisor wrote nothing and 8 jobs waited.
Every process the harness starts (goal workers, verifier criteria, autopilot claude, runbook steps, pipeline and
emerge stages, the guarded pre-commit suite) runs in a CONTAINER (`fieldkit/core/contain.py`: a Windows Job Object
with kill-on-close, a Linux process group + child subreaper, a macOS process group) with a WALL deadline, and output
goes to files, never pipes. On the deadline the whole tree stops, orphans included, and a criterion is `TIMEOUT`
(not FAIL); the supervisor goes on. What a step leaves running when it ends is stopped too.
- Every long run holds a beacon (`state/liveness/runs/`): heartbeat from its own thread, open steps with deadlines,
  every container member (PID + creation time). Its thread also stops its OWN overdue step, so an elevated
  supervisor reaps its own orphans; never elevate another session to do it.
- `fieldkit liveness check [--act] [--json]`: OK / STALLED / ORPHAN, exit 3 on findings. Runs on hog's 5-minute
  schedule, at supervisor start and in the preflight before every job. --act stops only beacon members whose PID AND
  creation time match, logs each to `state/liveness/actions.jsonl` and puts ONE inbox line, never a prompt.
- `fieldkit liveness modes` lists the failure modes (orphaned grandchild, silent supervisor, pipe held by an orphan,
  stale lock with a stuck owner, hung pre-commit test, background job outliving its session, PID reuse, elevated
  orphan) and the check and test for each. A new harness runner starts its processes through `contain.run` /
  `Container`, never a bare `subprocess.run(capture_output=..., timeout=...)`.
- Any command, contained: `python -m fieldkit.core.contain --deadline 1800 -- CMD ...` (exit 124 on the deadline).

## Budgets, checkpoints, resume (every agent job)
- Per session: 10000 turns, 12 h wall (the process tree is killed by PID after it), optional dollars. The hook says
  where the job stands at 50 / 80 / 95 %; at 80 % it denies everything except writing HANDOFF.md / committing until
  HANDOFF.md is fresh. Keep `HANDOFF.md` current (done / next / blockers / commands that worked / evidence).
- Any end that is not success checkpoints first: HANDOFF.md + a WIP commit on `refs/heads/wip/<job>` (plumbing: the
  checkout's HEAD, index and branch are untouched). Then: turn/wall/dollar end -> resume (same session by id when
  fresh, else a fresh one from HANDOFF.md; never from scratch) up to 3 times, then exit 75 paused-resumable + inbox;
  429/529 -> backoff (retry-after honored); usage limit -> sleep until "resets at", then resume; crash -> one fix
  run from the checkpoint, then failed.
- `fieldkit autopilot claude PROMPT --job DIR [--wall-minutes M --cost-usd X]` runs one such job (exit 75 = paused).

## Long jobs: let the harness run them
- Put the steps in a runbook (a ```runbook block: id, run, cwd, retries, needs, done_if, timeout, owner,
  fix) and start it with `fieldkit runbook start FILE` (its own window, survives the session). The harness runs
  the steps, waits, retries, prints the ETA and writes the report from exit codes; it calls a model only to fix a
  failed step marked `fix: true`. Do not poll a running job yourself: `fieldkit runbook status FILE`.
- Agent steps: `agent: BRIEF` (+ `budget`, `max_continuations`, `fallback`) or a `run:` calling `fieldkit autopilot
  claude` get a job folder (state/jobs/<runbook>/<step>/); exit 75 = paused-resumable, not failed; agent steps default
  to `stop_on_fail: false`. Validate first: `fieldkit runbook check FILE`.

## While running
- Never ask for approval. What only the owner may do goes to `fieldkit inbox add TITLE WHY [COMMAND]`; carry on
  with whatever does not depend on it.
- A hard-line DENY is final: report it in the inbox; never look for another route (2026-10-18: a blocked
  `git push` was followed by `gh release create` - that is exactly what the hard lines and the missing credential
  now stop).
- Clean up after yourself, always, without asking (owner, 2026-10-26: "do you wanna delete over 8 GB of crap left
  behind? yes of course i do ... make it a permanent record / decision. is always yes"). Before a job ends, remove what
  ITS OWN steps left behind - test copies, `.partial` resume state, scratch and test worktrees, playground folders,
  temporary builds, probe output - with `fieldkit size recycle PATH ...` (`--whole-folder` when the whole folder is
  this job's), check that each one arrived in the Recycle Bin, and report it in ONE line (what, how many GB). Never
  touched: the owner's own files and originals, other sessions' work, release artifacts, evidence a PLAN row cites,
  and branches or tags on GitHub. Not sure a thing is yours? It is not: leave it and name it in the report.
- Pushing under the owner-wide mandate (owner, 2026-10-26): the ONE door is `fieldkit ship push` (a feat/ fix/
  release- night/ branch), `fieldkit ship push --tag vX` (a new tag) and `fieldkit ship gh-release vX -- ARGS`; the
  hard lines open exactly these, the repository's pre-push hook asks `fieldkit mandate gate`, and the tool router
  refuses a hand-typed `git push` in a covered clone. Never put a per-push GORILLA_OWNER_APPROVED line in the inbox:
  that flag is the owner's own manual path for what the mandate does not cover. No mandate in force
  (`fieldkit mandate doctor`)? One inbox item for the whole run, not one per push. `mandate renew` and
  `mandate install-hooks --yes` are hard line 4 here: the owner's.
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
