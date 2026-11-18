# The Firefox cookbook

One procedure, from "Mozilla released Firefox X" to "Gorilla Unleashed X is released on Linux, Windows and the Mac".
It is the same for every version. Owner order, 2026-11-16 (P38): every Firefox step, command and lesson lives in one
place.

**In short:** run `fieldkit firefox status`, then `fieldkit firefox next`, and do what the `NEXT:` line says. Repeat
until every step reads done.

## Why this exists

The Firefox 160 night failed several times, and every failure was at a seam between two tools, never inside one.
Each tool had its own idea of the same thing. For example, two tools disagreed on what a "closed" step is, a tool
assumed the base branch was `main` when it was `master`, and a recipe kept the 158 source commit for the 160 build.
Patch groups were written and never registered. Five days of patch work were never committed. Lessons were written
down and never became rules. Now:

- every shared idea has one definition, in `fieldkit/core/concepts.py`, and every tool uses it;
- a test runs a toy Firefox through the whole chain on every pull request (`tests/test_firefox_cookbook_e2e.py`), and
  `tests/firefox_cookbook_mutations.yaml` breaks each seam to prove that the test catches it;
- every lesson becomes a rule, a tool or a recorded "no rule, because..."
  (`fieldkit/firefox/lesson_rules.yaml`), and the S9 gate does not close a migration while a lesson waits;
- the handover to Windows and the Mac is a tool, not a brief written by hand.

## The three systems

| system | what | device | how it is built |
|---|---|---|---|
| Linux | the VAIO-native .deb (`-march=native -O3` on purpose, for VAIO owners) | VAIO | `fieldkit build-harness` migration S0-S9, recipe `firefox-debian` |
| Windows | the build for everyone else | ThinkPad L15 | recipe `firefox-windows` and the Gorilla.firefox harness on the L15 |
| macOS 10.13 | Gorilla Unleashed for old Intel Macs (MacBook Air 2011) | cross-built on Linux, SSH to the Mac | `fieldkit macair` (skill fieldkit-macair-browser) |

All three use the same name: **Gorilla Unleashed** followed by the version number (`concepts.product_name`).

## The steps

The data is `fieldkit/firefox/cookbook.yaml`. `fieldkit firefox release VERSION` prints this list with the
commands filled in. `TASK` is `firefox-MAJOR-linux`.

| step | system | what | command | gate |
|---|---|---|---|---|
| S0 | Linux | preflight: inventory, previous release audit, machine ready | `fieldkit build-harness migrate seed TASK` | S0 |
| S1 | Linux | upstream intake: every new pref, host, module and about: page gets a disposition | `fieldkit build-harness migrate intake TASK` | S1 |
| S2 | Linux | mechanical apply of the patch set, in policy order | `fieldkit build-harness next TASK` | S2 |
| S3 | Linux | intent port: rebase-set and port steps; the PORT RULES from `lesson_rules.yaml` come with every step | `fieldkit build-harness rebase-set TASK` | S3 (closed step: `concepts.open_steps`) |
| S4 | Linux | tree verification | `fieldkit build-harness verify TASK` | S4 |
| S5 | Linux | build, build-verify, export-hand, register the new groups, replay; the set is committed and pushed | `fieldkit emerge run firefox-debian` | S5.build, S5.packaging, S5.replay, S5.published |
| S6 | Linux | install and proof | `fieldkit build-harness install TASK` | S6 |
| S7 | Linux | leak gate | `fieldkit build-harness leakgate TASK --release` | S7 |
| S8 | Linux | claims and consistency | `fieldkit build-harness claims TASK --strict` | S8 |
| H-win | Windows | handover package to the L15 | `fieldkit firefox handover VERSION --to windows --send` | package checks |
| B-win | Windows | build from the published set; the source base is the upstream release tag of the version (`base: upstream`) | `fieldkit emerge run firefox-windows` | emerge |
| H-mac | Mac | handover package to the Mac side (scp over SSH) | `fieldkit firefox handover VERSION --to mac --send` | package checks |
| P-mac | Mac | Check the patched Mac source against product decisions | `fieldkit macair preflight` | macair preflight |
| B-mac | Mac | Cross-build the macOS application | `fieldkit macair recreate` | macair recreate |
| C-mac | Mac | Check the staged ZIP against the Mac product decisions | `fieldkit macair app-check ZIP` | macair app-check |
| I-mac | Mac | Install the checked ZIP over SSH | `fieldkit macair install ZIP` | `fieldkit macair verify` |
| S9 | Linux | publish: export-hand, replay equals the built tree, every lesson ruled, privacy, the owner's yes | `fieldkit build-harness export-hand TASK` | S9 (S9.lessons) |
| REL | all | one release for the batch, with the plain-language page first | `fieldkit ship gh-release TAG -- --candidate FILE` | complete installer matrix and verified draft |

## The handover

`fieldkit firefox handover VERSION --to windows|mac` builds `state/firefox/handover/VERSION-OS/`:

- `manifest.json`: the patch-set commit and the remote branch that has it, the base branch (from `origin/HEAD`), the
  upstream tag and commit, the last replay proof of the Linux task, the lessons and their rules, and the steps for
  that system;
- `patches/`: the groups that apply on that system, in policy order. The group names follow the `.linux`,
  `.windows` and `.mac` suffixes, and each group's `exclude_on` is applied;
- `APPLY.txt`: the one apply step (check out the upstream tag, then `git apply` each patch in order);
- `SHA256SUMS`.

The package is checked before anything is sent. A dirty or unpushed patch set, no passing replay proof, a lesson
that is not yet a rule, or an unregistered patch group fails the check: the command exits 3 and sends nothing.
`--send` puts the package on the L15 over the cable (`peers link put` and a note) or copies it to the Mac with
`scp macair:` and compares the SHA-256 there. The Mac is reached by SSH only, never over the cable.

## One definition per concept (`fieldkit/core/concepts.py`)

| concept | function | used by |
|---|---|---|
| closed step | `closed_step`, `open_steps` | rebase-set, the S3 gate |
| base branch of a repository | `default_branch` (origin/HEAD) | `fieldkit ship`, mergeproof, the handover |
| which device a stage runs on | `measured_on` | the emerge preflight |
| upstream tag and commit | `upstream_tag`, `upstream_ref` | recipes (`base: upstream`), the emerge lint, the handover |
| platform groups | `PLATFORMS`, `platform_of_group`, `excluded_on`, `group_applies` | the handover |
| product name | `product_name` | `fieldkit firefox`, the handover |

## Where the rest of the knowledge lives

`fieldkit firefox knowledge` prints this list. This cookbook is the procedure, and the places below are its sources.
None of them is a second procedure.

- `docs/THE-BUILD-HARNESS-EXPLAINED.md`: how the build-harness works inside.
- `fieldkit/buildh/probes/about-pages.js`: which about: pages are kept and which are cut.
- `fieldkit/firefox/lesson_rules.yaml`: every port lesson, as a tool, a port rule or a no-rule.
- `state/build-harness/TASK/` (`journal.jsonl`, `lessons.jsonl`): what each migration did. This folder is git-ignored.
- gorilla-firefox: `config/patch_policy.json` and `patches/*/README.md` (the patch set and its order),
  `intents/` (why each patch exists), `decisions/PRODUCT-DECISIONS.yaml` (the owner's decisions), and
  `macos-legacy/recreate-mac.sh` (the public Mac recipe).
- The L15: Gorilla.firefox (`harness/gorilla_build.py`, `config/versions.lock.json`, `mozconfig.win64`).
- The skills fieldkit-build, fieldkit-emerge and fieldkit-macair-browser point here.

## A new lesson

When a port step says `lesson: ...`, first check whether it belongs to an existing family in `lesson_rules.yaml`.
If it matches none, add an entry of one of three kinds:

- `tool`, with the tool and the test that proves it;
- `port-rule`, a sentence every port step is given;
- `no-rule`, with the reason.

`fieldkit firefox lessons TASK` exits 3 while a lesson waits.
