---
name: fieldkit-gpudecode
description: Prove whether a browser (Gorilla Firefox, Firefox, any program) really decodes video on the graphics chip's video engine on Windows, with Microsoft's own tools - wpr.exe (built into Windows) records every GPU work packet, the Windows Performance Toolkit's wpaexporter reads them, and the gpudecode kit gives one VERDICT line. Also checks the machine first (GPU, driver, the "Microsoft Basic Display Adapter" no-driver trap, old Intel GPUs such as HD 4000), installs only the Windows Performance Toolkit when asked (Microsoft-signed, CEIP off), and gives a no-admin quick check from the built-in counters. Use when asked "does my browser use hardware decoding", "is the GPU decoding the video", "prove hardware acceleration", "video stutters / CPU high while watching", "check this user's computer", for a release page proof, or when a .etl GPU trace must be read. Windows only; the macOS side is planned (DTrace + VideoToolbox).
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
- Never record longer than needed: about 15 MB per second.
- Never cancel someone else's `wpr` session (`trace` refuses while one runs).
- Never send the .etl anywhere: it records every program's GPU work on the machine.
- Never let WPA load symbols (Microsoft's symbol server): not needed for this.

## Facts (measured 2026-10-22, Gorilla on Intel Iris Xe, 1080p60 H.264)

16,803 VideoDecode packets, 599-600 per 10 s (60.0/s) from 110 s to 380 s; VideoProcessing 16,030; 3D 52,592
(180/s). Engine map from `typeperf -qx "GPU Engine"`: 0 3D, 1 VideoDecode, 2 Copy, 3 VideoProcessing,
4 VideoDecode. A 430 s trace was 6.3 GB; wpaexporter read it in under a minute.
