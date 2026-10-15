# Version: 1.1.0 · updated 26-10-15-11-31
"""Does the installed browser decode video on the GPU's video engine? Measured, not assumed.

    fieldkit build-harness decode-test TASK [--seconds N]

Born 2026-10-15 (D-158-06): the GPU blocklist unlock was in the code and the prefs, "not yet proven running". Owner:
"install the binary here and let's test it". Earlier proof on this VAIO (Gorilla 153 audit, owner's notes): YouTube
1080p25 avc1, CPU about 0%, intel_gpu_top Video about 8%, 0 of 1422 frames dropped.

Deterministic steps, no model:
  1. a local 1920x1080 30 fps H.264 High clip made by ffmpeg (testsrc2), so the result never depends on a website
  2. the installed browser opens it in a throwaway profile (--no-remote), next to whatever the person has running
  3. after a warm-up, `intel_gpu_top -J` samples the engines once a second (sudo -n: never a password prompt) while
     /proc gives the CPU time of that browser's processes only
  4. the page reports the video's own counters (getVideoPlaybackQuality: frames shown and dropped, and the play
     position) to a local HTTP server the test runs, so "nothing played" can never pass for "played cheaply"
  4b. a hidden or covered test window makes Firefox suspend the video and throttle the page's timers (2026-10-15:
     three of five runs "failed" while the owner was using the desktop; the page simply stopped reporting). The
     throwaway profile turns that off, and a run whose page reported for less than 80% of the measured seconds is
     INCONCLUSIVE, never SOFTWARE
  5. verdict: HARDWARE when the Video engine is busy and frames were shown; NOT-PLAYING when the position did not
     move; SOFTWARE when frames were shown with the Video engine at zero; the numbers are reported either way
"""
import http.server
import json
import os
import threading
import urllib.parse
import re
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path

APP = Path("/usr/lib/gorilla-unleashed/firefox")
CLK = os.sysconf("SC_CLK_TCK")


def make_clip(path, seconds=60):
    if Path(path).is_file():
        return True, "clip exists"
    r = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        f"testsrc2=size=1920x1080:rate=30:duration={seconds}", "-c:v", "libx264", "-profile:v",
                        "high", "-pix_fmt", "yuv420p", "-preset", "veryfast", "-b:v", "8M", "-movflags",
                        "+faststart", str(path)], capture_output=True, text=True, timeout=900)
    return r.returncode == 0, r.stderr.strip()[-300:]


def _tree_pids(root):
    """root pid plus every descendant (the browser's content, RDD, GPU and utility processes)."""
    kids = {}
    for p in Path("/proc").iterdir():
        if p.name.isdigit():
            try:
                ppid = int((p / "stat").read_text().rsplit(")", 1)[1].split()[1])
            except (OSError, IndexError, ValueError):
                continue
            kids.setdefault(ppid, []).append(int(p.name))
    out, todo = [], [root]
    while todo:
        x = todo.pop()
        out.append(x)
        todo += kids.get(x, [])
    return out


def _names(pids):
    """Process names (comm) of the browser's tree, e.g. RDD Process, GPU Process, Isolated Web Co."""
    out = []
    for pid in pids:
        try:
            out.append(Path(f"/proc/{pid}/comm").read_text().strip())
        except OSError:
            pass
    return sorted(out)


def _cpu_ticks(pids):
    t = 0
    for pid in pids:
        try:
            f = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
            t += int(f[11]) + int(f[12])                      # utime + stime
        except (OSError, IndexError, ValueError):
            pass
    return t


def parse_gpu_top(text):
    """intel_gpu_top -J output (a stream of JSON objects, sometimes as one array) -> [{engine: busy %}]."""
    out = []
    for m in re.finditer(r'"engines"\s*:\s*\{(.*?)\}\s*\}\s*(?:,|\}|\])', text, re.S):
        busy = {}
        for e in re.finditer(r'"([^"]+)"\s*:\s*\{\s*"busy"\s*:\s*([0-9.]+)', m.group(1)):
            busy[e.group(1)] = float(e.group(2))
        if busy:
            out.append(busy)
    return out


def verdict(video_avg, cpu_pct):
    if video_avg is None:
        return "UNMEASURED"
    if video_avg >= 0.5:
        return "HARDWARE"
    return "SOFTWARE"


def run(work, seconds=20, warmup=12, app=APP, extra_prefs=None):
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    clip = work / "h264-1080p30.mp4"
    ok, why = make_clip(clip)
    if not ok:
        return {"ok": False, "detail": f"ffmpeg could not make the clip: {why}"}
    page = work / "play.html"
    page.write_text(f'<!doctype html><title>decode-test</title><body style="margin:0;background:#000">'
                    f'<video id="v" src="{clip.name}" autoplay muted loop playsinline '
                    f'style="width:100vw;height:100vh"></video><script>'
                    f'setInterval(()=>{{const v=document.getElementById("v"),q=v.getVideoPlaybackQuality();'
                    f'fetch("/report?shown="+q.totalVideoFrames+"&dropped="+q.droppedVideoFrames+"&t="+v.currentTime'
                    f'+"&err="+(v.error?v.error.code:0)).catch(()=>{{}})}},1000)</script></body>', encoding="utf-8")
    reports = []

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=str(work), **k)

        def do_GET(self):
            if self.path.startswith("/report?"):
                q = urllib.parse.parse_qs(self.path.split("?", 1)[1])
                reports.append({k: float(v[0]) for k, v in q.items()})
                self.send_response(204)
                self.end_headers()
                return
            super().do_GET()

        def log_message(self, *a):
            pass
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/play.html"
    profile = Path(tempfile.mkdtemp(prefix="decode-test-profile-", dir=work))
    (profile / "user.js").write_text('user_pref("browser.shell.checkDefaultBrowser", false);\n'
                                     'user_pref("media.autoplay.default", 0);\n'
                                     'user_pref("browser.aboutwelcome.enabled", false);\n'
                                     'user_pref("media.suspend-background-video.enabled", false);\n'
                                     'user_pref("dom.timeout.enable_budget_timer_throttling", false);\n'
                                     'user_pref("dom.min_background_timeout_value", 1000);\n'
                                     'user_pref("widget.windows.window_occlusion_tracking.enabled", false);\n'
                                     + "".join(f'user_pref("{k}", {json.dumps(v)});\n' for k, v in (extra_prefs or {}).items()),
                                     encoding="utf-8")
    if subprocess.run(["sudo", "-n", "true"], capture_output=True).returncode != 0:
        return {"ok": False, "detail": "intel_gpu_top needs root and sudo asks for a password: a person runs this"}
    blog = open(work / "browser.log", "w", encoding="utf-8")
    # The engines block is the whole GPU; per-client figures are not trustworthy on Ivy Bridge (2026-10-15: one idle
    # program "used" 2259% of the video engine). So the video engine is measured BEFORE the browser starts: anything
    # else decoding (the person's own YouTube, 2026-10-15) makes the run INCONCLUSIVE instead of a false pass.
    pre = subprocess.run(["sudo", "-n", "timeout", "--signal=INT", "5", "intel_gpu_top", "-J", "-s", "1000"],
                         capture_output=True, text=True)
    pv = [x[k] for x in parse_gpu_top(pre.stdout) for k in x if k.lower().split("/")[0] == "video"]
    before = round(sum(pv) / len(pv), 2) if pv else None
    br = subprocess.Popen([str(app), "--name", "gorilla-unleashed", "--no-remote", "--profile", str(profile), url],
                          stdout=blog, stderr=subprocess.STDOUT, start_new_session=True)
    try:
        time.sleep(warmup)
        if br.poll() is not None:
            return {"ok": False, "detail": f"the browser exited during warm-up (code {br.returncode})"}
        pids = _tree_pids(br.pid)
        names_start = _names(pids)
        mark = len(reports)
        c0, t0 = _cpu_ticks(pids), time.monotonic()
        g = subprocess.run(["sudo", "-n", "timeout", "--signal=INT", str(seconds), "intel_gpu_top", "-J", "-s",
                            "1000"], capture_output=True, text=True)
        pids = _tree_pids(br.pid)
        c1, t1 = _cpu_ticks(pids), time.monotonic()
        names = _names(pids)
    finally:
        try:
            os.killpg(br.pid, signal.SIGTERM)
            br.wait(timeout=20)
        except (OSError, subprocess.TimeoutExpired):
            os.killpg(br.pid, signal.SIGKILL)
        shutil.rmtree(profile, ignore_errors=True)
        srv.shutdown()
        blog.close()
    samples = parse_gpu_top(g.stdout)
    def avg(key):
        # names are "Render/3D", "Blitter", "Video" (i965/Ivy Bridge) or "Video/0" (newer): compare the base name
        vals = [s[k] for s in samples for k in s if k.lower().split("/")[0] == key]
        return round(sum(vals) / len(vals), 2) if vals else None
    video, render = avg("video"), avg("render")
    cores = os.cpu_count() or 1
    cpu = round(100 * (c1 - c0) / CLK / max(t1 - t0, 0.001), 1)            # % of ONE core, summed over processes
    first = next((r for r in reports if r.get("t", 0) > 0), None)
    last = reports[-1] if reports else None
    played = bool(last and first and (last["shown"] > first["shown"] or last["t"] != first["t"]))
    shown = int(last["shown"]) if last else 0
    dropped = int(last["dropped"]) if last else 0
    v = verdict(video, cpu) if played else "NOT-PLAYING"
    during = len(reports) - mark
    if before is not None and before >= 0.3:
        v = "INCONCLUSIVE"                                      # reports that arrived while we measured
    if played and during < 0.8 * seconds:
        v = "INCONCLUSIVE"
    series = [int(r["shown"]) for r in reports]
    resets = sum(1 for a, b in zip(series, series[1:]) if b < a)          # the page's counters started again
    errors = [l.strip() for l in (work / "browser.log").read_text(errors="replace").splitlines()
              if re.search(r"crash|abort|channel error|segfault|assert|IPC|RDD|VA-API|vaapi|libva", l, re.I)][:12]
    return {"ok": v == "HARDWARE", "verdict": v, "video_engine_before_pct": before, "reports_while_measuring": during, "video_engine_pct": video, "render_pct": render,
            "browser_cpu_pct_of_one_core": cpu, "cores": cores, "samples": len(samples), "seconds": seconds,
            "clip": "1920x1080 30 fps H.264 High, 8 Mb/s (ffmpeg testsrc2)", "processes": len(pids),
            "process_names": names, "process_names_start": names_start, "counter_resets": resets,
            "frames_series": series, "browser_log": errors, "rdd": any(n.startswith("RDD") for n in names),
            "frames_shown": shown, "frames_dropped": dropped, "media_error": int(last["err"]) if last else None,
            "detail": (f"{v}: video engine {video}% busy, render {render}%, browser CPU {cpu}% of one core, "
                       f"frames shown {shown}, dropped {dropped} ({len(pids)} processes, {len(samples)} samples "
                       f"over {seconds} s); RDD process {'present' if any(n.startswith('RDD') for n in names) else 'ABSENT'}"
                       + (f"; page restarted {resets} time(s)" if resets else "")
                       + (f"; something else was using the video engine before the test ({before}%)"
                          if before is not None and before >= 0.3 else "")
                       + (f"; the page reported only {during} of {seconds} s: window hidden or covered?"
                          if played and during < 0.8 * seconds else ""))}
