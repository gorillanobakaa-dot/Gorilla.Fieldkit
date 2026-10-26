---
name: fieldkit-scratch
description: Put test copies, fixtures, big transfers and probe output in the scratch root and delete them fast and safely with fieldkit scratch - one short folder (C:\fk-scratch on Windows, ~/.cache/fieldkit-scratch elsewhere) of marked job folders; clean deletes directly (not through the Recycle Bin) and only jobs that are yours, stale, or claimed; long paths, junctions never followed; report with sizes, ages, owners; the owner's one-click Defender exclusion for the root. Use before making a large test fixture or test copy, when a cleanup of many files is slow or the Recycle Bin refuses long paths, when cleaning up after a job, when the preflight or the hog reports stale scratch jobs, or when received transfers must go.
license: AGPL-3.0-or-later
compatibility: "Needs the fieldkit command (Gorilla Fieldkit, github.com/gorillanobakaa-dot/Gorilla.Fieldkit; Python 3.11+ with PyYAML and psutil) on Windows, Linux, macOS. Works offline; no network access."
---

# fieldkit scratch - the scratch root

```
fieldkit scratch new [JOB] --purpose "what" [--keep] [--print-path]   a marked job folder; prints its path
fieldkit scratch clean JOB [JOB ...] [--dry-run]                       delete your jobs
fieldkit scratch clean --mine | --stale                                every job of yours / every stale job
fieldkit scratch clean JOB --claim                                     a job whose owner is gone, or a received transfer (link-<tid>)
fieldkit scratch report [--no-size] [--json]                           jobs, sizes (fieldkit size), ages, owners, states
fieldkit scratch doctor                                                root, fieldkit size, Defender exclusion, stale jobs
fieldkit scratch owner-script                                          the owner's elevated exclusion script (never run it yourself)
fieldkit scratch bench --files 100000 [--recycle]                      time clean (and the Recycle Bin path) on N small files
```

## The rule (a deliberate exception to the recycle rule, inside the scratch root ONLY)

Everywhere else your leftovers go to the Recycle Bin with `fieldkit size recycle`. Inside the scratch root,
`fieldkit scratch clean` deletes permanently, and only:

- a direct child of the root with a valid marker `.fk-scratch.json` (schema 1, job = folder name, root = this root);
- that is **yours** (made under the same agent session process: pid + creation time), or **stale** (owner gone AND
  older than `stale_hours`, default 24, AND not made with `--keep`), or named with **`--claim`** (owner gone, or
  kind `link`: a transfer that arrived over the cable, a hand-over owned by no session).

Root and job must be real folders (a junction, symlink or reparse point is refused). Inside a job a link is removed
as a link, never followed. Long paths (`\\?\`) and read-only files are handled. Every delete is a line in
`state/scratch/clean.jsonl`. A folder without a marker is reported as UNMARKED and never deleted by this tool:
leave it and name it.

## Use it

1. Make the job first, then write into it: `D=$(fieldkit scratch new mytest --purpose "transfer test" --print-path)`.
2. Never execute anything from the scratch root: Defender does not scan it (the cost the owner accepted).
   Transfers into it verify their sha256.
3. Before the job ends: `fieldkit scratch clean --mine` and report it in one line (what, how many GB).
4. Received transfers (`peers link put` of a folder or big file) land in `C:\fk-scratch\link-<tid>\`; the receipt
   names the path. Clean a test copy with `fieldkit scratch clean link-<tid> --claim`.

## Long paths, everywhere

`fieldkit/core/longpath.py` is the one helper: `extended(p)` (Windows `\\?\`, up to 32,767 characters),
`remove_tree(p)` (the platform's standard direct delete, links never followed), `python -m fieldkit.core.longpath
--probe DIR` (the machine's state: LongPathsEnabled, git core.longpaths, a 400-character tree made and deleted).
New code that walks or deletes files uses it.

## The Defender exclusion and the other machine settings (owner approves once)

Security settings: no session adds them on its own. They are the mandate's `machine_prerequisites`
(`fieldkit/mandate/owner-wide.prereqs.yaml`: scratch-defender-exclusion, longpaths-enabled, git-longpaths), shown on
the renew screen and approved ONCE by the owner (`fieldkit mandate renew ... --from ...`, typed yes + passphrase).
Then `fieldkit mandate start` applies what is missing (`fieldkit mandate prereqs` to check): an administrator entry
shows one UAC prompt the first time, never again. By hand instead: `fieldkit scratch owner-script` writes
`state/proposed/scratch-exclusion-2026-10-26/` (apply.ps1, verify.ps1, undo.ps1, scratch-exclusion.ps1): one path
exclusion for the root, nothing else, verified, with undo; the owner runs it with one UAC click through
`elevate.py run`. `fieldkit scratch doctor` says PRESENT / NOT PRESENT / UNKNOWN (Get-MpPreference lists exclusions
only for an administrator; then it reads the proof the script wrote, `state/scratch/exclusion.json`).
