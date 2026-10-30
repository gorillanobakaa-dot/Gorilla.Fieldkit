"""The three built-in drivers, standard library only. Each takes a browser row, a URL and a throwaway profile folder
and returns {"html", "text", "title", "final_url"}; the profile is made and removed by browser.fetch, the browser
process is stopped by its PID here.

  chromium  BIN --headless=new --dump-dom ... URL                        (the rendered DOM on stdout)
  gecko     BIN --headless --profile P --remote-debugging-port=0         (WebDriver BiDi over wsclient)
  webkit    safaridriver -p PORT + the W3C WebDriver HTTP API            (macOS; remote automation must be on)
"""
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import htmltext, wsclient


class BrowserError(Exception):
    pass


CHROMIUM_FLAGS = ["--no-first-run", "--no-default-browser-check", "--disable-sync", "--disable-background-networking",
                  "--disable-component-update", "--disable-default-apps", "--disable-extensions",
                  "--metrics-recording-only", "--disable-features=Translate,OptimizationHints,MediaRouter",
                  "--disable-gpu"]
# A user.js for the throwaway Firefox profile: every channel to the vendor is shut at the source.
GECKO_PREFS = {
    "datareporting.healthreport.uploadEnabled": False, "datareporting.policy.dataSubmissionEnabled": False,
    "toolkit.telemetry.enabled": False, "toolkit.telemetry.unified": False, "toolkit.telemetry.archive.enabled": False,
    "toolkit.telemetry.server": "", "toolkit.telemetry.reportingpolicy.firstRun": False,
    "toolkit.crashreporter.enabled": False, "browser.crashReports.unsubmittedCheck.enabled": False,
    "app.normandy.enabled": False, "app.normandy.api_url": "", "app.shield.optoutstudies.enabled": False,
    "app.update.auto": False, "app.update.enabled": False, "app.update.service.enabled": False,
    "app.update.checkInstallTime": False, "browser.shell.checkDefaultBrowser": False,
    "network.captive-portal-service.enabled": False, "network.connectivity-service.enabled": False,
    "browser.safebrowsing.malware.enabled": False, "browser.safebrowsing.phishing.enabled": False,
    "browser.safebrowsing.downloads.enabled": False, "browser.safebrowsing.downloads.remote.enabled": False,
    "network.prefetch-next": False, "network.dns.disablePrefetch": True, "network.predictor.enabled": False,
    "services.settings.server": "", "extensions.update.enabled": False, "extensions.getAddons.cache.enabled": False,
    "browser.search.update": False, "browser.newtabpage.activity-stream.feeds.telemetry": False,
    "browser.newtabpage.activity-stream.telemetry": False, "browser.startup.homepage_override.mstone": "ignore",
    "startup.homepage_welcome_url": "", "browser.aboutwelcome.enabled": False, "datareporting.usage.uploadEnabled": False,
    "extensions.pocket.enabled": False, "browser.region.network.url": "", "browser.region.update.enabled": False,
}
PAGE_JS = ("JSON.stringify({u: location.href, t: document.title, "
           "b: document.body ? document.body.innerText : '', h: document.documentElement.outerHTML})")


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def kill_tree(proc):
    """Stop what we started, by PID (and its children): never by image name."""
    if proc.poll() is not None:
        return
    try:
        if sys.platform.startswith("win"):
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, timeout=30)
        else:
            import signal
            os.killpg(proc.pid, signal.SIGKILL)
    except Exception:
        pass
    try:
        proc.kill()
        proc.wait(timeout=10)
    except Exception:
        pass


def _popen(cmd, **kw):
    if not sys.platform.startswith("win"):
        kw["start_new_session"] = True
    return subprocess.Popen(cmd, **kw)


def _page(html, final_url, text=None, title=None):
    p = htmltext.parse(html)
    return {"html": html, "text": text if text is not None else p["text"], "title": title or p["title"],
            "final_url": final_url}


# -- chromium ---------------------------------------------------------------------------------------------------
def chromium_cmd(binary, url, profile, new_headless=True):
    return ([binary, "--headless=new" if new_headless else "--headless", "--dump-dom", f"--user-data-dir={profile}"]
            + CHROMIUM_FLAGS + [url])


def _run_capture(cmd, timeout, popen=_popen):
    proc = popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        kill_tree(proc)
        raise BrowserError(f"timed out after {timeout:g}s") from None
    finally:
        kill_tree(proc)
    return proc.returncode, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")


def chromium(row, url, profile, timeout=30, popen=_popen):
    last = ""
    for new in (True, False):                              # older Chromium has no --headless=new
        code, out, err = _run_capture(chromium_cmd(row["path"], url, profile, new), timeout, popen)
        if "<html" in out.lower():
            return _page(out, url)
        last = f"exit {code}: {(err.strip().splitlines() or [''])[-1][:200]}"
        if "headless" not in err.lower() or not new:
            break
    raise BrowserError(f"no DOM came back ({last})")


# -- gecko: WebDriver BiDi --------------------------------------------------------------------------------------
def write_user_js(profile):
    lines = [f"user_pref({json.dumps(k)}, {json.dumps(v)});" for k, v in GECKO_PREFS.items()]
    (Path(profile) / "user.js").write_text("\n".join(lines) + "\n", encoding="utf-8")


class Bidi:
    """WebDriver BiDi commands over one websocket: ids, waiting for the reply with the same id, events skipped."""

    def __init__(self, ws):
        self.ws, self.n = ws, 0

    def call(self, method, params):
        self.n += 1
        self.ws.send(json.dumps({"id": self.n, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self.n:
                if msg.get("type") == "error" or "error" in msg:
                    raise BrowserError(f"{method}: {msg.get('error')}: {msg.get('message', '')}"[:300])
                return msg.get("result", {})


def bidi_session(bidi, url):
    """session.new -> browsingContext.getTree -> navigate (wait complete) -> script.evaluate. -> the page dict."""
    bidi.call("session.new", {"capabilities": {}})
    tree = bidi.call("browsingContext.getTree", {})
    ctxs = tree.get("contexts") or []
    ctx = ctxs[0]["context"] if ctxs else bidi.call("browsingContext.create", {"type": "tab"})["context"]
    bidi.call("browsingContext.navigate", {"context": ctx, "url": url, "wait": "complete"})
    res = bidi.call("script.evaluate", {"expression": PAGE_JS, "target": {"context": ctx}, "awaitPromise": False,
                                        "resultOwnership": "none"})
    r = res.get("result") or {}
    if res.get("type") == "exception" or r.get("type") != "string":
        raise BrowserError("script.evaluate gave no page")
    d = json.loads(r["value"])
    return _page(d["h"], d["u"], text=d["b"], title=d["t"])


def gecko_cmd(binary, profile, port):
    return [binary, "--headless", "--no-remote", "--profile", str(profile), "--remote-debugging-port", str(port)]


_BIDI_LINE = re.compile(r"WebDriver BiDi listening on (ws://\S+)")


def gecko(row, url, profile, timeout=30, popen=_popen, connect=wsclient.WebSocket.connect, port=None):
    """Firefox 129+ speaks BiDi itself. We choose the port (the stderr line "WebDriver BiDi listening on ws://..." is
    used when it comes); the connection is retried until the agent is up or the browser exits."""
    write_user_js(profile)
    port = port or _free_port()
    proc = popen(gecko_cmd(row["path"], profile, port), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    found = []

    def pump():                                            # also keeps the stderr pipe from filling up
        for raw in iter(proc.stderr.readline, b""):
            m = _BIDI_LINE.search(raw.decode("utf-8", "replace"))
            if m and not found:
                found.append(m.group(1).rstrip("/"))
    threading.Thread(target=pump, daemon=True).start()
    ws = None
    try:
        end = time.time() + timeout
        while ws is None:
            if proc.poll() is not None:
                raise BrowserError("the browser exited before WebDriver BiDi was up")
            if time.time() > end:
                raise BrowserError("no WebDriver BiDi in time (this build may not start its remote agent)")
            try:
                ws = connect((found[0] if found else f"ws://127.0.0.1:{port}") + "/session", timeout=5)
            except OSError:
                time.sleep(0.25)
        return bidi_session(Bidi(ws), url)
    except wsclient.WebSocketError as e:
        raise BrowserError(f"websocket: {e}") from None
    finally:
        if ws is not None:
            ws.close()
        kill_tree(proc)


# -- webkit: safaridriver ---------------------------------------------------------------------------------------
def _wd(method, url, body=None, timeout=30):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}").get("value")
    except urllib.error.HTTPError as e:
        msg = ""
        try:
            msg = json.loads(e.read()).get("value", {}).get("message", "")
        except Exception:
            pass
        raise BrowserError(f"safaridriver: HTTP {e.code} {msg}"[:300]) from None


def safari(row, url, profile, timeout=30, popen=_popen, wd=_wd, driver="/usr/bin/safaridriver"):
    port = _free_port()
    proc = popen([driver, "-p", str(port)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    sid = None
    try:
        end = time.time() + 15
        while True:
            try:
                wd("GET", base + "/status", None, 3)
                break
            except (OSError, BrowserError):
                if time.time() > end or proc.poll() is not None:
                    raise BrowserError("safaridriver did not start") from None
                time.sleep(0.2)
        try:
            v = wd("POST", base + "/session", {"capabilities": {"alwaysMatch": {"browserName": "safari"}}}, timeout)
        except BrowserError as e:
            if "enable" in str(e).lower() or "remote automation" in str(e).lower():
                raise BrowserError("Safari remote automation is off. The person's one step, in Terminal: "
                                   "safaridriver --enable (asks for their password; never run for them)") from None
            raise
        sid = v["sessionId"]
        wd("POST", f"{base}/session/{sid}/url", {"url": url}, timeout)
        js = "return " + PAGE_JS
        d = json.loads(wd("POST", f"{base}/session/{sid}/execute/sync", {"script": js, "args": []}, timeout))
        return _page(d["h"], d["u"], text=d["b"], title=d["t"])
    finally:
        if sid:
            try:
                wd("DELETE", f"{base}/session/{sid}", None, 10)
            except Exception:
                pass
        kill_tree(proc)


# -- optional: Selenium, used only when it happens to be installed ---------------------------------------------
def selenium_gecko(row, url, profile, timeout=30, **_):
    """Firefox older than 129 has no BiDi: geckodriver (already present) through Selenium (already installed)."""
    try:
        from selenium import webdriver
        from selenium.webdriver.firefox.service import Service
    except ImportError:
        raise BrowserError("Firefox is too old for BiDi and selenium is not installed") from None
    gd = os.environ.get("FIELDKIT_GECKODRIVER") or __import__("shutil").which("geckodriver")
    if not gd:
        raise BrowserError("Firefox is too old for BiDi and no geckodriver is present")
    os.environ.update({"SE_AVOID_STATS": "true", "SE_OFFLINE": "true"})
    write_user_js(profile)
    opts = webdriver.FirefoxOptions()
    opts.binary_location = row["path"]
    for a in ("-headless", "-profile", str(profile)):
        opts.add_argument(a)
    drv = webdriver.Firefox(service=Service(executable_path=gd, log_output=os.devnull), options=opts)
    try:
        drv.set_page_load_timeout(timeout)
        drv.get(url)
        return _page(drv.page_source, drv.current_url, text=drv.execute_script(
            "return document.body ? document.body.innerText : ''"), title=drv.title)
    finally:
        drv.quit()


METHODS = {"chromium-dump-dom": chromium, "gecko-bidi": gecko, "safaridriver": safari, "geckodriver": selenium_gecko}
