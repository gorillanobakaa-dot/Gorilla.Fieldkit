## What this is

Fieldkit v0.8.1: a refused push now says why and how to retry, the wait for CI looks at the right checks for each repository, and the Gorilla OpenCode build now starts from a clean copy of the source and fetches the helper programs it needs. Gorilla OpenCode v0.1.158 then built green in 7.2 minutes with no model call.

## Should you download it?

Yes, if you already run Fieldkit or build Gorilla OpenCode releases: it removes three reasons a job stopped and waited for you. Installing is unchanged (the install script, or `fieldkit update`). There is no separate program to download.

## Why this matters

A job that stops without saying why costs more than one that fails with a clear reason. Each of the three changes turns a silent stop or a wrong answer into a clear result.

---
# In plain language: everything in this release

This is the complete explanation, not a summary of one. Nothing below is behind a link.

<!-- plain-language track: in full, on this page -->

### Main goal: work that runs without you

**A refusal explains itself.** Fieldkit has a few actions it never lets a helper program do on its own, such as publishing code. When one is refused, the message now gives the reason the owner's standing permission states, instead of a bare "no" (B1).

**The wait for checks looks at the right checks.** After code is pushed, an automatic test service (CI) runs checks on Windows, Linux and sometimes more. Before, Fieldkit guessed which checks to wait for. Now each project has a short list of the checks that count, kept in one data file, and the waiting, syncing and permission proof all read that list (P14, idea I-11). Fieldkit also counts bad versions of the code, not bad runs, so one flaky rerun no longer looks like two failures.

**A push is asked for in full.** When the permission is checked, the command is now written with its complete location, and the refusal says plainly when the permission was never asked.

### Everyday problems fixed

**Gorilla OpenCode builds from a clean copy.** A build now refuses a working copy whose text files use Windows line endings, because that broke the build on one machine. It starts from a fresh clean copy of the source instead.

**Missing helper programs are fetched.** A clean copy lacks six small helper programs (for archives, video, and images). The build now downloads the exact official versions, checks each one against a known fingerprint, and goes on. Gorilla OpenCode v0.1.158 then built green in 7.2 minutes with no model call.

### Not in this release

Not checked on a real run: the new CI list on a repository other than Fieldkit and Gorilla OpenCode, and the helper download on Linux and macOS. No tag or release page is published by this change; that follows after it is merged.

---

# For developers: how it works, and how to check it

Written for someone who will audit, fork or change the code. It covers the same release as the plain-language part above; neither is a summary of the other.

<!-- developer track: in full, on this page -->

### Scope

`v0.8.0..origin/main`, merged PRs #273, #274, #275 (plan items B1, P14 / idea I-11, emerge gorilla-opencode).

- Hardlines (#273, B1): a refusal prints the mandate's own reason. HARD LINE 1's refusal line stays byte for byte, as rules/proofs.yaml swaps that text; reasons go after it.
- CI OS jobs (#274, P14, I-11): `ci-wait`, `sync` and the mandate's CI proof read each repository's OS jobs from `fieldkit/method/ci_os_jobs.yaml`. `ship push --auto` counts red commits, not runs. The hard line asks the mandate for a full-path push (`cd DIR && /path/fieldkit ship push`, `python3.12 -m fieldkit`) and says when the mandate was not asked.
- emerge gorilla-opencode (#275): the build refuses a CRLF working tree and builds from a clean clone. The embed fetch runs the script with OUTDIR `.` in a temp cwd, because GNU tar read `C:/...` as host `C`; a fetch failure is listed first, not cut off after five "missing" lines. It fetches the pinned official-release toolbox helpers missing from a clean clone (7za, ffmpeg, ffprobe, cwebp, avifenc, oxipng), hash-checked. Gorilla OpenCode v0.1.158 built green in 7.2 min, 0 model calls.

### Not in this release

- The OS-jobs list was not run against a repository outside Fieldkit and Gorilla OpenCode.
- The helper fetch was verified on Windows only; Linux and macOS runs were not.
- No tag, no GitHub release: Zero tags after this PR merges.
