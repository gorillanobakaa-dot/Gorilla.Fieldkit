---
name: fieldkit-gpudecode
description: Prove whether a browser (Gorilla Firefox, Firefox, any program) really decodes video on the graphics chip's video engine on Windows, with Microsoft's own tools - wpr.exe (built into Windows) records every GPU work packet, the Windows Performance Toolkit's wpaexporter reads them, and the gpudecode kit gives one VERDICT line. Also checks the machine first (GPU, driver, the "Microsoft Basic Display Adapter" no-driver trap, old Intel GPUs such as HD 4000), installs only the Windows Performance Toolkit when asked (Microsoft-signed, CEIP off), and gives a no-admin quick check from the built-in counters. Use when asked "does my browser use hardware decoding", "is the GPU decoding the video", "prove hardware acceleration", "video stutters / CPU high while watching", "check this user's computer", for a release page proof, or when a .etl GPU trace must be read. Also an old Mac (MacBook Air 2011, macOS 10.13) from Linux over SSH: `mac-ab` plays an H.264 clip with hardware decoding on and off and shows which process maps Apple's decoder driver.
---

# Proving hardware video decoding on Windows (gpudecode)

Tool: `gpudecode` (card in `fieldkit/desk/tools.yaml`; code `kits/gpudecode/gpudecode.py`). Run it with
`python -I`. Every command ends with a `VERDICT:` line: read that line, report it, do not reinterpret it.
`python -I gpudecode.py explain` prints this procedure in short.

## 1. Check the machine (always first)

```
python -I gpudecode.py doctor
fieldkit tools check --id gpudecode --run-tests   # the kit's own tests on this machine
```
- `PROBLEM: Microsoft Basic Display Adapter`: STOP. No hardware decoding is possible until the GPU maker's
  driver is installed. Tell the person that; do not run a trace.
- `Video-engine counters: NO`: normal for old GPUs (Intel HD 4000 and older: pre-WDDM-2.0 drivers). Use the trace.
- `toolkit: not installed`: step 2. `VERDICT: READY`: step 3.

## 2. Install the toolkit (only with the person's yes)

```
python -I gpudecode.py adk plan           # read it to the person: what is downloaded, from where, how big
python -I gpudecode.py adk install --yes  # one Windows admin prompt; refuses an installer not signed by Microsoft
```
Installs ONLY the Windows Performance Toolkit (about 450 MB), CEIP off. Never install the rest of the ADK.

## 3. Record while the video plays

1. The person opens the browser on an **H.264** video (most 1080p videos). Old Intel GPUs decode only H.264
   in hardware; a VP9 or AV1 video proves nothing on them. On YouTube, setting `media.av1.enabled` and `media.mediasource.webm.enabled` to false in about:config
   makes it send H.264.
2. ```
   python -I gpudecode.py trace --seconds 30 --yes
   ```
   The person clicks YES on the Windows prompt and plays the video during the 30 seconds.
   Output folder (default Desktop\gpudecode-<time>): `gpu.etl`, `engines.json`, `export\*.csv`,
   `verdict.json`, `RESULTS.md`.

## 4. Read the verdict

- `HARDWARE DECODE: firefox.exe (N) sent P packets to the VideoDecode engine (WPA node K) ... steady R per second`:
  proven. R should be close to the video's frame rate (60 for a 60 fps clip).
- `NO HARDWARE DECODE`: no packets on a video engine. Check the video was H.264 and playing; then it is
  software decoding (the browser's own setting or GPU blocklist, or the driver).
- `UNKNOWN`: two GPUs with video engines; give the person RESULTS.md and the .etl, say which was used is not
  certain.

Quick check without admin, newer GPUs only: `python -I gpudecode.py live --seconds 20` (VideoDecode avg % per process).

## 5. Show it to a person (WPA)

Open `gpu.etl` in Windows Performance Analyzer (`wpa.exe` in the toolkit folder): Video > GPU Utilization,
expand the GPU, then the browser process; the A/N/E column's middle number is the engine (node = Task
Manager engine + 1, from `engines.json`). The release page text with screenshots is in
`state/gpudecode/release-page/` (owner's copy).

## Never

- Never convert a trace with `tracerpt` to XML: 30 s of trace became more than 43 GB (2026-10-22).
- Never record longer than needed: about 40 MB per second (30 s = 1.2 GB).
- Never cancel someone else's `wpr` session (`trace` refuses while one runs).
- Never send the .etl anywhere: it records every program's GPU work on the machine.
- Never let WPA load symbols (Microsoft's symbol server): not needed for this.

## Facts (measured 2026-10-22, Gorilla on Intel Iris Xe, 1080p60 H.264)

16,803 VideoDecode packets, 599-600 per 10 s (60.0/s) from 110 s to 380 s; VideoProcessing 16,030; 3D 52,592
(180/s). Engine map from `typeperf -qx "GPU Engine"`: 0 3D, 1 VideoDecode, 2 Copy, 3 VideoProcessing,
4 VideoDecode (Iris Xe has two decoders: a second 30 s run used node 5, engine 4). A 430 s trace was
6.3 GB; wpaexporter read it in under a minute. `trace` must open a NORMAL visible admin window: launched
minimized with -NoProfile, three traces held 2-5 GPU packets while the video was decoding (2026-10-22);
if `trace` says INCOMPLETE with the video visibly playing, use the pasted command it prints, then `read`.

## An old Mac, from Linux (added 2026-10-25)

Run on the Linux machine, with the Mac reachable as `ssh NAME` (`fieldkit mac doctor NAME` first). It takes the
Mac's screen for about 2 minutes: say so in chat before running it.

```
python3 -I kits/gpudecode/gpudecode.py mac-ab macair [--seconds 30] [--app "/Applications/Gorilla Unleashed.app"]
python3 -I kits/gpudecode/gpudecode.py mac-verdict DIR      (judge the logs mac-ab saved)
```

What it does: copies a 1080p60 H.264 clip (made once by ffmpeg, kept in ~/.cache/fieldkit/gpudecode), a playback
page and a tiny Python 2.7 server to ~/gpudecode-test on the Mac; starts the browser twice with a NEW test profile
(hardware decoding on, then off), never the person's own profile; after each 30 s reads which process has Apple's
decoder driver mapped (vmmap) and the dropped frames the page counted; stops only the PIDs it started.

Read the VERDICT line:
- HARDWARE DECODE: the driver (AppleIntelSNBVA, AppleIntelHD3000GraphicsVADriver, AppleGVA) is mapped in a browser
  process (rdd on Gorilla 158) only with hardware decoding on. The dropped-frame numbers are printed beside it.
- NO HARDWARE DECODE: no browser process mapped it with hardware decoding on. Check the clip is H.264.
- INCOMPLETE: a run reported no frames, or the driver was mapped with decoding off. Proves nothing either way.

Never run vmmap on the browser during playback: it suspends the process (618 of 1,800 frames dropped, measured).
VideoToolbox is mapped in every browser process: it is the API, not the proof. The browser started over SSH plays and counts frames but its window does NOT appear on the Mac's screen (tried: open -n, System Events frontmost; 2026-10-25), so `fieldkit mac shot` shows the desktop: a picture of the playing video needs a person to start it. Not done: a per-frame DTrace count
(needs root on the Mac). Measured on the Air 2026-10-25, Gorilla 158: on 16 / 4 / 21 of 1,800 dropped, driver in
rdd; off 1,476 of 1,482 / 1,490 of 1,497 / 1,505 of 1,511 dropped, driver in no process.

