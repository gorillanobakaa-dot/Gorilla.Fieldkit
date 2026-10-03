"""The parts of SPEC.md that need their own machinery on Windows:

  DohServer      DNS LEAK TEST: a deterministic resolver. The build copy is pinned to it by policy (DNSOverHTTPS,
                 Locked, Fallback false = TRR-only), so every name the browser resolves arrives here; allowlisted
                 names get real answers, everything else NXDOMAIN; every query is logged.
  certificates   CERTIFICATE TEST: leaf certificates signed by the run's CA (valid, expired, wrong host), one
                 self-signed and one from an unknown issuer, served on local HTTPS ports for the /certs and
                 /certerror pages.
  graceful_close SHUTDOWN TEST "close normally": WM_CLOSE to the browser's top-level windows (no keyboard, no focus).
  periodicity    PERIODICITY TEST: request intervals per host from the decrypted flow timestamps.
  tls_details    TLS TEST: ClientHello (SNI, ALPN, offered versions) and ServerHello (version, cipher) from frames.
  firewall_*     NETWORK ENFORCEMENT (opt-in, elevated): an outbound block rule for the direct build copy.
"""
import base64
import ctypes
import datetime
import http.server
import json
import socket
import ssl
import subprocess
import tempfile
import threading
import time
from pathlib import Path


# ---------------------------------------------------------------------------------------------- certificates
def _load_ca(ca_pem):
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization
    data = Path(ca_pem).read_bytes()
    cert = x509.load_pem_x509_certificate(data)
    key_path = Path(ca_pem).with_name("mitmproxy-ca.pem")
    key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
    return cert, key


def make_cert(out_dir, name, ca_pem=None, host="127.0.0.1", expired=False, self_signed=False, unknown_ca=False):
    """-> (cert_path, key_path) for a leaf. IP SAN 127.0.0.1 unless `host` is a DNS name. `unknown_ca`: signed by a
    throwaway CA that exists nowhere else, so the browser reports SEC_ERROR_UNKNOWN_ISSUER (not the self-signed error)."""
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    import ipaddress
    key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.datetime.now(datetime.timezone.utc)
    nb, na = (now - datetime.timedelta(days=30), now - datetime.timedelta(days=1)) if expired else (now - datetime.timedelta(days=1), now + datetime.timedelta(days=30))
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)])
    try:
        san = x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(host))])
    except ValueError:
        san = x509.SubjectAlternativeName([x509.DNSName(host)])
    if unknown_ca:
        issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "leakgate throwaway CA (never installed)")])
        sign_key = ec.generate_private_key(ec.SECP256R1())
    elif self_signed or not ca_pem:
        issuer, sign_key = subject, key
    else:
        ca_cert, sign_key = _load_ca(ca_pem)
        issuer = ca_cert.subject
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(issuer).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(nb).not_valid_after(na)
            .add_extension(san, critical=False).sign(sign_key, hashes.SHA256()))
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    cp, kp = out / f"{name}.crt", out / f"{name}.key"
    cp.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    kp.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return cp, kp


class _Quiet(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass


def https_server(cert, key, handler_cls, bind="127.0.0.1"):
    httpd = http.server.ThreadingHTTPServer((bind, 0), handler_cls)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(cert), str(key))
    httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


class CertServers:
    """valid (signed by the run's CA), expired, wrong-host, self-signed, unknown issuer (a CA nobody trusts, for the
    top-level about:certerror scenario): each on its own local HTTPS port."""

    KINDS = ("valid", "expired", "wronghost", "selfsigned", "unknownissuer")

    def __init__(self, work, ca_pem):
        class H(_Quiet):
            def do_GET(self):
                b = b"ok"
                self.send_response(200)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)
        self.servers = {}
        specs = {"valid": dict(ca_pem=ca_pem), "expired": dict(ca_pem=ca_pem, expired=True),
                 "wronghost": dict(ca_pem=ca_pem, host="wrong.example.invalid"), "selfsigned": dict(self_signed=True),
                 "unknownissuer": dict(unknown_ca=True)}
        for k, spec in specs.items():
            c, key = make_cert(Path(work) / "certs", k, **spec)
            self.servers[k] = https_server(c, key, H)

    def ports(self):
        return {k: s.server_address[1] for k, s in self.servers.items()}

    def close(self):
        for s in self.servers.values():
            s.shutdown()


# ---------------------------------------------------------------------------------------------- DoH
class DohServer:
    """RFC 8484 DNS-over-HTTPS (GET ?dns= and POST) on 127.0.0.1 with a cert signed by the run's CA."""

    def __init__(self, work, ca_pem, allowed):
        import dpkt
        self.queries, self.allowed = [], [a.lower().lstrip("*.") for a in allowed]
        outer = self

        def answer(wire):
            q = dpkt.dns.DNS(wire)
            r = dpkt.dns.DNS(wire)
            r.qr, r.ra, r.an = dpkt.dns.DNS_R, 1, []
            for qd in q.qd:
                name = qd.name.lower()
                ok = any(name == a or name.endswith("." + a) for a in outer.allowed)
                outer.queries.append({"t": time.time(), "name": name, "type": qd.type, "answered": ok})
                if not ok:
                    r.rcode = dpkt.dns.DNS_RCODE_NXDOMAIN
                    continue
                fam = socket.AF_INET if qd.type == dpkt.dns.DNS_A else socket.AF_INET6 if qd.type == dpkt.dns.DNS_AAAA else None
                if fam is None:
                    continue
                try:
                    for ai in {x[4][0] for x in socket.getaddrinfo(name, 443, fam)}:
                        rr = dpkt.dns.DNS.RR(name=qd.name, type=qd.type, cls=dpkt.dns.DNS_IN, ttl=60)
                        rr.rdata = socket.inet_pton(fam, ai)
                        r.an.append(rr)
                except OSError:
                    r.rcode = dpkt.dns.DNS_RCODE_NXDOMAIN
            return bytes(r)

        class H(_Quiet):
            def _reply(self, wire):
                try:
                    body = answer(wire)
                except Exception:
                    self.send_response(400)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/dns-message")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                from urllib.parse import urlparse, parse_qs
                d = parse_qs(urlparse(self.path).query).get("dns", [""])[0]
                self._reply(base64.urlsafe_b64decode(d + "=" * (-len(d) % 4)))

            def do_POST(self):
                self._reply(self.rfile.read(int(self.headers.get("Content-Length", "0"))))

        c, k = make_cert(Path(work) / "certs", "doh", ca_pem=ca_pem)
        self.httpd = https_server(c, k, H)
        self.port = self.httpd.server_address[1]

    def url(self):
        return f"https://127.0.0.1:{self.port}/dns-query"

    def close(self):
        self.httpd.shutdown()


# ---------------------------------------------------------------------------------------------- graceful close
def graceful_close(pids, timeout=30):
    """Post WM_CLOSE to every visible top-level window of `pids`; -> True when the root process exits."""
    user32 = ctypes.windll.user32
    WM_CLOSE = 0x0010
    hwnds = []
    proto = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def cb(hwnd, _):
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids and user32.IsWindowVisible(hwnd):
            hwnds.append(hwnd)
        return True
    user32.EnumWindows(proto(cb), 0)
    for h in hwnds:
        user32.PostMessageW(h, WM_CLOSE, 0, 0)
    return len(hwnds)


def startupinfo_minimized():
    import subprocess as sp
    si = sp.STARTUPINFO()
    si.dwFlags |= sp.STARTF_USESHOWWINDOW
    si.wShowWindow = 7                                   # SW_SHOWMINNOACTIVE: minimised, never takes focus
    return si


# ---------------------------------------------------------------------------------------------- periodicity
def periodicity(flows, min_requests=3):
    """-> {host: {"count", "intervals_s", "regular"}} from mitm flow records with timestamps."""
    by = {}
    for f in flows:
        if f.get("kind") == "http":
            by.setdefault(f["host"], []).append(f["t"])
    out = {}
    for h, ts in by.items():
        ts.sort()
        iv = [round(b - a, 1) for a, b in zip(ts, ts[1:])]
        if len(ts) >= min_requests:
            mean = sum(iv) / len(iv) if iv else 0
            regular = bool(iv) and mean > 5 and all(abs(x - mean) <= max(2.0, 0.2 * mean) for x in iv)
            out[h] = {"count": len(ts), "intervals_s": iv[:20], "regular": regular}
    return out


# ---------------------------------------------------------------------------------------------- TLS details
def tls_details(pcap_path, local_ports):
    """-> [{sni, alpn, offered_versions, server_version, cipher}] for the browser's TLS connections."""
    import dpkt
    hellos, servers = {}, {}
    if not Path(pcap_path).is_file():
        return []
    with open(pcap_path, "rb") as f:
        for _ts, buf in dpkt.pcapng.Reader(f):
            try:
                ip = dpkt.ethernet.Ethernet(buf).data
                tcp = ip.data
                if not isinstance(tcp, dpkt.tcp.TCP):
                    continue
                data = bytes(tcp.data)
                if len(data) < 6 or data[0] != 0x16:
                    continue
                recs, _ = dpkt.ssl.tls_multi_factory(data)
                for rec in recs:
                    if rec.type != 22:
                        continue
                    hs = dpkt.ssl.TLSHandshake(rec.data)
                    if isinstance(hs.data, dpkt.ssl.TLSClientHello) and tcp.sport in local_ports:
                        ch, info = hs.data, {"sni": None, "alpn": [], "offered_versions": []}
                        for et, ev in getattr(ch, "extensions", []):
                            if et == 0 and len(ev) > 5:
                                info["sni"] = ev[5:].decode("ascii", "replace")
                            elif et == 16:
                                i, lst = 2, []
                                while i < len(ev):
                                    n = ev[i]
                                    lst.append(ev[i + 1:i + 1 + n].decode("ascii", "replace"))
                                    i += 1 + n
                                info["alpn"] = lst
                            elif et == 43:
                                info["offered_versions"] = [hex(int.from_bytes(ev[i:i + 2], "big")) for i in range(1, len(ev), 2)]
                        hellos[(tcp.sport, tcp.dport)] = info
                    elif isinstance(hs.data, dpkt.ssl.TLSServerHello) and tcp.dport in local_ports:
                        sh = hs.data
                        ver = hex(sh.version)
                        for et, ev in getattr(sh, "extensions", []):
                            if et == 43 and len(ev) == 2:
                                ver = hex(int.from_bytes(ev, "big"))
                        servers[(tcp.dport, tcp.sport)] = {"server_version": ver, "cipher": hex(getattr(sh, "cipher_suite", 0) or 0)}
            except Exception:
                continue
    return [dict(v, **servers.get(k, {})) for k, v in hellos.items()]


# ---------------------------------------------------------------------------------------------- firewall (opt-in, elevated)
def firewall_block(exe, name):
    """Outbound block for `exe` to every non-loopback address. Reversible: firewall_unblock(name)."""
    ranges = "0.0.0.0-126.255.255.255,128.0.0.0-255.255.255.255,::2-ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff"
    ps = (f"New-NetFirewallRule -DisplayName '{name}' -Direction Outbound -Program '{exe}' -RemoteAddress {ranges} "
          "-Action Block -Profile Any | Out-Null")
    return subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True).returncode == 0


def firewall_unblock(name):
    subprocess.run(["powershell", "-NoProfile", "-Command", f"Remove-NetFirewallRule -DisplayName '{name}' -ErrorAction SilentlyContinue"],
                   capture_output=True)
