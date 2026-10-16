## What this is

Fieldkit is a set of tested tools that let a small AI model, running on your own laptop, do real work safely: build
software and prove the result, keep backups, look after the laptop. This release is about one job above all:
building the Gorilla Unleashed browser (a hardened Firefox) on Linux, from Mozilla's source to an installed,
checked .deb, in a way the next model can repeat without guessing.

## Should you download it?

Yes, if you build or port Gorilla Unleashed, on Linux or Windows: the patch set can now be carried onto a new Firefox
release and proven, and the tools say what to do on the system you are on.

Yes, if you publish anything from a machine that also holds your browser: a new check refuses a push that would
upload bookmarks, history, cookies or saved passwords.

Yes, if you use Fieldkit v0.3.0: this is 81 changes later and replaces it.

No, if you only want the browser: download Gorilla Unleashed itself from its own release page.

## Why this matters

A model that has never seen a Firefox build will guess, and every guess costs time and money the people this is
built for do not have. Each fault found while building Firefox 158 on Linux is now a command that finds it again,
with a test that proves the command works, instead of a paragraph someone may not read.

---
# In plain language: everything in this release

This is the complete explanation, not a summary of one. Nothing below is behind a link.

<!-- plain-language track: in full, on this page -->

### Building the browser on Linux, start to finish

`fieldkit pipeline run firefox-linux` takes the browser's source, builds it, packs it as a Debian package and
installs it, checking each step before the next one starts. Before it builds anything it refuses to work on the
wrong folder, and it says which fonts, logos and graphics fixes must be in the build. After it installs, it checks
what a person sees: the name in the menu, the icon, and whether an old picture on the computer hides the new one.

### When something looks wrong

`fieldkit build-harness symptom "<what you see>"` takes a description in plain words, in English or Romanian, and
names the tool that was built for that problem: a dialog whose text is cut off, an old name or icon in the menu,
video that stutters, an add-on that refuses to install. Some of these are not faults at all: an add-on that will not
install is the browser's protection doing its job, and the tool says so.

`fieldkit build-harness clip-check` opens the browser's dialogs, measures every line of text in them, compares with
an unmodified Firefox, and names the exact style rule that cut a message short.

### Carrying the work to a new Firefox version

When Mozilla releases a new Firefox, many of Gorilla's changes no longer fit where they used to.
`fieldkit build-harness rebase-set` finds every change that no longer fits, writes its new form, and proves the whole
set: Mozilla's source plus Gorilla's changes must give exactly the browser that was built. It also checks, line by
line, that the idea of every old change made it into the new version, and lists anything that did not.

The same list of changes now serves Linux and Windows. The tools know which system they run on and leave out what
belongs only to the other one. `fieldkit build-harness platform-steps` prints what to do on the machine you are on.

### Publishing without leaking

`fieldkit privacy push-check` looks at exactly what a push would send and refuses it if it finds a password, a key,
a personal folder, or a browser's private files: bookmarks, history, cookies, saved passwords, open tabs.
`fieldkit privacy install-hook` makes it run by itself before every push.

### Nothing left unreleased

`fieldkit ship audit` says whether all the work has reached the main copy on GitHub and how long it has been since the
last release; every session starts with that answer. `fieldkit ship release` publishes a new version only when the
tests on GitHub passed, the version number is right and the notes are complete. This release, v0.4.0, came two days
and 81 changes after the last one because nothing asked; now something does.

### Proving the download, not just the build

`fieldkit build-harness release-roundtrip` removes the installed browser, downloads the published package from
GitHub the way anyone would, checks its fingerprint, installs that file and checks the result. A release counts only
when the file people download installs and works.

---

# For developers: how it works, and how to check it

Written for someone who will audit, fork or change the code. It covers the same release as the plain-language part above; neither is a summary of the other.

<!-- developer track: in full, on this page -->

### Scope

`v0.3.0..v0.4.0`: 81 commits (not counting merges), PRs #16 to #71. CI on PR #69 (head 0be1f14), the largest of them:
**ubuntu-latest 2269 passed, 92 skipped, 0 failed; windows-latest 2250 passed, 111 skipped, 0 failed.** Skips are
platform-gated (dpkg-deb, /proc, a .deb round trip, GNU patch, ffmpeg); none is a disabled test.

### build-harness (Firefox port)

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

### ship (releasing Fieldkit itself)

`ship audit [--offline]` (`method/shiprelease.py`): checkout on main, clean and equal to origin; remote branches whose
work main lacks (a merged pull request or changes that reverse-apply on main count as merged); open pull requests with
CI state; commits and days since the last `v*` tag, DUE past 20 commits or 7 days. The session-start brief prints the
offline rows. `ship release vX.Y.Z [--scaffold]` refuses until main, CI, version and notes agree, then pushes an
annotated tag and waits until the published page equals `page.md`. This release was cut with it.

### privacy

`privacy.profile_findings` (path, magic bytes, patch headers; never allow-listable), `privacy push-check REPO
--range A..B` (files read from the pushed commit), `privacy install-hook REPO` (`.git/hooks/pre-push`).
`EMAIL_OK` accepts `@mozilla.org` ids and `name@2x.png`; Windows' Default User folder is a placeholder, not a person.

### Measured on the Linux 158 port (2026-10-15/16)

77 patches failed on pristine Firefox 158 (107 with the cascade); after `rebase-set`, pristine 158 plus 681 patches
gives the built tree (same git tree hash). The superseded patches' logic: 2 functional losses and about 25 strings
found and ported. The published browser `v158.0-1` passed `release-roundtrip` 8/8 and its decision register 57
enforced, 0 violated.
