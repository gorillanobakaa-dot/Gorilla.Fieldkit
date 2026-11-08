"""Fetch one page through a browser already installed on this computer, with a throwaway profile.

Which browsers, and how each is driven: discover.py and drivers.py (standard library only, nothing is installed or
downloaded). The order of trying: the "prefer" names, the owner-built Gorilla Firefox, other Gecko, Chromium family,
Safari; the next one when one fails. Every run has its own profile folder in the scratch root, deleted after.
"""
import ipaddress
import socket
from urllib.parse import urlsplit

from ..core import settings
from ..evidence.urls import blocked_ip, unwrap
from . import discover, drivers
from .drivers import BrowserError


def config():
    c = settings.local_settings().get("browser_fetch") or {}
    return settings.expand(c, strict=False) if isinstance(c, dict) else {}


def installed(cfg=None, local=None, **kw):
    cfg = config() if cfg is None else cfg
    local = settings.local_settings() if local is None else local
    return discover.discover(cfg, local, **kw)


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
        if not unwrap(ip).is_global or blocked_ip(ip):
            raise BrowserError(f"{p.hostname} resolves to {ip}, which is not a public address: refused")


def run_one(row, url, timeout, scratch=None, methods=None):
    """One browser, one fresh profile in the scratch root, removed before this returns."""
    from .. import scratch as scratchmod
    scratch = scratch or scratchmod
    fn = (methods or drivers.METHODS)[row["method"]]
    d, m = scratch.new(purpose=f"fieldkit web fetch: throwaway {row['name']} profile", kind="web-profile")
    try:
        profile = d / "profile"
        profile.mkdir()
        return fn(row, url, profile, timeout)
    finally:
        scratch.delete_job(d, m, "web-fetch-own-profile")


def fetch(url, browser="auto", engine=None, timeout=30, cfg=None, rows=None, resolve=socket.getaddrinfo,
          scratch=None, methods=None):
    """-> {"url","final_url","title","text","html","browser","browser_path","engine","method","tried"}.
    tried = [{"browser","error"}] for the browsers that failed first. BrowserError when none answers."""
    check_public(url, resolve)
    cfg = config() if cfg is None else cfg
    rows = installed(cfg) if rows is None else rows
    order = discover.choose(rows, cfg.get("prefer") or (), None if browser == "auto" else browser, engine)
    if not order:
        raise BrowserError("no drivable browser" + ("" if browser == "auto" else f" matching {browser!r}")
                           + " is installed (fieldkit web doctor)")
    tried = []
    for r in order:
        try:
            got = run_one(r, url, timeout, scratch, methods)
        except BrowserError as e:
            tried.append({"browser": r["name"], "error": str(e)})
        except Exception as e:                           # a driver bug must not hide the next browser
            tried.append({"browser": r["name"], "error": f"{type(e).__name__}: {e}"[:300]})
        else:
            return got | {"url": url, "browser": r["name"], "browser_path": r["path"], "engine": r["engine"],
                          "method": r["method"], "tried": tried}
    raise BrowserError("every browser failed: " + "; ".join(f"{t['browser']}: {t['error']}" for t in tried))
