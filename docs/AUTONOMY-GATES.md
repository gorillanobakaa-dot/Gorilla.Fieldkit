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
| audit row `journal: no step skipped without a reason` (`fieldkit/buildh/audit.py`) | passes for a step whose LATEST decision is a skip with a reason: the owner's skip with `--note TEXT` (the note is journaled; a skip with no note is refused before the passphrase is asked), or an I-04 `auto-accept` of kind `skip` with a reviewer (its why is the reason). Distinct steps count, not repeats; a later retry undoes a skip. A bare skip still fails (`tests/test_buildh_no_owner_stall.py`, proven with `fieldkit mutate`) |
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

Without the grant in the owner's SIGNED file, behavior is as before. A model never signs the mandate and never types
an owner command: the draft goes to `state/mandates/owner-wide-owner-steps.yaml` and the one renew command to the
owner's inbox (typed yes + passphrase).

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
