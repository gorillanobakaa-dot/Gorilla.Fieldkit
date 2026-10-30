# The vision

Where Fieldkit is going, in one page. The measured gap and the work items that close it are in
[HARNESS-PLAN.md](HARNESS-PLAN.md).

## The Portage standard

One command builds a whole product, unattended, the way Gentoo's Portage compiles the whole OS. Deterministic
scripts do the work; a model is called only when a step fails, and its fix becomes a script so the next run needs no
model. Measured per job:

- time close to the pre-AI script (a Firefox compile + package: about 90 min to 2 h);
- 0 owner actions (no clicks, approvals or pastes; what truly needs the owner waits in one morning inbox);
- 0 AI tokens when nothing fails, and a capped budget when something does;
- each test runs once for the change it covers; the full suite runs once, when the project is finished.

Builds go through `fieldkit emerge RECIPE`. Every project gets a recipe.

## The household works on one goal

Every device in the house is connected by a direct cable or SSH and put to work on one shared goal, even without
internet, as long as Gorilla OpenCode is installed on it. One computer runs the harness (Linux, Windows, any). When a
project starts it sees which computers are available, splits the project into pieces, each with a checkable
acceptance test, and puts the others to work. Today that means the Linux laptop (the owner's main machine), the
Windows laptop over a direct cable, and an old MacBook Air reached by SSH only. Other machines are extra firepower,
with AI (an agent worker) or without it (plain compile and test jobs that return a checked result).

## Cheap workers, an expensive reviewer

Workers (local models, Sonnet, Haiku, cloud models through Gorilla OpenCode) do the work. Claude Opus, Fable or a
local Qwen only reviews: is the piece finished and done right? The verdict is one of three: pass; explain the
mistake and send it back; or, when the worker failed twice, fix it itself. The reviewer gets a small context: the
task and the result, not the whole history.

## Teach the harness

Every fix a reviewer makes becomes a deterministic program and a test, for the same or a close enough situation, so
that a low-reasoning model handles it next time and the same task is never a bottleneck again. The same failure is
never diagnosed twice. The point of all of it: build, compile, analyze and finish projects faster.

## Long Horizon: a goal, not a queue

A long horizon task is a goal handed over and worked toward for days without the owner (design of 2026-10-22):

- **Goal loop.** A goal file holds the objective, acceptance criteria a program can check, milestones, budgets and
  the decisions only the owner may make. A planner writes the next jobs; workers do them; a separate verifier runs
  the criteria (never the worker grading itself). A criterion is a command whenever it can be one. A supervisor at
  logon resumes unfinished goals after reboots, sleep and usage-limit resets.
- **Budgets and a controlled end.** Each job has a budget of turns, wall time and dollars, with checks at 50, 80
  and 95%. Running out is a resumable pause from a checkpoint (a handoff note and a work-in-progress commit), never
  a restart from scratch.
- **Circuit breakers.** Trip when cost velocity exceeds 3x the rolling average, on identical prompts within 10 s or
  the same tool call 5 times in a row, on more than 20% 4xx errors in 5 minutes, on no progress for 20 minutes, on
  the same failure 3 times, and on heat, low disk or battery power. A rate limit is not a failure: back off and wait.
- **ETA that knows the machine.** Calibrate each machine's CPU, disk and thermal headroom, record the work units of
  every finished step, and predict a range from measured history; "unknown" when there is none, never a guess.
- **Declarative fallback chains.** Decided before the run: for a rate limit, a tripped breaker, an exhausted budget
  or a failure, the ordered list of what to try next (back off, resume, switch to a cheaper allowed model, hand off
  to the inbox). Every switch is written to the report.
- **Parallel jobs.** Each job in its own git worktree; jobs that touch the same files never run at once; concurrency
  set by cores, heat, rate limits and the cost ceiling.
- **Across machines.** One coordinator owns the goal state; a peer runs a job under a lease and returns results that
  the coordinator verifies and records. Jobs carry capability tags (OS, cores, RAM, tools) and go where they fit.
  The transport is SSH over a private cable with a restricted key and a fixed list of verbs; nothing on the cable
  reaches the internet, and a dropped link fails closed.

## One system, later

When the items above are measured green, Fieldkit is baked into Gorilla OpenCode as one system.
