# One model and effort strategy, every project (2026-11-25)

Owner, 2026-11-25: Opus sessions run at medium or low effort; helpers for searches and reading run on Haiku;
code is written on Sonnet at the lowest effort that finishes the job; one session, usually Zero, stays at medium,
makes sure the job is done and corrects what Sonnet got wrong. This page maps that order onto the pieces Fieldkit
already has. Nothing here is new machinery: each row names the file that already decides it, and the gaps table
lists what is not yet enforced (plan row P49).

## Roles

| role | model | effort | decided today by |
|---|---|---|---|
| Zero (net control, owner's session) | Opus | medium | owner's session settings; not checked by any tool (gap G1) |
| Helpers: search, read, list, sitrep facts | Haiku | low | `fieldkit/tokens/agent_models.py` (Explore pinned to Haiku, `CLAUDE_CODE_SUBAGENT_MODEL=haiku`), `toolrouter.py` MODEL_OK (spawns must name sonnet or haiku), `cli.py --words` (prose never above Haiku), P34 `fieldkit sitrep gather` (no model) |
| Coders (task start, autopilot workers, side sessions) | Sonnet | low first | `lifecycle/models.yaml` default sonnet, `lifecycle/routing.yaml` worker_top sonnet + risk score, `counsel.work_kind` (P35), `escalate/run.py` RUNGS low -> high -> xhigh on Opus |
| Advisor inside a worker | next usable rung above the worker | - | `models.yaml` counsel.advisor (`--advisor`), never Haiku |
| Reviewer / auto-accept | a rung above the author | - | `lifecycle/autoaccept.yaml` (I-04), `models.yaml` review: [opus] |
| Repair of a parked red | Opus, Fable only with a mandate | medium | CLAUDE.md MODELS + TWO STRIKES, `models.yaml` escalation (own_limit 3, then climb), `lifecycle/counsel.py` |

## The escalation path (cheapest first, one journal)

1. A deterministic script does the step. No model, no tokens (Portage standard).
2. The step fails: a Sonnet worker at low effort repairs it (`fieldkit escalate`, rung 1).
3. A deterministic red the branch owns (`strikes.whose_red`; main's reds and unproven reds do not count) gets a
   fresh session at high effort with a short failure summary (rung 2), the advisor answering inside the session.
4. Three own reds on one model climb the ladder (`models.climb`, haiku -> sonnet -> opus -> fable; the spend guard
   and `availability.json` skip what cannot answer).
5. Zero, at medium, reads the parked job: fixes the code itself or rewrites the brief, and the fix becomes a script
   or a test (GOAL: a model's fix becomes a script).
6. Past the strongest allowed rung: `fieldkit ship ci-wait` parks it and the owner inbox gets one line.

## The GitHub workflow it rides on

- A worker commits on its own branch (feat/ fix/ release- night/), never main.
- `fieldkit ship push --auto` pushes, opens or reuses the PR (only a PR from this repository to this base), and
  enables auto-merge only when the base requires `test (ubuntu-latest)` and `test (windows-latest)` (2026-11-25).
- `fieldkit ship ci-wait` waits in the background; a red is counted per commit (P14) and fed to step 3 above.
- Zero checks every side job's diff and PR (ZERO COMMANDS) before reporting it done.
- `fieldkit ship audit` / `prune` class remote branches by same repo, base and head commit (`method/shiprefs.py`)
  and never delete on GitHub; remote deletions are an owner command.

## Gaps (P49, not enforced yet)

| id | gap | where it would go |
|---|---|---|
| G1 | Nothing checks that Zero runs Opus at medium, or that an Opus session is not at high/xhigh | the check-in hook (`fieldkit zero` establish) reads the session effort and warns |
| G2 | `fieldkit task start` / autopilot pass `--effort` only through `fieldkit escalate`; a plain worker runs at the CLI default | `autopilot/run.py` claude(): effort defaults to the escalate rung 1 (low) for role worker |
| G3 | Desktop side sessions (`start_session`) carry a model but no effort | toolrouter.py MODEL_OK check: require an effort, low for coders |
| G4 | Four parsers of a base's required checks: `ship.missing_contexts` (rulesets), `train.Gh.required_checks` (rulesets + classic), `cilocal.pinned`, `github/engine.values` | one pure `required_contexts(rules, classic)` used by all four |
| G5 | `repair/worktree_classify_recycle.open_names` matches open PRs by branch NAME (the bug class shiprefs fixed) | `shiprefs.matching` by repo, base and head commit |
| G6 | `shiptags` accepts a tag whose commit is on any remote branch, not on the base | check containment in origin/<base> |
