---
name: fieldkit-size
description: How big folders are, where the disk space went, and what can go, on Windows or Linux, with gorilla-size (Gorilla Size Calculation) through `fieldkit size` - every core, read-only, junctions and symlinks never followed, hard links counted once; find files or folders by size, age, name or extension; compare two measurements; on Windows read a whole NTFS drive from its master file table (--mft, one UAC click). Use whenever you need the size of a folder or drive, the biggest folders or files, "disk full", "what takes the space", before and after a cleanup, before deleting build trees, or when about to type du, dir /s, robocopy /L, Get-ChildItem -Recurse | Measure-Object, or ncdu.
license: AGPL-3.0-or-later
compatibility: "Needs the fieldkit command (Gorilla Fieldkit, github.com/gorillanobakaa-dot/Gorilla.Fieldkit; Python 3.11+ with PyYAML). Works offline; no network access unless a step says so."
---

# Folder sizes (fieldkit size)

**Never measure sizes with an improvised command.** 2026-10-16: `du` ran 25 minutes on one Firefox tree of 480,000
files and was stopped; a hand-typed robocopy line then got the numbers, but it was no tool. gorilla-size measured the
same trees in 0.4 to 11.5 seconds with the same totals.

| The question | Run |
|---|---|
| is it here? | `fieldkit size doctor` (MISSING: `fieldkit gather --only gorilla-size`, then `fieldkit size doctor --install`) |
| what takes the space here | `fieldkit size <folder>` |
| deeper, plus the biggest files | `fieldkit size <folder> -d 3 -f 10` |
| several folders at once | `fieldkit size <a> <b> <c> --json` (one entry per folder in `roots`) |
| the folders holding the space | `fieldkit size find <root> --type d --min-size 5G` |
| old big files | `fieldkit size find <root> --type f --min-size 1G --older 1y` |
| by name or extension | `fieldkit size find <root> --name "*.iso" --ext zip,7z` |
| before / after a cleanup | `fieldkit size <root> -d 3 --json > before.json`, the cleanup, again `> after.json`, then `fieldkit size compare before.json after.json` |
| a whole Windows drive, fast | `fieldkit size --mft C:\` needs administrator: run it through the elevate tool (skill fieldkit-admin-unattended): one UAC click for the owner |

## Read the report honestly

- **links not followed**: junctions/symlinks/mount points are listed, never counted. `Gorilla.firefox\src` is a
  junction to `Build.Work\firefox\157.0-truth`: the tree exists once.
- **hard links**: Linux always counts a file once. Windows says `NOT checked` unless `--links` (or `--mft`) was given.
  The seen-set is per PATH given: a file linked between two PATHs of one run is counted in both. Measured 2026-10-16:
  the Firefox vault and its working copy share no file (their git packs are separate files).
- **what a deletion frees** is what actually reached the Recycle Bin, checked there: 2026-10-16 a folder sent "to the
  Recycle Bin" with confirmations suppressed was deleted permanently instead (Windows does that silently for items it
  cannot recycle). Never report freed space from the sizes alone.
- **apparent vs disk**: the default is what the files hold; `--disk` is what they occupy.
- **unreadable N**: entries that could not be read; `--list-errors` names them. Say so in the answer when N > 0.
- Units are KiB/MiB/GiB (1024). Do not relabel them as decimal GB.

## Before removing anything: what cites it, never its date

2026-10-16: 139 GiB of leak-gate runs were cleaned up "keeping the latest". Chosen by date, the batch held the run the
leak-gate baseline came from, the only PASS (cited by the release notes), runs cited by the claims audit and
allow.json, and the capture behind the Widevine exception, which was deleted for good. Three were put back from
the bin in time.
- Leak-gate runs: `fieldkit build-harness leakgate-runs TASK` lists every run, what cites it, and which are FREE.
  Remove only those.
- Anything else: search what names it (stamps, paths) in the owner repo, the decision register, release notes,
  claims and the task's journal before proposing it for removal; show the citations, not just the size.
- Empty the bin only after that check: `gorilla-trash empty` deletes for good.

## Rules

- It is read-only. Removal stays with the person (move to the Recycle Bin; they empty it).
- `--json` for anything you will read back or compare; text for the person.
- A size you report must come from this tool's output in this session, with the path and the mode (apparent/disk,
  hard links checked or not).
