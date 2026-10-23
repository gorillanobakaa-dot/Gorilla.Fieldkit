---
name: fieldkit-macair-browser
description: Build, check, ship and verify Gorilla Unleashed for the MacBook Air and other old Intel Macs (macOS 10.13 High Sierra), the same way every time - status, preflight of every Mac decision in the source before a two-hour build, the 11-inch screen budget, exporting the Mac patch series, the public one-click recreate script, installing on the Mac over SSH and verifying there by measurement. Use when a Mac build is due, when a Mac fix may have gone missing, when something does not fit the Air's 1366x768 screen, when text looks thick on the Mac, or before publishing the Mac release.
---

# Gorilla for the MacBook Air

The MacBook Air 4,1 (2011, i5-2467M Sandy Bridge, Intel HD 3000, 4 GB, macOS 10.13.6, 1366x768) became a maintained
target on 2026-10-22. Mozilla dropped 10.13; the base is Momiji v157.0-mj (a Firefox kept alive for old macOS), so
the Mac has its own small patch series on top of Momiji: the one recorded exception to "one patch set".

## Testing a released program on the Air: as a new user, from the release page

Never test a build copied onto the Mac by hand when the question is "does the release work". Owner, 2026-10-23:
take the program from the GitHub release page like a person anywhere on the planet, and leave no old version
installed. One command does exactly that (older copies moved to `~/Agents.Work.Trash-linux/`, with a manifest;
download from the release page only; checksum; the release's own installer; version and single-copy check):

```bash
fieldkit mac release-install macair --tag latest --launch
```

`--plan` prints the script first. It does not exercise Gatekeeper's first-open prompt (curl over SSH sets no
quarantine flag): a report must say so.

"Publish" from the owner means the full cycle: commit, push, PR, merge into main, every package on the release page,
checked there (~/.agents/SETTLED.md, 2026-10-23). A pushed branch with an open PR is not published.

## Start every session here

```
fieldkit macair status        the recreate run, the Mac tree (~/momiji-157), the published series, preflight
fieldkit mac doctor           the Air reachable (fieldkit-old-mac skill)
```
Then read `Documents/Firefox.158.Macbook.air.Sierra.Legacy/BUILD-LOG-legacy-mac.md` from the newest entry back.

## The pipeline

1. Change the Mac tree (`~/momiji-157`), one commit per change, message says why and quotes the owner.
2. `fieldkit macair preflight` - 17 decisions read from the source; exit 3 names any LOST one. Never build on 3.
3. `fieldkit macair export-patches` - our commits on Momiji -> `release-mac/patches-mac.tar.xz` (Microsoft fonts
   refused), checksums into `gorilla-firefox/macos-legacy/`. Commit and push those two files.
4. `fieldkit macair recreate` (= `macos-legacy/recreate-mac.sh all`) - the PUBLIC recipe: preflight, Momiji, the
   series, fonts (Microsoft's own download, one manual step; or FONTS_FROM=folder), Apple SDK, configure (refuses
   -march=native), build (~2 h), package, check (every Mach-O x86_64 and minos <= 10.13). Ends `VERDICT READY: zip`.
   The harness never keeps a second copy of this recipe: fix the script, not a wrapper.
5. `fieldkit macair install ZIP` - copy, sha256 checked on the Mac, old app to ~/Agents.Work.Trash-user, unpack.
6. `fieldkit macair verify` - on the Mac: bundled fonts held open (>= 7), the stroke of a known sentence on a neutral
   grey page (<= 2.5 px; Linux reference 2.12) and LCD colour <= 5 %. `VERDICT VERIFIED` or it is not done.
7. Record in BUILD-LOG (WORKS / FAILED / UNTESTED, the numbers).

## Heat and memory runs (`fieldkit macair heat`)

```bash
fieldkit macair heat idle-blank --url blank --minutes 3
fieldkit macair heat youtube-1080p60 --url "https://www.youtube.com/watch?v=aqz-KE-bpKQ" --minutes 4 --warmup 60
fieldkit macair heat-report
```

Gorilla must be running on the Air first (the profile is read from it). Each run stops Macs Fan Control (owner
2026-10-23: tests measure a normal Air), reads sensors.gorilla `--json` every 15 s (CPU, die, fan, who controls the
fan), stops early at a CPU die of 95 C, and starts Macs Fan Control again afterwards if it had been running. A run
whose report says anything but `fan ctl off` does not describe a normal Air. `--url` takes http(s) or `blank`:
`open -a App about:blank` is read as a file name and the browser never came back (2026-10-23).

## The 11-inch budget (`fieldkit macair budget`)

1366x768 minus menu bar 22, Dock 35 (measured: its top at y 733), title bar 22, margin 20 = 669 px for the About
window; minus wordmark band 100, text 40, gap 20, bottom bar 50 = 459 px for its logo (400 used: the 1400 px master
at 3.5x). about:addons: the logo caps to `min(650px, 100vh - 170px)` under 820 px of page height. `(-moz-platform:
macos)` never matched on those pages: key on size, never on platform.

## Readability, measured 2026-10-22 (fieldkit-screen-compare skill)

- The browser's own pages used macOS's interface font with LCD smoothing: strokes 2.97 px, 1.40x Linux. Now
  Segoe UI (`gorilla.ui.font-family`, nsLookAndFeel) and greyscale smoothing (`gorilla.mac.thin-text`, ua.css).
- Segoe UI vs Segoe UI Variable on the Air: 1.00x. The macOS global AppleFontSmoothing key: no effect on Gorilla
  (identical numbers), so it is NOT a system tweak to add.
- Verify pages must be neutral grey: on a coloured page white text blends into "coloured" pixels (a blue page read
  28.7 %). macOS colour management shifts page colours (#123456 -> (3, 50, 89)): find colours, never assume them.

## Traps

- `open -a App about:x` and `data:` are read as file names: open a local .html via
  `osascript -e 'tell application "Gorilla Unleashed" to open POSIX file "/tmp/x.html"'`.
- With the Air's display asleep, `screencapture` exits 0 and writes nothing: `fieldkit mac shot` wakes it first.
- The Air's Wi-Fi drops SSH for seconds several times an hour: retry, never conclude from one timeout.
- Never `-march=native` (the build host is an Ivy Bridge VAIO; the Air would crash on AVX2-era code).
- Never touch on the Air: keymap.sh, Macs Fan Control, cloudd/bird, fontd (must stay ON).
- Newer Macs: Intel 2011+ on 10.13+ should run it (untested); Apple Silicon only through Rosetta; unsigned, so
  first start needs System Settings > Privacy & Security > Open Anyway.
