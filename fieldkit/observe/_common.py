"""observe, shared by the three readers: connections of one process and DNS sockets, through psutil (one call, the
same answer on every system). Read-only."""
import time

DNS_PORTS = ("53", "853")   # a test points this at a fixture port


def conns(pid):
    """Every TCP/UDP socket PID has: proto, local, remote, state."""
    import psutil
    try:
        p = psutil.Process(int(pid))
        socks = p.net_connections(kind="inet") if hasattr(p, "net_connections") else p.connections(kind="inet")
    except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
        return {"pid": int(pid), "error": str(e), "connections": []}
    fmt = lambda a: f"{a.ip}:{a.port}" if a else ""
    return {"pid": int(pid), "name": p.name(),
            "connections": [{"proto": "tcp" if c.type == 1 else "udp", "local": fmt(c.laddr), "remote": fmt(c.raddr),
                             "state": c.status} for c in socks]}


def dns(pid, seconds):
    """Remote DNS servers (port 53 or 853) PID talks to, sampled twice a second for SECONDS. This is sampling of
    its sockets, not a packet trace: a lookup shorter than half a second can be missed,
    and Windows does not report the remote end of a UDP socket, so there only TCP/853-style lookups show."""
    seen, end = {}, time.time() + float(seconds)
    while True:
        r = conns(pid)
        if r.get("error"):
            return {"error": r["error"], "servers": []}
        for c in r["connections"]:
            if c["remote"].rsplit(":", 1)[-1] in DNS_PORTS:
                seen[(c["proto"], c["remote"])] = c
        if time.time() >= end:
            break
        time.sleep(0.5)
    return {"servers": [{"pid": int(pid), **v} for v in seen.values()], "method": "socket sampling"}
