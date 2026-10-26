---
name: fieldkit-video-edit
description: Shorten and clean up a phone video, offline and repeatably, with the videokit tool and ffmpeg. Covers cutting dead time, replacing waiting with its steadiest real second (never a frozen still), cross-dissolving every join, gimbal- or tripod-like stabilisation of a filmed screen (OpenCV optical-flow camera path and homography warp), chosen per segment only where it measurably helps, sharpening, brightening dark parts, and measuring real camera shake (pixel shift per frame by phase correlation) to compare the source, a cut-only version and a stabilised version segment by segment. Use when the owner gives a video file and asks to cut, trim, shorten, speed up the boring parts, stabilise, unblur, sharpen, brighten or "tidy up" a recording, when an edited video looks shakier or wobblier than the original, or has sudden stops, freezes or jumpy transitions, or to document a session from a screen recording. Also use to make contact sheets of a video's frames.
license: AGPL-3.0-or-later
compatibility: "Needs the fieldkit command (Gorilla Fieldkit, github.com/gorillanobakaa-dot/Gorilla.Fieldkit; Python 3.11+ with PyYAML). Works offline; no network access unless a step says so."
---

# Editing a phone video with videokit

Tool: `videokit` (card in `fieldkit/desk/tools.yaml`; code `kits/videokit/videokit.py` in this repo).
Run it with `python -I`. It never modifies the source. The **plan file is the record of the edit**: same
source plus same plan gives the same video. Stills and contact sheets come from `photokit` (fieldkit-property-inventory).
First used 2026-10-12: a 140 s hand-held recording of a laptop screen became 43.5 s.

## 0. Machine check: always first

```
python -I videokit.py doctor --video VIDEO   # what is present/missing, and what is special about VIDEO
python -I videokit.py doctor --install       # pip numpy/pillow/opencv from PyPI; ffmpeg via scoop or winget
python -I photokit.py doctor
fieldkit tools check --id videokit --run-tests   # the kit's own test suite on this machine
```
- `doctor --video` reports, for the video itself:
  - a **variable frame rate** (phones: frames 25-67 ms apart). Handled: every analysis uses real frame timestamps;
  - **HDR** (PQ/HLG). Handled: tone-mapped to SDR with zscale and tonemap;
  - **rotation** metadata. Handled: ffmpeg auto-rotates and the size is reported as seen;
  - 10-bit, and over 30 fps (jitter numbers are per frame, so lower at 60 fps for the same shake).
- It also checks the ffmpeg filters used (vidstab, unsharp, eq, xfade, acrossfade, zscale, tonemap), OpenCV's
  optical-flow and homography functions, and at least 5 GB of temp space.
- Python packages: **numpy, Pillow, opencv-python** (OpenCV 5 provides SIFT, optical flow and homography).
  `doctor --install` installs them from PyPI.
- **Do NOT install the PyPI package `vidstab`.** It was last released in 2020 and breaks on import with OpenCV 5
  (its `imutils` dependency calls the removed `cv2.BRISK_create`); tested 2026-10-12. VideoKit does the same job
  directly with OpenCV. Downgrading OpenCV to suit it could break other tools.
- ffmpeg must be a **full build** (libvidstab, zimg). The doctor checks every filter it needs.
- On Windows use `scoop install ffmpeg`, or winget `Gyan.FFmpeg` (both are the gyan.dev full build).
  On Debian use `apt-get install ffmpeg`.
- Before installing, say who supplies it: PyPI for the Python packages, gyan.dev through scoop or winget for ffmpeg.
  Installing is a one-off pull; nothing updates itself afterwards.
- On a new machine, clone this repository: `kits/videokit` and `kits/photokit` are the tools; then run `doctor`.

## 1. Look before cutting

```
python -I videokit.py analyse VIDEO                    # once; cached in ~/.cache/videokit (VIDEOKIT_CACHE)
python -I videokit.py draft   VIDEO PLAN.txt           # first draft from screen-content changes
python -I videokit.py frames  VIDEO OUT\ev --at-events # stills just before/after each change
python -I videokit.py frames  VIDEO OUT\frames --every 2 --width 320
python -I photokit.py sheet   OUT\frames OUT\sheets --cols 6 --per 24 --size 320 --strip t
```
- **`analyse`** decodes every frame once, at its **real timestamp**, and caches per frame:
  - camera shift (phase correlation);
  - frame change;
  - **content change**: what is left after undoing the camera's full motion with a frame-to-frame homography;
  - brightness and sharpness.
  Every other command reuses it: on this laptop, 57-73 s for the first 140 s of 478 px video, then 0.2 s.
  Measured 2026-10-12: assuming 30 fps on the phone's variable frame rate drifted **+1.0 s by 2:20**. That moved
  `best` choices and shakecompare windows; for a 20-minute video the drift would be about 9 s.
- **`draft`** classifies each half-second by content change against the footage's own quiet level (the floor):
  - **change** (typing, a dialog, a page loading): kept with 1 s either side;
  - **continuous** (scrolling, browsing, a camera sweep): a run over 4 s becomes its steadiest 1 s, flagged "check";
  - **quiet** for 3 s or more: its steadiest 1 s.
  On the 2026-10-12 video it found both inbox scrolls, every wait, the subject/body typing and the Send. It missed
  the recipient appearing (a very small change) and drafted 87 s against the hand-made 38 s. **The draft is a
  start, never the edit.** Review it against the `--at-events` stills and the sheets, then tighten it.
  - Undoing only the camera's shift (not its rotation) made waiting and typing look alike. The full homography
    separates them: waiting at most about 1x the floor, typing 1.3-2.8x, scrolling medians around 3x.
- Read every sheet. Note what happens when: who does what, which moments matter, what is waiting.
- Then extract stills every 0.5-1 s around the key moments to pin the exact cut points
  (`ffmpeg -ss T -i VIDEO -frames:v 1 ...`).
- `measure`/`suggest` (camera-motion based) are kept for old plans. **`motion` in a hand-held video mostly measures
  the hand shaking**, so prefer `draft`. Either way, **finish the plan from the stills.**
- Mean `luma` below about 90 (of 255) means dark footage. `sharp` drops on blurred frames.

## 2. The plan (an editable text file next to the video)

```
keep   0.0   6.0        # what this shows
best  54.0  66.0  1     # waiting: the steadiest real 1 s inside 54-66 s, played at normal speed
# cut 66.0  76.0          why it was cut
```
Segment kinds:
- `keep S E`: normal speed.
- `best S E SECONDS`: the steadiest real stretch of that length inside S-E, picked by measured camera shift.
  **This is the default for waiting.**
- `hold S E SECONDS`: one still frame. **Reads as a freeze**: avoid.
- `speed S E FACTOR`: multiplies hand shake. Only for tripod or screen recordings.
- Keep each action at normal speed: typing appearing, a dialog, a "sent" toast.
- **Waiting becomes `best`, never `speed` and never `hold`, in hand-held footage.** Three versions, 2026-10-12:
  - v1 sped waits up 6-8x. Jitter went from 0.3-0.8 to 1.3-14 px/frame, and the owner saw "Parkinson" shaking.
  - v2 replaced them with `hold` stills. Zero shake, but the owner saw "sudden stops and freezes" at
    every one (12, 15, 18, 22 and 28 s). A still inside moving hand-held footage reads as the video freezing.
  - v3 uses `best`: a real second of footage chosen for the least camera movement (0.06-0.14 px jitter).
    No freeze, no shake.
  `suggest` writes `best` by default (`--best 1`).
- Cut repetition and anything off-topic. A second of a scene is enough to establish it.
- **Privacy:** screen recordings show mailboxes, names, keys and addresses. Keep such views short and say so.
  Do not publish a video before the owner has seen it.
- Comment every line. The plan explains the edit to the next person who opens it.

## 3. Render

```
python -I videokit.py render VIDEO PLAN.txt OUT.mp4            # per-segment, stabilise-if-better, gamma, sharpen
python -I videokit.py render VIDEO PLAN.txt CUTONLY.mp4 --no-stab   # the same cut without stabilising, to compare
```
- Each `keep`/`speed`/`best` segment is rendered as **every candidate**, and the one with the lowest measured jitter
  is kept (it must be at least 10% better than plain):
  - `plain`;
  - `smooth`: ffmpeg vidstab two-pass;
  - **`gimbal 0.25 / 0.5 / 1.2`** and **`tripod`**: OpenCV homography (`videokit lock`);
  - **`gimbal 0.5 sim`**: similarity only (shift, rotation, zoom, no tilt). Safe for rooms and other 3D scenes, and it
    won the Gmail-opening segment on 2026-10-12 (0.22 px against 0.35).
  All candidates' jitter values are logged.
- **Candidates are compared on a preview at most 540 px wide; only the winner is rendered at full size.** The
  camera path is measured once per segment and rescaled to full size. Gimbal and tripod **stream** frames in two
  passes (track, then warp), so memory does not grow with segment length or resolution. Earlier versions held a
  whole segment in memory: about 11 GB for 60 s of 1080p.
- **Distortion guard:** a candidate is rejected if its warp bends the picture's corners more than `--max-bend 3`
  degrees, or changes its scale implausibly. A homography on a non-flat scene, or a bad fit, shows up here even when
  its jitter looks low.
- **How gimbal/tripod work:**
  - corners (`goodFeaturesToTrack`) are tracked frame to frame with pyramidal Lucas-Kanade optical flow;
  - a RANSAC homography per step gives the camera path. A laptop screen is a flat plane, so a homography captures
    shift, rotation, tilt and zoom;
  - **gimbal:** the path's corners are Gaussian-smoothed (sigma = follow seconds). It follows slow drift and removes
    everything faster;
  - **tripod:** the path is held at its average: no motion at all;
  - each frame is warped onto the target path, cropped to what every frame covers, then scaled back.
- Measured 2026-10-12 (v3 to v4): ten calm segments went from 0.11-0.93 to **0.00-0.02 px** jitter (locked).
  Big hand movements went from 0.43-1.54 to 0.19-0.59. Overall 0.65 to 0.22 px, against 1.69 in the source.
- Why not lock every frame to one reference frame? Tried first: whenever the screen content changes (a page loads),
  matching fails (135 of 300 frames) and jitter rose (0.70 to 1.78). Frame-to-frame tracking survives page changes.
- **Crop budget** `--max-crop 0.18` per side: a candidate that needs more is rejected, because it would stretch edge
  pixels. Big hand drift needs 20-50% to lock. A faster gimbal (0.25 s follow) needs least: it still removes the
  shake and follows the drift. **The owner's judgement (2026-10-12, after watching v4): 20-50% zoom "will probably
  look unnatural". Keep the 18% cap.** Where a segment cannot be locked within it, smooth gimbal-following is the
  accepted result. Do not raise `--max-crop` unless the owner asks for it.
- **Dissolves are timed on picture length** (`video_duration`: frames / fps), never the container duration. A
  stabilised piece's picture can end 1-2 frames before its sound, and container-timed dissolves snapped at 3 joins.
- A tripod lock of a *perfectly static* synthetic pattern is reported as a freeze (identical frames). Real footage
  keeps light and noise changes: v4 has no freezes.
- `hold` shows the frame with the best sharpness/(1+motion) in its range: no shake and no blur.
- Plain segments are cropped 4% and scaled back, to match vidstab's 4% zoom, so framing doesn't jump between them.
- **Every join cross-dissolves** (`--xfade 0.3` by default; picture xfade, sound acrossfade). Each join's dissolve
  shrinks to at most 40% of either neighbour, so short pieces still fit. Hard cuts between hand-held shots look like
  jumps: v2 had 11. `--xfade 0` gives hard cuts.
- Levels: gamma from the used frames' mean brightness (target about 118). A brightness offset is added only if
  very dark; measured, an offset greys black and flattens white pages.
- The decisions go to `OUT.mp4.render.txt`: the levels, plain or stabilised per segment with both jitter
  values, the still chosen for each hold, and `#win` lines with each segment's real start and end in the output.

## 4. Prove it is steadier: measure the shake, never eyeball it

`motion` (mean pixel difference) **cannot see shake**. On 2026-10-12 it showed source, cut-only and stabilised as
nearly equal while the owner saw violent shaking. Measure the real camera movement:

```
python -I videokit.py shake VIDEO [--per-second] [--out shifts.csv]
python -I videokit.py shakecompare SOURCE PLAN CUTONLY.mp4 OUT.mp4 --strict
```
- `shake`: per-frame (dx, dy) **pixel shift by FFT phase correlation** between consecutive frames, at the
  video's own frame rate, on a copy 240 px wide. **Jitter** is the shift minus its 0.5 s moving average: the
  shaking, without the slow drift. A still frame scores 0.00.
- `shakecompare`: for **every plan segment, sped-up and held ones included**, shift and jitter in the source
  (at native speed) and in each output, for example the cut-only and the stabilised versions side by side.
  - It uses each output's real segment windows from its `.render.txt`.
  - It skips 3 frames either side of each cut: a cut is a jump, not shake.
  - `!` marks a segment more than 25% shakier than the source; `--strict` exits 1 if any is.
- **Done means `shakecompare --strict` AND `flow --strict` both exit 0**, and the output stills look right
  (pull stills across 2-3 joins and check the dissolves).
  - Typical healthy numbers for hand-held phone footage: jitter 0.3-1.5 px per frame; holds 0.00.
  - A sped-up segment at 5+ px is the shaking the owner will see.
- Validated against a known synthetic sway: calculated 0.73 px/frame, measured 0.84; the excess is ffmpeg
  snapping the crop to whole pixels. `testsrc2` animates by itself, so test with a static pattern.

## 5. Prove it flows: no freezes, no hard joins

```
python -I videokit.py flow OUT.mp4 [--strict]
```
- Per-frame change at the video's own frame rate:
  - **freeze**: nearly identical frames for at least 0.25 s;
  - **hard cut**: a one-frame change above 8 and over 4 times the frames around it.
- Using `OUT.mp4.render.txt` it maps each event back to the source:
  - a cut the **source already has** (a page loading, a window popping open) is "inside a shot: the screen changed";
  - a freeze where the **source is still too** is the source's.
  - Only freezes and cuts **at the edit's joins** count against it. `--strict` exits 1 on any.
- Measured 2026-10-12: v2 had 5 freezes and 10 hard joins (15 counted); v3 and v4 have 0.
  - v3's one cut, at 11.5 s, is the source's compose window opening at 53.87 s.
  - In v4 a page change spread over 2 source frames became a one-frame step once locked (13.9 s). An output event
    is therefore the screen's when the source changes by at least 40% of it within 0.1 s, not only when the source
    has the same spike.
- The thresholds were tuned on test clips: 1-pixel crop snaps on a high-contrast pattern give a change of
  6.5-7.1; real hard joins gave 9.6-106.

## 6. Long, large or different videos (proved 2026-10-12)

`kits/videokit/bench_long.py SOURCE PLAN WORKDIR` loops a clip into a long 1080p VFR video, then runs analyse, draft,
render, flow and shakecompare under a memory monitor, and writes `bench.json`.

21-minute 1080x1920 VFR video (the owner's clip looped 9 times), 45 segments, 125 s kept and 113 s out:

| Step | Time | Peak memory |
|---|---|---|
| analyse (once; cached after) | 11.8 min (about 0.56x real time) | 223 MB |
| draft | 0.5 s | 2 MB |
| render, one 45-input join (run 1) | 23.3 min | **3.5 GB** |
| render, joins batched by 8 (run 2) | 38.7 min* | 1.39 GB |
| flow and shakecompare --strict | 1.5 s | 39 MB |

*Same work plus a few short batch encodes; most of the extra time is probably the laptop's load or heat, but that
is not proven. Plan on about 12-20 s of rendering per output second at 1080p on this ThinkPad.

- Output jitter 0.14 px (source 0.3-1.9), 0 freezes, 0 hard joins. The last segments, 20 minutes in, line up exactly,
  where the old 30-fps assumption would have been about 9 s out.
- **Memory is bounded by design:**
  - analysis and gimbal/tripod stream frames;
  - candidates run on a 540 px preview;
  - joins open at most 8 inputs. One chain over 45 1080p pieces peaked at 3.5 GB and would grow with the
    number of cuts.
- **A non-screen video** (walking round a room, people, outdoors): expect `gimbal ... sim` (similarity) to win. The
  homography candidates are rejected by the distortion guard when they bend the picture. Still check stills
  for warping. Tripod is meaningless when the camera walks.
- **HDR, rotation, 60 fps:** run `doctor --video` first. HDR is tone-mapped; at 60 fps jitter per frame is about
  half the 30 fps figure for the same shake, so compare against the source, never against fixed numbers.
- **Planning a long video:** `draft`, then `frames --at-events`, then sheets of those stills. Review and tighten
  the plan; for a 20-minute video that is a few dozen event stills instead of 600 sheet tiles.

## Report

Give:
- the source and output length, and what was cut or held, with the times;
- the brightness and gamma applied, and which segments were stabilised (from `.render.txt`);
- the `shakecompare` table (source, cut-only and stabilised) and the `flow` result;
- the limits that remain (blur, low resolution).

Keep the plan file and `.render.txt` next to the video. Before a video or its stills go anywhere public, run
`fieldkit privacy scan` on any text that goes with them, and remember a screen recording itself can show
mailboxes and keys.
