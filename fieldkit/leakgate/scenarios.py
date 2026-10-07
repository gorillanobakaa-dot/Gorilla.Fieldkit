"""Scenarios and the controlled local test server.

Every scenario runs on a fresh throwaway profile. Local pages are served from 127.0.0.1 by this process, so the
test controls them byte for byte; canaries are unique strings planted where the spec says (URL, title, cookie,
localStorage, IndexedDB, form value, download name/content, private window). A canary in any captured byte that
leaves the machine is an automatic failure.

User-action scenarios (ACTION_SCENARIOS) exercise leaks that fire only when the user, or a page, does something:
  certerror-toplevel   a top-level visit to a local HTTPS server whose certificate comes from an unknown issuer, so
                       about:certerror really loads (SEC_ERROR_UNKNOWN_ISSUER is the only error that fires the
                       MITM-priming request in NetErrorParent.primeMitm). Expect no external destination.
  download-exe         the page downloads a small .exe from the local server: the Safe Browsing / application
                       reputation remote-verdict path. Expect no external destination.
  lan-probe            the page, treated as PUBLIC (network.lna.address_space.public.override), fetches a closed
                       loopback port, 192.168.0.1 and 10.0.0.1 (Local Network Access). Expect no external
                       destination; LAN_POLICY fails if any of those requests left: a headless run has nobody to
                       answer a permission prompt, so a request that left went without one.
  drm-request          navigator.requestMediaKeySystemAccess('com.widevine.alpha'): the video compromise (decision
                       D-157-12). The ONLY external destinations allowed are Google's Widevine download hosts, and only
                       in this scenario; any Mozilla host fails. The first run fails (no allowlist entry): the
                       maintainer runs leakgate-propose, which proposes an entry scoped to this scenario, and approves
                       it at a real terminal (leakgate-approve). Nothing here is pre-approved.
  h264-call            a loopback RTCPeerConnection call sending a canvas video track with H.264 preferred, so
                       OpenH264 is needed. Allowed external: Cisco's ciscobinary.openh264.org only, only here, after
                       the same maintainer approval; any Mozilla host fails.
  profile-idle-actions opens about:preferences#privacy and about:welcome. Expect no external destination.
  early-hints          the main document answers first with "103 Early Hints": a preconnect to an https host the user
                       never opened (D-157-14: no speculative connection may follow, not even a DNS lookup) and a
                       cross-site preload of a tracker script (uBlock Origin must stop it, or early-hint preloads
                       slip past the ad blocker). Expect no external destination. Added 2026-10-07 (audit B1): a
                       103 preconnect carried its own 10-connection allowance past speculative-parallel-limit 0, and
                       no scene ever sent a 103, so four release runs could not see it.
Every one of them fails closed: a page that never reports, a download the server never served, or a sensor that
collected nothing in that scenario is a failure (gate.action_checks).
"""
import http.server
import json
import threading

CANARIES = {
    "url": "GORILLA_CANARY_URL_001", "title": "GORILLA_CANARY_TITLE_001", "cookie": "GORILLA_CANARY_COOKIE_001",
    "storage": "GORILLA_CANARY_STORAGE_001", "idb": "GORILLA_CANARY_IDB_001", "form": "GORILLA_CANARY_FORM_001",
    "download": "GORILLA_CANARY_DOWNLOAD_001", "private": "GORILLA_CANARY_PRIVATE_DATA_001",
    "search": "GORILLA_CANARY_SEARCH_001", "history": "GORILLA_CANARY_HISTORY_001",
}
REAL_PAGE = "https://www.anthropic.com/legal/archive/21d66aa9-68f6-4356-ba01-2825b0f81805"

# name, target (absolute URL, about: page or a local path), seconds, extra args, hosts the test itself requires,
# seconds to keep watching after the browser is killed
SCENARIOS = [
    ("startup-idle", "about:blank", 300, [], [], 60),
    ("newtab", "about:newtab", 60, [], [], 10),
    ("home", "about:home", 45, [], [], 10),
    ("addons", "about:addons", 45, [], [], 10),
    ("preferences", "about:preferences", 45, [], [], 10),
    ("canary", "/canary?q=" + CANARIES["url"] + "&s=" + CANARIES["search"], 60, [], [], 10),
    ("canary-private", "/canary?q=" + CANARIES["private"], 60, ["-private-window"], [], 10),
    ("workers", "/workers", 90, [], [], 30),
    ("webrtc", "/webrtc", 45, [], [], 10),
    ("page", REAL_PAGE, 60, [], ["www.anthropic.com", "*.anthropic.com"], 10),
    ("crash", "about:crashcontent", 30, [], [], 30),
    ("certs", "/certs", 30, [], [], 5),
    # user-action scenarios: the gate fills in the ports (target_for); none of them lists a host the test requires
    ("certerror-toplevel", "/certerror", 30, [], [], 10),
    ("download-exe", "/download-exe", 45, [], [], 10),
    ("lan-probe", "/lan", 45, [], [], 10),
    ("drm-request", "/drm", 90, [], [], 30),
    ("h264-call", "/h264", 90, [], [], 30),
    ("profile-idle-actions", "about:preferences#privacy", 45, ["-new-tab", "about:welcome"], [], 10),
    ("early-hints", "/early-hints", 45, [], [], 10),
    ("shutdown-graceful", REAL_PAGE, 60, [], ["www.anthropic.com", "*.anthropic.com"], 60),
]
ACTION_SCENARIOS = ("certerror-toplevel", "download-exe", "lan-probe", "drm-request", "h264-call", "profile-idle-actions",
                    "early-hints")
# pages that must POST {"scenario": name, ...} to /result, and local paths the server must have served (fail closed)
REPORTING = ("certerror-toplevel", "download-exe", "lan-probe", "drm-request", "h264-call", "early-hints")
SERVED = {"download-exe": ("/tiny.exe",), "early-hints": ("/early-hints",)}
# the 103 reply of the early-hints scene: neither host may ever be contacted
EARLY_HINTS_PRECONNECT = "https://gorilla-leakgate-early-hints.example"
EARLY_HINTS_PRELOAD = "https://www.googletagmanager.com/gtm.js?id=GTM-GORILLALEAKGATE"
EARLY_HINTS_PAGE = """<!doctype html><meta charset=utf-8><title>early hints</title><body>early hints
<script>setTimeout(() => fetch("/result", {method: "POST", body: JSON.stringify({scenario: "early-hints", loaded: true})}), 3000);</script>
</body>"""
# the video compromise (decision D-157-12): candidate hosts per scenario. These are NOT allowlist entries: they say
# which hosts leakgate-propose may propose (scoped to that one scenario) and which hosts fail anywhere else.
VIDEO_COMPROMISE = {"drm-request": ("dl.google.com", "*.gvt1.com"),          # Widevine CDM, straight from Google
                    "h264-call": ("ciscobinary.openh264.org",)}               # OpenH264, straight from Cisco
MOZILLA_HOSTS = ("mozilla.org", "*.mozilla.org", "mozilla.net", "*.mozilla.net", "mozilla.com", "*.mozilla.com")
# per-scenario profile prefs ({host}/{port} = the local server)
SCENARIO_PREFS = {
    "lan-probe": {"network.lna.address_space.public.override": "{host}:{port}"},      # the local page counts as public
    "h264-call": {"media.peerconnection.ice.loopback": True,                         # the call stays on loopback
                  "media.peerconnection.ice.obfuscate_host_addresses": False},         # literal candidates, filtered by the page
}
# extra modes beyond direct + proxied, for the scenarios where they say something
DNS_CONTROLLED = ("startup-idle", "newtab", "home", "page", "canary")
POISONED = ("startup-idle", "newtab", "home", "addons")
GRACEFUL = ("shutdown-graceful",)          # a minimised window that never takes focus, closed with WM_CLOSE
QUICK = {"startup-idle": 120, "workers": 45}

CANARY_PAGE = """<!doctype html><meta charset=utf-8><title>%(title)s</title><body>
<form id=f><input name=v value="%(form)s"><input type=search name=s value="%(search)s"></form>
<a id=d href="/dl" download="%(download)s.txt">dl</a>
<script>
localStorage.setItem("k", "%(storage)s"); sessionStorage.setItem("k", "%(storage)s");
const r = indexedDB.open("gorilla", 1);
r.onupgradeneeded = () => r.result.createObjectStore("s");
r.onsuccess = () => { const tx = r.result.transaction("s", "readwrite"); tx.objectStore("s").put("%(idb)s", "k"); };
history.pushState({}, "", "?h=%(history)s");
setTimeout(() => fetch("/submit", {method: "POST", body: new FormData(document.getElementById("f"))}), 2000);
setTimeout(() => document.getElementById("d").click(), 3000);
setTimeout(() => navigator.sendBeacon("/beacon", "%(form)s"), 4000);
</script></body>"""

WORKERS_PAGE = """<!doctype html><meta charset=utf-8><title>workers</title><body><script>
const out = {};
navigator.serviceWorker.register("/sw.js").then(r => out.sw = "registered", e => out.sw = String(e));
try { const s = new SharedWorker("/shared.js"); s.port.start(); out.shared = "started"; } catch (e) { out.shared = String(e); }
try { const w = new Worker("/worker.js"); out.worker = "started"; } catch (e) { out.worker = String(e); }
setTimeout(() => fetch("/result", {method: "POST", body: JSON.stringify(out)}), 5000);
</script></body>"""

WEBRTC_PAGE = """<!doctype html><meta charset=utf-8><title>webrtc</title><body><script>
(async () => {
  const out = {candidates: [], error: null};
  try {
    const pc = new RTCPeerConnection();        // no ICE servers: nothing may leave the machine
    pc.createDataChannel("x");
    pc.onicecandidate = e => { if (e.candidate) out.candidates.push(e.candidate.candidate); };
    await pc.setLocalDescription(await pc.createOffer());
    await new Promise(r => setTimeout(r, 6000));
    pc.close();
  } catch (e) { out.error = String(e); }
  fetch("/result", {method: "POST", body: JSON.stringify(out)});
})();
</script></body>"""

CERTS_PAGE = """<!doctype html><meta charset=utf-8><title>certs</title><body><script>
(async () => {
  const q = new URLSearchParams(location.search), out = {};
  for (const k of ["valid", "expired", "wronghost", "selfsigned"]) {
    try { const r = await fetch(`https://127.0.0.1:${q.get(k)}/`, {cache: "no-store"}); out[k] = r.ok ? "loaded" : "status " + r.status; }
    catch (e) { out[k] = "refused"; }
  }
  fetch("/result", {method: "POST", body: JSON.stringify({certs: out})});
})();
</script></body>"""

REPORT_JS = """const report = o => fetch("/result", {method: "POST", body: JSON.stringify(o)});"""

CERTERROR_PAGE = """<!doctype html><meta charset=utf-8><title>certerror</title><body><script>
%s
const port = new URLSearchParams(location.search).get("port");
// report first: after the navigation this page is gone and about:certerror is the top-level document
report({scenario: "certerror-toplevel", navigating_to: port}).finally(() => { location.href = `https://127.0.0.1:${port}/`; });
</script></body>""" % REPORT_JS

DOWNLOAD_EXE_PAGE = """<!doctype html><meta charset=utf-8><title>download-exe</title><body>
<a id=d href="/tiny.exe" download="gorilla-leakgate-test.exe">exe</a><script>
%s
report({scenario: "download-exe", clicked: true}).finally(() => document.getElementById("d").click());
</script></body>""" % REPORT_JS

LAN_PAGE = """<!doctype html><meta charset=utf-8><title>lan-probe</title><body><script>
%s
(async () => {
  const q = new URLSearchParams(location.search), out = {scenario: "lan-probe", closed: q.get("closed"), targets: {}};
  try { out.permission = (await navigator.permissions.query({name: "local-network-access"})).state; }
  catch (e) { out.permission = "query failed: " + e.name; }
  const targets = {loopback_closed: `http://127.0.0.1:${q.get("closed")}/`, private_192: "http://192.168.0.1/", private_10: "http://10.0.0.1/"};
  await Promise.all(Object.entries(targets).map(async ([k, u]) => {
    const t0 = performance.now(), ctl = new AbortController(), timer = setTimeout(() => ctl.abort(), 8000);
    try { await fetch(u, {mode: "no-cors", cache: "no-store", signal: ctl.signal}); out.targets[k] = {url: u, result: "reached"}; }
    catch (e) { out.targets[k] = {url: u, result: e.name === "AbortError" ? "timeout (the request may have left)" : "failed: " + e.name}; }
    clearTimeout(timer);
    out.targets[k].ms = Math.round(performance.now() - t0);
  }));
  report(out);
})();
</script></body>""" % REPORT_JS

DRM_PAGE = """<!doctype html><meta charset=utf-8><title>drm-request</title><body><script>
%s
const cfg = [{initDataTypes: ["cenc"], audioCapabilities: [{contentType: 'audio/mp4; codecs="mp4a.40.2"'}],
              videoCapabilities: [{contentType: 'video/mp4; codecs="avc1.42E01E"', robustness: "SW_SECURE_DECODE"}]}];
async function attempt(n) {
  const out = {scenario: "drm-request", attempt: n};
  try {
    const access = await navigator.requestMediaKeySystemAccess("com.widevine.alpha", cfg);
    out.access = "granted " + access.keySystem;
    try { await access.createMediaKeys(); out.mediaKeys = "created"; } catch (e) { out.mediaKeys = "failed: " + e.name; }
  } catch (e) { out.access = "refused: " + e.name + " " + e.message; }
  report(out);
}
attempt(1);
setTimeout(() => attempt(2), 45000);   // after an on-demand download the second attempt can succeed
</script></body>""" % REPORT_JS

H264_PAGE = """<!doctype html><meta charset=utf-8><title>h264-call</title><body><canvas id=c width=320 height=240></canvas><script>
%s
(async () => {
  const out = {scenario: "h264-call"};
  try {
    const c = document.getElementById("c"), g = c.getContext("2d");
    let n = 0;
    setInterval(() => { g.fillStyle = `hsl(${(n += 7) %% 360}, 80%%, 50%%)`; g.fillRect(0, 0, 320, 240); }, 50);
    const stream = c.captureStream(15);
    const a = new RTCPeerConnection(), b = new RTCPeerConnection();       // no ICE servers
    const loop = cand => / (127[.]0[.]0[.]1|::1) /.test(" " + cand.candidate + " ");
    a.onicecandidate = e => { if (e.candidate && loop(e.candidate)) b.addIceCandidate(e.candidate); };
    b.onicecandidate = e => { if (e.candidate && loop(e.candidate)) a.addIceCandidate(e.candidate); };
    const tr = a.addTransceiver(stream.getVideoTracks()[0], {direction: "sendonly", streams: [stream]});
    const caps = (RTCRtpSender.getCapabilities && RTCRtpSender.getCapabilities("video")) || {codecs: []};
    const h264 = caps.codecs.filter(x => /h264/i.test(x.mimeType));
    out.h264_capabilities = h264.length;
    if (h264.length && tr.setCodecPreferences) tr.setCodecPreferences(h264.concat(caps.codecs.filter(x => !/h264/i.test(x.mimeType))));
    await a.setLocalDescription(await a.createOffer());
    await b.setRemoteDescription(a.localDescription);
    await b.setLocalDescription(await b.createAnswer());
    await a.setRemoteDescription(b.localDescription);
    out.sdp_h264 = /H264/i.test(a.localDescription.sdp);
    await new Promise(r => setTimeout(r, 10000));
    const st = await a.getStats();
    out.codecs = [];
    st.forEach(s => { if (s.type === "codec") out.codecs.push(s.mimeType); if (s.type === "outbound-rtp") out.frames = s.framesEncoded; });
    out.state = a.connectionState;
  } catch (e) { out.error = String(e); }
  report(out);
})();
</script></body>""" % REPORT_JS

# a minimal PE-looking stub (never executed): enough for the download to be classed as an executable
TINY_EXE = b"MZ" + b"\x90\x00" * 29 + b"\x40\x00\x00\x00" + b"\x00" * 448 + b"PE\x00\x00" + b"\x00" * 60

PAGES = {"/certerror": CERTERROR_PAGE, "/download-exe": DOWNLOAD_EXE_PAGE, "/lan": LAN_PAGE, "/drm": DRM_PAGE,
         "/h264": H264_PAGE}


def target_for(name, target, cert_ports, closed_port):
    """The scenario's URL with the run's ports filled in (certificate servers, a closed loopback port)."""
    if name == "certs":
        return "/certs?" + "&".join(f"{k}={p}" for k, p in cert_ports.items() if k in ("valid", "expired", "wronghost", "selfsigned"))
    if name == "certerror-toplevel":
        return f"/certerror?port={cert_ports['unknownissuer']}"
    if name == "lan-probe":
        return f"/lan?closed={closed_port}"
    return target


def prefs_for(name, host, port):
    """-> {pref: value} for this scenario's profile (SCENARIO_PREFS with the local server filled in)."""
    out = {}
    for k, v in SCENARIO_PREFS.get(name, {}).items():
        out[k] = v.format(host=host, port=port) if isinstance(v, str) else v
    return out


SCRIPTS = {"/sw.js": "self.addEventListener('install', e => self.skipWaiting());",
           "/shared.js": "onconnect = e => e.ports[0].postMessage('ok');",
           "/worker.js": "postMessage('ok');"}


class Server:
    """The controlled local server. Records every request it receives (local, allowed by definition)."""

    def __init__(self, bind="127.0.0.1", port=0):
        self.bind = bind
        self.requests, self.results = [], []
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, body, ctype="text/html; charset=utf-8", extra=()):
                b = body.encode("utf-8") if isinstance(body, str) else body
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(b)))
                for k, v in extra:
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(b)

            def do_GET(self):
                outer.requests.append({"method": "GET", "path": self.path, "headers": dict(self.headers)})
                p = self.path.split("?")[0]
                if p == "/early-hints":
                    # 103 first, on the same connection, then the page (HTTP/1.x: Firefox honours a 103 there too)
                    self.wfile.write((f"{self.protocol_version} 103 Early Hints\r\n"
                                      f"Link: <{EARLY_HINTS_PRECONNECT}>; rel=preconnect\r\n"
                                      f"Link: <{EARLY_HINTS_PRELOAD}>; rel=preload; as=script\r\n\r\n").encode("ascii"))
                    self.wfile.flush()
                    self._send(EARLY_HINTS_PAGE)
                elif p == "/canary":
                    self._send(CANARY_PAGE % CANARIES, extra=[("Set-Cookie", "gc=%s; Path=/; SameSite=Lax" % CANARIES["cookie"])])
                elif p == "/workers":
                    self._send(WORKERS_PAGE)
                elif p == "/certs":
                    self._send(CERTS_PAGE)
                elif p == "/webrtc":
                    self._send(WEBRTC_PAGE)
                elif p == "/dl":
                    self._send(CANARIES["download"] + " download content\n", "application/octet-stream")
                elif p in PAGES:
                    self._send(PAGES[p])
                elif p == "/tiny.exe":
                    self._send(TINY_EXE, "application/x-msdownload",
                               extra=[("Content-Disposition", 'attachment; filename="gorilla-leakgate-test.exe"')])
                elif p in SCRIPTS:
                    self._send(SCRIPTS[p], "text/javascript")
                else:
                    self._send("ok", "text/plain")

            def do_POST(self):
                n = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(n)
                outer.requests.append({"method": "POST", "path": self.path, "headers": dict(self.headers), "body": body[:2000].decode("utf-8", "replace")})
                if self.path == "/result":
                    try:
                        outer.results.append(json.loads(body.decode("utf-8", "replace")))
                    except ValueError:
                        outer.results.append({"raw": body[:500].decode("utf-8", "replace")})
                self._send("ok", "text/plain")

        self.httpd = http.server.ThreadingHTTPServer((bind, port), H)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def url(self, target):
        return f"http://{self.bind}:{self.port}{target}" if target.startswith("/") else target

    def close(self):
        self.httpd.shutdown()
