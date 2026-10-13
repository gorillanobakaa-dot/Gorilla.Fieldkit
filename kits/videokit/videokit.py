"""videokit - measure, cut, stabilise, sharpen and brighten a phone video, deterministically.

Built 2026-10-12 to edit hand-held phone recordings of a screen. The procedure that uses it is
the fieldkit-video-edit skill. Needs ffmpeg/ffprobe (a full build with vidstab, zscale, tonemap,
xfade), Python with numpy, Pillow and opencv-python. Originals are never modified.

One analysis pass per video (`analyse`, cached under ~/.cache/videokit) decodes every frame at
its REAL timestamp and records camera shift (FFT phase correlation), frame change, content change
(change left after undoing the camera shift), brightness and sharpness. Every command reuses it.
Phone videos have a VARIABLE frame rate: assuming 30 fps drifted +1.0 s by 2:20 (measured).

  videokit.py doctor [--install]          check / install numpy, Pillow, opencv-python, ffmpeg+filters
  videokit.py probe VIDEO [--json]        size (after rotation), fps, VFR, HDR, bit depth, audio
  videokit.py analyse VIDEO               build the per-frame analysis cache (others call it on demand)
  videokit.py frames VIDEO OUT [--every 1] [--at-events] [--width 360]
                                          stills named by time (t0012.0.jpg) for contact sheets
  videokit.py measure VIDEO OUT.csv [--fps 4]   per-sample motion/brightness/sharpness CSV
  videokit.py events VIDEO [--out events.csv]   screen-content activity: when the screen changes
  videokit.py draft VIDEO PLAN.txt              first-draft plan from content activity (review it!)
  videokit.py suggest MEASURE.csv PLAN.txt      older draft from camera motion (weak on hand-held)
  videokit.py shake VIDEO [--per-second]        camera shift and jitter (px per frame, at 240 px wide)
  videokit.py shakecompare SOURCE PLAN OUT... [--strict]   per segment: source vs each output
  videokit.py lock VIDEO OUT [--mode gimbal|tripod] [--follow 0.7] [--model homography|similarity]
  videokit.py flow VIDEO [--strict]       freezes and hard cuts; with the render log only the edit's count
  videokit.py render VIDEO PLAN OUT.mp4 [...]   the edit (see below); decisions in OUT.mp4.render.txt

render, per plan segment: keep/speed/best are tried as plain, ffmpeg-smooth, gimbal 0.25/0.5/1.2 s,
tripod and a similarity-only gimbal ON A SMALL PREVIEW (<= 540 px wide); the lowest measured jitter
that needs no more than --max-crop and passes the distortion guard wins and only it is rendered at
full size. Gimbal/tripod stream frames (two passes), so memory does not grow with length or size.
HDR (PQ/HLG) sources are tone-mapped to SDR. Joins cross-dissolve, timed on picture length.

PLAN format, one segment per line, '#' starts a comment:
  keep  START END            normal speed
  best  START END SECONDS    the steadiest real stretch of SECONDS inside START-END (default for waits)
  hold  START END SECONDS    a still of the steadiest frame - reads as a FREEZE in hand-held footage
  speed START END FACTOR     play FACTOR times faster (2-16) - multiplies hand shake, prefer best
Times are seconds in the source. Anything not listed is cut.

Exit codes: 0 ok, 1 tool/input failure or --strict finding, 2 usage error.
"""
import argparse
import csv
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

ANALYSIS_VERSION = 4
AW = 240            # analysis width in px: every jitter/shift number is at this width
PREVIEW_W = 540     # stabiliser candidates are compared at most this wide


def need(tool):
    if not shutil.which(tool):
        print(f"{tool} not found on PATH (run: videokit doctor --install)", file=sys.stderr)
        sys.exit(1)


def run(cmd, **kw):
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, encoding="utf-8", errors="replace", **kw)
    if r.returncode != 0:
        print(" ".join(map(str, cmd)), file=sys.stderr)
        print(r.stderr[-3000:], file=sys.stderr)
        raise SystemExit(1)
    return r


# -- probe -------------------------------------------------------------------------
def probe(video):
    need("ffprobe")
    r = run(["ffprobe", "-v", "error", "-show_entries",
             "format=duration,start_time:stream=codec_type,width,height,r_frame_rate,avg_frame_rate,pix_fmt,"
             "color_transfer:stream_side_data=rotation:stream_tags=rotate", "-of", "json", str(video)])
    j = json.loads(r.stdout)
    v = next(s for s in j["streams"] if s["codec_type"] == "video")

    def rate(x):
        try:
            n, d = (float(q) for q in x.split("/"))
            return n / d if d else 0.0
        except (ValueError, AttributeError):
            return 0.0
    rot = 0
    for sd in v.get("side_data_list", []) or []:
        if "rotation" in sd:
            rot = int(float(sd["rotation"]))
    rot = rot or int(float((v.get("tags") or {}).get("rotate", 0) or 0))
    w, h = v["width"], v["height"]
    if abs(rot) % 180 == 90:
        w, h = h, w  # ffmpeg auto-rotates on decode; report what the viewer sees
    rf, af = rate(v.get("r_frame_rate")), rate(v.get("avg_frame_rate"))
    return {"duration": float(j["format"]["duration"]), "start": float(j["format"].get("start_time", 0) or 0),
            "width": w, "height": h, "rotation": rot, "fps": rf or af or 30.0, "avg_fps": af or rf,
            "vfr": bool(rf and af and abs(rf - af) / rf > 0.005),
            "pix_fmt": v.get("pix_fmt", ""), "bit10": "10" in v.get("pix_fmt", ""),
            "hdr": v.get("color_transfer", "") in ("smpte2084", "arib-std-b67"),
            "audio": any(s["codec_type"] == "audio" for s in j["streams"])}


def out_fps(info):
    """Output frame rate: the nominal rate, rounded, at most 60."""
    return min(60, max(1, round(info["fps"])))


def video_duration(video):
    """Length of the PICTURE (frames / fps), not the container: a stabilised piece's picture can end a
    frame or two before its sound, and a dissolve timed on the container then snaps (found 2026-10-12)."""
    r = run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets", "-show_entries",
             "stream=nb_read_packets,r_frame_rate", "-of", "json", str(video)])
    st = json.loads(r.stdout)["streams"][0]
    num, den = (int(x) for x in st["r_frame_rate"].split("/"))
    return int(st["nb_read_packets"]) / (num / den)


def cmd_probe(a):
    p = probe(a.video)
    print(json.dumps(p) if a.json else "  ".join(f"{k}={v}" for k, v in p.items()))
    return 0


def hdr_filter(info):
    """HDR (PQ/HLG) to SDR BT.709, so decoded pixels are not washed out."""
    if not info.get("hdr"):
        return []
    return ["zscale=t=linear:npl=100", "format=gbrpf32le", "zscale=p=bt709", "tonemap=hable:desat=0",
            "zscale=t=bt709:m=bt709:r=tv", "format=yuv420p"]


# -- analysis: one decode, real timestamps, cached -------------------------------------
def cache_dir():
    d = pathlib.Path(os.environ.get("VIDEOKIT_CACHE", pathlib.Path.home() / ".cache" / "videokit"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def frame_times(video):
    """Presentation time of every frame, seconds from the start, from the packets (no decode)."""
    r = run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "packet=pts_time",
             "-of", "csv=p=0", str(video)])
    ts = sorted(float(x.split(",")[0]) for x in r.stdout.split() if x.strip() and x.split(",")[0] != "N/A")
    if not ts:
        return []
    t0 = ts[0]
    return [t - t0 for t in ts]


def _phase_shift(a, b, win):
    """Sub-pixel (dx, dy) that moves frame a onto frame b (FFT phase correlation)."""
    import numpy as np
    fa = np.fft.fft2(a * win)
    fb = np.fft.fft2(b * win)
    r = fa * np.conj(fb)
    r /= np.abs(r) + 1e-9
    c = np.fft.ifft2(r).real
    h, w = c.shape
    y, x = np.unravel_index(int(np.argmax(c)), c.shape)

    def sub(m1, m0, p1):
        d = m1 - 2 * m0 + p1
        return 0.0 if abs(d) < 1e-12 else 0.5 * (m1 - p1) / d
    dy = y + sub(c[(y - 1) % h, x], c[y, x], c[(y + 1) % h, x])
    dx = x + sub(c[y, (x - 1) % w], c[y, x], c[y, (x + 1) % w])
    if dy > h / 2:
        dy -= h
    if dx > w / 2:
        dx -= w
    return -float(dx), -float(dy)


def _analysis_key(video):
    st = pathlib.Path(video).stat()
    raw = f"{pathlib.Path(video).resolve()}|{st.st_size}|{st.st_mtime_ns}|{ANALYSIS_VERSION}|{AW}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def analyse(video, quiet=True):
    """Per frame, at real timestamps: t, dx, dy (camera shift from the previous frame, px at AW wide),
    diff (mean abs change), resid (change left after undoing the shift = screen content changing),
    luma, sharp (Laplacian variance). Cached by file path, size and mtime."""
    import numpy as np
    need("ffmpeg")
    cache = cache_dir() / f"{_analysis_key(video)}.npz"
    if cache.is_file():
        z = np.load(cache)
        return {k: z[k] for k in z.files}
    info = probe(video)
    w = AW
    h = int(round(info["height"] * w / info["width"] / 2) * 2)
    win = np.outer(np.hanning(h), np.hanning(w)).astype(np.float32)
    vf = ",".join(hdr_filter(info) + [f"scale={w}:{h}", "format=gray"])
    proc = subprocess.Popen(["ffmpeg", "-v", "error", "-i", str(video), "-fps_mode", "passthrough", "-vf", vf,
                             "-f", "rawvideo", "-"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    import cv2
    my, mx = max(1, h // 10), max(1, w // 10)
    cols = {k: [] for k in ("dx", "dy", "diff", "resid", "luma", "sharp")}
    prev = None
    while True:
        buf = proc.stdout.read(w * h)
        if len(buf) < w * h:
            break
        f = np.frombuffer(buf, dtype=np.uint8).reshape(h, w).astype(np.float32)
        if prev is None:
            dx = dy = diff = resid = 0.0
        else:
            dx, dy = _phase_shift(prev, f, win)
            diff = float(np.abs(f - prev).mean())
            # content change: what is left after undoing the camera's FULL motion (shift, rotation, tilt,
            # zoom) with a frame-to-frame homography. Undoing only the shift left the hand's rotation as
            # fake "change": waiting and typing then looked alike (measured 2026-10-12).
            resid = None
            p8, g8 = prev.astype(np.uint8), f.astype(np.uint8)
            pts = cv2.goodFeaturesToTrack(p8, 400, 0.01, 5)
            if pts is not None and len(pts) >= 12:
                nxt, st, _ = cv2.calcOpticalFlowPyrLK(p8, g8, pts, None, winSize=(15, 15), maxLevel=2)
                ok = st.reshape(-1) == 1
                if ok.sum() >= 12:
                    Hh, _m = cv2.findHomography(pts[ok], nxt[ok], cv2.RANSAC, 1.0)
                    if Hh is not None:
                        wp = cv2.warpPerspective(prev, Hh, (w, h), flags=cv2.INTER_LINEAR)
                        resid = float(np.abs(f[my:-my, mx:-mx] - wp[my:-my, mx:-mx]).mean())
            if resid is None:
                resid = diff
        lap = (-4 * f[1:-1, 1:-1] + f[:-2, 1:-1] + f[2:, 1:-1] + f[1:-1, :-2] + f[1:-1, 2:])
        for k, v in (("dx", dx), ("dy", dy), ("diff", diff), ("resid", resid), ("luma", float(f.mean())),
                     ("sharp", float(lap.var()))):
            cols[k].append(v)
        prev = f
    proc.wait()
    n = len(cols["dx"])
    ts = frame_times(video)
    if len(ts) != n:  # fall back to the nominal rate only if the packet list does not line up
        if not quiet:
            print(f"note: {len(ts)} packet times for {n} decoded frames; using nominal {info['fps']:.3f} fps",
                  file=sys.stderr)
        ts = [i / (info["fps"] or 30.0) for i in range(n)]
    A = {k: np.array(v, dtype=np.float64) for k, v in cols.items()}
    A["t"] = np.array(ts[:n], dtype=np.float64)
    np.savez(cache, **A)
    return A


def cmd_analyse(a):
    import numpy as np
    A = analyse(a.video, quiet=False)
    t = A["t"]
    gaps = np.diff(t) * 1000 if len(t) > 1 else np.array([0.0])
    print(f"{pathlib.Path(a.video).name}: {len(t)} frames over {t[-1] if len(t) else 0:.2f} s; frame gaps "
          f"{gaps.min():.1f}-{gaps.max():.1f} ms; cache {cache_dir()}")
    return 0


def idx(A, s, e):
    """Frame indices with s <= t < e."""
    import numpy as np
    return int(np.searchsorted(A["t"], s, "left")), int(np.searchsorted(A["t"], e, "left"))


def fps_of(A):
    import numpy as np
    t = A["t"]
    return float(1.0 / np.median(np.diff(t))) if len(t) > 2 else 30.0


# -- frames ------------------------------------------------------------------------
def cmd_frames(a):
    need("ffmpeg")
    out = pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    info = probe(a.video)
    if a.at_events:
        times = sorted({round(max(0.0, x), 1) for t, _k, _v in content_events(analyse(a.video)) for x in (t - 0.3, t + 0.7)})
    else:
        times, t = [], 0.0
        while t < info["duration"]:
            times.append(round(t, 1))
            t += a.every
    vf = ",".join(hdr_filter(info) + [f"scale={a.width}:-2", "format=yuvj420p"])
    for t in times:
        run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", str(a.video), "-frames:v", "1",
             "-vf", vf, str(out / f"t{t:06.1f}.jpg")])
    print(f"{len(times)} frame(s) in {out}")
    return 0


# -- measure (sampled; timing exact via the fps filter) --------------------------------
def _gray_frames(video, fps, width=160):
    import numpy as np
    p = probe(video)
    h = int(round(p["height"] * width / p["width"] / 2) * 2)
    vf = ",".join(hdr_filter(p) + [f"fps={fps}", f"scale={width}:{h}", "format=gray"])
    proc = subprocess.Popen(["ffmpeg", "-v", "error", "-i", str(video), "-vf", vf, "-f", "rawvideo", "-"],
                            stdout=subprocess.PIPE)
    size = width * h
    while True:
        buf = proc.stdout.read(size)
        if len(buf) < size:
            break
        yield np.frombuffer(buf, dtype=np.uint8).reshape(h, width).astype(np.float32)
    proc.wait()


def measure(video, fps):
    import numpy as np
    need("ffmpeg")
    rows, prev = [], None
    for i, f in enumerate(_gray_frames(video, fps)):
        motion = 0.0 if prev is None else float(np.abs(f - prev).mean())
        lap = (-4 * f[1:-1, 1:-1] + f[:-2, 1:-1] + f[2:, 1:-1] + f[1:-1, :-2] + f[1:-1, 2:])
        rows.append({"t": round(i / fps, 3), "motion": round(motion, 3), "luma": round(float(f.mean()), 2),
                     "sharp": round(float(lap.var()), 1)})
        prev = f
    w = max(1, int(round(fps)))
    for i, r in enumerate(rows):
        window = rows[max(0, i - w // 2): i + w // 2 + 1]
        r["motion_1s"] = round(sum(x["motion"] for x in window) / len(window), 3)
    return rows


def cmd_measure(a):
    rows = measure(a.video, a.fps)
    with open(a.out, "w", newline="", encoding="utf-8") as fh:
        wr = csv.DictWriter(fh, fieldnames=["t", "motion", "motion_1s", "luma", "sharp"])
        wr.writeheader()
        wr.writerows(rows)
    print(f"{len(rows)} sample(s) -> {a.out}")
    return 0


# -- content events and the first-draft plan -------------------------------------------
def activity(A, bin_s=0.5):
    """Per half-second bin: (bin start times, max content change, median content change, noise floor).
    The floor is the 25th percentile of the bins' maxima: the footage's own quiet level."""
    import numpy as np
    t, r = A["t"], A["resid"]
    if len(t) < 3:
        return np.array([]), np.array([]), np.array([]), 0.0
    nb = int(t[-1] / bin_s) + 1
    b = (t / bin_s).astype(int)
    mx, md = np.zeros(nb), np.zeros(nb)
    for i in range(nb):
        v = r[1:][b[1:] == i]
        if len(v):
            mx[i], md[i] = v.max(), np.median(v)
    floor = max(float(np.percentile(mx[mx > 0], 25)) if (mx > 0).any() else 1.0, 0.5)
    return np.arange(nb) * bin_s, mx, md, floor


def content_events(A, bin_s=0.5, small=1.7, big=2.5):
    """(time, kind, strength): kind 'change' when a bin's max content change is >= small x the floor
    (typing, a dialog, a page loading), 'continuous' when its median, smoothed over 1.5 s, is >= big x
    the floor (scrolling, browsing, the camera sweeping to something else). Measured 2026-10-12 on a
    hand-held screen recording, as median/floor: waiting 0.5-0.8, typing/dialogs <= 1.4, inbox
    scrolling p50 3.3 (max 5.5). Bin maxima: waiting up to ~1x, typing 1.3-2.8x the floor."""
    import numpy as np
    times, mx, md, floor = activity(A, bin_s)
    # continuous change is judged on the median smoothed over 1.5 s, so a scroll reads as one run
    k = max(1, int(round(1.5 / bin_s)))
    mds = np.convolve(md, np.ones(k) / k, mode="same") if len(md) else md
    ev = []
    for tm, m, d in zip(times, mx, mds):
        if d >= big * floor:
            ev.append((round(float(tm), 2), "continuous", round(float(d / floor), 1)))
        elif m >= small * floor:
            ev.append((round(float(tm), 2), "change", round(float(m / floor), 1)))
    return ev


def draft_plan(A, pad=1.0, merge_gap=2.5, wait_min=3.0, best_s=1.0, long_continuous=4.0):
    """Keep moments where the screen changes (padded, merged); a long run of continuous change
    (scrolling, browsing) and every long quiet stretch become their steadiest real second.
    A first draft for an agent to review against stills, never a final edit."""
    ev = content_events(A)
    end = float(A["t"][-1]) if len(A["t"]) else 0.0
    runs = []  # [start, end, kind] of consecutive event bins of one kind
    for t, kind, _ in ev:
        if runs and runs[-1][2] == kind and t - runs[-1][1] <= 0.5 + 1e-6:
            runs[-1][1] = t + 0.5
        else:
            runs.append([t, t + 0.5, kind])
    spans = []
    for s0, e0, kind in runs:
        if kind == "continuous" and e0 - s0 >= long_continuous:
            spans.append([s0, e0, "continuous"])
            continue
        s, e = max(0.0, s0 - pad), min(end, e0 + pad)
        if spans and spans[-1][2] == "change" and s - spans[-1][1] <= merge_gap:
            spans[-1][1] = max(spans[-1][1], e)
        else:
            spans.append([s, e, "change"])
    plan, cur = [], 0.0
    for s, e, kind in spans:
        s = max(s, cur)
        if e <= s:
            continue
        gap = s - cur
        if gap >= wait_min:
            plan.append(("best", round(cur, 2), round(s, 2), best_s, "quiet: steadiest real second"))
        elif gap > 0:
            plan.append(("keep", round(cur, 2), round(s, 2), None, "short quiet, kept"))
        if kind == "continuous":
            plan.append(("best", round(s, 2), round(e, 2), best_s,
                         f"{e - s:.0f} s of continuous change (scrolling/browsing?): 1 s kept - check"))
        else:
            plan.append(("keep", round(s, 2), round(e, 2), None, "screen content changes"))
        cur = e
    if end - cur >= wait_min:
        plan.append(("best", round(cur, 2), round(end, 2), best_s, "quiet: steadiest real second"))
    elif end > cur:
        plan.append(("keep", round(cur, 2), round(end, 2), None, "tail"))
    return plan


def cmd_events(a):
    ev = content_events(analyse(a.video))
    if a.out:
        with open(a.out, "w", newline="", encoding="utf-8") as fh:
            wr = csv.writer(fh)
            wr.writerow(["t", "kind", "x_floor"])
            wr.writerows(ev)
    for t, kind, v in ev:
        print(f"{t:8.2f}s  {kind:<10} {v:5.1f} x floor")
    print(f"{len(ev)} active half-second(s): {sum(k == 'change' for _, k, _ in ev)} change, "
          f"{sum(k == 'continuous' for _, k, _ in ev)} continuous")
    return 0


def cmd_draft(a):
    A = analyse(a.video)
    plan = draft_plan(A, a.pad, a.merge_gap, a.wait_min, a.best)
    lines = ["# videokit DRAFT plan from screen-content activity - review every line against stills",
             "# (videokit frames VIDEO OUT --at-events), cut what is off-topic or private, then render.",
             "# keep S E | best S E SECONDS | hold S E SECONDS | speed S E FACTOR | unlisted = cut"]
    total = 0.0
    for kind, s, e, x, why in plan:
        lines.append(f"{kind:<5} {s:8.2f} {e:8.2f}" + (f" {x:g}" if x else "") + f"    # {why}")
        total += (e - s) if kind == "keep" else x
    lines.append(f"# output length about {total:.1f} s from {A['t'][-1]:.1f} s")
    pathlib.Path(a.plan).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{len(plan)} segment(s), about {total:.1f} s -> {a.plan}")
    return 0


# -- suggest (camera-motion draft, kept for small screen recordings) --------------------
def read_measure(path):
    with open(path, encoding="utf-8") as fh:
        return [{k: float(v) for k, v in r.items()} for r in csv.DictReader(fh)]


def suggest(rows, idle=1.2, min_idle=2.0, keep_idle=1.0, idle_speed=8.0, start=0.0, end=None):
    if not rows:
        return []
    step = rows[1]["t"] - rows[0]["t"] if len(rows) > 1 else 1.0
    end = rows[-1]["t"] + step if end is None else end
    rows = [r for r in rows if start <= r["t"] < end]
    runs, cur = [], None
    for r in rows:
        quiet = r["motion_1s"] < idle
        if quiet and cur is None:
            cur = [r["t"], r["t"] + step]
        elif quiet:
            cur[1] = r["t"] + step
        elif cur is not None:
            runs.append(cur)
            cur = None
    if cur is not None:
        runs.append(cur)
    runs = [r for r in runs if r[1] - r[0] >= min_idle]
    plan, t = [], start
    for s, e in runs:
        if s > t:
            plan.append(("keep", round(t, 2), round(s, 2)))
        k = min(e, s + keep_idle)
        plan.append(("keep", round(s, 2), round(k, 2)))
        if e > k:
            plan.append(("speed", round(k, 2), round(e, 2), idle_speed) if idle_speed else ("cut", round(k, 2), round(e, 2)))
        t = e
    if t < end:
        plan.append(("keep", round(t, 2), round(end, 2)))
    merged = []
    for p in plan:
        if merged and p[0] == "keep" and merged[-1][0] == "keep" and abs(merged[-1][2] - p[1]) < 1e-6:
            merged[-1] = ("keep", merged[-1][1], p[2])
        elif p[2] - p[1] > 1e-6:
            merged.append(p)
    return merged


def seg_out_len(kind, s, e, x):
    return {"keep": e - s, "speed": (e - s) / (x or 1), "hold": x or 0, "best": x or 0}[kind]


def write_plan(plan, path, note=""):
    lines = ["# videokit plan: keep S E | best S E SECONDS | hold S E SECONDS | speed S E FACTOR | unlisted = cut"]
    if note:
        lines.append("# " + note)
    out_len = 0.0
    for p in plan:
        if p[0] == "cut":
            lines.append(f"# cut   {p[1]:8.2f} {p[2]:8.2f}")
            continue
        extra = f" {p[3]:g}" if p[0] in ("speed", "hold", "best") else ""
        lines.append(f"{p[0]:<5} {p[1]:8.2f} {p[2]:8.2f}{extra}")
        out_len += seg_out_len(p[0], p[1], p[2], p[3] if len(p) > 3 else None)
    lines.append(f"# output length about {out_len:.1f} s")
    pathlib.Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_len


def read_plan(path):
    """[(kind, start, end, x)] where x is the speed factor or the hold/best seconds."""
    segs = []
    for n, line in enumerate(pathlib.Path(path).read_text(encoding="utf-8").splitlines(), 1):
        parts = line.split("#", 1)[0].split()
        if not parts:
            continue
        kind = parts[0]
        try:
            if kind == "keep" and len(parts) == 3:
                segs.append(("keep", float(parts[1]), float(parts[2]), 1.0))
            elif kind == "speed" and len(parts) == 4:
                f = float(parts[3])
                if not 1 < f <= 16:
                    raise ValueError("factor must be >1 and <=16")
                segs.append(("speed", float(parts[1]), float(parts[2]), f))
            elif kind == "best" and len(parts) == 4:
                d = float(parts[3])
                if not 0.3 <= d <= 10:
                    raise ValueError("best seconds must be 0.3-10")
                if d > float(parts[2]) - float(parts[1]):
                    raise ValueError("best seconds longer than its range")
                segs.append(("best", float(parts[1]), float(parts[2]), d))
            elif kind == "hold" and len(parts) == 4:
                d = float(parts[3])
                if not 0.2 <= d <= 10:
                    raise ValueError("hold seconds must be 0.2-10")
                segs.append(("hold", float(parts[1]), float(parts[2]), d))
            else:
                raise ValueError("expected 'keep S E', 'best S E SECONDS', 'hold S E SECONDS' or 'speed S E FACTOR'")
        except ValueError as e:
            raise SystemExit(f"plan line {n}: {e}: {line!r}")
        if segs[-1][2] <= segs[-1][1]:
            raise SystemExit(f"plan line {n}: end must be after start: {line!r}")
    if not segs:
        raise SystemExit("plan has no segments")
    return segs


def cmd_suggest(a):
    rows = read_measure(a.measure)
    plan = suggest(rows, a.idle, a.min_idle, a.keep_idle, a.idle_speed, a.start, a.end)
    if a.best:
        plan = [("best", p[1], p[2], min(a.best, p[2] - p[1])) if p[0] == "speed" else p for p in plan]
    elif a.hold:
        plan = [("hold", p[1], p[2], a.hold) if p[0] == "speed" else p for p in plan]
    length = write_plan(plan, a.plan, f"suggested from {pathlib.Path(a.measure).name}: idle<{a.idle} for >={a.min_idle}s")
    print(f"{len(plan)} segment(s), output about {length:.1f} s -> {a.plan}")
    return 0


# -- shake ------------------------------------------------------------------------------
def shake_stats(dx, dy, fps=30.0, smooth_s=0.5):
    """mean shift/frame and mean jitter (shift minus its moving average over smooth_s), px at AW wide."""
    import numpy as np
    dx, dy = np.asarray(dx, dtype=np.float64), np.asarray(dy, dtype=np.float64)
    if len(dx) < 2:
        return 0.0, 0.0
    k = max(1, min(len(dx), int(round(smooth_s * fps))))
    sx = np.convolve(dx, np.ones(k) / k, mode="same")
    sy = np.convolve(dy, np.ones(k) / k, mode="same")
    return float(np.hypot(dx, dy).mean()), float(np.hypot(dx - sx, dy - sy).mean())


def shake_window(A, s, e, edge_s=0.1):
    """shake_stats over [s, e), skipping edge_s each side (a cut or dissolve is not shake), but always
    keeping at least the middle half so short segments are still measured."""
    i0, i1 = idx(A, s, e)
    i0 = max(i0, 1)
    fps = fps_of(A)
    edge = min(int(round(edge_s * fps)), max(0, (i1 - i0) // 4))
    a, b = i0 + edge, max(i0 + edge + 2, i1 - edge)
    return shake_stats(A["dx"][a:b], A["dy"][a:b], fps)


def cmd_shake(a):
    A = analyse(a.video)
    fps = fps_of(A)
    if a.out:
        with open(a.out, "w", newline="", encoding="utf-8") as fh:
            wr = csv.writer(fh)
            wr.writerow(["t", "dx", "dy", "shift"])
            for t, dx, dy in zip(A["t"], A["dx"], A["dy"]):
                wr.writerow([round(t, 4), round(dx, 3), round(dy, 3), round((dx * dx + dy * dy) ** 0.5, 3)])
    m, j = shake_stats(A["dx"][1:], A["dy"][1:], fps)
    print(f"{pathlib.Path(a.video).name}: {len(A['t'])} frames at {fps:.2f} fps  shift/frame {m:.2f} px  jitter {j:.2f} px")
    if a.per_second:
        for s in range(int(A["t"][-1]) + 1):
            mm, jj = shake_window(A, s, s + 1, 0)
            print(f"  {s:6.1f}s  shift {mm:5.2f}  jitter {jj:5.2f}")
    return 0


# -- render log helpers -------------------------------------------------------------
def _log_lines(video):
    log = pathlib.Path(str(video) + ".render.txt")
    return log.read_text(encoding="utf-8").splitlines() if log.is_file() else None


def recorded_xfade(video):
    for line in _log_lines(video) or []:
        if line.startswith("xfade "):
            return float(line.split()[1])
    return 0.0


def recorded_source(video):
    lines = _log_lines(video)
    if lines and lines[0].startswith("source ") and "  plan " in lines[0]:
        return lines[0][len("source "):lines[0].index("  plan ")]
    return None


def recorded_windows(video):
    """Actual (out_start, out_end) per segment from VIDEO.render.txt, if render wrote one."""
    lines = _log_lines(video)
    if lines is None:
        return None
    return [(float(p[5]), float(p[6])) for p in (l.split() for l in lines) if p and p[0] == "#win"]


def recorded_source_ranges(video):
    """(kind, src_start, src_end) per segment as actually used (best -> the stretch it picked)."""
    lines = _log_lines(video)
    if lines is None:
        return None
    return [(q[2], float(q[3]), float(q[4])) for q in (l.split() for l in lines) if q and q[0] == "#win"]


def plan_windows(segs):
    """(kind, src_start, src_end, out_start, out_end) for each plan segment, from the plan alone."""
    t, out = 0.0, []
    for kind, s, e, x in segs:
        n = seg_out_len(kind, s, e, x)
        out.append((kind, s, e, t, t + n))
        t += n
    return out


def cmd_shakecompare(a):
    """Per plan segment: shift and jitter in the source (native speed) and in each rendered output."""
    segs = read_plan(a.plan)
    SA = analyse(a.source)
    outs = [(pathlib.Path(o).name, analyse(o), recorded_windows(o), recorded_xfade(o)) for o in a.outputs]
    print(f"{'segment':<26}{'source':>16}" + "".join(f"{n[:22]:>24}" for n, _, _, _ in outs))
    print(f"{'':<26}{'shift  jitter':>16}" + "".join(f"{'shift  jitter':>24}" for _ in outs))
    worse = 0
    planned = plan_windows(segs)
    for k, (kind, s, e, os_, oe) in enumerate(planned):
        sm, sj = shake_window(SA, s, e)
        row = f"{kind:<5} {s:6.1f}-{e:6.1f} ({oe - os_:4.1f}s) {sm:7.2f} {sj:7.2f}"
        for _, OA, rec, xf in outs:
            ws, we = rec[k] if rec and len(rec) == len(planned) else (os_, oe)
            m, j = shake_window(OA, ws, we, 0.1 + xf)
            flag = " !" if j > sj * 1.25 + 0.3 else "  "
            worse += flag == " !"
            row += f"{m:15.2f} {j:6.2f}{flag}"
        print(row)
    print("! = output jitter more than 25% above the source for that segment")
    return 1 if (a.strict and worse) else 0


# -- flow: freezes and hard cuts ----------------------------------------------------------
def flow_events(A, freeze_s=0.25, spike=4.0):
    """Freezes (consecutive frames nearly identical for >= freeze_s) and hard cuts (a one-frame change
    above 8 and over `spike` x the frames around it), on the analysis' real timestamps."""
    import numpy as np
    d, t = A["diff"], A["t"]
    freezes, run0 = [], None
    for i in range(1, len(d) + 1):
        still = i < len(d) and d[i] < 0.15
        if still and run0 is None:
            run0 = i
        elif not still and run0 is not None:
            dur = (t[i - 1] if i - 1 < len(t) else t[-1]) - t[run0 - 1]
            if dur >= freeze_s:
                freezes.append((round(float(t[run0]), 2), round(float(dur), 2)))
            run0 = None
    cuts = []
    for i in range(2, len(d) - 1):
        around = np.concatenate([d[max(1, i - 8):i - 1], d[i + 2:i + 9]])
        if len(around) and d[i] > 8 and d[i] > spike * max(float(np.median(around)), 0.5) \
                and d[i - 1] < 0.5 * d[i] and d[i + 1] < 0.5 * d[i]:
            cuts.append((round(float(t[i]), 2), round(float(d[i]), 1)))
    return freezes, cuts


def cmd_flow(a):
    """Freezes always count. A hard cut counts against an edit only at one of its joins (from
    VIDEO.render.txt): a page loading inside a kept shot is the screen changing, not the edit."""
    import numpy as np
    A = analyse(a.video)
    freezes, cuts = flow_events(A, a.freeze)
    wins = recorded_windows(a.video) or []
    joins = [w[0] for w in wins[1:]]
    xf = recorded_xfade(a.video)
    src, ranges = recorded_source(a.video), recorded_source_ranges(a.video)
    SA, src_cuts, src_freezes = None, [], []
    if src and pathlib.Path(src).is_file() and ranges:
        SA = analyse(src)
        src_freezes, cl = flow_events(SA, a.freeze)
        src_cuts = [c for c, _ in cl]

    def to_source(t):
        for (o0, o1), (kind, s0, e0) in zip(wins, ranges or []):
            if kind in ("keep", "best") and o0 <= t <= o1:
                return s0 + (t - o0)
        return None

    def in_source(t, v):
        st = to_source(t)
        if st is None or SA is None:
            return False
        if any(abs(st - c) <= 0.1 for c in src_cuts):
            return True
        i0, i1 = idx(SA, st - 0.1, st + 0.1)
        win = SA["diff"][max(1, i0):i1 + 1]
        return len(win) > 0 and float(win.max()) >= 0.4 * v  # the screen changed there too

    def frozen_in_source(t, n):
        st = to_source(t + n / 2)
        return st is not None and any(f0 - 0.1 <= st <= f0 + fn + 0.1 for f0, fn in src_freezes)
    bad = 0
    for t, n in freezes:
        if joins and frozen_in_source(t, n):
            print(f"freeze    at {t:6.2f}s for {n:.2f}s  (the source is still here too)")
            continue
        print(f"freeze    at {t:6.2f}s for {n:.2f}s")
        bad += 1
    for t, v in cuts:
        at_join = (any(abs(t - j) <= xf + 0.15 for j in joins) and not in_source(t, v)) if joins else True
        bad += at_join
        print(f"hard cut  at {t:6.2f}s (one-frame change {v})" + ("  <- at a join" if joins and at_join else
                                                                   "  (inside a shot: the screen changed)" if joins else ""))
    print(f"{pathlib.Path(a.video).name}: {len(freezes)} freeze(s) >= {a.freeze}s, {len(cuts)} hard cut(s)"
          + (f", {bad} counted against the edit" if joins else ""))
    return 1 if (a.strict and bad) else 0


# -- gimbal / tripod (OpenCV), streamed --------------------------------------------------
def decode_bgr(video, w, h, vf_pre=()):
    """Stream frames as BGR numpy arrays at w x h (ffmpeg decodes, auto-rotates, tone-maps if asked)."""
    import numpy as np
    vf = ",".join(list(vf_pre) + [f"scale={w}:{h}", "format=bgr24"])
    proc = subprocess.Popen(["ffmpeg", "-v", "error", "-i", str(video), "-fps_mode", "passthrough", "-vf", vf,
                             "-f", "rawvideo", "-"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    size = w * h * 3
    try:
        while True:
            buf = proc.stdout.read(size)
            if len(buf) < size:
                break
            yield np.frombuffer(buf, dtype=np.uint8).reshape(h, w, 3)
    finally:
        proc.stdout.close()
        proc.wait()


def camera_path(video, w, h, model="homography", track_side=480):
    """Pass 1 (streamed): cumulative transform frame t -> frame 0 from frame-to-frame tracking
    (goodFeaturesToTrack + pyramidal Lucas-Kanade + RANSAC). homography: a flat screen's shift,
    rotation, tilt and zoom. similarity: shift, rotation, zoom only - safe for 3D scenes.
    Returns (list of 3x3, steps that fell back to identity, median RANSAC inlier ratio)."""
    import cv2
    import numpy as np
    k = min(1.0, track_side / max(w, h))
    S = np.diag([k, k, 1.0])
    Si = np.linalg.inv(S)
    P, failed, ratios, prev = [np.eye(3)], 0, [], None
    for f in decode_bgr(video, w, h):
        g = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
        if k < 1:
            g = cv2.resize(g, None, fx=k, fy=k, interpolation=cv2.INTER_AREA)
        if prev is None:
            prev = g
            continue
        step = None
        pts = cv2.goodFeaturesToTrack(prev, maxCorners=800, qualityLevel=0.01, minDistance=7, blockSize=7)
        if pts is not None and len(pts) >= 20:
            nxt, st, _ = cv2.calcOpticalFlowPyrLK(prev, g, pts, None, winSize=(21, 21), maxLevel=3)
            ok = st.reshape(-1) == 1
            if ok.sum() >= 20:
                if model == "similarity":
                    M, mask = cv2.estimateAffinePartial2D(nxt[ok], pts[ok], method=cv2.RANSAC,
                                                          ransacReprojThreshold=1.5)
                    Hm = None if M is None else np.vstack([M, [0, 0, 1]])
                else:
                    Hm, mask = cv2.findHomography(nxt[ok], pts[ok], cv2.RANSAC, 1.5)  # cur -> prev
                if Hm is not None and mask is not None and int(mask.sum()) >= 20:
                    step = Si @ Hm @ S
                    ratios.append(float(mask.sum()) / float(ok.sum()))
        if step is None:
            failed += 1
            step = np.eye(3)
        P.append(P[-1] @ step)
        prev = g
    return P, failed, (float(np.median(ratios)) if ratios else 0.0)


def _corners(H, w, h):
    import cv2
    import numpy as np
    c = np.float32([[0, 0], [w, 0], [w, h], [0, h]]).reshape(-1, 1, 2)
    return cv2.perspectiveTransform(c, H).reshape(4, 2)


def stabilise_transforms(P, w, h, fps, mode="gimbal", follow=0.7):
    """Warp per frame onto a smoothed camera path. gimbal: corners Gaussian-smoothed (sigma = follow s);
    tripod: held at their average."""
    import cv2
    import numpy as np
    paths = np.array([_corners(H, w, h) for H in P])
    n = len(paths)
    if mode == "tripod" or n < 3:
        target = np.repeat(paths.mean(0, keepdims=True), n, 0)
    else:
        sig = max(1.0, follow * fps)
        r = int(3 * sig)
        x = np.arange(-r, r + 1)
        kern = np.exp(-0.5 * (x / sig) ** 2)
        kern /= kern.sum()
        padded = np.concatenate([paths[:1].repeat(r, 0), paths, paths[-1:].repeat(r, 0)])
        target = np.stack([np.stack([np.convolve(padded[:, c, d], kern, mode="valid") for d in (0, 1)], -1)
                           for c in range(4)], 1)
    base = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    out = []
    for A, T in zip(P, target):
        B = cv2.getPerspectiveTransform(base, T.astype(np.float32))
        out.append(np.linalg.inv(B) @ A)
    return out


def lock_crop(Hs, w, h, cap=0.18):
    """(crop used, crop needed) per side: the smallest centred crop every warped frame covers."""
    m = 0.0
    for H in Hs:
        c = _corners(H, w, h)
        need_ = max(max(c[0, 0], c[3, 0]) / w, (w - min(c[1, 0], c[2, 0])) / w,
                    max(c[0, 1], c[1, 1]) / h, (h - min(c[2, 1], c[3, 1])) / h)
        m = max(m, need_)
    needed = max(0.0, m + 0.004)
    return min(cap, needed), needed


def distortion(Hs, w, h):
    """How much the warps bend the picture: max corner-angle deviation from 90 degrees and max area change.
    A homography on a non-flat (3D) scene, or a bad fit, shows up here even when jitter looks low."""
    import numpy as np
    worst_angle, worst_area = 0.0, 0.0
    for H in Hs:
        c = _corners(H, w, h)
        for i in range(4):
            v1, v2 = c[(i + 1) % 4] - c[i], c[(i - 1) % 4] - c[i]
            cosang = float(np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2) + 1e-9))
            worst_angle = max(worst_angle, abs(np.degrees(np.arccos(np.clip(cosang, -1, 1))) - 90.0))
        x, y = c[:, 0], c[:, 1]
        area = 0.5 * abs(float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))
        worst_area = max(worst_area, abs(area / (w * h) - 1.0))
    return worst_angle, worst_area


def lock_video(src, dst, look=(), audio=True, fps=None, mode="gimbal", follow=0.7, max_crop=0.18,
               model="homography", P=None, track=None):
    """Pass 2 (streamed): warp every frame onto the stabilised path, crop what all frames cover, apply
    `look`, keep the audio. P (and its tracking size `track` = (w, h)) can be passed in to reuse pass 1.
    Returns dict(crop, needed, failed, inliers, angle, area)."""
    import cv2
    import numpy as np
    info = probe(src)
    w, h = info["width"], info["height"]
    fps = fps or out_fps(info)
    failed, inl = 0, 1.0
    if P is None:
        P, failed, inl = camera_path(src, w, h, model)
    elif track and track != (w, h):  # path measured on a preview: rescale to this size
        S = np.diag([w / track[0], h / track[1], 1.0])
        Si = np.linalg.inv(S)
        P = [S @ H @ Si for H in P]
    Hs = stabilise_transforms(P, w, h, fps, mode, follow)
    m, needed = lock_crop(Hs, w, h, max_crop)
    ang, area = distortion(Hs, w, h)
    cw, ch = int(round(w * (1 - 2 * m) / 2) * 2), int(round(h * (1 - 2 * m) / 2) * 2)
    vf = [f"crop={cw}:{ch}:{(w - cw) // 2}:{(h - ch) // 2}", f"scale={w}:{h}:flags=lanczos"] + list(look) + ["format=yuv420p"]
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{w}x{h}", "-r", f"{fps}",
           "-i", "-", "-i", str(src), "-map", "0:v", "-vf", ",".join(vf), "-c:v", "libx264", "-crf", "16",
           "-preset", "medium", "-pix_fmt", "yuv420p", "-r", f"{fps}"]
    cmd += (["-map", "1:a", "-c:a", "copy"] if audio and info["audio"] else ["-an"]) + [str(dst)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    n = 0
    for f, H in zip(decode_bgr(src, w, h), Hs):
        proc.stdin.write(cv2.warpPerspective(f, H, (w, h), flags=cv2.INTER_CUBIC,
                                             borderMode=cv2.BORDER_REPLICATE).tobytes())
        n += 1
    proc.stdin.close()
    err = proc.stderr.read().decode("utf-8", "replace")
    if proc.wait() != 0:
        raise SystemExit("stabilise encode failed: " + err[-2000:])
    return {"crop": m, "needed": needed, "failed": failed, "inliers": inl, "angle": ang, "area": area}


def cmd_lock(a):
    info = probe(a.video)
    r = lock_video(a.video, a.out, audio=info["audio"], mode=a.mode, follow=a.follow, model=a.model,
                   max_crop=a.max_crop)
    jin = shake_stats(analyse(a.video)["dx"][1:], analyse(a.video)["dy"][1:])[1]
    OA = analyse(a.out)
    jout = shake_stats(OA["dx"][1:], OA["dy"][1:])[1]
    print(f"{a.mode} ({a.model}): {pathlib.Path(a.video).name} -> {pathlib.Path(a.out).name}: crop "
          f"{r['crop'] * 100:.1f}% per side, {r['failed']} step(s) untracked, inliers {r['inliers']:.2f}, "
          f"bend {r['angle']:.1f} deg / area {r['area'] * 100:.0f}%, jitter {jin:.2f} -> {jout:.2f} px"
          + (f"  (needed {r['needed'] * 100:.1f}%: edges stretched)" if r["needed"] > r["crop"] + 1e-6 else ""))
    return 0


# -- joining: dissolves in bounded batches ----------------------------------------------
JOIN_BATCH = 8


def _xfade_join(files, lens, xs, out, fps, audio, crf, preset, td, final):
    """Join files with dissolves xs[i] between files[i-1] and files[i] (xs[0] unused). One ffmpeg
    decodes every input at once, so this is only ever called with <= JOIN_BATCH inputs."""
    ins = sum((["-i", str(f)] for f in files), [])
    g = [f"[{i}:v]settb=AVTB,fps={fps},format=yuv420p[v{i}]" for i in range(len(files))]
    prev, merged = "v0", lens[0]
    for i in range(1, len(files)):
        g.append(f"[{prev}][v{i}]xfade=transition=fade:duration={xs[i]}:offset={merged - xs[i]:.4f}[x{i}]")
        prev = f"x{i}"
        merged += lens[i] - xs[i]
    pa = "0:a"
    if audio:
        for i in range(1, len(files)):
            g.append(f"[{pa}][{i}:a]acrossfade=d={xs[i]}:c1=tri:c2=tri[a{i}]")
            pa = f"a{i}"
    vgraph = [x for x in g if not x.startswith("[0:a]") and "acrossfade" not in x]
    agraph = [x for x in g if "acrossfade" in x]
    venc = ["-c:v", "libx264", "-crf", str(crf), "-preset", preset, "-pix_fmt", "yuv420p"]
    abr = "128k" if final else "192k"
    tail = ["-movflags", "+faststart"] if final else []

    # ------------------------------------------------------------------
    # FOUR OR MORE INPUTS WITH AUDIO MUST BE RENDERED IN SEPARATE PASSES.
    # ------------------------------------------------------------------
    # ffmpeg 7.1.5 cannot run an xfade video chain and a chained acrossfade
    # audio chain in ONE command past three inputs. The audio encoder is never
    # opened and the output file is zero bytes:
    #
    #   [aost#0:1/aac] Could not open encoder before EOF
    #   [out#0/mp4] Nothing was written into output file
    #
    # Measured 2026-10-13, bisected by input count on this exact ffmpeg:
    #   2 inputs WORKS, 3 WORKS, 4 FAILS, 5 FAILS, 6 FAILS, 8 FAILS.
    #
    # Each chain alone is fine at eight inputs: video alone produced 2.60 s,
    # audio alone 2.70 s. Only the combination deadlocks, because acrossfade
    # must consume its whole first input before emitting anything while xfade
    # emits progressively, and the two compete for the same demuxers.
    #
    # Neither -filter_complex_threads 1, -filter_threads 1, -shortest nor
    # -max_muxing_queue_size makes any difference; all were tried.
    #
    # So: render video, render audio, then MUX BY STREAM COPY, which re-encodes
    # nothing. The audio pass is cheap. -shortest holds the result to the video
    # timeline, which is the planned one; acrossfade runs about 0.1 s long.
    if audio and len(files) >= 4:
        vtmp = pathlib.Path(td) / (pathlib.Path(out).stem + ".v.mp4")
        atmp = pathlib.Path(td) / (pathlib.Path(out).stem + ".a.m4a")
        run(["ffmpeg", "-v", "error", "-y"] + ins + ["-filter_complex", ";".join(vgraph),
             "-map", f"[{prev}]", "-an"] + venc + [str(vtmp)], cwd=td)
        run(["ffmpeg", "-v", "error", "-y"] + ins + ["-filter_complex", ";".join(agraph),
             "-map", f"[{pa}]", "-vn", "-c:a", "aac", "-b:a", abr, str(atmp)], cwd=td)
        run(["ffmpeg", "-v", "error", "-y", "-i", str(vtmp), "-i", str(atmp),
             "-map", "0:v", "-map", "1:a", "-c", "copy", "-shortest"] + tail + [str(out)], cwd=td)
        return merged

    cmd = ["ffmpeg", "-v", "error", "-y"] + ins + ["-filter_complex", ";".join(g), "-map", f"[{prev}]"]
    cmd += (["-map", f"[{pa}]", "-c:a", "aac", "-b:a", abr] if audio else ["-an"])
    cmd += venc
    run(cmd + tail + [str(out)], cwd=td)
    return merged


def join_with_dissolves(files, lens, xs, out, fps, audio, crf, td):
    """Same timeline as one big xfade chain, but at most JOIN_BATCH inputs open at once: batches are
    joined first (near-lossless), then the batches are joined with the dissolve that sits between them.
    Measured 2026-10-12: one chain over 45 1080p pieces peaked at 3.5 GB of memory."""
    level, items = 0, list(zip(files, lens, xs))
    while len(items) > JOIN_BATCH:
        nxt = []
        for b in range(0, len(items), JOIN_BATCH):
            grp = items[b:b + JOIN_BATCH]
            if len(grp) == 1:
                nxt.append(grp[0])
                continue
            o = pathlib.Path(td) / f"join{level}_{b // JOIN_BATCH:03d}.mp4"
            gx = [0.0] + [x for _, _, x in grp[1:]]
            n = _xfade_join([f for f, _, _ in grp], [l for _, l, _ in grp], gx, o, fps, audio, 12, "veryfast", td, False)
            nxt.append((o, n, grp[0][2]))  # the dissolve INTO this batch is the one into its first piece
        items, level = nxt, level + 1
    return _xfade_join([f for f, _, _ in items], [l for _, l, _ in items], [x for _, _, x in items], out, fps,
                       audio, crf, "slow", td, True)


# -- render ------------------------------------------------------------------------
def _atempo(f):
    parts = []
    while f > 2.0:
        parts.append("atempo=2.0")
        f /= 2.0
    parts.append(f"atempo={f:.6f}")
    return ",".join(parts)


def auto_levels(A, windows):
    """Gamma (and an offset only if very dark) so the used footage's mean luma lands near 118.
    Measured 2026-10-12: a brightness offset greys black and flattens white pages."""
    import numpy as np
    vals = np.concatenate([A["luma"][slice(*idx(A, s, e))] for s, e in windows] or [np.array([118.0])])
    mean = float(vals.mean()) if len(vals) else 118.0
    gamma = min(1.6, max(1.0, (118.0 / max(mean, 1.0)) ** 0.6))
    bright = round(min(0.05, max(0.0, (60.0 - mean) / 255.0)), 3)
    return mean, bright, gamma


def best_still(A, s, e):
    """Time of the steadiest, sharpest frame in [s, e]."""
    import numpy as np
    i0, i1 = idx(A, s, e)
    if i1 <= i0:
        return s
    score = A["sharp"][i0:i1] / (1.0 + A["diff"][i0:i1])
    return float(A["t"][i0 + int(np.argmax(score))])


def best_window(A, s, e, d):
    """Start time of the d-second stretch inside [s, e] with the least camera movement (real timestamps)."""
    import numpy as np
    i0, i1 = idx(A, s, e)
    i0 = max(i0, 1)
    if i1 - i0 < 3:
        return s
    t = A["t"]
    mag = np.hypot(A["dx"], A["dy"])
    c = np.concatenate([[0.0], np.cumsum(mag)])
    best, best_t = None, s
    for i in range(i0, i1):
        j = int(np.searchsorted(t, t[i] + d, "left"))
        if t[i] + d > e + 1e-6 or j > i1:
            break
        v = (c[j] - c[i]) / max(1, j - i)
        if best is None or v < best:
            best, best_t = v, float(t[i])
    return round(best_t, 3)


def cmd_render(a):
    """Each plan segment is rendered on its own, then joined with dissolves (see the module help)."""
    import numpy as np
    need("ffmpeg")
    info = probe(a.video)
    segs = read_plan(a.plan)
    for _, s, e, _x in segs:
        if e > info["duration"] + 0.5:
            raise SystemExit(f"plan segment ends at {e}s, video is {info['duration']:.1f}s")
    SA = analyse(a.video)
    audio = info["audio"] and a.audio == "keep"
    fps = out_fps(info)
    W, H = info["width"], info["height"]
    pw = min(W, PREVIEW_W)
    ph = int(round(H * pw / W / 2) * 2)
    mean, bright, gamma = auto_levels(SA, [(s, e) for _, s, e, _ in segs])
    if a.brighten != "auto":
        bright = float(a.brighten)
    if a.gamma != "auto":
        gamma = float(a.gamma)
    look = []
    if bright or gamma != 1.0:
        look.append(f"eq=brightness={bright}:gamma={gamma:.3f}")
    if a.sharpen > 0:
        look.append(f"unsharp=5:5:{a.sharpen}:5:5:0")
    pre = hdr_filter(info)
    crop4 = lambda w_, h_: f"crop=iw*0.96:ih*0.96,scale={w_}:{h_}"
    enc = ["-r", str(fps), "-c:v", "libx264", "-crf", "16", "-preset", "medium", "-pix_fmt", "yuv420p"]
    aenc = ["-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2"] if audio else ["-an"]
    acopy = ["-c:a", "copy"] if audio else ["-an"]
    log = [f"source {a.video}  plan {a.plan}",
           f"probe: {W}x{H} fps {info['fps']:.3f} (avg {info['avg_fps']:.3f}{', VFR' if info['vfr'] else ''})"
           f"{', HDR tone-mapped' if info['hdr'] else ''}{', rotated ' + str(info['rotation']) if info['rotation'] else ''}",
           f"levels: measured mean luma {mean:.1f} -> brightness {bright} gamma {gamma:.3f}; sharpen {a.sharpen}"]
    out = pathlib.Path(a.out).resolve()
    with tempfile.TemporaryDirectory() as td:
        td = pathlib.Path(td)
        files, used = [], []
        for i, (kind, s, e, x) in enumerate(segs):
            if kind == "best":
                t = best_window(SA, s, e, x)
                log.append(f"{i:3d} best  {s:7.2f}-{e:7.2f} -> steadiest {x:g}s starts at {t:.2f}s")
                kind, s, e = "keep", t, t + x
            used.append((segs[i][0], s, e))
            plain = td / f"s{i:03d}.mp4"
            if kind == "hold":
                t = best_still(SA, s, e)
                png = td / f"s{i:03d}.png"
                run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", str(a.video), "-frames:v", "1"]
                    + (["-vf", ",".join(pre)] if pre else []) + [str(png)])
                cmd = ["ffmpeg", "-v", "error", "-y", "-loop", "1", "-t", f"{x}", "-i", str(png)]
                if audio:
                    cmd += ["-f", "lavfi", "-t", f"{x}", "-i", "anullsrc=r=48000:cl=stereo"]
                cmd += ["-vf", ",".join(look + [crop4(W, H), "format=yuv420p"])] + enc + aenc + ["-shortest", str(plain)]
                run(cmd)
                files.append(plain)
                log.append(f"{i:3d} hold  {s:7.2f}-{e:7.2f} -> still at {t:.2f}s for {x}s")
                continue
            # raw cut at full size (tone-mapped, sped up if asked); the source of every candidate
            raw = td / f"r{i:03d}.mp4"
            vf_raw = pre + ([f"setpts=PTS/{x}"] if kind == "speed" else [])
            af = (["-af", _atempo(x)] if kind == "speed" else []) if audio else []
            run(["ffmpeg", "-v", "error", "-y", "-ss", f"{s:.3f}", "-to", f"{e:.3f}", "-i", str(a.video),
                 "-vf", ",".join(vf_raw) or "null"] + enc + aenc + af + [str(raw)])
            if a.no_stab:
                run(["ffmpeg", "-v", "error", "-y", "-i", str(raw), "-vf", ",".join(look + [crop4(W, H), "format=yuv420p"])]
                    + enc + acopy + [str(plain)])
                files.append(plain)
                log.append(f"{i:3d} {kind:<5} {s:7.2f}-{e:7.2f}" + (f" x{x:g}" if kind == "speed" else "") + " -> plain")
                continue
            # candidates on a preview (<= PREVIEW_W wide); only the winner is rendered at full size
            small = raw
            if W > PREVIEW_W:
                small = td / f"p{i:03d}.mp4"
                run(["ffmpeg", "-v", "error", "-y", "-i", str(raw), "-vf", f"scale={pw}:{ph}"] + enc + acopy + [str(small)])
            cands = []

            def measure_cand(path):
                CA = analyse(path)
                return shake_stats(CA["dx"][1:], CA["dy"][1:], fps_of(CA))[1]
            c_plain = td / f"c{i:03d}_plain.mp4"
            run(["ffmpeg", "-v", "error", "-y", "-i", str(small), "-vf", ",".join([crop4(pw, ph), "format=yuv420p"])]
                + enc + acopy + [str(c_plain)])
            jp = measure_cand(c_plain)
            cands.append({"jit": jp, "name": "plain", "crop": 0.04})
            trf = f"s{i:03d}.trf"  # relative: vidstab rejects a Windows drive colon in the path
            run(["ffmpeg", "-v", "error", "-y", "-i", small.name, "-vf",
                 f"vidstabdetect=shakiness=8:accuracy=15:result={trf}", "-f", "null", "-"], cwd=td)
            c_smooth = td / f"c{i:03d}_smooth.mp4"
            run(["ffmpeg", "-v", "error", "-y", "-i", small.name, "-vf",
                 f"vidstabtransform=input={trf}:smoothing=20:zoom=4:optzoom=0:interpol=bicubic,format=yuv420p"]
                + enc + acopy + [c_smooth.name], cwd=td)
            cands.append({"jit": measure_cand(c_smooth), "name": "smooth", "crop": 0.04})
            if a.stab in ("auto", "gimbal"):
                paths = {}
                for model in ("homography", "similarity"):
                    paths[model] = camera_path(small, pw, ph, model)
                for mode, follow, model in (("gimbal", 0.25, "homography"), ("gimbal", 0.5, "homography"),
                                            ("gimbal", 1.2, "homography"), ("tripod", 0.7, "homography"),
                                            ("gimbal", 0.5, "similarity")):
                    name = f"{mode}{' ' + str(follow) if mode == 'gimbal' else ''}{' sim' if model == 'similarity' else ''}"
                    P, failed, inl = paths[model]
                    g = td / f"c{i:03d}_{name.replace(' ', '_')}.mp4"
                    r = lock_video(small, g, (), audio, fps, mode, follow, a.max_crop, model, P, (pw, ph))
                    r["failed"], r["inliers"] = failed, inl
                    why = None
                    if r["needed"] > a.max_crop + 1e-6:
                        why = f"needs {r['needed'] * 100:.1f}% crop"
                    elif r["angle"] > a.max_bend:
                        why = f"bends the picture {r['angle']:.1f} deg"
                    elif r["area"] > 0.15 + 2 * a.max_crop:
                        why = f"changes scale {r['area'] * 100:.0f}%"
                    if why:
                        log.append(f"      {name} rejected: {why}")
                        continue
                    cands.append({"jit": measure_cand(g), "name": name, "crop": r["crop"], "mode": mode,
                                  "follow": follow, "model": model, "P": P})
            best = min(cands, key=lambda c: c["jit"])
            if best["name"] != "plain" and best["jit"] > jp * 0.9:  # must clearly beat doing nothing
                best = cands[0]
            note = best["name"] + (f" crop {best['crop'] * 100:.0f}%" if best.get("mode") else "")
            note += "  (jitter " + ", ".join(f"{c['name']} {c['jit']:.2f}" for c in cands) + " px)"
            # final render of the winner at full size
            if best["name"] == "plain":
                run(["ffmpeg", "-v", "error", "-y", "-i", str(raw), "-vf", ",".join(look + [crop4(W, H), "format=yuv420p"])]
                    + enc + acopy + [str(plain)])
            elif best["name"] == "smooth":
                if W > PREVIEW_W:
                    run(["ffmpeg", "-v", "error", "-y", "-i", raw.name, "-vf",
                         f"vidstabdetect=shakiness=8:accuracy=15:result=f{trf}", "-f", "null", "-"], cwd=td)
                    tf = f"f{trf}"
                else:
                    tf = trf
                run(["ffmpeg", "-v", "error", "-y", "-i", raw.name, "-vf",
                     ",".join([f"vidstabtransform=input={tf}:smoothing=20:zoom=4:optzoom=0:interpol=bicubic"] + look
                              + ["format=yuv420p"])] + enc + acopy + [plain.name], cwd=td)
            else:
                lock_video(raw, plain, look, audio, fps, best["mode"], best["follow"], a.max_crop, best["model"],
                           best["P"], (pw, ph))
            files.append(plain)
            log.append(f"{i:3d} {kind:<5} {s:7.2f}-{e:7.2f}" + (f" x{x:g}" if kind == "speed" else "") + f" -> {note}")
        lens = [video_duration(f) for f in files]
        X = a.xfade if len(files) > 1 else 0.0
        xs = [0.0] + [round(min(X, 0.4 * lens[i - 1], 0.4 * lens[i]), 3) for i in range(1, len(lens))]
        log.append(f"xfade {X}")
        starts, merged = [0.0], lens[0]
        for i in range(1, len(lens)):
            starts.append(merged - xs[i])
            merged += lens[i] - xs[i]
        for i, d in enumerate(lens):
            k0, s0, e0 = used[i]
            log.append(f"#win {i} {k0} {s0} {e0} {starts[i]:.4f} {starts[i] + d:.4f}")
        if X:
            join_with_dissolves(files, lens, xs, out, fps, audio, a.crf, td)
        else:
            lst = td / "list.txt"
            lst.write_text("".join(f"file '{f.name}'\n" for f in files), encoding="utf-8")
            run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst.name, "-c:v", "libx264",
                 "-crf", str(a.crf), "-preset", "slow", "-pix_fmt", "yuv420p", "-movflags", "+faststart"]
                + (["-c:a", "aac", "-b:a", "128k"] if audio else ["-an"]) + [str(out)], cwd=td)
    o = probe(out)
    OA = analyse(out)
    m, j = shake_stats(OA["dx"][1:], OA["dy"][1:], fps_of(OA))
    log.append(f"output {o['duration']:.1f} s {o['width']}x{o['height']}  shift/frame {m:.2f} px  jitter {j:.2f} px")
    pathlib.Path(str(out) + ".render.txt").write_text("\n".join(log) + "\n", encoding="utf-8")
    print("\n".join(x for x in log[1:] if not x.startswith("#win")))
    print("flow check (freezes, and hard cuts at joins):")
    cmd_flow(argparse.Namespace(video=str(out), freeze=0.25, strict=False))
    print(f"wrote {out}  (source {info['duration']:.1f} s; decisions in {out.name}.render.txt)")
    return 0


# -- doctor ------------------------------------------------------------------------
PIP_NEEDS = {"numpy": "numpy", "PIL": "pillow", "cv2": "opencv-python"}
FILTERS = ("vidstabdetect", "vidstabtransform", "unsharp", "eq", "xfade", "acrossfade", "zscale", "tonemap")


def doctor_checks():
    """(name, ok, detail, how-to-install) for everything this tool needs on this machine."""
    import importlib.util
    import platform
    out = [("python>=3.9", sys.version_info >= (3, 9), platform.python_version(), "install Python 3.9+")]
    for mod, pkg in PIP_NEEDS.items():
        out.append((pkg, importlib.util.find_spec(mod) is not None, "", f"{sys.executable} -m pip install {pkg}"))
    if importlib.util.find_spec("cv2") is not None:
        import cv2
        ok = all(hasattr(cv2, n) for n in ("goodFeaturesToTrack", "calcOpticalFlowPyrLK", "findHomography",
                                           "estimateAffinePartial2D", "warpPerspective"))
        out.append(("opencv features (flow, homography)", ok, cv2.__version__, "pip install --upgrade opencv-python"))
    if importlib.util.find_spec("vidstab") is not None:
        out.append(("PyPI vidstab ABSENT", False, "installed", f"{sys.executable} -m pip uninstall -y vidstab   "
                    "(broken with OpenCV 5; not used)"))
    ff = shutil.which("ffmpeg")
    out.append(("ffmpeg", bool(ff), ff or "", ffmpeg_install_hint()))
    out.append(("ffprobe", bool(shutil.which("ffprobe")), "", ffmpeg_install_hint()))
    if ff:
        r = subprocess.run([ff, "-hide_banner", "-filters"], capture_output=True, text=True, errors="replace")
        for flt in FILTERS:
            out.append((f"ffmpeg filter {flt}", f" {flt} " in r.stdout, "",
                        "this ffmpeg build lacks it: install a full build (" + ffmpeg_install_hint() + ")"))
    try:
        free = shutil.disk_usage(tempfile.gettempdir()).free / 2 ** 30
        out.append(("temp space >= 5 GB", free >= 5, f"{free:.1f} GB free in {tempfile.gettempdir()}",
                    "free disk space: renders keep every segment's candidates until the join"))
    except OSError:
        pass
    return out


def ffmpeg_install_hint():
    if os_name() == "windows":
        if shutil.which("scoop"):
            return "scoop install ffmpeg   (gyan.dev full build via scoop; user-level, no admin)"
        if shutil.which("winget"):
            return "winget install --id Gyan.FFmpeg -e   (gyan.dev full build)"
        return "download the full build from https://www.gyan.dev/ffmpeg/builds/ and put bin\\ on PATH"
    if shutil.which("apt-get"):
        return "sudo apt-get install -y ffmpeg   (Debian/Ubuntu build includes vidstab)"
    if shutil.which("dnf"):
        return "sudo dnf install -y ffmpeg"
    return "install ffmpeg with libvidstab, zimg and xfade from your package manager"


def os_name():
    import platform
    return platform.system().lower()


def cmd_doctor(a):
    checks = doctor_checks()
    missing = [c for c in checks if not c[1]]
    for name, ok, detail, how in checks:
        print(f"{'ok ' if ok else 'MISSING'}  {name:<36} {detail}" + ("" if ok else f"\n         -> {how}"))
    if a.video:
        p = probe(a.video)
        notes = []
        if p["vfr"]:
            notes.append(f"variable frame rate (avg {p['avg_fps']:.3f} vs nominal {p['fps']:.3f}): handled by real timestamps")
        if p["hdr"]:
            notes.append("HDR: tone-mapped to SDR with zscale+tonemap")
        if p["rotation"]:
            notes.append(f"rotation {p['rotation']}: ffmpeg auto-rotates; size reported as seen")
        if p["bit10"]:
            notes.append("10-bit: decoded to 8-bit")
        if p["fps"] > 31:
            notes.append(f"{p['fps']:.0f} fps: jitter numbers are per frame, so lower than at 30 fps for the same shake")
        print(f"video {pathlib.Path(a.video).name}: {p['width']}x{p['height']}, {p['duration']:.1f} s; "
              + ("; ".join(notes) or "nothing special"))
    if missing and a.install:
        pip = [PIP_NEEDS[m] for m in PIP_NEEDS if any(c[0] == PIP_NEEDS[m] and not c[1] for c in checks)]
        if pip:
            print("installing:", " ".join(pip), "(from PyPI)")
            subprocess.run([sys.executable, "-m", "pip", "install", *pip], check=False)
        if any(c[0] == "PyPI vidstab ABSENT" for c in missing):
            subprocess.run([sys.executable, "-m", "pip", "uninstall", "-y", "vidstab"], check=False)
        if any(c[0].startswith("ffmpeg") or c[0] == "ffprobe" for c in missing):
            hint = ffmpeg_install_hint()
            if hint.startswith(("scoop ", "winget ")):
                cmd = hint.split("   ")[0].split()
                print("installing:", " ".join(cmd))
                subprocess.run(cmd, check=False)
            else:
                print("ffmpeg needs a system install; run yourself:", hint)
        checks = doctor_checks()
        missing = [c for c in checks if not c[1]]
        print("after install:", "all present" if not missing else "still missing: " + ", ".join(c[0] for c in missing))
    elif missing:
        print(f"{len(missing)} missing. Re-run with --install to fetch them (pip from PyPI; ffmpeg via scoop/winget).")
    return 1 if missing else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("doctor")
    p.add_argument("--install", action="store_true", help="install what is missing (pip, scoop or winget)")
    p.add_argument("--video", default="", help="also report what is special about this video (VFR, HDR, rotation)")
    p.set_defaults(fn=cmd_doctor)
    p = sub.add_parser("probe")
    p.add_argument("video")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_probe)
    p = sub.add_parser("analyse")
    p.add_argument("video")
    p.set_defaults(fn=cmd_analyse)
    p = sub.add_parser("frames")
    p.add_argument("video")
    p.add_argument("out")
    p.add_argument("--every", type=float, default=1.0)
    p.add_argument("--width", type=int, default=360)
    p.add_argument("--at-events", action="store_true", help="stills just before and after each content change")
    p.set_defaults(fn=cmd_frames)
    p = sub.add_parser("measure")
    p.add_argument("video")
    p.add_argument("out")
    p.add_argument("--fps", type=float, default=4)
    p.set_defaults(fn=cmd_measure)
    p = sub.add_parser("events")
    p.add_argument("video")
    p.add_argument("--out", default="")
    p.set_defaults(fn=cmd_events)
    p = sub.add_parser("draft")
    p.add_argument("video")
    p.add_argument("plan")
    p.add_argument("--pad", type=float, default=1.0, help="seconds kept around each content change")
    p.add_argument("--merge-gap", type=float, default=2.5, help="join kept moments closer than this")
    p.add_argument("--wait-min", type=float, default=3.0, help="quiet stretches at least this long become 'best'")
    p.add_argument("--best", type=float, default=1.0, help="seconds of steadiest footage per quiet stretch")
    p.set_defaults(fn=cmd_draft)
    p = sub.add_parser("suggest")
    p.add_argument("measure")
    p.add_argument("plan")
    p.add_argument("--idle", type=float, default=1.2)
    p.add_argument("--min-idle", type=float, default=2.0)
    p.add_argument("--keep-idle", type=float, default=1.0)
    p.add_argument("--idle-speed", type=float, default=8.0, help="0 = cut idle time instead of speeding it")
    p.add_argument("--start", type=float, default=0.0)
    p.add_argument("--end", type=float, default=None)
    p.add_argument("--best", type=float, default=1.0)
    p.add_argument("--hold", type=float, default=0.0)
    p.set_defaults(fn=cmd_suggest)
    p = sub.add_parser("shake")
    p.add_argument("video")
    p.add_argument("--out", default="", help="per-frame CSV: t,dx,dy,shift")
    p.add_argument("--per-second", action="store_true")
    p.set_defaults(fn=cmd_shake)
    p = sub.add_parser("lock")
    p.add_argument("video")
    p.add_argument("out")
    p.add_argument("--mode", choices=["gimbal", "tripod"], default="gimbal")
    p.add_argument("--follow", type=float, default=0.7, help="gimbal: seconds of drift smoothing (sigma)")
    p.add_argument("--model", choices=["homography", "similarity"], default="homography",
                   help="homography for a flat screen; similarity (no tilt) for rooms and other 3D scenes")
    p.add_argument("--max-crop", type=float, default=0.18)
    p.set_defaults(fn=cmd_lock)
    p = sub.add_parser("flow")
    p.add_argument("video")
    p.add_argument("--freeze", type=float, default=0.25, help="report frozen runs at least this long (s)")
    p.add_argument("--strict", action="store_true", help="exit 1 on any freeze or hard cut the edit introduced")
    p.set_defaults(fn=cmd_flow)
    p = sub.add_parser("shakecompare")
    p.add_argument("source")
    p.add_argument("plan")
    p.add_argument("outputs", nargs="+", help="rendered versions to compare, e.g. cut-only and stabilised")
    p.add_argument("--strict", action="store_true", help="exit 1 if any segment is shakier than the source")
    p.set_defaults(fn=cmd_shakecompare)
    p = sub.add_parser("render")
    p.add_argument("video")
    p.add_argument("plan")
    p.add_argument("out")
    p.add_argument("--no-stab", action="store_true")
    p.add_argument("--max-crop", type=float, default=0.18,
                   help="largest crop per side a gimbal/tripod candidate may need (owner: more looks unnatural)")
    p.add_argument("--max-bend", type=float, default=3.0,
                   help="reject a stabiliser whose warp bends corners more than this many degrees")
    p.add_argument("--stab", choices=["auto", "gimbal", "smooth"], default="auto")
    p.add_argument("--sharpen", type=float, default=0.6, help="unsharp luma amount; 0 = off")
    p.add_argument("--brighten", default="auto")
    p.add_argument("--gamma", default="auto")
    p.add_argument("--audio", choices=["keep", "drop"], default="keep")
    p.add_argument("--crf", type=int, default=20)
    p.add_argument("--xfade", type=float, default=0.3, help="cross-dissolve seconds at every join (0 = hard cuts)")
    p.set_defaults(fn=cmd_render)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
