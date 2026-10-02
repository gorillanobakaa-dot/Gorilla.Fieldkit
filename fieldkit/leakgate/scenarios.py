"""Scenarios and the controlled local test server.

Every scenario runs on a fresh throwaway profile. Local pages are served from 127.0.0.1 by this process, so the
test controls them byte for byte; canaries are unique strings planted where the spec says (URL, title, cookie,
localStorage, IndexedDB, form value, download name/content, private window). A canary in any captured byte that
leaves the machine is an automatic failure.
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
]
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
                if p == "/canary":
                    self._send(CANARY_PAGE % CANARIES, extra=[("Set-Cookie", "gc=%s; Path=/; SameSite=Lax" % CANARIES["cookie"])])
                elif p == "/workers":
                    self._send(WORKERS_PAGE)
                elif p == "/webrtc":
                    self._send(WEBRTC_PAGE)
                elif p == "/dl":
                    self._send(CANARIES["download"] + " download content\n", "application/octet-stream")
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
