# Version: 1.1.0 · updated 26-10-28-21-00
"""No name lookups on the real network: the guard the test suite has used since 2026-10-06, for processes that are not
pytest.

2026-10-19, found by the leak gate's DNS witness: `fieldkit canary run` planted incoming.telemetry.mozilla.org for the  (telemetry-ok: names the host it stopped)
leak-gate judge, which resolved every name it saw, so every canary, mutation and witness run made a real DNS query
for Mozilla's telemetry host. Tests were offline (tests/conftest.py); the canary runner was not.

    FIELDKIT_OFFLINE=1         any process that imports fieldkit installs the guard (fieldkit/__init__.py)
    FIELDKIT_OFFLINE_LOG=PATH  every refused name is appended there, one per line, so the caller can show what
                               WOULD have been looked up

Loopback names and IP literals still resolve; every other name raises socket.gaierror and nothing leaves the machine.

    FIELDKIT_BLOCKED_PEERS=A,B/30   a connect to one of these addresses or networks (the cable peers) raises
                                    ConnectionRefusedError; set by the test suite (tests/conftest.py)
    FIELDKIT_BLOCKED_PEERS_LOG=PATH every refused connect is appended there, so a test that swallows the error
                                    still fails (2026-10-28: a child hook process of a Zero test sent fixture
                                    check-ins over the REAL cable to the other computer)
"""
import ipaddress
import os
import socket

MARK = "__fieldkit_offline__"


def _log(name):
    path = os.environ.get("FIELDKIT_OFFLINE_LOG")
    if path:
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(f"{name}\n")
        except OSError:
            pass


def _local(h):
    if h in (None, "", "localhost") or str(h).endswith(".localhost"):
        return True
    try:
        ipaddress.ip_address(str(h).strip("[]"))
        return True
    except ValueError:
        return False


def install():
    """Replace getaddrinfo/gethostbyname/gethostbyaddr in this process (and guard the cable peers when
    FIELDKIT_BLOCKED_PEERS is set). Idempotent."""
    install_peer_guard()
    if getattr(socket.getaddrinfo, MARK, False):
        return
    real_getaddrinfo, real_gethostbyname = socket.getaddrinfo, socket.gethostbyname

    def getaddrinfo(host, *args, **kwargs):
        h = host.decode() if isinstance(host, bytes) else host
        if _local(h):
            return real_getaddrinfo(host, *args, **kwargs)
        _log(h)
        raise socket.gaierror(11001, f"offline: {h} was not looked up")

    def gethostbyname(host):
        if _local(host):
            return real_gethostbyname(host)
        _log(host)
        raise socket.gaierror(11001, f"offline: {host} was not looked up")

    def gethostbyaddr(addr):
        _log(addr)
        raise socket.herror(1, f"offline: no reverse lookup of {addr}")

    for f in (getaddrinfo, gethostbyname, gethostbyaddr):
        setattr(f, MARK, True)
    socket.getaddrinfo, socket.gethostbyname, socket.gethostbyaddr = getaddrinfo, gethostbyname, gethostbyaddr


def _blocked(addr, nets):
    try:
        ip = ipaddress.ip_address(str(addr[0]).split("%")[0].strip("[]"))
    except (ValueError, TypeError, IndexError):
        return False
    return any(ip in n for n in nets)


def install_peer_guard(spec=None, log_path=None):
    """Refuse every socket connect to the cable peers (`spec`: "A,B/30"; default FIELDKIT_BLOCKED_PEERS). Idempotent.
    -> the networks guarded (empty: nothing installed)."""
    spec = os.environ.get("FIELDKIT_BLOCKED_PEERS", "") if spec is None else spec
    nets = []
    for x in spec.split(","):
        try:
            nets.append(ipaddress.ip_network(x.strip(), strict=False))
        except ValueError:
            continue
    if not nets or getattr(socket.socket.connect, MARK, False):
        return nets
    real_connect, real_connect_ex = socket.socket.connect, socket.socket.connect_ex

    def refuse(addr):
        line = f"{addr[0]}:{addr[1] if len(addr) > 1 else ''}"
        path = log_path or os.environ.get("FIELDKIT_BLOCKED_PEERS_LOG")
        if path:
            try:
                with open(path, "a", encoding="utf-8") as f:
                    f.write(f"{line}\n")
            except OSError:
                pass
        raise ConnectionRefusedError(f"REFUSED: a connect to the cable peer {line} (tests use the fake cable; "
                                     "nothing may reach the other computer)")

    def connect(self, address):
        if self.family in (socket.AF_INET, socket.AF_INET6) and _blocked(address, nets):
            refuse(address)
        return real_connect(self, address)

    def connect_ex(self, address):
        if self.family in (socket.AF_INET, socket.AF_INET6) and _blocked(address, nets):
            refuse(address)
        return real_connect_ex(self, address)

    for f in (connect, connect_ex):
        setattr(f, MARK, True)
    socket.socket.connect, socket.socket.connect_ex = connect, connect_ex
    return nets


def installed():
    return bool(getattr(socket.getaddrinfo, MARK, False))


SITE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "offline_site")


def child_env(log_path=None):
    """The environment for a child process that must stay offline: a fieldkit child installs the guard on import,
    any other Python child through offline_site/sitecustomize.py on PYTHONPATH (not under -I or -S, which ignore it).
    A non-Python program (go, a browser) is NOT covered: that needs the leak gate's own sensors."""
    env = dict(os.environ, FIELDKIT_OFFLINE="1")
    env["PYTHONPATH"] = os.pathsep.join(p for p in (SITE, os.environ.get("PYTHONPATH", "")) if p)
    if log_path:
        env["FIELDKIT_OFFLINE_LOG"] = str(log_path)
    return env
