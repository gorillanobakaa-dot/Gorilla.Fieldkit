"""P37 end-to-end smoke of the Linux leak gate's enforcement layer, run as root on a CI runner (or any Linux box).

It calls the REAL code the gate calls (fieldkit.leakgate.linux: selftest, netns_up, allow_tcp_ports, netns_down) and
makes two connections from inside the gate's network namespace to servers on the host side of the veth:
  allowed    -> the port netns_up opens (the gate's own proxy/server port)        must CONNECT
  forbidden  -> any other port on the same host address (not in the nft rule)      must be BLOCKED
The gate's rule is `policy drop`; a forbidden connection that gets through, or an allowed one that does not, fails.
Exit 0 only when both hold. It does not need a browser. (A full gate run on a fake browser is NOT part of this job.)
"""
import socket
import subprocess
import sys
import threading

from fieldkit.leakgate import linux as lx


def listener(port):
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((lx.HOST_IP, port))
    s.listen(5)
    threading.Thread(target=lambda: [s.accept()[0].close() for _ in iter(int, 1)], daemon=True).start()
    return s


def connect_from_ns(port):
    code = ("import socket,sys;s=socket.socket();s.settimeout(4)\n"
            f"try: s.connect(({lx.HOST_IP!r},{port}));print('CONNECTED')\n"
            "except Exception as e: print('BLOCKED',type(e).__name__)")
    r = subprocess.run(["ip", "netns", "exec", lx.NS, sys.executable, "-c", code], capture_output=True, text=True, timeout=30)
    return r.stdout.strip() or r.stderr.strip()


def main():
    st = lx.selftest()
    print("selftest:", {k: v for k, v in st.items() if k != "tools"}, flush=True)
    if not st["ok"]:
        print("FAIL: the selftest says this machine cannot run the gate:", st["missing_required"], "root:", st["root"])
        return 3
    allowed, forbidden, proxy = 18080, 18081, 18082
    servers = [listener(allowed), listener(forbidden)]
    try:
        lx.netns_up(proxy, allowed)
        a, f = connect_from_ns(allowed), connect_from_ns(forbidden)
        print(f"allowed port {allowed}: {a}\nforbidden port {forbidden}: {f}", flush=True)
        ok = a.startswith("CONNECTED") and f.startswith("BLOCKED")
        print("PASS: allowed connected, forbidden blocked" if ok else "FAIL: the namespace did not enforce its allowlist")
        return 0 if ok else 3
    finally:
        lx.netns_down()
        for s in servers:
            s.close()


if __name__ == "__main__":
    sys.exit(main())
