"""fieldkit web: discovery of installed browsers (fixtures), the three stdlib drivers (fakes and a local fake websocket
server), the fallback order, the throwaway profile, the HTTP-first rule, search parsing, the doctor (asks, never
installs). No network, no real browser."""
import base64
import hashlib
import json
import plistlib
import socket
import struct
import subprocess
import threading

import pytest

from fieldkit.web import browser, cli, discover, doctor, drivers, htmltext, rule, search, wsclient

GOOD = {"status": 200, "has_text": True, "text_chars": 5000, "challenge": False, "title": "T", "final_url": "https://x/"}
PUBLIC = lambda host, port: [(2, 1, 6, "", ("93.184.216.34", port))]       # noqa: E731


@pytest.fixture(autouse=True)
def scratch_env(tmp_path, monkeypatch):
    monkeypatch.setenv("FIELDKIT_SCRATCH_ROOT", str(tmp_path / "scratch"))
    monkeypatch.setenv("FIELDKIT_SCRATCH_STATE", str(tmp_path / "state"))
    return tmp_path / "scratch"


def R(name, method="chromium-dump-dom", engine="chromium", path="/b/x"):
    return {"name": name, "path": path, "version": "1", "engine": engine, "method": method, "why": "", "source": "t"}


# -- the rule -------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("patch,reason", [
    ({}, ""), ({"status": 403}, "HTTP 403"), ({"status": 429}, "HTTP 429"), ({"status": 404}, ""),
    ({"challenge": True}, "bot check page"), ({"has_text": False}, "no text in the answer"),
    ({"text_chars": 50}, "only 50 characters of text")])
def test_rule(patch, reason):
    assert rule.blocked_or_empty(GOOD | patch) == reason


# -- discovery ------------------------------------------------------------------------------------------------
def test_windows_registry_and_folders():
    files = {r"C:\P\Chrome\chrome.exe", r"C:\PF86\Microsoft\Edge\Application\msedge.exe", r"C:\L\Vivaldi\Application\vivaldi.exe"}
    reg = [("Google Chrome", r'"C:\P\Chrome\chrome.exe" --single-argument %1', "HKLM StartMenuInternet"),
           ("Gone", r'"C:\nope.exe"', "HKCU"), ("Dup", r"C:\P\Chrome\chrome.exe", "App Paths")]
    env = {"ProgramFiles(x86)": r"C:\PF86", "LOCALAPPDATA": r"C:\L"}
    got = discover.windows_candidates(reg, env, is_file=lambda p: p in files or p.replace("/", "\\") in files)
    names = [g[0] for g in got]
    assert names[:2] == ["Google Chrome", "Dup"] and "Microsoft Edge" in names and "Vivaldi" in names
    assert "Gone" not in names


def test_clean_exe_forms():
    assert discover._clean_exe(r'"C:\a b\x.exe" -flag') == r"C:\a b\x.exe"
    assert discover._clean_exe(r"C:\a\x.exe -flag") == r"C:\a\x.exe"


DESKTOP = "[Desktop Entry]\nName=Firefox\nName[de]=Feuerfuchs\nExec=/usr/lib/firefox/firefox %u\nCategories=Network;WebBrowser;\n"


def test_linux_desktop_files(tmp_path):
    (tmp_path / "ff.desktop").write_text(DESKTOP)
    (tmp_path / "ed.desktop").write_text("[Desktop Entry]\nName=Editor\nExec=ed\nCategories=Utility;\n")
    (tmp_path / "fp.desktop").write_text("[Desktop Entry]\nName=Chromium\nExec=flatpak run org.chromium.Chromium @@u %U @@\n"
                                         "MimeType=x-scheme-handler/http;\n")
    got = discover.linux_candidates(dirs=[str(tmp_path)], names=("brave-browser",),
                                    which=lambda n: "/usr/bin/brave-browser" if n == "brave-browser" else None)
    by = {g[0]: g for g in got}
    assert by["Firefox"][1] == "/usr/lib/firefox/firefox" and "Editor" not in by
    assert by["Chromium"][1] == "" and "Flatpak" in by["Chromium"][3]
    assert by["brave-browser"][1] == "/usr/bin/brave-browser"


def test_mac_bundles_and_safari(tmp_path):
    def load(p):
        if "Firefox" in p:
            return {"CFBundleName": "Firefox", "CFBundleExecutable": "firefox", "CFBundleShortVersionString": "130.0",
                    "CFBundleURLTypes": [{"CFBundleURLSchemes": ["http", "https"]}]}
        if "Notes" in p:
            return {"CFBundleName": "Notes", "CFBundleURLTypes": [{"CFBundleURLSchemes": ["notes"]}]}
        return plistlib.loads(plistlib.dumps({"CFBundleName": "Safari", "CFBundleShortVersionString": "13.1"}))
    got = discover.mac_candidates(dirs=["/A"], listdir=lambda d: ["Firefox.app", "Notes.app", "Safari.app"], load=load)
    names = {g[0]: g for g in got}
    assert names["Firefox"][1].replace("\\", "/") == "/A/Firefox.app/Contents/MacOS/firefox" and names["Firefox"][2] == "130.0"
    assert "Notes" not in names and "Safari" in names


def test_methods_and_engines():
    assert discover.engine_of("Microsoft Edge", "msedge.exe") == "chromium"
    assert discover.engine_of("Gorilla Unleashed", r"C:\g\firefox.exe") == "gecko"
    assert discover.engine_of("Safari") == "webkit" and discover.engine_of("Internet Explorer") == "unknown"
    assert discover.method_for("gecko", "158.0")[0] == "gecko-bidi"
    m, why = discover.method_for("gecko", "115.2")
    assert m == "" and "too old" in why and "geckodriver" in why
    assert discover.method_for("gecko", "115.2", have_geckodriver=True)[0] == "geckodriver"
    assert discover.method_for("webkit", "13", platform="darwin")[0] == "safaridriver"
    assert discover.method_for("webkit", "13", platform="linux")[0] == ""


def test_version_from_ini_and_folder(tmp_path):
    (tmp_path / "application.ini").write_text("[App]\nVersion=158.0\n")
    assert discover.version_of(tmp_path / "firefox.exe") == "158.0"
    c = tmp_path / "chrome"
    (c / "156.0.8078.74").mkdir(parents=True)
    assert discover.version_of(c / "chrome.exe", platform="win32") == "156.0.8078.74"


def test_gorilla_firefox_tree(tmp_path):
    b = tmp_path / "obj-x" / "dist" / "bin"
    b.mkdir(parents=True)
    (b / "firefox").write_text("x")
    assert discover.gorilla_firefox(str(tmp_path), windows=False)[0][0] == "Gorilla Firefox"
    assert discover.gorilla_firefox(str(tmp_path), windows=True) == []


def test_choose_order_prefer_gorilla_gecko_chromium_safari():
    rows = [R("Safari", "safaridriver", "webkit"), R("Google Chrome"), R("Mozilla Firefox", "gecko-bidi", "gecko"),
            R("Gorilla Unleashed", "gecko-bidi", "gecko"), R("Microsoft Edge"), R("Old", "", "gecko")]
    assert [r["name"] for r in discover.choose(rows)] == [
        "Gorilla Unleashed", "Mozilla Firefox", "Google Chrome", "Microsoft Edge", "Safari"]
    assert discover.choose(rows, prefer=["edge"])[0]["name"] == "Microsoft Edge"
    assert [r["name"] for r in discover.choose(rows, name="chrome")] == ["Google Chrome"]
    assert {r["engine"] for r in discover.choose(rows, engine="gecko")} == {"gecko"}


def test_discover_dedupes_and_reads_extra(tmp_path):
    a = tmp_path / "mybrowser.exe"
    a.write_text("x")
    rows = discover.discover({"extra": [str(a), str(a)]}, {}, env={}, platform="linux")
    assert len([r for r in rows if r["path"] == str(a)]) == 1


# -- html -> text ---------------------------------------------------------------------------------------------
def test_htmltext_drops_script_style_and_keeps_links():
    h = ("<html><head><title> T  1 </title><style>p{}</style></head><body><script>var a=1</script>"
         "<h1>Head</h1><p>one <b>two</b></p><noscript>nojs</noscript><a href='http://e.com/x'>link text</a></body></html>")
    p = htmltext.parse(h)
    assert p["title"] == "T 1" and p["text"] == "Head\none two\nlink text"
    assert ("a", "http://e.com/x", "link text") in p["events"]


# -- chromium -------------------------------------------------------------------------------------------------
class FakeProc:
    def __init__(self, out=b"", err=b"", code=0, hang=False):
        self.out, self.err, self.returncode, self.hang, self.killed, self.pid = out, err, code, hang, False, 4242
        self.stuck = hang

    def communicate(self, timeout=None):
        if self.hang:
            self.hang = False
            raise subprocess.TimeoutExpired("x", timeout)
        return self.out, self.err

    def poll(self):
        return None if self.stuck and not self.killed else 0

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        return 0


def test_chromium_flags():
    cmd = drivers.chromium_cmd("/c/chrome", "https://x/", "/tmp/p")
    assert cmd[:3] == ["/c/chrome", "--headless=new", "--dump-dom"] and "--user-data-dir=/tmp/p" in cmd
    for f in ("--no-first-run", "--disable-sync", "--disable-background-networking", "--disable-component-update",
              "--metrics-recording-only", "--disable-features=Translate,OptimizationHints,MediaRouter"):
        assert f in cmd
    assert cmd[-1] == "https://x/" and drivers.chromium_cmd("c", "u", "p", new_headless=False)[1] == "--headless"


def test_chromium_dump_dom_parsed():
    dom = b"<html><head><title>Hi</title></head><body><script>x</script><p>Body text</p></body></html>"
    got = drivers.chromium(R("Edge"), "https://x/", "/p", popen=lambda cmd, **k: FakeProc(dom))
    assert got["title"] == "Hi" and got["text"] == "Body text" and got["final_url"] == "https://x/"


def test_chromium_falls_back_to_old_headless():
    calls = []

    def popen(cmd, **k):
        calls.append(cmd[1])
        return FakeProc(b"", b"Unknown flag headless=new", 1) if len(calls) == 1 else FakeProc(b"<html><body>ok</body></html>")
    assert drivers.chromium(R("Old"), "https://x/", "/p", popen=popen)["text"] == "ok"
    assert calls == ["--headless=new", "--headless"]


def test_chromium_timeout_kills():
    p = FakeProc(hang=True)
    with pytest.raises(browser.BrowserError, match="timed out"):
        drivers.chromium(R("Edge"), "https://x/", "/p", timeout=1, popen=lambda *a, **k: p)
    assert p.killed


# -- websocket ------------------------------------------------------------------------------------------------
def test_accept_key_rfc_example():
    assert wsclient.accept_key("dGhlIHNhbXBsZSBub25jZQ==") == "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="


def test_frame_masking_roundtrip():
    f = wsclient.encode_frame("héllo", mask=True)
    assert f[1] & 0x80
    key, body = f[2:6], f[6:]
    assert bytes(b ^ key[i % 4] for i, b in enumerate(body)).decode() == "héllo"
    big = wsclient.encode_frame("x" * 70000, mask=False)
    assert big[1] == 127 and struct.unpack(">Q", big[2:10])[0] == 70000


class FakeServer:
    """A stdlib websocket server on 127.0.0.1: handshake, then handler(conn_recv, conn_send)."""

    def __init__(self, handler):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.seen = []
        self.t = threading.Thread(target=self._run, args=(handler,), daemon=True)
        self.t.start()

    def _run(self, handler):
        c, _ = self.sock.accept()
        c.settimeout(10)
        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += c.recv(4096)
        head = buf.decode("latin-1")
        self.path = head.split(" ")[1]
        key = [ln.split(":")[1].strip() for ln in head.split("\r\n") if ln.lower().startswith("sec-websocket-key")][0]
        c.sendall(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                   f"Sec-WebSocket-Accept: {wsclient.accept_key(key)}\r\n\r\n").encode())
        ws = wsclient.WebSocket(c)

        def send(text, split=False):
            if split:                                        # two fragments, to prove they are joined
                b = text.encode()
                c.sendall(bytes([0x01, 0x00 | len(b[:5])]) + b[:5] + bytes([0x80, len(b[5:])]) + b[5:])
            else:
                c.sendall(wsclient.encode_frame(text, mask=False))
        handler(ws, send, self.seen)

    def close(self):
        self.sock.close()


def test_websocket_client_against_fake_server():
    def handler(ws, send, seen):
        seen.append(ws.recv())
        c = ws.sock
        c.sendall(wsclient.encode_frame(b"p", 0x9, mask=False))          # a ping first
        send("pong-split-message", split=True)
        try:
            ws.recv()                                        # stay open until the client closes
        except OSError:
            pass
    srv = FakeServer(handler)
    ws = wsclient.WebSocket.connect(f"ws://127.0.0.1:{srv.port}/session", timeout=5)
    ws.send("hello")
    assert ws.recv() == "pong-split-message"
    srv.t.join(5)
    assert srv.seen == ["hello"] and srv.path == "/session"
    ws.close()


def test_handshake_rejects_bad_accept():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)

    def serve():
        c, _ = s.accept()
        c.recv(4096)
        c.sendall(b"HTTP/1.1 101 Switching Protocols\r\nSec-WebSocket-Accept: wrong\r\n\r\n")
    threading.Thread(target=serve, daemon=True).start()
    with pytest.raises(wsclient.WebSocketError, match="Accept"):
        wsclient.WebSocket.connect(f"ws://127.0.0.1:{s.getsockname()[1]}/", timeout=5)


# -- gecko: BiDi ----------------------------------------------------------------------------------------------
def _bidi_handler(page):
    def handler(ws, send, seen):
        while True:
            try:
                m = json.loads(ws.recv())
            except wsclient.WebSocketError:
                return
            seen.append((m["method"], m["params"]))
            res = {}
            if m["method"] == "browsingContext.getTree":
                res = {"contexts": [{"context": "CTX1"}]}
            if m["method"] == "script.evaluate":
                res = {"type": "success", "result": {"type": "string", "value": json.dumps(page)}}
            send(json.dumps({"type": "event", "method": "browsingContext.load", "params": {}}))   # events are skipped
            send(json.dumps({"type": "success", "id": m["id"], "result": res}))
    return handler


def test_bidi_message_order_and_page():
    page = {"u": "https://x/final", "t": "Title", "b": "inner text", "h": "<html><body>inner text</body></html>"}
    srv = FakeServer(_bidi_handler(page))
    ws = wsclient.WebSocket.connect(f"ws://127.0.0.1:{srv.port}/session", timeout=5)
    got = drivers.bidi_session(drivers.Bidi(ws), "https://x/")
    ws.close()
    assert [m for m, _ in srv.seen] == ["session.new", "browsingContext.getTree", "browsingContext.navigate",
                                        "script.evaluate"]
    nav = dict(srv.seen)["browsingContext.navigate"]
    assert nav == {"context": "CTX1", "url": "https://x/", "wait": "complete"}
    assert got["text"] == "inner text" and got["title"] == "Title" and got["final_url"] == "https://x/final"


def test_bidi_error_reply():
    def handler(ws, send, seen):
        m = json.loads(ws.recv())
        send(json.dumps({"type": "error", "id": m["id"], "error": "session not created", "message": "no"}))
    srv = FakeServer(handler)
    ws = wsclient.WebSocket.connect(f"ws://127.0.0.1:{srv.port}/session", timeout=5)
    with pytest.raises(browser.BrowserError, match="session not created"):
        drivers.bidi_session(drivers.Bidi(ws), "https://x/")


def test_gecko_driver_flags_userjs_and_kill(tmp_path, monkeypatch):
    page = {"u": "https://x/", "t": "T", "b": "txt", "h": "<html></html>"}
    srv = FakeServer(_bidi_handler(page))
    cmds, procs = [], []

    class P(FakeProc):
        def __init__(self):
            super().__init__()
            import io
            self.stderr = io.BytesIO(f"WebDriver BiDi listening on ws://127.0.0.1:{srv.port}\n".encode())

        def poll(self):
            return None if not self.killed else 0

    def popen(cmd, **k):
        cmds.append(cmd)
        procs.append(P())
        return procs[0]
    monkeypatch.setattr(drivers, "kill_tree", lambda p: p.kill())
    prof = tmp_path / "prof"
    prof.mkdir()
    got = drivers.gecko(R("Gorilla Firefox", "gecko-bidi", "gecko", "/g/firefox"), "https://x/", prof, popen=popen)
    assert got["text"] == "txt" and procs[0].killed
    c = cmds[0]
    assert c[:4] == ["/g/firefox", "--headless", "--no-remote", "--profile"] and "--remote-debugging-port" in c
    js = (prof / "user.js").read_text()
    for pref in ("toolkit.telemetry.enabled", "app.normandy.enabled", "network.captive-portal-service.enabled",
                 "network.connectivity-service.enabled", "browser.safebrowsing.malware.enabled", "app.update.auto",
                 "datareporting.healthreport.uploadEnabled", "network.prefetch-next"):
        assert f'user_pref("{pref}"' in js


def test_gecko_that_exits_is_an_error(tmp_path):
    import io

    class Dead(FakeProc):
        stderr = io.BytesIO(b"")

        def poll(self):
            return 1
    with pytest.raises(browser.BrowserError, match="exited"):
        drivers.gecko(R("F", "gecko-bidi", "gecko"), "https://x/", tmp_path, popen=lambda *a, **k: Dead(), port=1)


# -- safari ---------------------------------------------------------------------------------------------------
def test_safari_webdriver_calls():
    calls = []
    page = {"u": "https://x/", "t": "S", "b": "safari text", "h": "<html></html>"}

    def wd(method, url, body=None, timeout=30):
        calls.append((method, url.split("127.0.0.1:")[1].split("/", 1)[1], body))
        if url.endswith("/session"):
            return {"sessionId": "SID"}
        if url.endswith("/execute/sync"):
            return json.dumps(page)
        return None
    got = drivers.safari(R("Safari", "safaridriver", "webkit"), "https://x/", "/p", popen=lambda *a, **k: FakeProc(),
                         wd=wd)
    assert got["text"] == "safari text"
    assert [(m, p) for m, p, _ in calls] == [("GET", "status"), ("POST", "session"), ("POST", "session/SID/url"),
                                              ("POST", "session/SID/execute/sync"), ("DELETE", "session/SID")]


def test_safari_automation_off_says_the_step():
    def wd(method, url, body=None, timeout=30):
        if url.endswith("/session"):
            raise browser.BrowserError("safaridriver: HTTP 500 You must enable Remote Automation")
        return None
    with pytest.raises(browser.BrowserError, match="safaridriver --enable"):
        drivers.safari(R("Safari", "safaridriver", "webkit"), "https://x/", "/p", popen=lambda *a, **k: FakeProc(), wd=wd)


# -- fetch: order, fallback, profile cleanup, refusals --------------------------------------------------------
def test_fetch_falls_back_to_next_and_reports_which(scratch_env):
    rows = [R("Gorilla Unleashed", "gecko-bidi", "gecko"), R("Microsoft Edge")]
    order = []

    def gecko(row, url, profile, timeout):
        order.append(row["name"])
        assert profile.is_dir()
        raise browser.BrowserError("no BiDi")

    def chrom(row, url, profile, timeout):
        order.append(row["name"])
        return {"html": "<p>x</p>", "text": "x", "title": "t", "final_url": url}
    got = browser.fetch("https://x/", cfg={}, rows=rows, resolve=PUBLIC,
                        methods={"gecko-bidi": gecko, "chromium-dump-dom": chrom})
    assert order == ["Gorilla Unleashed", "Microsoft Edge"] and got["browser"] == "Microsoft Edge"
    assert got["tried"] == [{"browser": "Gorilla Unleashed", "error": "no BiDi"}]
    assert list(scratch_env.iterdir()) == []                  # every profile folder gone, the failed one too


def test_fetch_prefer_list_and_named_browser():
    rows = [R("Gorilla Unleashed", "gecko-bidi", "gecko"), R("Microsoft Edge"), R("Google Chrome")]
    seen = []
    m = {"gecko-bidi": lambda r, u, p, t: seen.append(r["name"]) or {"html": "", "text": "", "title": "", "final_url": u},
         "chromium-dump-dom": lambda r, u, p, t: seen.append(r["name"]) or {"html": "", "text": "", "title": "", "final_url": u}}
    browser.fetch("https://x/", cfg={"prefer": ["chrome"]}, rows=rows, resolve=PUBLIC, methods=m)
    browser.fetch("https://x/", "edge", cfg={}, rows=rows, resolve=PUBLIC, methods=m)
    browser.fetch("https://x/", cfg={}, engine="chromium", rows=rows, resolve=PUBLIC, methods=m)
    assert seen == ["Google Chrome", "Microsoft Edge", "Microsoft Edge"]


def test_fetch_all_fail_and_none_found():
    def boom(*a):
        raise RuntimeError("driver bug")
    with pytest.raises(browser.BrowserError, match="every browser failed: Microsoft Edge: RuntimeError: driver bug"):
        browser.fetch("https://x/", cfg={}, rows=[R("Microsoft Edge")], resolve=PUBLIC, methods={"chromium-dump-dom": boom})
    with pytest.raises(browser.BrowserError, match="no drivable browser"):
        browser.fetch("https://x/", cfg={}, rows=[R("Old", "", "gecko")], resolve=PUBLIC)


def test_private_address_refused():
    local = lambda h, p: [(2, 1, 6, "", ("127.0.0.1", p))]                      # noqa: E731
    with pytest.raises(browser.BrowserError, match="not a public address"):
        browser.fetch("http://localhost/", cfg={}, rows=[R("E")], resolve=local)
    with pytest.raises(browser.BrowserError, match="http"):
        browser.check_public("file:///etc/passwd")


def test_nat64_addresses_follow_the_ipv4_inside():
    nat64 = lambda ip: (lambda h, p: [(23, 1, 6, "", (ip, p, 0, 0))])         # noqa: E731
    browser.check_public("https://excalidraw.com/", resolve=nat64("64:ff9b::d896:101"))
    with pytest.raises(browser.BrowserError, match="not a public address"):
        browser.check_public("https://x/", resolve=nat64("64:ff9b::7f00:1"))
    with pytest.raises(browser.BrowserError, match="not a public address"):
        browser.check_public("https://x/", resolve=nat64("64:ff9b::a9fe:a9fe"))


# -- the command: HTTP first ----------------------------------------------------------------------------------
def _got(**k):
    return {"title": "B", "final_url": "https://x/", "text": "x" * 500, "browser": "Microsoft Edge",
            "engine": "chromium", "method": "chromium-dump-dom", "tried": []} | k


def test_auto_uses_http_when_page_is_fine():
    calls = []
    r = cli.fetch("https://x/", http=lambda u, t: (GOOD, "plain text"), browse=lambda *a, **k: calls.append(1))
    assert r["via"] == "http" and r["text"] == "plain text" and not calls


def test_auto_goes_to_browser_on_blocked_page():
    r = cli.fetch("https://x/", http=lambda u, t: (GOOD | {"status": 403}, ""), browse=lambda u, **k: _got())
    assert r["via"] == "browser" and r["reason"] == "HTTP 403" and r["browser"] == "Microsoft Edge"


def test_auto_goes_to_browser_on_network_failure():
    def http(u, t):
        raise OSError("reset")
    r = cli.fetch("https://x/", http=http, browse=lambda u, **k: _got())
    assert r["via"] == "browser" and "reset" in r["reason"]


def test_robots_refusal_is_obeyed_not_bypassed():
    from fieldkit.evidence.fetch import Refused

    def http(u, t):
        raise Refused("robots.txt disallows")
    with pytest.raises(Refused):
        cli.fetch("https://x/", http=http, browse=lambda *a, **k: pytest.fail("browser must not run"))


def test_explicit_browser_skips_http():
    seen = {}
    r = cli.fetch("https://x/", "edge", "chromium", http=lambda *a: pytest.fail("no http"),
                  browse=lambda u, browser, engine, timeout: seen.update(b=browser, e=engine) or _got())
    assert r["via"] == "browser" and r["reason"] == "" and seen == {"b": "edge", "e": "chromium"}


# -- doctor: asks, never installs -----------------------------------------------------------------------------
def test_doctor_names_the_one_step_and_installs_nothing(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("the doctor runs nothing"))
    rows = [R("Microsoft Edge"),
            discover.row("Firefox", "/x/firefox", "t", version="115.2", run=lambda *a, **k: pytest.fail("x")),
            R("Internet Explorer", "", "unknown")]
    rows[2]["why"] = "no automation interface for this browser"
    out = doctor.check(rows=rows, cfg={})
    by = {r["item"]: r for r in out}
    assert by["Microsoft Edge"]["ok"]
    assert not by["Firefox"]["ok"] and "update Firefox" in by["Firefox"]["fix"] and "geckodriver" in by["Firefox"]["fix"]
    assert not by["Internet Explorer"]["ok"] and by["drivable browsers"]["ok"]
    assert not hasattr(doctor, "install") and not hasattr(doctor, "install_geckodriver")


def test_safari_off_mac_is_reported_not_driven():
    out = doctor.check(rows=[discover.row("Safari", "/S", "t", version="13", platform="linux")], cfg={})
    assert not out[0]["ok"] and "only on macOS" in out[0]["fix"] and not out[-1]["ok"]


# -- search ---------------------------------------------------------------------------------------------------
RESULTS = ("<html><body><a href='https://www.mojeek.com/about'>About the engine</a>"
           "<a href='https://a.example/page'>First result title</a><p>This is a long enough snippet for the first result page.</p>"
           "<a href='https://b.example/'>Second result title</a><p>Another snippet that is certainly long enough to keep.</p>"
           "<a href='https://a.example/page'>First result title</a></body></html>")


def test_parse_results():
    got = search.parse_results(RESULTS, "mojeek.com")
    assert [g["url"] for g in got] == ["https://a.example/page", "https://b.example/"]
    assert got[0]["title"] == "First result title" and got[0]["snippet"].startswith("This is a long")


def test_search_engine_order_http_then_browser():
    calls = []

    def http(url, t):
        calls.append(("http", url.split("/")[2]))
        if "brave" in url:
            raise OSError("HTTP Error 429")
        return "<html><title>Captcha</title><body>please solve the captcha</body></html>"

    def browse(url, timeout):
        calls.append(("browser", url.split("/")[2]))
        return {"html": RESULTS.replace("www.mojeek.com", "www.ecosia.org") if "ecosia" in url else "<html><body>nothing</body></html>"}
    r = search.search("q w", http=http, browse=browse)
    assert r["engine"] == "ecosia" and r["via"] == "browser" and len(r["results"]) == 2
    assert calls[:3] == [("http", "search.brave.com"), ("browser", "search.brave.com"), ("http", "www.ecosia.org")]
    assert any("brave.com/http: HTTP Error 429" in t.replace("brave/", "brave.com/") or "brave/http" in t for t in r["tried"])
    assert any("blocked" in t for t in r["tried"])


def test_search_unknown_engine():
    with pytest.raises(browser.BrowserError, match="unknown engine"):
        search.search("q", engine="nope")


def test_no_install_or_download_path():
    import fieldkit.web as w
    import pathlib
    src = "".join(p.read_text(encoding="utf-8") for p in pathlib.Path(w.__file__).parent.glob("*.py")
                  if p.name != "search.py")
    assert "pip" not in src.replace("pipe", "").replace("pump", "") or "pip install" not in src
    assert "urlretrieve" not in src and "geckodriver-v" not in src
    assert base64 and hashlib                                  # (the websocket handshake uses both)
