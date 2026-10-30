"""Fetch one page through a real browser (Selenium) with a throwaway profile.

No Selenium Manager: Selenium looks for a driver and a browser on its own, and downloads them, only when it is not
given both paths. We always give both (driver_paths) and refuse to start when either is missing, so no download path
is reachable. Its usage pings are cut at the source: SE_AVOID_STATS=true; SE_OFFLINE=true as well.
"""
import ipaddress
import os
import socket
import sys
from pathlib import Path
from urllib.parse import urlsplit

from ..core import settings

TELEMETRY_ENV = {"SE_AVOID_STATS": "true", "SE_OFFLINE": "true"}
BODY_JS = "return document.body ? document.body.innerText : ''"
FIREFOX_PREFS = {                       # off at the source, in the throwaway profile as well as in Gorilla Firefox
    "toolkit.telemetry.enabled": False, "toolkit.telemetry.unified": False,
    "datareporting.healthreport.uploadEnabled": False, "datareporting.policy.dataSubmissionEnabled": False,
    "app.update.auto": False, "app.update.enabled": False, "browser.shell.checkDefaultBrowser": False,
}
SAFARIDRIVER = "/usr/bin/safaridriver"


class BrowserError(Exception):
    pass


def apply_env(env=None):
    env = os.environ if env is None else env
    env.update(TELEMETRY_ENV)
    return env


def config():
    c = settings.local_settings().get("browser_fetch") or {}
    return settings.expand(c, strict=False) if isinstance(c, dict) else {}


def driver_paths(cfg, browser="firefox"):
    """-> (browser binary, driver) as configured, each an existing file; BrowserError says what is missing. Never a guess."""
    if browser == "safari":
        return None, SAFARIDRIVER
    out = []
    for key in ("firefox", "geckodriver"):
        v = cfg.get(key)
        if not v:
            raise BrowserError(f'fieldkit.local.json has no "browser_fetch": {{"{key}": PATH}} (fieldkit web doctor)')
        if not Path(v).is_file():
            raise BrowserError(f"browser_fetch.{key} is not a file: {v} (fieldkit web doctor)")
        out.append(str(v))
    return out[0], out[1]


def check_public(url, resolve=socket.getaddrinfo):
    """The evidence fetcher's rule for the browser too: http(s) only, every address the host resolves to is public.
    Not covered: a redirect the page makes inside the browser to a private address."""
    p = urlsplit(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise BrowserError(f"not an http(s) URL: {url}")
    try:
        infos = resolve(p.hostname, p.port or (443 if p.scheme == "https" else 80))
    except OSError as e:
        raise BrowserError(f"cannot resolve {p.hostname}: {e}") from None
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if not ip.is_global:
            raise BrowserError(f"{p.hostname} resolves to {ip}, which is not a public address: refused")


def _selenium_driver(browser, cfg, profile):
    from selenium import webdriver                      # imported late: selenium is optional (fieldkit web doctor)
    binary, driver = driver_paths(cfg, browser)
    if browser == "safari":
        from selenium.webdriver.safari.service import Service
        return webdriver.Safari(service=Service(executable_path=driver))
    from selenium.webdriver.firefox.service import Service
    opts = webdriver.FirefoxOptions()
    opts.binary_location = binary
    opts.add_argument("-headless")
    opts.add_argument("-profile")
    opts.add_argument(str(profile))
    for k, v in FIREFOX_PREFS.items():
        opts.set_preference(k, v)
    return webdriver.Firefox(service=Service(executable_path=driver, log_output=os.devnull), options=opts)


def fetch(url, browser="firefox", timeout=30, cfg=None, factory=None, resolve=socket.getaddrinfo, scratch=None):
    """-> {"url", "final_url", "title", "text", "browser"}. The profile folder is deleted before this returns."""
    from .. import scratch as scratchmod
    scratch = scratch or scratchmod
    if browser == "safari" and sys.platform != "darwin":
        raise BrowserError("safari is only on macOS")
    check_public(url, resolve)
    cfg = config() if cfg is None else cfg
    if factory is None:
        driver_paths(cfg, browser)                       # fail before anything is made
    apply_env()
    d, m = scratch.new(purpose="fieldkit web fetch: throwaway browser profile", kind="web-profile")
    drv = None
    try:
        profile = d / "profile"
        profile.mkdir()
        drv = (factory or _selenium_driver)(browser, cfg, profile)
        drv.set_page_load_timeout(timeout)
        try:
            drv.get(url)
            text = drv.execute_script(BODY_JS) or ""
            return {"url": url, "final_url": drv.current_url, "title": drv.title or "", "text": text,
                    "browser": browser}
        except Exception as e:
            raise BrowserError(f"{type(e).__name__}: {str(e).splitlines()[0] if str(e) else ''}") from None
    finally:
        if drv is not None:
            try:
                drv.quit()
            except Exception:
                pass
        scratch.delete_job(d, m, "web-fetch-own-profile")
