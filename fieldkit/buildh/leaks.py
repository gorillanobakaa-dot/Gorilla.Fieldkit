"""What a web page learns from this browser: the browserleaks.com battery, run locally and headless.

The installed browser loads a page from 127.0.0.1 that does what the leak-test sites do (WebRTC ICE gathering
against a public STUN server, canvas and WebGL fingerprints, font probing, navigator/screen/timezone values,
API presence) and POSTs the result back. No site, no keyboard, no Marionette (the owner's lock stands): the page
measures itself and the harness reads the JSON. Rows judge the things a privacy build must not do; the rest is
reported for the owner to decide on (resistFingerprinting and friends are pref decisions, the owner's to record).
"""
import http.server
import json
import subprocess
import tempfile
import threading
import time
from pathlib import Path

PAGE = r"""<!doctype html><meta charset=utf-8><title>leaks</title><body>
<canvas id=c width=220 height=60></canvas><canvas id=g width=64 height=64></canvas>
<script>
const out = {};
function post() { fetch("/result", {method: "POST", body: JSON.stringify(out)}).then(() => document.title = "done"); }
async function sha(s) { const b = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s)); return [...new Uint8Array(b)].slice(0, 8).map(x => x.toString(16).padStart(2, "0")).join(""); }
(async () => {
  const n = navigator;
  out.navigator = {userAgent: n.userAgent, platform: n.platform, oscpu: n.oscpu, hardwareConcurrency: n.hardwareConcurrency,
    deviceMemory: n.deviceMemory, languages: n.languages, language: n.language, doNotTrack: n.doNotTrack,
    globalPrivacyControl: n.globalPrivacyControl, maxTouchPoints: n.maxTouchPoints, pdfViewerEnabled: n.pdfViewerEnabled,
    webdriver: n.webdriver, cookieEnabled: n.cookieEnabled, plugins: n.plugins.length, mimeTypes: n.mimeTypes.length};
  out.screen = {width: screen.width, height: screen.height, availWidth: screen.availWidth, availHeight: screen.availHeight,
    colorDepth: screen.colorDepth, pixelDepth: screen.pixelDepth, dpr: devicePixelRatio, inner: [innerWidth, innerHeight], outer: [outerWidth, outerHeight]};
  out.time = {offset: new Date().getTimezoneOffset(), zone: Intl.DateTimeFormat().resolvedOptions().timeZone, locale: Intl.DateTimeFormat().resolvedOptions().locale, now: Date.now(), perfNow: performance.now()};
  out.apis = {geolocation: "geolocation" in n, getBattery: "getBattery" in n, bluetooth: "bluetooth" in n, usb: "usb" in n, serial: "serial" in n,
    mediaDevices: !!n.mediaDevices, RTCPeerConnection: typeof RTCPeerConnection, serviceWorker: "serviceWorker" in n, webgl2: !!document.createElement("canvas").getContext("webgl2"),
    notifications: "Notification" in window, speech: "speechSynthesis" in window, gamepads: "getGamepads" in n, storage: "storage" in n, credentials: "credentials" in n, clipboard: !!n.clipboard, share: "share" in n, wakeLock: "wakeLock" in n};
  try { const b = await n.getBattery?.(); out.battery = b ? {charging: b.charging, level: b.level} : null; } catch (e) { out.battery = String(e); }
  // canvas
  const c = document.getElementById("c"), x = c.getContext("2d");
  x.textBaseline = "top"; x.font = "14px Arial"; x.fillStyle = "#f60"; x.fillRect(125, 1, 62, 20); x.fillStyle = "#069"; x.fillText("Gorilla <canvas> 1.0", 2, 15);
  x.fillStyle = "rgba(102,204,0,0.7)"; x.fillText("Gorilla <canvas> 1.0", 4, 17); x.beginPath(); x.arc(50, 50, 20, 0, Math.PI * 2); x.fill();
  out.canvas = {hash: await sha(c.toDataURL()), dataLength: c.toDataURL().length};
  // webgl
  try { const gl = document.getElementById("g").getContext("webgl"); const d = gl.getExtension("WEBGL_debug_renderer_info");
    out.webgl = {vendor: gl.getParameter(gl.VENDOR), renderer: gl.getParameter(gl.RENDERER), unmaskedVendor: d ? gl.getParameter(d.UNMASKED_VENDOR_WEBGL) : null,
      unmaskedRenderer: d ? gl.getParameter(d.UNMASKED_RENDERER_WEBGL) : null, version: gl.getParameter(gl.VERSION), extensions: (gl.getSupportedExtensions() || []).length}; } catch (e) { out.webgl = String(e); }
  // fonts: width differences against the generic family
  const probe = ["Arial", "Calibri", "Cambria", "Comic Sans MS", "Consolas", "Courier New", "Georgia", "Impact", "Segoe UI", "Tahoma", "Times New Roman", "Trebuchet MS", "Verdana", "Wingdings", "Symbol", "MS Gothic", "Malgun Gothic", "Microsoft YaHei", "Noto Sans", "Roboto", "Ubuntu", "DejaVu Sans", "Liberation Sans", "Helvetica", "Lucida Console", "Garamond", "Palatino Linotype", "Franklin Gothic Medium", "Candara", "Constantia"];
  const s = document.createElement("span"); s.textContent = "mmmmmmmmmmlli"; s.style.fontSize = "72px"; document.body.appendChild(s);
  const base = {}; for (const f of ["monospace", "sans-serif", "serif"]) { s.style.fontFamily = f; base[f] = s.offsetWidth + "x" + s.offsetHeight; }
  out.fonts = probe.filter(f => ["monospace", "sans-serif", "serif"].some(g => { s.style.fontFamily = `'${f}',${g}`; return s.offsetWidth + "x" + s.offsetHeight != base[g]; }));
  // webrtc: candidates against a public STUN server
  out.ice = {candidates: [], error: null};
  try {
    const pc = new RTCPeerConnection({iceServers: [{urls: "stun:stun.l.google.com:19302"}]});
    pc.createDataChannel("x"); pc.onicecandidate = e => { if (e.candidate) out.ice.candidates.push(e.candidate.candidate); };
    await pc.setLocalDescription(await pc.createOffer());
    await new Promise(r => setTimeout(r, 6000)); pc.close();
  } catch (e) { out.ice.error = String(e); }
  out.ice.summary = {host: out.ice.candidates.filter(c => / typ host/.test(c)).length, srflx: out.ice.candidates.filter(c => / typ srflx/.test(c)).length,
    mdns: out.ice.candidates.filter(c => /\.local /.test(c)).length, rawIPs: [...new Set(out.ice.candidates.map(c => (c.match(/ (\d+\.\d+\.\d+\.\d+|[0-9a-f:]+:[0-9a-f:]+) \d+ typ/) || [])[1]).filter(Boolean))]};
  post();
})();
</script></body>"""


class _Handler(http.server.BaseHTTPRequestHandler):
    result = None

    def log_message(self, *a):
        pass

    def do_GET(self):
        body = PAGE.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", "0"))
        _Handler.result = json.loads(self.rfile.read(n).decode("utf-8", "replace"))
        self.send_response(204)
        self.end_headers()


def measure(install_dir, seconds=45):
    """-> the page's JSON (or None), after starting the installed browser headless on the local page."""
    _Handler.result = None
    srv = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    prof = Path(tempfile.mkdtemp(prefix="gleaks_"))
    (prof / "user.js").write_text('user_pref("browser.shell.checkDefaultBrowser", false);\nuser_pref("browser.aboutwelcome.enabled", false);\n', encoding="utf-8")
    proc = subprocess.Popen([str(Path(install_dir) / "firefox.exe"), "-headless", "-no-remote", "-profile", str(prof), f"http://127.0.0.1:{port}/"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    t0 = time.time()
    while _Handler.result is None and time.time() - t0 < seconds:
        time.sleep(0.5)
    subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
    srv.shutdown()
    return _Handler.result


def rows(install_dir, seconds=45):
    r = measure(install_dir, seconds)
    if r is None:
        return [{"check": "leaks: the local leak page reported back", "ok": False, "evidence": f"no result in {seconds} s", "bad": ["no result"]}]
    ice = r.get("ice", {}).get("summary", {})
    raw = [ip for ip in ice.get("rawIPs", []) if not ip.endswith(".local")]
    private = lambda ip: ip.startswith(("10.", "192.168.", "127.", "169.254.", "fe80", "fd", "fc", "::1")) or (ip.startswith("172.") and 16 <= int(ip.split(".")[1]) <= 31)
    public = [ip for ip in raw if not private(ip)]
    local = [ip for ip in raw if private(ip)]
    out = [
        # the public address via STUN (srflx) is what a WebRTC call needs to work behind NAT and the page already
        # knows it from the HTTP connection; the leak that matters is a raw LOCAL address (host candidates must be mDNS)
        {"check": "leaks: WebRTC reveals no local IP address to a page (host candidates are mDNS)", "ok": not local,
         "evidence": f"host {ice.get('host', 0)}, srflx {ice.get('srflx', 0)} (public {public[:1]}), mDNS {ice.get('mdns', 0)}, raw local IPs {local[:3]}", "bad": local},
        {"check": "leaks: WebGL renderer not unmasked (no GPU model string)", "ok": not (r.get("webgl") or {}).get("unmaskedRenderer") if isinstance(r.get("webgl"), dict) else True,
         "evidence": str((r.get("webgl") or {}).get("unmaskedRenderer") or (r.get("webgl") or {}).get("renderer"))[:120], "bad": []},
        {"check": "leaks: battery API absent", "ok": not r.get("apis", {}).get("getBattery"), "evidence": f"getBattery in navigator: {r.get('apis', {}).get('getBattery')}", "bad": []},
    ]
    nav, scr, tm = r.get("navigator", {}), r.get("screen", {}), r.get("time", {})
    report = (f"UA '{nav.get('userAgent')}' | platform {nav.get('platform')} oscpu {nav.get('oscpu')} | cores {nav.get('hardwareConcurrency')} mem {nav.get('deviceMemory')} | "
              f"languages {nav.get('languages')} | screen {scr.get('width')}x{scr.get('height')} avail {scr.get('availWidth')}x{scr.get('availHeight')} dpr {scr.get('dpr')} depth {scr.get('colorDepth')} | "
              f"tz {tm.get('zone')} offset {tm.get('offset')} | canvas {r.get('canvas', {}).get('hash')} | fonts {len(r.get('fonts', []))}/30 | "
              f"geolocation {r.get('apis', {}).get('geolocation')} mediaDevices {r.get('apis', {}).get('mediaDevices')} webgl2 {r.get('apis', {}).get('webgl2')}")
    out.append({"check": "leaks: what the page saw (report; resistFingerprinting is the owner's pref decision)", "ok": True, "evidence": report, "bad": [], "raw": r})
    return out
