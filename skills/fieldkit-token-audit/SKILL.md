---
name: fieldkit-token-audit
description: What an AI coding session costs, where its tokens went, and when to replace it with a fresh one - dollars per session, live running total with a warning, cost per call, the share spent re-reading old context, for Claude Code (CLI and the desktop Code tab, subagents included) and Google Antigravity, with fieldkit session-cost, token-audit and rotate; every live session judged OK / WARN / ROTATE by context per call and dollars, a handover written and a fresh session started for the monsters, finished sessions listed for archive. Use when the owner asks what a session costs, to watch the cost live, why usage limits run out, whether sessions should be split, when a hook line says SESSION ROTATION, to hand a long session over to a fresh one, or to find finished sessions to archive.
license: AGPL-3.0-or-later
compatibility: "Needs the fieldkit command (Gorilla Fieldkit, github.com/gorillanobakaa-dot/Gorilla.Fieldkit; Python 3.11+ with PyYAML) on Windows, Linux, macOS. Works offline; no network access unless a step says so (rotate finished --gh asks the local gh)."
---

# Session cost, token audit and session rotation

**Never estimate cost or token use by hand or re-write these tools.** Source: `fieldkit/tokens/` (moved from the
local kit TokenAudit 2026-10-26), cards `session-cost`, `token-audit`, `rotate`.

| The question | Run |
|---|---|
| is it ready here? | `fieldkit session-cost --doctor`, `fieldkit rotate doctor` |
| what did the newest session cost? | `fieldkit session-cost` |
| the newest sessions of each product, with dollars | `fieldkit session-cost --list 5` |
| one session, per call | `fieldkit session-cost <session-id-start> --calls` |
| watch the cost live, warn past $5 | `fieldkit session-cost --live --warn-usd 5` (Ctrl+C stops) |
| tokens, context per call, hook text, as a table | `fieldkit token-audit --all` |
| which sessions are monsters now? | `fieldkit rotate check` (exit 0 OK, 1 WARN, 2 ROTATE) |
| hand this session over to a fresh one | `fieldkit rotate handover --transcript PATH --title T --done D --next N --not-verified V` |
| finished sessions to archive | `fieldkit rotate finished --gh --exclude <own session id>` |
| am I (Codex, Antigravity, Gorilla OpenCode, ...) over the line? | `fieldkit rotate check --source <agent> --cwd . --newest` |
| which agents are measured and told? | `fieldkit rotate doctor` (per agent: measured yes/no, triggered yes/no) |
| tell an agent with no hook to check itself | `fieldkit rotate rule --install <its AGENTS.md / GEMINI.md>` |
| an agent Fieldkit cannot read | write `~/.fieldkit/usage/<agent>/<session>.jsonl`, one line per call: `ts, session, model, input_tokens` (cached included), `cached_tokens, output_tokens` (+ optional `provider`) |

## Session rotation (owner 2026-10-26: "get rid of the monster sessions and create new ones that costs less")

- Verdict per session: context sent with its own last call >= 250k WARN, >= 350k ROTATE; its bill >= $40 WARN,
  >= $75 ROTATE (override under `"rotate"` in `fieldkit.local.json`). Logged in `state/rotate/verdicts.jsonl`.
- The room hook adds a line to the session's context: WARN once; ROTATE at every prompt and once mid-turn. You do
  not have to ask for it.
- On **SESSION ROTATION**: start no new work. At the next safe point (work committed or written down) run the
  `fieldkit rotate handover` line the hook gave you. It appends a dated section to `state/coordination/PLAN.md`,
  writes `state/coordination/rotate-<date>-<id>.md` and prints the brief. Then start a fresh session with that brief
  (Claude desktop: `start_session`, context "fresh", the brief as its prompt) and archive this one
  (`archive_session "self"`). In the CLI: a fresh `claude` in the same folder with the brief.
- Under `fieldkit autopilot` this is automatic: the job's hook denies all but the checkpoint (HANDOFF.md + WIP
  commit), and the job restarts from it in a NEW session, never `--resume` of the old one (5 rotations, then the
  owner's inbox).
- `rotate finished` lists sessions whose last reply ended its turn, idle >= 60 min, with no open PR. Without `--gh`
  a session that mentioned a PR is held (its state unknown). The tool never deletes or edits a transcript; the
  Desktop agent archives.

## How to read the cost

- Tokens are roughly **calls x context per call**: every call sends the whole conversation again (cache reads).
  The report's "re-reading old context" line is that share of the bill.
- Dollars are **what the same calls cost on API credits**, from `fieldkit/tokens/prices.json` (one table per
  provider, source page and fetch date). On a subscription this is not money spent; on API credits it is.
- **Unpriced is said, never guessed.** Antigravity's internal names have no public price; `--assume NAME=PUBLIC`
  only if the owner knows which public model it is - the report then says so.
- Antigravity never records cache writes: its Claude totals are a lower bound.
- Prices change. To update: `fieldkit evidence fetch` the source URLs in prices.json, change the numbers, `fetched`
  and `sha256`, then run the tests. Never type a price from memory.
- 2026-10-21: Claude Code session 98ea1ad9 (64 h) = $303.14 with its subagents, 72% re-reading; calls at 500k+
  cost $0.189 each against $0.051 at 100k-200k. 2026-10-26 first `rotate check`: 25 sessions, 5 ROTATE (up to
  924k per call), 7 WARN.

Tests: `python -m pytest -q tests/test_tokens_session_cost.py tests/test_tokens_token_audit.py tests/test_rotate.py`;
mutations `tests/tokens_mutations.yaml`, `tests/rotate_mutations.yaml`.
