# v0.8.1, for developers

## Scope

`v0.8.0..origin/main`, merged PRs #273, #274, #275 (plan items B1, P14 / idea I-11, emerge gorilla-opencode).

- Hardlines (#273, B1): a refusal prints the mandate's own reason. HARD LINE 1's refusal line stays byte for byte, as rules/proofs.yaml swaps that text; reasons go after it.
- CI OS jobs (#274, P14, I-11): `ci-wait`, `sync` and the mandate's CI proof read each repository's OS jobs from `fieldkit/method/ci_os_jobs.yaml`. `ship push --auto` counts red commits, not runs. The hard line asks the mandate for a full-path push (`cd DIR && /path/fieldkit ship push`, `python3.12 -m fieldkit`) and says when the mandate was not asked.
- emerge gorilla-opencode (#275): the build refuses a CRLF working tree and builds from a clean clone. The embed fetch runs the script with OUTDIR `.` in a temp cwd, because GNU tar read `C:/...` as host `C`; a fetch failure is listed first, not cut off after five "missing" lines. It fetches the pinned official-release toolbox helpers missing from a clean clone (7za, ffmpeg, ffprobe, cwebp, avifenc, oxipng), hash-checked. Gorilla OpenCode v0.1.158 built green in 7.2 min, 0 model calls.

## Not in this release

- The OS-jobs list was not run against a repository outside Fieldkit and Gorilla OpenCode.
- The helper fetch was verified on Windows only; Linux and macOS runs were not.
- No tag, no GitHub release: Zero tags after this PR merges.
