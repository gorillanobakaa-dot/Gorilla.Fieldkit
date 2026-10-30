# The harness plan (cost and friction fixes, 2026-10-27/28)

Why this exists: a cost audit of the Windows laptop found 75 Claude Code sessions costing $986 (API-equivalent) in
5 days, 2026-10-23 to 2026-10-27. This file is the plan that came out of it: the goal the harness is measured
against, the owner's wishes against what was measured, every fix item with its evidence and status, and the
proposed order. The vision behind it is in [VISION.md](VISION.md).

## The harness goal (owner, 2026-10-28)

One command builds a whole product, like Gentoo's Portage compiling the whole OS with every program in it, and the
owner does not need to be in front of the computer. Zero friction, for every project (Fieldkit, Gorilla OpenCode,
Gorilla Firefox, the kernel, all the others). Deterministic scripts do the work; a model is called only when a step
fails, and its fix becomes a script so the next run needs no model.

Acceptance, measured on every job, not claimed:

- **Time:** a product builds in its pre-AI script time (Firefox compile + package: about 90 min to 2 h), not in
  hours or days.
- **Owner presence:** 0 clicks, 0 approvals, 0 pastes during a job; what truly needs the owner waits in one morning
  inbox.
- **Tests:** a test runs once for the change it covers; a passed test is never re-run; the whole project's test
  suite runs once, at the end, when the project is finished.
- **AI cost:** 0 tokens when nothing fails; when something fails, a capped budget and a short session.

## The cost audit (totals only)

Source: `fieldkit token-audit --all --json`, run once on 2026-10-27, read-only. API-equivalent dollars at the
prices of 2026-10-21, not an invoice.

| measure | value |
|---|---|
| Claude Code sessions active in the window (Windows laptop only) | 75 |
| Total cost | $986 |
| Wall hours / API calls | 267 h / 11,224 calls |
| Spent re-reading old context | $580 (59%) |
| Sessions started by another session, not by the owner | 58 sessions, $703 |
| Sessions with a Board row in PLAN.md naming their id | 17 of 75 ($283); without one: $703 |
| Sessions on claude-opus-5-5 | 73 of 75 |

Not in the total: one Firefox 158 upgrade session that ran 95.5 h for $384 and ended just before the window;
Antigravity's Gemini calls (no published price, so counted as unknown, not zero); the Linux laptop's sessions. The
owner paid about $3,000 in the same period; the gap to the about $1,000 visible on the Windows laptop cannot be
explained from that machine alone (item I1).

## Wishes vs reality (2026-10-27)

| # | the owner's wish | reality, measured | items |
|---|---|---|---|
| W1 | Cheap: low cost, short sessions | $986 in 5 days; 59% re-reading old context; sessions up to 36.7 h in the window (95.5 h before it) | R1 R2 O1 I3 I4 |
| W2 | No unnecessary tool calls, retries, lost steps | 568 tool-router refusals (30% from one rule, with false positives), 424 background wake-ups, 190 CI-wait commands; a doc-only commit took 0.7 min under guard | R4 R5 O2 O4 O5 |
| W3 | Like Portage: deterministic scripts build everything, the model only on failure | `fieldkit emerge` was built on 10-26 (5 recipes) but was in no rules file and never run on the Windows laptop until 10-27; the firefox-windows plan dry-run passes. The rule was added to AGENTS.md (PR #150) | R6 |
| W4 | Firefox compile + package in about 2 h, as with plain scripts | Firefox 158 took 80-90 h with AI ($384) | R6 |
| W5 | Every device in the house works on one goal, offline | the cable between the Windows laptop and the Linux laptop works (96 signed notes, 9 jobs, 6 passed); the MacBook Air (SSH only) is not in the comms layer; Zero only ever saw the Windows laptop's sessions | C1 C2 C4 C7 |
| W6 | Other machines as extra firepower, with or without AI | the Linux laptop is the owner's main machine, where every project is created; it has Fieldkit, Python and Gorilla OpenCode, so it can take every job kind today. The weak one is the MacBook Air (4 GB, SSH only). `run` jobs exist (Fieldkit commands only); `agent` jobs default to cloud Gemini and pass on exit code 0 alone | C3 I6 |
| W7 | Cheap workers do the work; Opus/Fable/Qwen only reviews | Opus ran 73 of 75 sessions and did the work itself (7,958 Bash calls, 2,020 edits); the cheap workers ran a few test sessions under $0.02 | I5 I7 |
| W8 | Teach the harness: each fix becomes a deterministic tool, so the same problem never costs again | the mechanism exists for build logs (triage signatures); not wired into reviews | I8 |
| W9 | The owner knows what every session did | 58 of 75 sessions left no Board row with their id | R3 I4 |
| W10 | Fieldkit, once flawless, is baked into Gorilla OpenCode as one system | Fieldkit is a separate Python harness; OpenCode v0.1.156 carries an embedded toolbox; no merge plan yet | FINAL GOAL, not now: only when the owner says the harness works perfectly (owner, 2026-10-29: "not today, tomorrow or the day after"); no agent proposes or starts it |

Status words: PLANNED = agreed in the list, nothing built yet. `fieldkit plan next` reads this file: a merged pull request whose title ends in
"(ID)" marks a PLANNED item DONE (`--write` writes it); any other status written by hand (PARTIAL, WINDOWS DONE)
wins over a merge. Every fix is measured before and after with the same
`token-audit` numbers (the "measure" column).

## Repairs (something is broken or contradicts itself)

| id | what | evidence | measure | status |
|---|---|---|---|---|
| R1 | A session limit that stops, not warns: past the rotate limit (350k tokens per call) the session may only hand over | $580 (59%) went on re-reading old context; one session averaged 529k per call; 5 sessions of 12 h or more cost $328 | calls with 300k+ context, $ per session | DONE (PR #152) |
| R2 | One rule, one place, for every rules file an agent loads. The global CLAUDE.md (13.9 KB) and Fieldkit's AGENTS.md (24.3 KB) were sent on every Claude call; GEMINI.md was 32.4 KB, the Codex AGENTS.md 3.4 KB, Gorilla Firefox's AGENTS.md and GEMINI.md 7 KB each, plus 34 skill descriptions. They repeated each other, contradicted each other (two different done protocols), and some rules made the agent stop and ask, which blocks unattended work. Fix: one core file (goal + the few hard rules, 2-3 KB) as the single source, copied into each agent's file by the rule installer; each project keeps a tiny file with only its own rules; everything long moves into skills and docs; a pre-commit check fails a rules file over its size limit or one repeating the core. Honest cost: the rules files are about 9.5k of the 72k tokens on each call (about $20-30 in the window); the bigger gain is behavior | file sizes measured 2026-10-28 | bytes loaded per call; stops per job | DONE (PR #150, #166): CLAUDE.md 13.9 -> 6.9 KB, GEMINI.md 32.4 -> 7.9 KB, Fieldkit AGENTS.md 25.8 -> 6.7 KB on Windows; the VAIO's global files 8,163 B each (old text in ~/.agents/RULES-REFERENCE.md); `fieldkit rules check` and `fieldkit rules install` are the size check and the installer (#166) |
| R3 | No session ends without its Board row in PLAN.md (with the session id) | 58 of 75 sessions ($703) have no row with their id | sessions with a row / all | DONE (PR #182) |
| R4 | `fieldkit guard` writes its 11 status lines to stdout: they mix into the program's output and enter the model's context on every guarded command | `guard -- token-audit --json` produced invalid JSON; 460 guarded commands in the window | lines added per guarded command | DONE (PR #161) |
| R5 | Tool-router refusals cost a retry each. By rule: heredoc with backslashes 247 (30%, with false positives), recursive search 151, sleep-to-wait 102, tree copy 95, hashing 69, heavy work outside guard 55, delete 25, download 25, git push 11. Fix each top rule at its cause: no false positive, or the router runs the right tool itself instead of refusing | 568 refusals in the window, about $47 in retry calls | refusals per 100 calls | DONE (PR #163) |
| R6 | `fieldkit emerge` (the Portage idea, PR #127, 5 recipes including firefox-windows) must be THE way to build. It was in no rules file and never run on the Windows laptop. Step 1 done 2026-10-27: the rule is in AGENTS.md (PR #150). Next: run the firefox-windows recipe once and measure it against the plain-script baseline (about 2 h compile + package) | Firefox 158 took 80-90 h with AI ($384, before emerge existed) | hours per Firefox build; AI tokens per build (target 0 when nothing fails) | DONE (PR #176, #183, #190): every project in fieldkit/emerge/inventory.yaml has a recipe, 23 of 23 |
| R7 | Tests once: a test runs once for the change it covers, a passed test is never re-run, the whole-project suite runs once at the end. Today every PR runs the full suite in CI on Windows and Ubuntu, and goal criteria re-run suites every round. Owner decided 2026-10-28: the full suite only at the end, when a project is finished ("there is no point wasting time and money"); per change only the tests covering it, locally and in CI. This changes the CI workflows and the mandate's "merge only on green full CI" | 673 CI runs in 14 days; 133 broad pytest runs in the window; one goal produced 284 verdicts in 16 rounds | test minutes per change | DONE (PR #155) |
| R8 | 26 old Fieldkit worktrees left in the Documents folder, each a full checkout with its own stale AGENTS.md (16-24 KB): list each with its branch and whether it is merged, then recycle the merged ones (made by earlier sessions, so listed for the owner, not removed silently) | a listing of the worktrees' AGENTS.md files | worktrees left | PLANNED |

## Optimizations (works, but costs too much)

| id | what | evidence | measure | status |
|---|---|---|---|---|
| O1 | Smaller base prompt: fewer skills, MCP servers and tool lists loaded per project | 72k tokens on the first call, re-sent on every call: about $166 (17%). VAIO 10-21..28: median 60.5k (41 sessions); the Artifact tool schema alone 37.8 KB | first-call context | DONE (PR #180, #199: phase 3 done; headless first call 41,975 -> 28,526 tokens, `fieldkit cost firstcall` measures it by part, `fieldkit skills sets` keeps a skill set per project as data) |
| O2 | The room hook injects about 1,100 characters on every message: inject only when something changed | 6,026 injections, 6.7M characters, about $37 re-read | hook characters per call | DONE (PR #162) |
| O3 | Fewer sessions started by sessions; short briefs; a subagent or a headless run when only the result comes back | 58 of 75 sessions were started by another session, $703 | sessions started by sessions, $ each | DONE (PR #213) |
| O4 | Waiting costs calls: one wake when CI or a job ends, no polling | 424 background wake-ups (about $44), 190 CI-wait commands. VAIO, 41 transcripts 10-21..10-28: 326 background wakes, 103 hand-written wait loops (41 with gh, 62 on a log), 210 gh status polls | wake-ups per session | DONE (`fieldkit ship ci-wait PR [--merge]`: one background call, the train's verdict, the failed log tail on red; the tool router sends `gh pr checks --watch`, `gh run watch` and gh loops to it; a job or log wait is `fieldkit wait` in the background, already routed). After-numbers not measured yet: re-run the count a week after merge |
| O5 | Guard preflight takes 4-7 s per command | a 4.4 s preflight warning; 10 recorded runs averaging 7.0 s | seconds per guarded command | DONE (PR #161) |

## Long Horizon (the 2026-10-22 design)

The owner's Long Horizon design (goal loop, budgets, circuit breakers, ETA, fallback chains, parallel jobs, and the
multi-machine extension) lived only in a git-ignored folder: no rules file pointed to it and no session loaded it.
Its essentials are now in [VISION.md](VISION.md).

| id | what | evidence | status |
|---|---|---|---|
| L1 | One short vision file in the repository (goal, the Portage bar, roles, devices, cost rules), linked from AGENTS.md and read by every session; the long designs stay as references | the designs lived only in a git-ignored folder | DONE (PR #150: AGENTS.md links VISION.md and this file) |
| L2 | Build the circuit breakers the owner specified on 10-22: cost velocity above 3x the rolling average; repeated identical prompts within 10 s; more than 20% 4xx errors in 5 minutes. None exists in `fieldkit/autopilot`; they would have stopped the 20-36 h sessions | no breaker in the code | DONE (PR #154) |
| L3 | The goal loop runs, but slowly: one goal did 16 rounds, met 7 of 15 criteria, produced 284 verdicts (154 PASS, 127 FAIL, 3 TIMEOUT) and was stopped by a STOP file; another stayed at 0 rounds. Criteria judged by an agent cost a model call each time. Rule: a criterion is a command whenever it can be one; an agent judges only where no command can decide | goal state and verdict files | DONE (PR #213) |
| L4 | Built and in use: goal file + planner/worker/verifier, supervisor at logon, dollar budgets, checkpoint/resume, runbook, inbox, mandate pushes. Keep and measure | `fieldkit/autopilot/` | DONE (exists) |

## Comms (read 2026-10-27: about 11k lines in fieldkit/peers, fieldkit/zero, fieldkit/orchestrate*, autopilot/room.py)

| id | what | evidence | status |
|---|---|---|---|
| C1 | Reach every device by the path it has: the cable (the Linux laptop) and SSH (the MacBook Air, which cannot take the cable). Today the comms layer knows only the cable: one peer, no household list, the Mac is not a worker | the link configuration; owner 2026-10-27 | DONE (PR #188) |
| C2 | Zero coordinates only the Windows laptop's Claude sessions: 115 stations, all from that laptop, 113 missing; no other machine ever checked in | `fieldkit zero --json` | DONE (PR #188) |
| C3 | Workers with and without AI. The Linux laptop and the Mac cannot run a local model. (a) No AI: a `run` job does plain heavy work (compile, tests) and returns a checked result; today it accepts only a `fieldkit <command>` line and needs Fieldkit + Python + the listener on the worker, not Gorilla OpenCode alone. (b) With AI: Gorilla OpenCode on a cloud endpoint as the worker. An `agent` job today passes on exit code 0 alone and needs the reviewer step (I7) | `orchestrate_work.py` | DONE (PR #189: (a) no-AI `run` jobs for build and test programs, `fieldkit task start --on DEV --run`, done only on a signed pass with every artefact's sha256; PR #206, #207: (b) an `agent` job passes only on a review verdict, the cheap worker and reviewer roles are data) |
| C4 | The connection kept dropping, so five keep-alive mechanisms were added (heal, rescue, watchdog, keepalive task, zero check). Find the cause of the drops once (sleep, network card power saving, cable, listener crash) and keep one mechanism. A device shut down on purpose is "off", not "lost": the heal step kept retrying every 15 minutes after the owner shut the Linux laptop down | the link log; owner 2026-10-27 | DONE (PR #186, #187, #193, #201, #206: every drop on the L15 with its cause and a verdict per keep-alive in docs/findings/c4-link-drops.yaml; the listener fast-forwards its own clean main checkout every 5 min; one keep-alive kept, a peer switched off is counted as off, not lost, and not retried) |
| C5 | The Windows laptop knocks on its own listener and is refused: 46 refused connections from its own cable address since 10-22 | the link log | DONE (PR #186): the own-listener check binds the port instead of dialling its own cable address |
| C6 | Every tool call starts 2 Python processes (tool router, room) that read and rewrite the net files, the room log and the transcript | `room.py`, the Claude settings hooks | DONE (PR #162, #195) |
| C7 | Every order says which channel carried it and gets a receipt: the Linux laptop accepted a shutdown order on 10-27, and nobody knows whether it came through a Claude desktop remote session or the cable | owner 2026-10-27 | DONE (PR #171) |

## Improvements (new visibility, so the money cannot disappear again)

| id | what | evidence | measure | status |
|---|---|---|---|---|
| I1 | Cost from both machines in one report (Linux sessions, Antigravity with a price for its Gemini models) and the real bill next to the API-equivalent | about $1,000 visible on the Windows laptop against about $3,000 paid | gap between report and bill | DONE (PR #184): `fieldkit cost report --since DATE [--bill USD] [--peer FILE]`, both machines per day and per source (Antigravity priced from prices.json), the real bill beside the API-equivalent. The measure (the gap against the owner's bill) is not recorded here yet |
| I2 | `token-audit --since DATE` and clean JSON (no guard text inside) | the audit had to be filtered by hand | - | DONE (PR #179): `token-audit` and `session-cost` take --since/--until; --json puts only JSON on stdout |
| I3 | A dollar budget per job and per autopilot step: stop and hand over at the cap | no cap today; one session reached $384 | $ per job | DONE (PR #153) |
| I4 | One daily cost line for the owner: yesterday's $, the most expensive session, what it did | the owner lost track over 4 days | - | DONE (PR #179): `fieldkit cost daily`, one line: the day's $, the most expensive session and what it did, both machines' total when the other's JSON came over the cable |
| I5 | The roles the goal needs: workers (local models, Sonnet, Haiku) do the work; Opus/Fable or a local Qwen only reviews a result against its acceptance check, with a small context (the task + the result, not the whole history) | 73 of 75 sessions were Opus doing everything | share of calls by worker models; $ per finished task | DONE (PR #207) |
| I6 | Inventory and split: the harness lists the devices on the link that are up and what each can run, then splits a project into pieces, each with a checkable acceptance test | the goal | pieces finished per hour | DONE (PR #200) |
| I7 | A review verdict is one of three: pass / explain the mistake and send back / fix it itself (when the worker failed twice) | the goal | rounds per piece | DONE (PR #207) |
| I8 | Teach the harness, measured: every review fix becomes a deterministic tool + test (as triage signatures already do), and the next similar task is checked to need no reviewer fix | the goal; rule "the same failure is never diagnosed twice" | repeat failures of the same kind | DONE (PR #206) |
| I9 | Owner presence measured per job: every prompt, approval, click and paste the owner had to do is counted; the target is 0 (`fieldkit friction` lists what could wait on a person; this counts what did) | owner: "50 hours in front of the computer to click accept" | owner actions per job | DONE (PR #174) |
| Z1 | NO PASSIVE WAITING: a deterministic task watch. `fieldkit task watch [--once] [--act]` flags UNHANDLED (ended blocked/failed, no follow-up), STALLED (alive, silent > N min) and DIED (no process, no report); a scheduled run every 5 min plus a prompt hook inject the findings into Zero; `peers link relay listen --follow` never exits after a message; rule in rules/CORE.md | 2026-10-29 23:17 to 00:04: a VAIO job ended blocked, Zero sat idle about 45 min because nothing woke it; the relay was restarted by hand 7 times (owner 2026-10-30: "waiting costs us money") | minutes from a task ending to Zero acting; hand restarts of the relay (0) | DONE (branch feat/task-watch) |
| P1 | Parked ideas reach the owner, never the running plan: the room hook shows "N parked ideas - fieldkit ideas review" and the brief on a session's first prompt; CORE.md says a mid-job suggestion goes to `fieldkit ideas add`; the markdown is rebuilt from ideas.json when missing or stale; `fieldkit backup` carries state/ideas/ | owner, 2026-10-30: `fieldkit ideas` was merged (#220) but nothing used it; the finish line kept moving as suggestions entered the plan | parked ideas the owner reviewed; plan rows added mid-job (target 0) | DONE (PR #225) |

## Order (proposed 2026-10-28, approved by the owner the same evening)

| phase | items | why first |
|---|---|---|
| 1. Brakes on the money | R1 + L2 + I3 (hard session stop, circuit breakers, $ per job) | 59% of the money went on long sessions; nothing stops them today |
| 2. Tests once | R7 | "50 hours in front of the computer" and hours of repeated test runs |
| 3. Lighter every call | R2 + O1 + L1 (one goal/vision file, rules once, smaller base prompt), R4 + O5 (guard), R5 (the router does instead of refusing), O2, O4 | every call pays for these |
| 4. Portage for real, every project | R6 (an emerge recipe for every project, about 20: Debian, the Linux kernel, Firefox, Gorilla OpenCode, Fieldkit, ...; 5 recipes exist), I9 (owner clicks per job = 0) | the goal itself |
| 5. Know where it went | R3, I4, I1, I2 | the owner never loses track again |
| 6. The household | C4, C5, C7, C1, C2, C3 + I6, C6 | the extra firepower |
| 7. Cheap workers, Opus reviews | I5, I7, O3, L3, I8 | the multi-agent vision |
| 8. One system | Z1 (no passive waiting), W10: bake Fieldkit into Gorilla OpenCode | only when 1-7 are measured green |
| 7b. Ideas never move the finish line | P1 (parked ideas surface to the owner, not into the running plan) | the plan kept growing mid-job |
| 8. One system | W10: bake Fieldkit into Gorilla OpenCode | only when 1-7 are measured green |

## Owner decisions already made

- **The full test suite runs once, at the end**, when a project is finished; per change only the tests covering it
  (2026-10-27/28).
- **Builds go through `fieldkit emerge` for every project**: a project with no recipe gets one; stages are never
  hand-run (2026-10-27).
- **Ask before work stays out of git**: before a job ends, every needed document written into a git-ignored folder
  is named, and the owner is asked once whether to add it to git.
- **The order above is approved** (2026-10-28), with R7 (CI runs only the tests covering a change) and R8 (old
  worktrees that are no longer needed go to the recycle bin). Sessions may merge their own pull requests on green
  CI without the owner's click.
- **Fieldkit stays public.** Everything published here is sanitized and in English only; private paths, names and
  machine details stay in git-ignored local files.
