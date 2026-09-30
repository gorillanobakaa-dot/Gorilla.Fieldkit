---
name: fieldkit-tools
description: Find out which of the owner's tools already exist for a job (icons, JSON/PowerShell validation, encoding repair, duplicate files, Firefox patch helpers, thermal watch, prompt audit and more), whether a tool is safe to probe, and scan files for secrets or personal data before anything is published. Use before writing a new script, before running an unfamiliar script, and before any commit, push or release.
---

# Tools registry and privacy scan

## Before writing a new script

```
fieldkit tools list [--harness desk|build|office|core] [--json]
fieldkit tools list --all        # plus every script gathered into toolbox/ (about 260)
```

GitHub repositories and local folders are gathered into `toolbox/<name>/` by
`fieldkit gather` (public rules in imports.yaml, your own in local/imports.yaml).
The public list includes searchfox-tools, code-review-toolkit, pfind, model-eval,
the dual-track generator and the debian-kernel tooling. `fieldkit gather --test` runs their own test
suites, and `fieldkit gather --check` shows where a copy differs from its source.
Change the source, not the toolbox copy: the toolbox is regenerated.

The list shows each tool's purpose, the systems it runs on, whether it changes things,
and whether it has a test. If a tool already does the job, use it. Tools marked
`changes` modify files or system state, so ask the owner before running one.

## Before running a script you have not used

```
fieldkit tools check [--id NAME]
```

The check is `probe-safe` only when the code has a `__main__` guard, uses argparse
and does no work at module level. A tool marked `DO NOT PROBE` runs its whole job
even when called with `--help` (on 2026-09-29 `organize.py --help` created folders).
Read the code of such a tool instead of running it.

`fieldkit tools check --run-tests` runs only the tests listed in the registry.

## Before any commit, push or release

```
fieldkit privacy scan REPO --git        # exactly what git would publish
```

It finds:
- tokens and API keys, including a token written into a URL;
- Windows and Linux user paths;
- email addresses;
- the private words listed in `fieldkit.local.json`.

Values are shown masked. Exit code 3 means there are findings; fix them before
publishing. A line holding a deliberate fake, such as a test fixture, can carry the
comment `privacy-scan: allow`.

## Machine facts

`fieldkit host --json` reports the system, the distribution and the CPU count.
