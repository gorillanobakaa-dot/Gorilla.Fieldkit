"""Local pages a probe visits, started and stopped by `build-harness probe` itself (127.0.0.1 only).

What a site receives cannot be asked from inside the browser: the User-Agent header, whether a page that says
"private, max-age=0, must-revalidate" is fetched again or opened from the cache. On 2026-10-04 the Satellite probes
(satellite-mobile, satellite-revisit) answered those questions against two throwaway scripts (echo_ua.py and
revisit_server.py) that had to be started by hand in another window on the ports the probes name; a probe run
without them reported "(page did not load)" for every visit, which reads like a browser fault. They are part of the
harness now: a probe declares the page it needs on a line of its own,

    // gprobe-server: <kind> <port>        e.g.  // gprobe-server: echo-ua 8765

and `probe.run` starts that page for the run, on that port when it is free, else on a free one (every
"127.0.0.1:<port>" in the probe text is rewritten to match), bound to 127.0.0.1 only, and stops it afterwards.
Every request the page received comes back with the probe's own lines, in time order.

Kinds:
  echo-ua   a page whose script writes navigator.userAgent and navigator.platform into its title ("NAV:<ua>|<pf>",
            title "x" when scripts do not run); every request's User-Agent header is logged. Never cached.
  revisit   a 20 kB page sent with Cache-Control "private, max-age=0, must-revalidate" (like news front pages);
            every request is logged, with the request's own cache headers, so a revisit served from the cache
            shows as a missing request.
  bad-cert  HTTPS on 127.0.0.1 with a throwaway self-signed certificate made for the run (no authority signed it):
            the browser shows its real certificate error page (2026-10-08: screenshots of every hidden about: page,
            the error pages by their real cause; probe error-pages).
"""
import http.server
import re
import socket
import threading
import time
from contextlib import contextmanager

DECLARE = re.compile(r"^\s*//\s*gprobe-server:\s*([\w-]+)\s+(\d+)\s*$", re.M)
LOGGED = ("user-agent", "cache-control", "pragma", "if-none-match", "if-modified-since")


def _echo_ua(handler):
    body = (b"<!doctype html><title>x</title><script>document.title='NAV:'+navigator.userAgent+'|'+navigator.platform"
            b"</script>")
    return body, {"content-type": "text/html", "cache-control": "no-store"}


def _revisit(handler):
    body = b"<!doctype html><title>revisit</title><p>" + b"x" * 20000
    return body, {"content-type": "text/html", "cache-control": "private, max-age=0, must-revalidate"}


def _bad_cert(handler):
    return b"<!doctype html><title>bad-cert</title><p>should never be shown", {"content-type": "text/html"}


KINDS = {"echo-ua": _echo_ua, "revisit": _revisit, "bad-cert": _bad_cert}
TLS_KINDS = {"bad-cert"}


def self_signed(folder):
    """A throwaway key and self-signed certificate for 127.0.0.1, valid one day -> (cert path, key path)."""
    import datetime
    import ipaddress
    from pathlib import Path
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "gorilla probe (self-signed, not trusted)")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(minutes=5))
            .not_valid_after(now + datetime.timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), False)
            .sign(key, hashes.SHA256()))
    folder = Path(folder)
    c, k = folder / "probe-cert.pem", folder / "probe-key.pem"
    c.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    k.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                    serialization.NoEncryption()))
    return c, k


class Page:
    """One local page on 127.0.0.1:<port>, in a thread. `log` holds every request: {t, method, path, headers}."""

    def __init__(self, kind, port=0):
        if kind not in KINDS:
            raise ValueError(f"no probe server {kind!r}; known: {', '.join(KINDS)}")
        self.kind = kind
        self.log = []
        lock = threading.Lock()
        page, log = KINDS[kind], self.log

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                with lock:
                    log.append({"t": time.time(), "method": "GET", "path": self.path,
                                "headers": {k: self.headers.get(k) for k in LOGGED if self.headers.get(k) is not None}})
                body, headers = page(self)
                self.send_response(200)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.port = self.httpd.server_address[1]
        self._tmp = None
        if kind in TLS_KINDS:
            import ssl
            import tempfile
            self._tmp = tempfile.mkdtemp(prefix="gprobe_cert_")
            cert, key = self_signed(self._tmp)
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(cert, key)
            self.httpd.socket = ctx.wrap_socket(self.httpd.socket, server_side=True)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(5)
        if self._tmp:
            import shutil
            shutil.rmtree(self._tmp, ignore_errors=True)


def port_free(port):
    """True when nothing listens on 127.0.0.1:<port> and it can be bound."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def declared(body):
    """-> [(kind, port)] the probe text asks for."""
    return [(k, int(p)) for k, p in DECLARE.findall(body)]


@contextmanager
def serving(body, say=print):
    """Start every page the probe declares -> (probe text with its ports rewritten when one was busy, [Page]).
    The pages are stopped when the block ends, whatever happens in it."""
    pages = []
    try:
        for kind, want in declared(body):
            page = Page(kind, want if port_free(want) else 0)
            pages.append(page)
            if page.port != want:
                body = body.replace(f"127.0.0.1:{want}", f"127.0.0.1:{page.port}")
                say(f"  probe: port {want} is busy; the {kind} page runs on {page.port} (the probe text was rewritten)")
            else:
                say(f"  probe: {kind} page on 127.0.0.1:{page.port}")
        yield body, pages
    finally:
        for page in pages:
            page.close()


def requests(pages):
    """-> [{t, server, method, path, headers}] every request the pages received, in time order."""
    rows = [dict(r, server=f"{p.kind} 127.0.0.1:{p.port}") for p in pages for r in p.log]
    return sorted(rows, key=lambda r: r["t"])


def request_line(r):
    heads = "; ".join(f"{k}: {v}" for k, v in r["headers"].items())
    return f"[{r['server']}] {r['method']} {r['path']}" + (f" | {heads}" if heads else "")
