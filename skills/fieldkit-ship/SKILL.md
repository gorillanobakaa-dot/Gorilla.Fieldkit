---
name: fieldkit-ship
description: >-
  The one GitHub entry point, fieldkit ship: every step from opening a job to a published release with the evidence that proves it and how to recover, the refusals made before a call, the merge train, tags, releases and branch cleanup. Use before any commit, push, pull request, merge, tag, release or GitHub branch deletion, after a timeout or a red CI, and when ship or the tool router refuses.
license: AGPL-3.0-or-later
compatibility: "Needs the fieldkit command (Gorilla Fieldkit, github.com/gorillanobakaa-dot/Gorilla.Fieldkit; Python 3.11+ with PyYAML), git and gh on Windows, Linux. Needs network access to GitHub."
---

# GitHub, one cookbook (fieldkit ship)

This is the only GitHub procedure. Captured release and sync recipes (fieldkit/method/workflows/) are historical.

| Step | Do | Evidence it is done | Recovery |
|---|---|---|---|
| 1 Open the job | `fieldkit sync`, `fieldkit mandate status --repo .`, own worktree and branch | clean tree, right remote, mandate names the repository | dirty shared tree or wrong remote: stop, use a worktree |
| 2 Stage | `fieldkit privacy scan . --git`, `fieldkit integration check --repo . --changed main` | 0 findings; nothing unexpected staged | unstage what is not the job's |
| 3 Commit | `fieldkit testcost staged`, `fieldkit ship commit` (the hook runs) | the commit exists on the job's branch | interrupted: look for the commit before committing again |
| 4 Push and PR | `fieldkit ship push --auto` | remote head equals local head; the PR has this repository, base and head | PR create timed out: look the PR up before creating another |
| 5 Test and merge | `fieldkit ship ci-wait PR --merge` (background), or the merge train | both `test (ubuntu-latest)` and `test (windows-latest)` green on that head; PR reads MERGED | base moved: `fieldkit ship update`; runner down: authorized local CI or wait |
| 6 Finish | Board row, recycle your worktree; `fieldkit ship prune` | pruned branches logged in state/ship/deleted-branches.jsonl | restore a branch with the line in that log; no proof = kept |
| 7 Prepare a release | version in sync, `fieldkit release-page compose`, `fieldkit emerge`, `fieldkit ship release-ready vX` | release-ready passes; notes landed by PR | fix what it names; one release per OS per 24 h |
| 8 Tag | `fieldkit ship push --tag vX` | the peeled tag on GitHub is the intended commit | a different target: stop; a tag never moves |
| 9 Publish | `fieldkit ship gh-release vX -- --check-candidate FILE`, then `--candidate FILE` | read back the draft, then the published release | bad tag or artifact: a new corrective release |
| 10 Close | Board row with receipt, recycle scratch | the row says what was NOT verified | merged regression: a corrective PR |

Always: a failed API read records the operation and keeps the last confirmed identity. After a timeout on a write,
inspect the remote before retrying, and cap retries. Reuse a check only under the proof policy.

## Refused before the call
Raw `git push` and `gh pr merge`; `ship push` run from a checkout on another job's branch (tool router, override
`# toolrouter-ok: <why>`). `ship push` / `ship commit` on a stale index.lock; a rules proof anchor that no longer
occurs exactly once; auto-merge or PR merge on a base that does not require both OS checks.

## Shipping
```
fieldkit ship status        where the branch stands
fieldkit ship push          sync a squash-merged branch (merge -s ours, tree unchanged) and push; never forces
fieldkit ship push --tag vX a NEW tag on a commit GitHub has (or that this push carries); never moves a tag
fieldkit ship gh-release vX -- ARGS   gh release create --verify-tag, the repository from git, CI green
fieldkit ship gh-release vX -- --check-candidate FILE   validate the tagged policy and installer proofs with read-only access
fieldkit ship gh-release vX -- --candidate FILE   publish the verified candidate through the mandate
fieldkit overhead --fixture RECEIPT [--json]      measured release and repair fixtures from CI test receipts
fieldkit ship prune                        delete remote branches proven done by commit (logged, restore line); refused = DO row with git's words
fieldkit ship tags [--repo R | --all] [--fix-local]   every tag classed (release / version-no-release / archive / other), only real
                            candidates flagged (unreachable, duplicate, local-only); GitHub tags are never deleted by an agent: the
                            owner command is printed; --fix-local removes only local tags never pushed
fieldkit ship prune|audit|tags --all   every non-archived repo of the owner (local clone, else scratch), one before/after table
fieldkit strikes recount BRANCH   whose red: own / main / unproven per head (a failed test run on the merge base with main); a strike is only an own one
fieldkit mandate doctor     the owner-wide mandate: in force, days left, keys, every covered hook honoring the gate
```
Under the owner-wide mandate (owner, 2026-10-26) these ARE the owner's yes: every covered repository's pre-push hook
asks `fieldkit mandate gate`, which lets a push go without the owner flag only when it came through `fieldkit ship
push`, the mandate is in force and names the repository, every branch matches feat/ fix/ release- night/ and
fast-forwards under its own name, and every tag is new. A hand-typed `git push` in a covered clone is refused by the
tool router. Never hand the owner a GORILLA_OWNER_APPROVED line per push: that flag stays the owner's own manual path.
Then the pull request with the host's tools: open it, wait for CI (`fieldkit wait` or the host's events), merge only
when green (the mandate's merge_to_main: ci_green, by pull request). A repository the mandate does not name needs the
owner's yes again. A clone whose hook does not ask the gate: `fieldkit mandate install-hooks` (preview), `--yes`.
Expiry: 3 days before, one line in `mandate status` / `mandate start` / the prompt hook; the owner runs `fieldkit
mandate renew FILE.yaml` (the approve screen, typed yes + passphrase).
The renewed pair reaches the other computer by itself: the renewal runs `fieldkit mandate share`, whose files land
there in the scratch root (`link-*/NAME-N.yaml`, `NAME-M.seal`; link.log says where) and whose link verb
mandate-receive (cable only) runs `fieldkit mandate receive FILE.yaml FILE.seal` there. It installs ONLY a pair the
owner key already pinned on that computer verifies (any changed byte, other key, stop or expiry is refused), never an
older approval over a newer one; the old pair goes to ~/Agents.Work.Trash. A far listener without that verb gets a
note with the command.
Windows, Linux and macOS alike (2026-10-26). The protected trust store: %ProgramData%\Fieldkit\mandate-trust (UAC),
/etc/fieldkit/mandate-trust (pkexec), /Library/Application Support/Fieldkit/mandate-trust (sudo). On a Mac without
Apple's developer tools /usr/bin/git is a stub and is never run (the doctor says "no clone here"); the lean set has no
`cryptography`, so a mandate there is NOT in force until the owner installs it.

### The merge train (parallel PRs, 2026-10-26)
```
fieldkit ship overlap             before work starts and at PR open: open PRs / live stations on the same files
fieldkit ship update              main moved: merge it the train's way (generated files regenerated, cards entry by
                                  entry, proven only a merge); the hook then runs only the affected tests
fieldkit ship ready PR            the SESSION says the PR is final: refused while anything is unpushed, uncommitted
                                  or still in the pre-commit hook. One more commit coming: `ready PR --pending TEXT`
fieldkit ship train --dry-run     Zero: the order it would merge in, what each PR needs, stale / orphan flags
fieldkit ship train               Zero: merge the declared PRs one at a time (gh pr merge --merge
                                  --match-head-commit), never red, never a head nobody declared; resumable
fieldkit ship freeze --reason T   a checkout that must not move; `unfreeze` = ff + `fieldkit parity baseline --set`
fieldkit ship train --modes       every failure mode this closes, with its check
fieldkit cmd check TEXT|--file F   a command is checked before it is given: every fieldkit verb looked up in the live command tree (exit 3 + did-you-mean)
fieldkit ship batch run           many ready PRs, one branch (night/batch-STAMP), one CI, one merge (SOP for 100 machines):
fieldkit ship batch before-pr --branches BRANCH ...  combine local changes first and open only the protected batch PR
                                  `batch collect` takes only PRs green on every required check at their head; `build`
                                  joins them (a generated-file conflict is regenerated, a real one leaves that PR out);
                                  red -> the culprit from the failing test names (infra re-runs, never bisects), dropped
                                  with its log + brief, the rest re-run. `batch status|select`. Tests are sharded per OS.
fieldkit ship ci-wait PR [--merge]   one PR: run it in the BACKGROUND, one wake when CI ends (green, or red with
                                  the failed log tail); --merge merges on green and checks the PR reads MERGED.
                                  A red that is the runner's own outage (DNS, apt fetch, network: signatures in
                                  fieldkit/build/signatures/ci-infra.yaml, no failed test) is re-run by itself with
                                  backoff, 3 times per head, journaled, never a strike (state INFRA when it gives up)
```
Never `gh pr checks --watch && gh pr merge` or an `until gh pr checks ...; sleep` loop by hand (the tool router
refuses both): the exit code of a watch is not the CI of the head (a re-run still running read as green).
Never `git merge origin/main` by hand on a PR branch when `ship update` can do it.
