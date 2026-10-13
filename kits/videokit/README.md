# VideoKit

Measure, cut, speed up, stabilise, sharpen and brighten a phone video, deterministically,
with ffmpeg. Card `videokit` in `fieldkit/desk/tools.yaml`; test `tests/test_videokit.py`. The procedure is the skill `fieldkit-video-edit`.

| Command | What it does |
|---|---|
| `doctor [--install] [--video V]` | checks Python, numpy, Pillow, opencv-python, ffmpeg and every filter used, temp space; with `--video`, VFR/HDR/rotation/10-bit; installs what is missing |
| `analyse V` | one decode at real timestamps: shift, change, content change, brightness, sharpness per frame; cached |
| `events V` / `draft V PLAN` | screen-content changes (change / continuous); first-draft plan from them |
| `probe V` | duration, size, fps, audio |
| `frames V OUT --every 1 [--at-events]` | stills named by time (`t0012.0.jpg`), for `photokit sheet`; or around each content change |
| `measure V OUT.csv` | per 0.25 s: motion, brightness, sharpness (same input, same numbers) |
| `suggest CSV PLAN` | draft plan: idle runs become `best` (their steadiest real second) |
| `flow V [--strict]` | freezes and hard cuts; with the render log, only those at the edit's joins count |
| `shake V` | real camera movement: pixel shift per frame by phase correlation, and jitter |
| `shakecompare SRC PLAN OUT...` | shift/jitter per plan segment in the source and each output; `--strict` fails if any is shakier |
| `lock V OUT [--mode gimbal\|tripod] [--follow 0.7]` | OpenCV stabilisation: optical-flow camera path, homography warp; gimbal follows slow drift, tripod holds still |
| `render V PLAN OUT.mp4` | per segment: `best` picks the steadiest real stretch, `hold` a still; keep/speed/best rendered plain, ffmpeg-smooth, gimbal 0.25/0.5/1.2 and tripod, the lowest-jitter one within the 18% crop budget kept; gamma, sharpen; 0.3 s cross-dissolve at every join; decisions in `OUT.render.txt` |

Dependencies: Python 3.9+, numpy, Pillow, opencv-python, a full ffmpeg build (vidstab, zscale/tonemap, xfade)
(NOT the PyPI `vidstab` package: broken with OpenCV 5)
(Windows: `scoop install ffmpeg` or `winget install Gyan.FFmpeg`; Debian: `apt-get install ffmpeg`).

Measured 2026-10-12 on a 140 s hand-held phone recording of a laptop screen (478x850):
- motion mostly measures hand shake, not screen activity: plan from stills, not from `suggest` alone;
- a brightness offset turned black backgrounds grey; gamma alone (1.28) brightened without that;
- result 43.5 s, mean luma 73 -> about 110, render about 70 s on this laptop.

Measured 2026-10-12, version 1 to version 2 (the owner saw v1 shake "like Parkinson's"):
- v1 sped waits up 6-8x: jitter in those parts went from 0.3-0.8 to 1.3-14 px/frame. v2 holds a still: 0.00.
- v1 stabilised the whole video: jitter on the blank compose window went from 1.3 to 4.1 px. v2 decides per segment:
  stabilised 5 of 10, and none is above the source (`shakecompare --strict` exit 0).
- the mean-difference `motion` number could not see any of this; phase-correlation shift can.

v3, same day: the owner saw v2's stills as "sudden stops and freezes". `best` (a real, steadiest second)
plus 0.3 s dissolves gave 0 freezes and 0 hard joins (`flow --strict` exit 0), with jitter at or below the source.

v4, same day ("eliminate any motion, like a gimbal"): OpenCV gimbal/tripod per segment. Ten calm segments locked to
0.00-0.02 px jitter, big movements 0.19-0.59 px; whole video 0.22 px (source 1.69). Locking every frame to one
reference failed when the page changed (135/300 frames); frame-to-frame optical flow did not.

Tool v2, 2026-10-12 (owner: "how would the harness deal with a larger video?"):
- real frame timestamps everywhere (the phone's VFR had drifted the old 30-fps assumption +1.0 s by 2:20);
- one cached analysis per video, so no command re-scans;
- gimbal/tripod stream in two passes, so memory is flat;
- candidates are compared on a 540 px preview, and only the winner is rendered at full size;
- similarity-only gimbal and a distortion guard for non-flat scenes;
- HDR tone-mapping, rotation and VFR reported by `doctor --video`;
- content-change `draft` for long videos.
`bench_long.py` proved it on a 21-minute 1080x1920 VFR video (45 segments):
- analyse: 11.8 min, 223 MB;
- render: 23.3 min with a 3.5 GB peak, then 38.7 min with a 1.39 GB peak after batching the joins by 8;
- output jitter 0.14 px; flow and shakecompare --strict both pass.

Limits: blur from camera shake or focus cannot be undone; unsharp only crisps edges.
