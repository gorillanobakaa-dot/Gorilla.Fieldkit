"""URLs as evidence: one spelling per page, and the addresses a fetch must never reach."""
import ipaddress
import re
import socket
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING = re.compile(r"^(utm_[a-z]+|fbclid|gclid|mc_cid|mc_eid|_hsenc|_hsmi|ref_src)$", re.I)
URL_RE = re.compile(r"https?://[^\s<>\"'`|\])}]+", re.I)
DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s\"'<>|\])}]+)", re.I)
SHARED = ipaddress.ip_network("100.64.0.0/10")          # carrier-grade NAT: not public, not flagged by is_private


def normalize(url):
    """One spelling per page: case of scheme and host, default port, fragment, tracking parameters, trailing
    slash. A DOI, in any of its URL forms, becomes doi:10.x/y (lower case, as DOIs are case-insensitive)."""
    url = url.strip().rstrip(".,;:")
    m = re.match(r"^(?:doi:\s*|https?://(?:dx\.)?doi\.org/)(10\.\S+)$", url, re.I)
    if m:
        return "doi:" + m.group(1).lower()
    p = urlsplit(url)
    if p.scheme.lower() not in ("http", "https") or not p.hostname:
        return url
    host = p.hostname.lower()
    port = p.port
    netloc = host if port in (None, 80 if p.scheme.lower() == "http" else 443) else f"{host}:{port}"
    query = urlencode([(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if not TRACKING.match(k)])
    path = p.path or "/"
    if len(path) > 1:
        path = path.rstrip("/")
    return urlunsplit((p.scheme.lower(), netloc, path, query, ""))


def extract(text):
    """Every URL and DOI in a piece of text, normalized, in order, once each."""
    out = []
    for u in URL_RE.findall(text or ""):
        n = normalize(u)
        if n not in out:
            out.append(n)
    for d in DOI_RE.findall(text or ""):
        n = normalize("doi:" + d)
        if n not in out and not any(n[4:] in o.lower() for o in out if not o.startswith("doi:")):
            out.append(n)
    return out


def fetchable(url):
    """A DOI is fetched through its resolver; everything else as written."""
    return "https://doi.org/" + url[4:] if url.startswith("doi:") else url


def blocked_ip(ip):
    """Why an address must not be fetched, or ''. The guard exists for model-chosen URLs: loopback, private
    networks, link-local (the cloud metadata service lives at 169.254.169.254), multicast and the rest."""
    ip = ipaddress.ip_address(ip)
    if getattr(ip, "ipv4_mapped", None):
        ip = ip.ipv4_mapped
    for test, why in ((ip.is_loopback, "loopback"), (ip.is_link_local, "link-local (cloud metadata lives here)"),
                      (ip.is_private, "a private network"), (ip.is_multicast, "multicast"),
                      (ip.is_unspecified, "unspecified"), (ip.is_reserved, "reserved"),
                      (ip.version == 4 and ip in SHARED, "carrier-grade NAT")):
        if test:
            return why
    return ""


def resolve_public(host, port):
    """-> (an address to connect to, '') or (None, why not). Every address the name resolves to must be public:
    a name with one public and one private address is refused (DNS rebinding hides behind exactly that)."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        return None, f"{host} does not resolve ({e})"
    addrs = []
    for info in infos:
        a = info[4][0]
        why = blocked_ip(a)
        if why:
            return None, f"{host} resolves to {a}, {why}"
        addrs.append(a)
    return (addrs[0], "") if addrs else (None, f"{host} has no address")
