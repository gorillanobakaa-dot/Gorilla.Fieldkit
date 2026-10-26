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

## When you build a tool during a job (the owner's standing rule)

Anything written on the fly becomes a harness tool in the same session. It is never left as a loose script.
A tool is finished when it has all of these:
1. **One script with subcommands**: an argparse `__main__` guard and no work at import, with a README that
   says what was measured and what the limits are. Put it in `kits/<name>/` in this repo when it is generic
   and holds nothing personal (privacy scan first), otherwise in `local/kits/<name>/` (git-ignored). A script
   saved in `Documents/Scripts` during the session is only a scratch copy that survives the session: it is
   not finished until step 5 has moved it in.
2. **`doctor [--install]`**:
   - it probes the machine for every dependency: interpreter version, Python packages, external
     programs, and the program features used (for example ffmpeg filters);
   - it prints the exact install command for each missing one, and exits 1 while anything is missing;
   - `--install` fetches what can be fetched unattended (pip from PyPI, scoop or winget, apt with sudo);
   - it states what only the owner can do (browser extensions, sign-ins).
   Run `doctor` first on any machine. Say who supplies each install before installing.
3. **A card and a test**: `fieldkit/desk/tools.yaml` and `tests/` for a kit in this repo, or `local/tools.yaml`
   and `local/tests/` (git-ignored) for a private tool. The test runs the real tool on real or synthetic data
   and shows it failing as well as passing.
4. **A skill** in `skills/fieldkit-<job>/SKILL.md` holding the procedure: the order of steps, the decisions
   made by looking, what was measured, the traps hit, and what is refused or needs the owner. A private
   procedure (the owner's machines, their accounts) goes in `local/skills/` instead (git-ignored).
   Install either with `python install_skills.py`.
5. **Inside Fieldkit, not in Scripts.** A tool first saved in `Documents/Scripts` is moved in with
   `fieldkit adopt NAME --apply` (to `local/kits/`; it repoints cards, shortcuts, Startup, skills and memory, and
   rolls back if its tests fail). Private tests find tools through `local/tests/_owner.py`.
6. `fieldkit sweep --run-tests` exits 0, and `fieldkit privacy scan` is clean before anything public.

Examples, 2026-10-12: `kits/photokit` with fieldkit-property-inventory; `kits/videokit` with fieldkit-video-edit.

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

Then push with the tool, never with a hand-typed `git push` (the tool router refuses one in every clone the
owner-wide mandate covers):

```
fieldkit mandate doctor                 # mandate in force? days left, keys, every covered hook honoring the gate
fieldkit ship push --repo CLONE         # a feat/ fix/ release- night/ branch, fast-forward, through the gate
fieldkit ship push --tag vX --repo CLONE        # a new tag on a commit GitHub has
fieldkit ship gh-release vX --repo CLONE -- --title vX --notes-file NOTES.md   # CI green, tag on GitHub
```

Never hand the owner a GORILLA_OWNER_APPROVED line per push: that flag is the owner's own manual path for what the
mandate does not cover (deletes, moved tags, the decision register) or when no mandate is in force.

## Machine facts

`fieldkit host --json` reports the system, the distribution and the CPU count.
