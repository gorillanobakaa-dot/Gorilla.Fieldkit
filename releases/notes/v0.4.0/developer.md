# Fieldkit v0.4.0, for developers

## Scope

`v0.3.0..v0.4.0`: 81 commits (not counting merges), PRs #16 to #71. CI on PR #69 (head 0be1f14), the largest of them:
**ubuntu-latest 2269 passed, 92 skipped, 0 failed; windows-latest 2250 passed, 111 skipped, 0 failed.** Skips are
platform-gated (dpkg-deb, /proc, a .deb round trip, GNU patch, ffmpeg); none is a disabled test.

## build-harness (Firefox port)

| command | what it does | module |
|---|---|---|
| `pipeline run firefox-linux` | preflight, bootstrap, build, package, install; `src` from `fieldkit.local.json` | `build/pipelines/firefox-linux.yaml`, `build/firefox_linux.py`, `build/firefox_preflight.py` |
| `rebase-set TASK [write=1]` | supersede patches that fail on pristine `<major>` (`superseded_<major>`), write `23.REBASE.<major>[.<platform>]`, replay; `carried()` and `fluent_moves()` judge each superseded patch | `buildh/rebase.py` |
| `export-hand`, `check-change` | export takes every commit above the pristine root; check-change runs export, rebase-set, replay; `patchset_owner` finds the patch-set repo without the Windows script | `buildh/export.py`, `buildh/ownercheck.py`, `buildh/buildrun.py` |
| `platform-steps TASK` | per-OS steps computed from the machine; the session brief prints open actions | `buildh/platformsteps.py` |
| `symptom "<words>"` | words -> tool registry, tested on the owner's own phrasing | `buildh/symptoms.py` |
| `clip-check TASK` | dialog text measured at `SubDialog.resizeVertically` and settled; `InspectorUtils.getMatchingCSSRules` + `getRuleLine` name the rule | `buildh/clipcheck.py`, `buildh/probes/clipped-text.js` |
| `release-roundtrip TASK tag=` | GitHub API (no login), SHA256SUMS, `deb_check`, `apt-get remove`, install the downloaded file, `verify_install` | `build/release_roundtrip.py` |
| `lessons TASK [--all]` | a lesson is waiting until `lessonrules.RULES` names its tool and an existing test | `buildh/lessonrules.py` |
| `vault restore-files` | files back to the pristine release, the vault verified first | `buildh/vault.py` |
| `decode-test`, `pref-bisect` | GPU video engine proof; pref bisection with a confirmed, alternating control | `build/decode_test.py`, `build/prefbisect.py` |
| `profile-check` | which profile a launcher starts, and whether its new tab paints | `buildh/profilecheck.py` |

Patch policy: `firefox.for_platform` applies `platforms` and `exclude_on` for `this_platform()`; every reader of
`config/patch_policy.json` goes through `_policy()`. `replay` applies with `--binary` (Strawberry patch 2.5.9).
`task.exclude_build_output` keeps `build_logs/` out of checkpoints.

## ship (releasing Fieldkit itself)

`ship audit [--offline]` (`method/shiprelease.py`): checkout on main, clean and equal to origin; remote branches whose
work main lacks (a merged pull request or changes that reverse-apply on main count as merged); open pull requests with
CI state; commits and days since the last `v*` tag, DUE past 20 commits or 7 days. The session-start brief prints the
offline rows. `ship release vX.Y.Z [--scaffold]` refuses until main, CI, version and notes agree, then pushes an
annotated tag and waits until the published page equals `page.md`. This release was cut with it.

## privacy

`privacy.profile_findings` (path, magic bytes, patch headers; never allow-listable), `privacy push-check REPO
--range A..B` (files read from the pushed commit), `privacy install-hook REPO` (`.git/hooks/pre-push`).
`EMAIL_OK` accepts `@mozilla.org` ids and `name@2x.png`; Windows' Default User folder is a placeholder, not a person.

## Measured on the Linux 158 port (2026-10-15/16)

77 patches failed on pristine Firefox 158 (107 with the cascade); after `rebase-set`, pristine 158 plus 681 patches
gives the built tree (same git tree hash). The superseded patches' logic: 2 functional losses and about 25 strings
found and ported. The published browser `v158.0-1` passed `release-roundtrip` 8/8 and its decision register 57
enforced, 0 violated.
