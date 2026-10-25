# Version: 1.0.0 · updated 26-10-25-18-40
"""gpudecode on an old Mac, driven from Linux over SSH: does the browser decode H.264 on the Intel GPU?

Built 2026-10-25 on the MacBook Air 4,1 (macOS 10.13.6, Intel HD 3000). The same question as the Windows
backend, answered with Apple's own tools and the browser's own counter, as an A/B on one build:

  1. A 1080p60 H.264 clip (made here by ffmpeg, 40 s) and a page that plays it and reports
     video.getVideoPlaybackQuality() per second, served on the Mac's 127.0.0.1 by /usr/bin/python (2.7).
  2. The browser started TWICE with a fresh test profile (never the person's own): hardware decoding on, then off
     (media.hardware-video-decoding.enabled). Only the PIDs this run started are measured and stopped.
  3. After each measurement (vmmap suspends the process it reads, so never during it): which process has Apple's
     decoder driver mapped (AppleIntelSNBVA / AppleIntelHD3000GraphicsVADriver / AppleGVA), and each process's CPU.

Verdict, three outcomes, never two:
  HARDWARE DECODE     the decoder driver is mapped in a browser process with hardware decoding on, and not with it off
  NO HARDWARE DECODE  the driver is not mapped in any browser process with hardware decoding on
  INCOMPLETE          a run reported no frames, or the driver was mapped with hardware decoding off (the switch did
                      not take): the recording proves nothing either way
The dropped-frame counts of both runs are printed beside it. Not done here: a per-frame count of calls into the
driver (DTrace needs root; sudo asks for a password over SSH). Measured 2026-10-25, Gorilla 158 for the Air:
on 1,800 frames dropped 16 and 4, driver mapped in the rdd process; off 1,476 of 1,482 and 1,490 of 1,497
dropped, driver not mapped.

  gpudecode.py mac-ab MAC [--seconds 30] [--app "/Applications/Gorilla Unleashed.app"] [--out DIR] [--json]
  gpudecode.py mac-verdict DIR [--json]        judge the logs an earlier mac-ab saved
"""
import hashlib
import json
import re
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = Path.home() / ".cache" / "fieldkit" / "gpudecode"
CLIP = "clip-1080p60-h264.mp4"
REMOTE = "gpudecode-test"      # under the Mac user's home
PORT = 8765
DRIVERS = ("AppleIntelSNBVA", "AppleIntelHD3000GraphicsVADriver", "AppleGVA")
DRIVER_RE = re.compile(r"AppleIntel\w*VA\w*|AppleGVA\w*|VideoToolbox|AppleVADriver")

PAGE = """<!doctype html><meta charset="utf-8"><title>gpudecode playback</title>
<body style="margin:0;background:#000;color:#0f0;font:20px monospace">
<video id="v" src="CLIP" muted autoplay loop playsinline style="width:100vw;height:90vh"></video>
<div id="s">starting</div>
<script>
const q = new URLSearchParams(location.search), secs = +(q.get("secs") || 30), warm = +(q.get("warm") || 8);
const v = document.getElementById("v"), s = document.getElementById("s");
function pq() { const p = v.getVideoPlaybackQuality(); return {total: p.totalVideoFrames, dropped: p.droppedVideoFrames}; }
let a = null, per = [], t0 = 0;
v.play().catch(e => s.textContent = "play refused: " + e);
setTimeout(() => {
  a = pq(); t0 = performance.now(); let last = a;
  const iv = setInterval(() => { const n = pq(); per.push({total: n.total - last.total, dropped: n.dropped - last.dropped}); last = n;
    s.textContent = q.get("label") + " " + per.length + "s total " + (n.total - a.total) + " dropped " + (n.dropped - a.dropped); }, 1000);
  setTimeout(() => { clearInterval(iv); const b = pq();
    const r = {label: q.get("label"), seconds: (performance.now() - t0) / 1000, total: b.total - a.total,
               dropped: b.dropped - a.dropped, per_second: per, ua: navigator.userAgent, w: v.videoWidth, h: v.videoHeight};
    fetch("/result?label=" + encodeURIComponent(q.get("label")), {method: "POST", body: JSON.stringify(r)})
      .then(() => s.textContent = "DONE " + JSON.stringify({total: r.total, dropped: r.dropped}));
  }, secs * 1000);
}, warm * 1000);
</script>
""".replace("CLIP", CLIP)

SERVER = """# Python 2.7 (macOS 10.13's /usr/bin/python): serve this folder on 127.0.0.1, store POST /result?label=X
import BaseHTTPServer, SimpleHTTPServer, os, sys, urlparse
class H(SimpleHTTPServer.SimpleHTTPRequestHandler):
    def do_POST(self):
        label = urlparse.parse_qs(urlparse.urlparse(self.path).query).get("label", ["x"])[0]
        body = self.rfile.read(int(self.headers.getheader("content-length") or 0))
        with open("result-%s.json" % "".join(c for c in label if c.isalnum() or c in "-_"), "w") as f:
            f.write(body)
        self.send_response(204); self.end_headers()
    def log_message(self, *a): pass
os.chdir(os.path.dirname(os.path.abspath(__file__)))
BaseHTTPServer.HTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
"""

RUN = r"""#!/bin/bash
# One run on the Mac: LABEL HW(true|false) SECONDS APP. A fresh test profile; stops only the PIDs it started.
set -u
D="$HOME/__REMOTE__"; LABEL=$1; HW=$2; SECS=${3:-30}; APP=$4; PORT=__PORT__
cd "$D" || exit 1
rm -f "result-$LABEL.json"
P="$D/prof-$LABEL"; rm -rf "$P"; mkdir -p "$P"
cat > "$P/user.js" <<JS
user_pref("media.hardware-video-decoding.enabled", $HW);
user_pref("media.hardware-video-decoding.force-enabled", false);
user_pref("media.autoplay.default", 0);
user_pref("browser.shell.checkDefaultBrowser", false);
user_pref("browser.startup.homepage_override.mstone", "ignore");
user_pref("datareporting.policy.dataSubmissionEnabled", false);
user_pref("browser.aboutwelcome.enabled", false);
JS
pgrep -f "serve.py $PORT" >/dev/null || { /usr/bin/python "$D/serve.py" $PORT & sleep 1; }
BEFORE=$(pgrep -f "$APP/Contents/MacOS" | sort)
open -n -a "$APP" --args -profile "$P" -no-remote "http://127.0.0.1:$PORT/play.html?label=$LABEL&secs=$SECS&warm=8"
sleep 14
MINE=$(comm -13 <(echo "$BEFORE") <(pgrep -f "$APP/Contents/MacOS" | sort))
echo "PIDS: $(echo $MINE)"
for i in $(seq 1 $((SECS + 6))); do [ -s "result-$LABEL.json" ] && break; sleep 1; done
echo "CPU-END: $(for p in $MINE; do printf '%s=%s ' $p "$(ps -o %cpu= -p $p | tr -d ' ')"; done)"
echo "RESULT: $(cat "result-$LABEL.json" 2>/dev/null || echo none)"
# vmmap suspends the process it reads, so it runs only after the measurement
for p in $MINE; do
  role=$(ps -o command= -p $p | awk '{print $NF}' | cut -c1-12)
  maps=$(vmmap $p 2>/dev/null | grep -o -E 'AppleIntel[A-Za-z0-9]*VA[A-Za-z0-9]*|AppleGVA[A-Za-z]*|VideoToolbox|AppleVADriver' | sort | uniq -c | tr '\n' ' ')
  echo "PROC $p $role maps: $maps"
done
PARENT=$(for p in $MINE; do ps -o command= -p $p | grep -q contentproc || echo $p; done | head -1)
[ -n "$PARENT" ] && kill "$PARENT" 2>/dev/null
sleep 3
echo "END"
""".replace("__REMOTE__", REMOTE).replace("__PORT__", str(PORT))


def parse_log(text):
    """-> {pids, cpu: {pid: %}, result: dict|None, procs: [{pid, role, drivers: {name: n}}]}"""
    out = {"pids": [], "cpu": {}, "result": None, "procs": []}
    for line in text.splitlines():
        if line.startswith("PIDS:"):
            out["pids"] = line.split(":", 1)[1].split()
        elif line.startswith("CPU-END:"):
            out["cpu"] = {k: float(v) for k, v in (x.split("=") for x in line.split(":", 1)[1].split() if "=" in x)
                          if re.fullmatch(r"[\d.]+", v)}
        elif line.startswith("RESULT:"):
            body = line.split(":", 1)[1].strip()
            try:
                out["result"] = json.loads(body)
            except ValueError:
                out["result"] = None
        elif line.startswith("PROC "):
            head, _, maps = line.partition(" maps:")
            parts = head.split()
            drivers = {m.group(2): int(m.group(1)) for m in re.finditer(r"(\d+)\s+(\S+)", maps)}
            out["procs"].append({"pid": parts[1], "role": parts[2] if len(parts) > 2 else "?", "drivers": drivers})
    return out


def decoder_procs(run):
    """Processes with a hardware decoder driver mapped (VideoToolbox alone is the API, mapped everywhere)."""
    return [p for p in run["procs"] if any(DRIVER_RE.fullmatch(d) and d != "VideoToolbox" for d in p["drivers"])]


def judge(hw, sw):
    """hw, sw: parse_log() of the run with hardware decoding on / off -> {verdict, ...}."""
    def frames(run):
        r = run.get("result") or {}
        return r.get("total", 0), r.get("dropped", 0)
    (ht, hd), (st, sd) = frames(hw), frames(sw)
    hp, sp = decoder_procs(hw), decoder_procs(sw)
    facts = (f"on: {hd} of {ht} frames dropped, driver in "
             f"{', '.join(p['role'] + ' ' + p['pid'] for p in hp) or 'no process'}; "
             f"off: {sd} of {st} dropped, driver in {', '.join(p['role'] + ' ' + p['pid'] for p in sp) or 'no process'}")
    if not ht or not st:
        v = "INCOMPLETE: a run reported no frames (the page did not play or did not report); " + facts
    elif sp:
        v = "INCOMPLETE: the decoder driver was mapped with hardware decoding OFF, so the switch did not take; " + facts
    elif hp:
        v = "HARDWARE DECODE: Apple's decoder driver is mapped in the browser only with hardware decoding on; " + facts
    else:
        v = "NO HARDWARE DECODE: no browser process mapped Apple's decoder driver with hardware decoding on; " + facts
    return {"verdict": v, "hw": {"total": ht, "dropped": hd, "decoder_procs": hp},
            "sw": {"total": st, "dropped": sd, "decoder_procs": sp},
            "not_done": "per-frame count of driver calls (DTrace needs root on the Mac)"}


def ssh(mac, cmd, timeout=180, input_text=None, tries=3, pause=10.0, run=subprocess.run, sleep=None):
    """ssh with retries on 255 (no connection): the Air drops off Wi-Fi for seconds at a time (2026-10-25,
    mid-run). A command that ran and failed is not retried."""
    import time
    sleep = sleep or time.sleep
    for i in range(tries):
        r = run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", mac, cmd], capture_output=True, text=True,
                timeout=timeout, input=input_text)
        if r.returncode != 255 or i == tries - 1:
            return r.returncode, r.stdout, r.stderr
        sleep(pause)


def make_clip():
    CACHE.mkdir(parents=True, exist_ok=True)
    clip = CACHE / CLIP
    if not clip.is_file():
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        "testsrc2=size=1920x1080:rate=60", "-t", "40", "-c:v", "libx264", "-profile:v", "high",
                        "-level", "4.2", "-pix_fmt", "yuv420p", "-preset", "veryfast", "-b:v", "6M",
                        "-movflags", "+faststart", str(clip)], check=True)
    return clip


def stage(mac, clip):
    """Copy the clip (only when the Mac's copy differs), the page, the server and the run script."""
    want = hashlib.sha256(clip.read_bytes()).hexdigest()
    code, have, _ = ssh(mac, f"mkdir -p ~/{REMOTE} && shasum -a 256 ~/{REMOTE}/{CLIP} 2>/dev/null | cut -c1-64")
    if code != 0:
        raise RuntimeError(f"cannot reach {mac} over ssh")
    if have.strip() != want:
        subprocess.run(["scp", "-q", str(clip), f"{mac}:{REMOTE}/{CLIP}"], check=True, timeout=1800)
    for name, text in (("play.html", PAGE), ("serve.py", SERVER), ("run-ab.sh", RUN)):
        ssh(mac, f"cat > ~/{REMOTE}/{name}", input_text=text)


def cmd_mac_ab(a):
    out = Path(a.out or Path.cwd() / f"gpudecode-mac-{a.mac}")
    out.mkdir(parents=True, exist_ok=True)
    stage(a.mac, make_clip())
    for label, hw in (("hw", "true"), ("sw", "false")):
        code, so, se = ssh(a.mac, f"bash ~/{REMOTE}/run-ab.sh {label} {hw} {a.seconds} '{a.app}' < /dev/null",
                           timeout=a.seconds + 120)
        (out / f"log-{label}.txt").write_text(so + (("\nSTDERR: " + se) if se.strip() else ""), encoding="utf-8")
    ssh(a.mac, f"pkill -f 'serve.py {PORT}'")
    return cmd_mac_verdict(type("A", (), {"dir": str(out), "json": a.json})())


def cmd_mac_verdict(a):
    d = Path(a.dir)
    runs = {}
    for label in ("hw", "sw"):
        f = d / f"log-{label}.txt"
        runs[label] = parse_log(f.read_text(encoding="utf-8")) if f.is_file() else {"procs": [], "result": None}
    r = judge(runs["hw"], runs["sw"])
    (d / "verdict.json").write_text(json.dumps(r, indent=1), encoding="utf-8")
    if a.json:
        print(json.dumps(r, indent=1))
    else:
        print(f"VERDICT: {r['verdict']}\nNOT DONE: {r['not_done']}\nlogs: {d}")
    return 0 if r["verdict"].startswith("HARDWARE DECODE") else 3
