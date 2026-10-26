---
name: fieldkit-emerge
description: Build, compile or package a product the way Gentoo's Portage does it, and better, with fieldkit emerge - one recipe per product (Gorilla Firefox for Windows and Debian, the Debian kernel, a Gorilla OpenCode release, gorilla-size), every stage content-addressed and skipped when its inputs were already built, stages for the other OS offloaded to the other machine over the cable, the leak gate once per release, a model called only when a stage fails and its fix accepted only as a commit, the owner's inbox past the limit, an ETA and an overhead number per run. Use when asked to emerge, build, compile, rebuild or package something, to make it "like portage", to resume a failed build, before running a build pipeline or a release by hand, or when a build takes days instead of the 90 minutes it should.
license: AGPL-3.0-or-later
compatibility: "Needs the fieldkit command (Gorilla Fieldkit, github.com/gorillanobakaa-dot/Gorilla.Fieldkit; Python 3.11+ with PyYAML) and git, on Windows, Linux, macOS. No network except what a recipe's own fetch stage pulls by pin; offload uses the household cable (fieldkit peers link)."
---

# fieldkit emerge - recipe once, a plain runner, a model only on failure

The AI is NOT the driver. Do not run build stages by hand, do not poll, do not re-run checks the runner skipped.
Start the run, wait for it to end (background + the completion notice), read the report.

```
fieldkit emerge list                                  the recipes
fieldkit emerge plan RECIPE [--mode M]                the dry run: what runs, what is cached, ETA, preflight
fieldkit emerge run RECIPE [--mode iterate|install|release] [--only S] [--var k=v] [--no-fix] [--local]
fieldkit emerge status RECIPE                         last report + overhead
fieldkit emerge manifest RECIPE [--write]             pin file sources by sha256 (never moves a recorded pin)
fieldkit emerge export RECIPE --out F / repro RECIPE --with F    same key on two machines = same bytes?
```

## What it does, in order

1. **Preflight once**, before stage 1: pins (a file by sha256, a git tree by commit or by its pinned base under our
   commits), disk, RAM, long paths, the thermal cap, the mandate (for offload), then the one run guard (hog,
   testcost, scratch, parity). A STOP refuses before anything runs; it never stops mid-run.
2. **Each stage has a key**: the stage's template text, the non-path vars, the pins, the patch set, the config
   (mandate entry, decision register, config files, the owner's test tiers), toolchain versions, the declared input
   files, and the keys of the stages it `needs`. A key already built whose outputs still have their sha256 is
   SKIPPED: compiles, packages, tests and gates alike. Machine paths are not in the key, so the same inputs on the
   VAIO give the same key; different bytes for the same key are reported (REPRO DIFF).
3. **Tiers** (owner-approved, test cost audit 2026-10-26): `iterate` builds; `install` adds package checks and
   post-install on the installed build; `release` adds the leak gate, once, on the final build. A docs-only change
   runs only the `privacy: true` stages.
4. **Offload**: a stage for the other OS (or `prefer: true`) goes to the device in `offload:` as a run job; stages
   whose needs are met are sent first, so both machines work at once.
5. **On failure**: one autopilot model run with the log tail and the recipe, per attempt (`max_fix`, default 2).
   The fix counts only as a commit on a `fix/emerge-...` branch (recipe or patch set): an uncommitted edit, a commit
   on main or no change stops the run. Pins, offload and `fix: false` stages (the leak gate) never go to a model.
   Past the limit: ONE `fieldkit inbox` item, the run stops cleanly with its report.
6. **Report**: one line per stage with the ETA, `state/emerge/<recipe>/last-report.json`, `overhead.json` (fieldkit
   overhead's shape: wall vs bare, buckets, ratio, the pre-AI baseline) and `journal.jsonl`.

## Writing a recipe

`fieldkit/emerge/recipes/<name>.yaml`: either `pipeline: <fieldkit/build/pipelines name>` plus an overlay per
stage, or `stages:` inline in the pipeline schema. Overlay keys: `inputs`, `outputs`, `needs`, `uses` (which of
sources/patches/config/toolchain/inputs feed the key; `[]` = only its needs), `tier`, `offload`, `always_run`,
`advisory`, `fix`, `max_fix`, `privacy`, `bucket` (bare/tests/tools for the overhead). Declare narrow inputs: a
README change must rebuild nothing. Prove a new recipe with `plan` and one real `run` of its smallest form.

## What it never does

Ask the owner anything (the inbox only), call a model on a green run, change a pin, move main, push.
