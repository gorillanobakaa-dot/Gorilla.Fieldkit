# Firefox migration runbook (P29)

The order a new Firefox version (161, 162, ...) goes through, so the next migration starts from this list rather than
from memory. Target (P29): `fieldkit overhead` ratio under 1.5x, idle under 1 hour. Firefox 160 took 10.1x (38 minutes
of bare build, 6 h 18 min on the clock, 4 h 36 min idle).

## 1. Port the patch set

Port the groups to the new tag in Gorilla.firefox. Each group's `exclude_on` / `carry_on` reasons go in
`config/patch_policy.json`. A patch carried on a system it does not build for needs
`carry_on.<system>."<patch>": "<reason>"`. A blank reason fails the set proof.

## 2. Preflight before any compile

```
fieldkit build-harness migrate preflight harness=<Gorilla.firefox> [rev=REV] [root=REV] [skip=a,b]
```

One verdict and one NEXT line:

| Check | Catches | 160 evidence |
|---|---|---|
| static | a lazy getter whose module is gone; a Setting used but never registered; a data-l10n-id with no linked .ftl; an empty support baseURL with an unguarded `new URL` | tabs-browsing.mjs lazy.GenAI, linkPreviewEnabled, highlight-to-search-settings-*, moz-support-link |
| policy | a group reason naming a foreign patch it does not declare (CONTRADICTION); a carry_on with no reason (STALE) | linux 23.REBASE.160/0122 |
| set-proof | pristine + each system's selection applies, foreign patches declared, code dependencies, pref conflicts, undeclared lazy | 21.PORT.FIXES.160/006 |
| stage-key | the source stage's key includes the patch set, so a new patch re-runs it | source SKIP with a new patch |
| stall | the replay, snapshot and task patch children run under core/childrun (deadline, stall stop, no terminal) | 67 min patch.exe hang |

The verdict sets the exit code: PASS 0, FAIL 3 (do not compile), INCOMPLETE 1 (a check was skipped or could not run).
The firefox-windows recipe runs the preflight as stage `migrate-preflight` between `source` and `bootstrap`, so a FAIL
stops the run before the compile.

## 3. Build, unattended

```
fieldkit emerge plan firefox-windows
fieldkit emerge run firefox-windows --mode install
```

## 4. Steps that still need the owner

- UAC: handled unattended (PRs 567 and 571, skill fieldkit-admin-unattended).
- The mandate door: one approve for the build-harness plan (`harness-task`, `fix: false`), through the morning inbox.
- `mandate renew` when the mandate expires: one inbox item.
- A leak-gate policy FAIL (allowlists are a hard line): through the inbox.

## 5. Linux

The Linux pipeline (`fieldkit/build/firefox_linux.py`) applies the patches without committing them. A preflight at
HEAD sees the pristine tree, so wiring it into the Linux pipeline needs a working-tree mode (a follow-up). The CLI runs
on Linux against a committed tree.

## 6. After the run

Use `fieldkit overhead` for the ratio and where the time went. Add a triage signature for every new failure (rule 5),
so it is never diagnosed twice.
