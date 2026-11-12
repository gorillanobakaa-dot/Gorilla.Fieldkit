# Autonomy gates: every place Fieldkit waits for the owner

Idea I-04 (accepted by the owner on 2026-11-11): a long-horizon job runs by itself. The **harness** accepts a unit of
work when every deterministic check passes **and** an independent reviewer model, on a higher ladder rung than the
author, says yes. A model still cannot accept anything. This page lists every gate in the code where the work stops
for the owner. For each gate it says what the gate protects and who handles it now:

- **core**: the shared auto-accept core (`fieldkit/lifecycle/autoaccept.py`) decides it now
- **follow-up**: a later job wires the core in; until then the gate is unchanged
- **owner**: stays the owner's alone, with the reason

Every gate that stays with the owner keeps working as before. The owner can also add `--force` and prove who they
are with the signing-key passphrase (I-06, `fieldkit/lifecycle/ownerproof.py`). That works from any shell, chat `!`
included. A model has neither a terminal nor the passphrase.

## How the core works

The core works for any kind of project. The work item can be a code change, a build stage, an essay section, a video
edit or a CV. Each project brings its own checks.

| Part | Where |
|---|---|
| Policy switch (`auto_accept.enabled`, default on) and the kinds it covers | `fieldkit/lifecycle/autoaccept.yaml` |
| Reviewer pick: the lowest rung above the author that the spend guard allows; reviewer != author; an unknown author gets the top rung | `autoaccept.pick_reviewer`, ladder in `fieldkit/lifecycle/models.yaml` |
| Decision: every check passes AND the reviewer says `VERDICT: YES`. Anything else, an unclear reply included, refuses (fail closed) | `autoaccept.decide` |
| Checker-vs-reviewer deadlock (the check refused one attempt, the reviewer refused another, on the same item): a tie-break by the highest rung the spend guard allows above the author. It may only answer `HOLDS: CHECK`, `HOLDS: REVIEWER` or `HOLDS: UNSURE` and must quote a line of the evidence (for a port: the upstream change of the hunk's lines). REVIEWER holds: the check's reasons are waived for that one item (journal `override`, listed for review with its undo line); the check is never changed. Unsure, unquoted or no tie-breaker: the item parks with both reasons. Off: `auto_accept.tiebreak.enabled: false` | `autoaccept.tiebreak`, `autoaccept.overridden`, `buildh/autoport.tiebreaks` |
| Journal: both models, the evidence, the reason and a one-line undo command for each decision | ledger `auto-accepted.jsonl`, `fieldkit autoaccept list` |
| After-the-fact review by the owner | `fieldkit review`, `state/INBOX.md`, `fieldkit autoaccept seen` (owner only) |
| The owner's proof for `--force` | `fieldkit/lifecycle/ownerproof.py` (passphrase read from the terminal device or a desktop dialog, never from argv or the environment) |

When the core refuses, the refusal counts as a failed attempt on the author's rung. The escalation ladder (P9) then
retries, climbs to the next rung, or parks the step at the top rung.

## Build-harness (the first caller, `fieldkit/buildh/autoport.py`)

| Gate | Protects | Now |
|---|---|---|
| `build-harness accept` (adapted or hand port waiting as a proposal) | the port changes nothing beyond the original hunk's meaning | **core**. Checks: the diff applies, only the step's files change, the meaning check passes (removed lines gone, added text present, renames followed), the changed files parse. Then the reviewer decides. The owner can still accept by hand |
| `build-harness unblock STEP skip` | a port step is marked done with no edit | **core**, through `build-harness request TASK STEP skip --note "lesson: ..."`. Checks: a port step, a lesson given, and either the result already holds or none of the hunk's lines are left in the file. Then the reviewer decides. Owner: `--force` plus the passphrase |
| `build-harness unblock STEP retry` | a blocked check step is not re-run without a fix | **core**, through `request ... retry`. Checks: the step is blocked, automatic retries are left (`max_retries`), and a commit landed after the failure. Then the reviewer decides |
| deferred step, "renamed upstream" / "no longer exist" | a change whose target moved is not dropped silently | **core** (I-04 B). The step goes back to being a model step, with the harness's finding in the packet (`offer`). The model ports it with `renames: OLD -> NEW` in its note. The meaning check judges the renamed hunk and checks the rename itself |
| `build-harness deferred --do` (drop a deferred change) | the owner's own patch is never lost | **owner**: dropping the owner's change is a decision about their project. `--force` plus the passphrase works away from a terminal |
| `build-harness decide` / briefs (`fieldkit/briefs/record.py`) | the owner's recorded decisions (the decision register) | **owner**: changing an owner decision is a hard line |
| `build-harness rewind`, `withdraw`, `approve` | undoing accepted work | **owner** for now. A rewind is the undo line the core prints. **follow-up**: run it through the core when a reviewer finds a regression |
| `decision.apply` revert (`fieldkit/buildh/decision.py`) | the owner's repository history | **owner** |
| `leakgate-approve`, `leakgate-dispositions`, `leakgate-rejudge`, `leakgate-baseline` (`fieldkit/leakgate/allow.py`, `dispositions.py`, `buildh/cli.py`) | what the browser may contact (THREAT MODEL: every channel is a door) | **owner**: a leak-gate exemption is a privacy decision; a reviewer model must never open a door |
| release publish (`fieldkit/buildh/releasepublish.py`) | publishing | **owner**: push/release mandate |

## No owner-only stall (P9, 2026-11-11)

firefox-160-linux sat ~6 hours on two owner commands: the audit row "no step skipped" failed on every skip with no way
to record a reason, and a stale `final-checks` waited for an owner `retry`. Every owner-only stop a long job can hit
in build-harness and lifecycle now goes one of these ways:

| Stop | Now |
|---|---|
| audit row `journal: no step skipped without a reason` (`fieldkit/buildh/audit.py`) | see "The mandate is the reason" below: an owner skip passes with no note; a harness skip passes only with a reviewer's (or the mandate door's) why. Distinct steps count, not repeats; a later retry undoes a skip |
| a skip both rungs agree on | **core**: `request ... skip`, checks + independent reviewer (I-04); the reviewer's why is the recorded reason |
| a blocked CHECK step after a commit landed (`autoport.retry_scripts`, in the sweep) | **harness**: re-run automatically, kind `retry`, capped by `max_retries`. Running the same check again cannot weaken it, so no reviewer is asked |
| a stale `final-checks` (changes after its last pass, `buildrun.py`) | **harness**: re-run when ANY failing row is stale (before: only when all were) |
| no independent reviewer can be had (none usable, the author's own model, out of budget, not answering) and every check passed | **mandate door**: a mandate in force whose `owner_steps` grants the step lets the harness take it, journaled `pre-approved by mandate <name> (seal ..., key ...)` in the auto-accept ledger and the mandate log, listed in `fieldkit review`. A reviewer's NO is never overruled; `mandate_door: false` in `autoaccept.yaml` turns it off |
| BLOCKED / PROPOSED / DEFERRED with nothing else left (`task.owner_wait`) | the task record carries `waiting_owner` (state, steps, why, the command; `build-harness status` shows it), the journal gets `waiting-owner`, and `fieldkit inbox add` files it once (kind `decision`: the agent ledger Zero reads). Everything that does not depend on it already went on |
| `approve` | can be pre-approved through the mandate's existing `owner_approvals` list |
| `deferred --do` (drop), `decide`, the leak-gate verbs, `rewind`/`withdraw`, release publish | stay the owner's (decision register, THREAT MODEL, push mandate); a job reaching one gets `waiting_owner` + the ledger row, never a silent stop |

The session-start pre-approval is the owner-wide mandate. Its schema (the template `fieldkit/mandate/owner-wide.yaml`
and `owner-wide.prereqs.yaml`) carries the door:

```yaml
owner_steps:
  unblock: [skip, retry]   # the unblock words the harness may take
  accept: true             # accept a finished piece of work (an adapted port)
  when: [evidence]         # every deterministic check passed and the reason is recorded
```

An owner-wide mandate already signed carries this door without a renewal (next section). A model never signs the
mandate and never types an owner command.

## The mandate is the reason (P9, 2026-11-11)

The owner, 2026-11-11, verbatim: "Fuck the motive, fuck the reason. Eliminate that ... One single approval. That was
our mandate. One single approval. At the beginning of the job, it's all the reason, all the motives you ever need.
Eliminate everything that slows us down." (The owner changing his own decision.) Every place in build-harness,
lifecycle and mandate that asked the owner for a per-step reason, note, confirmation or second approval while a
mandate in force covers the work:

| Place | Was | Now |
|---|---|---|
| `build-harness unblock TASK STEP skip` (`task.unblock`) | refused without `--note` (PR 460) | no note needed. The owner proof (a real terminal, or `--force` + passphrase) is the decision; the journal event carries `by: owner`, the proof and the mandate(s) in force. `--note` is still journaled when given |
| audit row `journal: no step skipped without a reason` (`audit.journal_checks`) | failed on every owner skip without a note (firefox-160-linux, the 12:42:12 skip) | an OWNER skip (`unblock`, `how: skip`, written only behind the owner proof) passes; old journals pass as they are, no re-run. Evidence: "N skipped by the owner (mandate NAME), M by the harness with a reviewer". A HARNESS skip (`auto-accept`, kind `skip`) with no reviewer or no why still FAILS (`tests/test_buildh_audit.py`, `tests/test_buildh_no_owner_stall.py`; `fieldkit mutate` proves the row can fail) |
| mandate `owner_steps` door (`mandate.owner_steps`) | open only when the SIGNED file names it: the signed owner-wide copy (in force to 2026-12-28) did not, so a renew (inbox item 48) was needed | an owner-wide mandate (`scope: owner-wide`) that leaves `owner_steps` out gets the template's door (`OWNER_STEPS_OWNER_WIDE`: unblock skip/retry, accept, `when: evidence`). The copy already signed covers it; no renew. Writing `owner_steps` yourself (`{}` or `false` closes it) still wins; other mandates open it only when they name it. The door's own conditions stay: every deterministic check passed, a reviewer's NO is never overruled, each use is journaled and listed in `fieldkit review` |

Looked at and kept (each is a single command, a hard line, or a guard on what a MODEL may do, not a second owner stop):

| Place | Why it stays |
|---|---|
| the owner proof on an owner skip / `--force` (`lifecycle/ownerproof.py`) | it is how the tool knows the command is the owner's and not a model's (hard line: a model never acts as the owner). It is the one approval, not an extra one |
| the `--note` of `withdraw` and `drop` (`task.withdraw`, `task.drop`) | no owner gate: a model can run them, and the note (the author's words for drop) is what stops a model from silently throwing ported work away. Removing it would weaken a guard (hard line) |
| the `--note` of `request ... skip/retry` (`autoport.request`) | a MODEL asks the harness; its reason is what the reviewer judges |
| the `lesson:` line of `submit --hand` / `--adapted` | teaching data for the next run (GOAL: a fix becomes a script), not an approval; the owner's `accept` already skips it (`require_lesson=False`) |
| `deferred --do "DROP THIS CHANGE: ..."`, `brief --do "PUT BACK THE OLD ..."` (`deferred.py`, `decision.py`) | the typed phrase IS the single command (one line, no second stop); they change owner decisions and patch content (decision register) |
| `release-publish` and `night-pack arm` "Type yes" | release outside `fieldkit ship` (hard line 1); `night-pack arm` is itself the one pre-approval for the night |
| `mandate approve/renew` typed yes + passphrase | the single approval itself |

## GitHub's runner down: the same CI runs locally and counts (P9, 2026-11-11)

The owner, 2026-11-11, verbatim: "Add a rule. If the GitHub's remote machine is not available, we can run a CI
locally. We have all the infrastructure needed. We have all the dependencies. We have all the programs." (The owner
changing his own merge rule, hard line 1: main moves only by a PR merged on green CI.) Evidence: PR 461's Linux job
failed 7 times from 13:59 to 16:43 at the apt step ("Temporary failure resolving 'archive.ubuntu.com'"), no test
ran; Windows passed; githubstatus.com showed no incident; on the Debian machine the PR's tests passed (83 passed).

| Place | Was | Now |
|---|---|---|
| a PR whose GitHub job for one OS fails by a runner-infrastructure signature (`build/signatures/ci-infra.yaml`), or never starts within 30 min | `ship ci-wait` re-ran it 3 times per head, then waited for GitHub | `fieldkit ship ci-local PR` (`fieldkit/method/cilocal.py`; ci-wait names it when its re-runs are spent) runs THE SAME workflow job for that OS (`.github/workflows/tests.yml` at the head: its steps, `if:`, `shell:`, `timeout-minutes:`) at the PR's exact head commit in a clean scratch worktree: Linux here, Windows on a Windows device over the cable (`fieldkit ci-steps` as a signed run job). Green: a proof (commit, OS, every step's exit code and seconds, the log's sha256, host) signed with this computer's review key, kept in `state/ci-local/`, posted as a commit status with the CI job's own context, so auto-merge counts it |

What it never does (each one a test, `tests/cilocal_mutations.yaml` proves each can fail):

| Never | Why |
|---|---|
| a proof for a test failure on GitHub | only a known runner outage is infrastructure; a log that names a failed test never is, and anything unrecognised is a test failure (fail closed) |
| a proof for any commit but the PR head | the head is read before and after the run; a full 40-hex match or nothing |
| a proof for the other OS | the OS of the job is the OS of the machine that ran it; the other OS's GitHub green is still required |
| a red local check step without a strike | it counts as a GitHub red would (TWO STRIKES); a red in setup (checkout, venv, pip) proves nothing either way |
| a workflow step it cannot reproduce | an unknown action, expression or condition refuses the whole run |
| merging past a ruleset that pins the check to the GitHub Actions app | a commit status cannot satisfy it; that is said, and the owner gets one inbox item with the ruleset change |

## Counsel: one module for every stronger-model call (P9, 2026-11-11)

The owner, 2026-11-11, verbatim: "Toate aceste piese de tip advisor trebuie combinate într-un singur model unificat
care să poată face totul, inclusiv ceea ce lipsește ... Unifică-le, nu le lăsa împrăștiate." Every "ask a stronger
model" path now lives in `fieldkit/lifecycle/counsel.py`, with one data section (`counsel:` in
`fieldkit/lifecycle/models.yaml`), one rung choice and one journal (`state/lifecycle/counsel.jsonl`). The paths are
tried cheapest first. A job parks only past the top rung, as before.

| Path (order) | What it does | Reused piece, now a thin call into counsel |
|---|---|---|
| 1. advise | Every headless Claude Code worker (`autopilot/run.claude`: task runbooks, `emerge` fix runs, goals, roles) starts with `--advisor <the next usable rung above its model>`. That is Claude Code's own advisor tool: a worker at a hard decision asks the stronger model in the same session, with no restart. If the CLI refuses the flag, the launch is retried once without it (journal `rejected`). Haiku is never an advisor (`counsel.advisor.never`). No usable rung above the worker means no advisor and no error. Off: `counsel.advisor.enabled: false` or `CLAUDE_CODE_DISABLE_ADVISOR_TOOL=1` Wired by I10 (2026-11-11) in `autopilot/run.claude`; the one other headless launch (`capture/recipes.py`, the naming call) goes through `counsel.run_advised`, the same add-retry-journal path. A lint test fails when a launch skips it. | new (`counsel.advisor_for`, `launch_args`, `run_advised`, `advisor_calls`) |
| 2. review | An independent reviewer on a higher rung answers `VERDICT: YES/NO`. It gets no tools and no advisor (`CLAUDE_CODE_DISABLE_ADVISOR_TOOL=1` is set even if a settings file carries `advisorModel`) | `autoaccept.pick_reviewer`, `ask_reviewer` (PR 450/453/455) |
| 3. tiebreak | The highest allowed rung settles a check-vs-reviewer deadlock | `autoaccept.pick_tiebreaker`, `ask_raw`, `tiebreak` (PR 452) |
| 4. climb | A job that has used up its attempts on one rung moves up one rung | `models.climb`, `models.next_model` (PR 449; called by `buildh/task.next_status`, `method/strikes`) |
| one rung choice | `counsel.pick(author, "next"/"top")` walks `escalation.ladder`. A rung counts only if it is available (`models.availability`, PR 454) and the spend guard allows it (`models.check_model`, which includes a mandate's `models_allowed`). All four paths use it; the advisor and the reviewer use the same `"next"` walk | `autoaccept._usable`, `_held_back`, `_rung` |
| one journal | One row per call `{at, path, author, model, outcome, why, job, calls}`. The advise row counts the worker's advisor calls from its stream-json log: each call is a `server_tool_use` block named `advisor`, checked live on 2026-11-11 | read by `fieldkit overhead counsel` and `fieldkit review` |

`tests/test_counsel.py` holds a lint test. It fails when a new `claude -p ... --model` call, or a new function that
picks a rung, appears outside counsel. The places that existed before are listed in the test, each with the reason
it may stay. Zero's orders and delegation (PR 342) only say who does which job. They never pick a stronger model, so
they are not part of counsel.

## The rest of Fieldkit

| Gate | Protects | Now |
|---|---|---|
| `fieldkit ideas` accept/forget, `fieldkit review` (`fieldkit/ideas.py`, `fieldkit/review.py`) | what goes into the plan | **owner**: only the owner sets the plan |
| `fieldkit goal approve` (`fieldkit/autopilot/goal.py`) | a goal's acceptance criteria | **owner**: the definition of done is the owner's |
| `fieldkit autopilot` publish (`fieldkit/autopilot/inbox.py`) | push and release | **owner**: push/release mandate |
| `mandate keygen/trust/approve/renew`, `install-hooks --yes` (`fieldkit/mandate/`) | the owner's signing key and the push mandate | **owner**: the key that every `--force` proof rests on |
| `GORILLA_OWNER_APPROVED` (push hooks, `fieldkit/core/privacy.py`, `mandate/hooks`) | pushes outside `fieldkit ship` | **owner**: the owner's own signature; hard line 1 |
| `record_decisions.py` | the owner's pref decisions | **owner**: decision register |
| hard lines 1-5 in `fieldkit/autopilot/hardlines.py` (`OWNER_ONLY`, credentials, re-arming GitHub, guards and hooks) | secrets, the push/release mandate, the guards themselves | **owner**. `build-harness accept/unblock/drop` stay in `OWNER_ONLY` for **models**: the harness accepts, a model does not |
| telemetry cut-outs | the THREAT MODEL | **owner**: never automated away |
| inbox items routed by `fieldkit inbox add` | anything only the owner may decide | **owner**; the hardline routes are unchanged |

## Turning it off

Set `auto_accept.enabled: false` in `fieldkit/lifecycle/autoaccept.yaml`, or point `FIELDKIT_AUTOACCEPT_POLICY` at a
copy with it off. Every gate above then waits for the owner as before (journal event `auto-wait-owner`).
