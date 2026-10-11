"""Fetch a page as evidence: safely, politely, and kept.

Safe: http and https only; every address the host resolves to must be public (no loopback, private network,
link-local/metadata, multicast, reserved), and the connection goes to the address that was checked, so a second
DNS answer cannot swap it (rebinding). Redirects are followed by hand, at most 10, each one checked the same way.
Behind a proxy (HTTPS_PROXY / HTTP_PROXY) the tunnel goes through it; the target is still checked first.

Polite: robots.txt is read and obeyed (RFC 9309: a 4xx robots.txt allows everything, a 5xx or unreachable one
allows nothing); one request per host per --min-interval seconds, also across runs; 429 and 503 wait as long as
Retry-After says (at most 60 s) and are retried three times. The User-Agent names Fieldkit, with a contact address
when FIELDKIT_CONTACT_EMAIL is set: a page read as evidence is read openly.

Kept: every answer, an error page included, goes into the store with its status, so "the page said X" and "the page
was gone" are both on record.
"""
import email.utils
import gzip
import http.client
import ipaddress
import os
import re
import socket
import ssl
import time
import urllib.request
import zlib
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

from .. import __version__
from . import text as textmod
from . import urls

MAX_BYTES = 25 * 1024 * 1024
RETRY_STATUS = (429, 503)
# A page that answers 200 with a bot check is not the page. Measured 2026-10-11: pypi.org served "Client Challenge"
# with status 200 to a plain client.
CHALLENGE = re.compile(r"client challenge|just a moment|attention required|are you a robot|verify you are human|"
                       r"captcha|enable javascript and cookies|access denied", re.I)
UA_TOKEN = "fieldkit"


def user_agent():
    contact = os.environ.get("FIELDKIT_CONTACT_EMAIL", "").strip()
    tail = f"; {contact}" if contact and "@" in contact and " " not in contact else ""
    return f"{UA_TOKEN}/{__version__} (+https://github.com/gorillanobakaa-dot/Gorilla.Fieldkit{tail})"


class Refused(Exception):
    """The fetch was not made, on purpose: an address that must not be reached, robots.txt, a bad URL."""


class _Pinned(http.client.HTTPConnection):
    def __init__(self, host, ip, **kw):
        super().__init__(host, **kw)
        self._ip = ip

    def connect(self):
        self.sock = socket.create_connection((self._ip, self.port), self.timeout)


class _PinnedTLS(http.client.HTTPSConnection):
    def __init__(self, host, ip, **kw):
        super().__init__(host, **kw)
        self._ip = ip

    def connect(self):
        sock = socket.create_connection((self._ip, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def _is_ip(host):
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def _proxy_for(scheme, host):
    proxies = urllib.request.getproxies()
    if urllib.request.proxy_bypass(host):
        return None
    p = proxies.get(scheme) or (proxies.get("https") if scheme == "https" else None)
    return urlsplit(p if "://" in p else "http://" + p) if p else None


def _context():
    ctx = ssl.create_default_context()
    bundle = os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE")
    if bundle and Path(bundle).is_file():
        ctx.load_verify_locations(bundle)
    return ctx


def _one(url, ua, timeout, allow_private, max_bytes, connect=None):
    """One request, no redirects followed. -> (status, headers dict lower-case, body, truncated)."""
    p = urlsplit(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise Refused(f"not an http(s) URL: {url}")
    port = p.port or (443 if p.scheme == "https" else 80)
    proxy = None if connect else _proxy_for(p.scheme, p.hostname)
    ip, why = (p.hostname, "") if allow_private else urls.resolve_public(p.hostname, port)
    if ip is None:
        # Behind a proxy this machine may have no DNS of its own: a name that does not resolve HERE is resolved by
        # the proxy, which is then the guard. A name that resolves here to a blocked address is refused all the
        # same, and so is a blocked address written as an IP.
        if not (proxy and "does not resolve" in why and not _is_ip(p.hostname)):
            raise Refused(f"refused: {why}")
    path = (p.path or "/") + (f"?{p.query}" if p.query else "")
    headers = {"User-Agent": ua, "Accept-Encoding": "gzip, deflate", "Accept": "*/*", "Connection": "close"}
    if connect:                                       # tests: a connection factory
        conn = connect(p, port)
    else:
        if proxy and p.scheme == "https":
            conn = http.client.HTTPSConnection(proxy.hostname, proxy.port or 8080, timeout=timeout,
                                               context=_context())
            conn.set_tunnel(p.hostname, port)
        elif proxy:
            conn = http.client.HTTPConnection(proxy.hostname, proxy.port or 8080, timeout=timeout)
            path = url
        elif p.scheme == "https":
            conn = _PinnedTLS(p.hostname, ip, port=port, timeout=timeout, context=_context())
        else:
            conn = _Pinned(p.hostname, ip, port=port, timeout=timeout)
    try:
        conn.request("GET", path, headers=headers)
        r = conn.getresponse()
        raw = r.read(max_bytes + 1)
        truncated = len(raw) > max_bytes
        raw = raw[:max_bytes]
        hdrs = {k.lower(): v for k, v in r.getheaders()}
        status = r.status
    finally:
        conn.close()
    enc = hdrs.get("content-encoding", "").lower()
    try:
        if enc == "gzip" or raw[:2] == b"\x1f\x8b":
            raw = gzip.decompress(raw) if not truncated else zlib.decompressobj(31).decompress(raw)
        elif enc == "deflate":
            raw = zlib.decompress(raw)
    except (OSError, zlib.error, EOFError):
        pass                                          # kept as received; the snapshot says what it was
    return status, hdrs, raw, truncated


def _retry_after(v, cap=60.0):
    if not v:
        return None
    try:
        return min(float(v), cap)
    except ValueError:
        try:
            when = email.utils.parsedate_to_datetime(v)
            return max(0.0, min((when - datetime.now(timezone.utc)).total_seconds(), cap))
        except (TypeError, ValueError):
            return None


class Fetcher:
    def __init__(self, store, ua=None, timeout=30, allow_private=False, respect_robots=True, min_interval=1.0,
                 max_bytes=MAX_BYTES, sleep=time.sleep, clock=time.time, connect=None):
        self.store, self.ua, self.timeout = store, ua or user_agent(), timeout
        self.allow_private, self.respect_robots = allow_private, respect_robots
        self.min_interval, self.max_bytes = min_interval, max_bytes
        self.sleep, self.clock, self.connect = sleep, clock, connect
        self._robots = {}

    # -- politeness -------------------------------------------------------------------------------------------
    def _pace(self, host):
        d = self.store.root / "pace"
        d.mkdir(parents=True, exist_ok=True, mode=0o700)
        f = d / host.replace(":", "_")
        try:
            last = f.stat().st_mtime
        except OSError:
            last = 0
        gap = self.min_interval - (self.clock() - last)
        if gap > 0:
            self.sleep(gap)
        f.touch()
        os.utime(f, (self.clock(), self.clock()))

    def _request(self, url):
        tries = 0
        while True:
            self._pace(urlsplit(url).netloc.lower())
            status, hdrs, body, trunc = _one(url, self.ua, self.timeout, self.allow_private, self.max_bytes,
                                             self.connect)
            if status in RETRY_STATUS and tries < 3:
                wait = _retry_after(hdrs.get("retry-after"))
                self.sleep(wait if wait is not None else 2 ** (tries + 1))
                tries += 1
                continue
            return status, hdrs, body, trunc

    def allowed(self, url):
        """robots.txt for this URL's site: (allowed, why)."""
        if not self.respect_robots:
            return True, "robots.txt not consulted (--ignore-robots)"
        p = urlsplit(url)
        site = f"{p.scheme}://{p.netloc}"
        if site not in self._robots:
            rp = RobotFileParser()
            target, status, body = site + "/robots.txt", 599, b""
            try:
                for _ in range(6):                    # RFC 9309: follow at least five redirects
                    status, hdrs, body, _ = self._request(target)
                    if status not in (301, 302, 303, 307, 308) or not hdrs.get("location"):
                        break
                    target = urljoin(target, hdrs["location"])
            except Refused:
                raise                                 # the site itself must not be reached: say that, not robots
            except (OSError, http.client.HTTPException) as e:
                status, body = 599, str(e).encode()
            if 200 <= status < 300:
                rp.parse(body.decode("utf-8", "replace").splitlines())
                note = "robots.txt read"
            elif 400 <= status < 500:
                rp.allow_all, note = True, f"robots.txt answered {status}: everything allowed (RFC 9309)"
            else:
                rp.disallow_all, note = True, f"robots.txt unreachable ({status}): nothing allowed (RFC 9309)"
            self._robots[site] = (rp, note)
        rp, note = self._robots[site]
        ok = rp.can_fetch(UA_TOKEN, url)
        if ok:
            return True, note
        return False, note if rp.disallow_all else f"robots.txt of {site} disallows this page for crawlers"

    # -- the fetch --------------------------------------------------------------------------------------------
    def fetch(self, url, run=None):
        """-> the snapshot record (also appended to the store). Raises Refused when nothing was fetched."""
        start = urls.fetchable(urls.normalize(url) if url.lower().startswith(("doi:", "https://doi.org/",
                                                                                 "http://dx.doi.org/")) else url)
        p = urlsplit(start)
        if p.scheme not in ("http", "https") or not p.hostname:
            raise Refused(f"not an http(s) URL: {url}")
        current, chain = start, []
        for _ in range(11):
            ok, robots_note = self.allowed(current)
            if not ok:
                raise Refused(robots_note)
            status, hdrs, body, trunc = self._request(current)
            if status in (301, 302, 303, 307, 308) and hdrs.get("location"):
                nxt = urljoin(current, hdrs["location"])
                chain.append({"url": current, "status": status})
                current = nxt
                continue
            break
        else:
            raise Refused(f"more than 10 redirects from {url}")
        ctype = hdrs.get("content-type", "")
        text, title, how = textmod.extract(body, ctype)
        sha = self.store.put(body, text)
        rec = {
            "url": url, "url_norm": urls.normalize(url), "final_url": current, "final_norm": urls.normalize(current),
            "status": status, "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "sha256": sha, "bytes": len(body), "truncated": trunc, "content_type": ctype,
            "has_text": text is not None, "text_chars": len(text or ""), "text_how": how, "title": title[:300],
            "etag": hdrs.get("etag", ""), "last_modified": hdrs.get("last-modified", ""),
            "redirects": chain, "robots": robots_note, "run": run or "", "user_agent": self.ua,
            "challenge": bool(CHALLENGE.search(title or "") or (text is not None and len(text) < 2000
                                                                  and CHALLENGE.search(text))),
        }
        self.store.record(rec)
        return rec
