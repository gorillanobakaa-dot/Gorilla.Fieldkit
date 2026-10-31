# Where things live

One page that says where each kind of plan, idea, hand-over and brief is kept, so nobody has to search for it. Paths are
relative to the repository root. `state/` and `_private/` are git-ignored: they exist on the machine that made them
and are not published.

## Find it

| You are looking for | Where it is | In git | How to read it |
|---|---|---|---|
| Where the harness is going | `docs/VISION.md` | yes | open it |
| What is left to do (the to-do list) | `docs/HARNESS-PLAN.md` | yes | `fieldkit plan next` says the next step |
| A suggestion made while a job runs | `state/ideas/` (`ideas.json` is the store, `TODO.Features.Plan.md` is its readable copy) | no | `fieldkit ideas list`, `fieldkit review` |
| What waits for the owner | the inbox | no | `fieldkit review` (answers it; page `state/REVIEW.md`), `fieldkit inbox --all` |
| What each session did | the Board, `state/coordination/PLAN.md` | no | one row per session, with its id |
| Where the last session stopped | `state/coordination/HANDOFF.md` | no | read it first in a new session |
| Briefs sent to jobs | `state/briefs/`, `state/tasks-briefs/`, `state/coordination/BRIEF-*.md` | no | one file per job |
| How work gets done, as tools | `docs/METHODS.md` | yes | the numbered methods |
| Lessons turned into tools | `docs/LESSONS.yaml` | yes | open it |
| Findings and measurements | `docs/findings/` | yes | one file per finding |
| Rules for agents | `rules/CORE.md`, `AGENTS.md`; the long version is `docs/AGENTS-REFERENCE.md` | yes | `CORE.md` first |
| What a release contains | `releases/notes/vX.Y.Z/` | yes | `page.md` is the page |
| Closed briefs and hand-overs | `state/archive/` | no | nothing reads from it; it is only history |

## Where a new thing goes

1. A task that must be done goes to `docs/HARNESS-PLAN.md`, as one row, on its own branch.
2. A new suggestion while a job or plan runs goes to `fieldkit ideas add TITLE --why W`. It is never added to the
   running plan; the owner decides at `fieldkit review`.
3. Something only the owner may decide goes to `fieldkit inbox add TITLE WHY [COMMAND]`.
4. What a session did goes to its Board row in `state/coordination/PLAN.md`; if work continues, to `HANDOFF.md`.
5. Do not start a new status, to-do or ideas file anywhere else. If a new kind of list is needed, add its row to the
   table above first, in the same change.

## Closing a job

Once a job's pull request is merged, its brief is history. Move it to `state/archive/`; never delete it. The code reads
only `PLAN.md` and `NET.yaml` from `state/coordination/`, so the dated hand-overs (`rotate-*.md`, `ZERO-HANDOVER-*`,
`COMMAND-POST-HANDOVER-*`) and the `BRIEF-*` files can be archived once their work is merged.
