# Version: 1.0.0 · updated 26-10-26
"""fieldkit net time: the clocks of the net, compared without opening a new channel.

    this computer   whether its OS says the clock is synchronised (w32tm /query /status, timedatectl show): a LOCAL
                    read of what the OS already knows; the harness never sends an NTP query itself
    each peer       on the cable, the signed `hello` already carries the peer's time; the skew is the peer's time
                    minus the midpoint of the round trip (the error is at most half the round trip, shown)

WARN over 2 s (logs from two machines no longer line up to the second), STOP over 30 s: a measuring run (a leak gate,
a bench, a timed test across both machines) must not start, and the link itself refuses requests over 120 s.
Exit 0 OK, 1 WARN (or a peer did not answer), 2 STOP.
"""
import time

WARN_S = 2.0
STOP_S = 30.0


def local(run=None):
    """-> {"synced": True|False|None, "detail", "fix"} from the OS's own record."""
    from ..mandate import health
    ok, detail, fix = health.clock(run) if run else health.clock()
    return {"synced": ok, "detail": detail, "fix": fix}


def skew(hello, now=time.time):
    """-> {"ok", "skew", "rtt", "detail"} for one peer; hello is a function returning the peer's hello answer."""
    t0 = now()
    r = hello()
    t1 = now()
    if not r.get("ok"):
        return {"ok": False, "skew": None, "rtt": t1 - t0, "detail": f"no answer: {r.get('error')}"}
    if not isinstance(r.get("time"), (int, float)):
        return {"ok": False, "skew": None, "rtt": t1 - t0, "detail": "its hello carries no time (older link code)"}
    s = r["time"] - (t0 + t1) / 2
    return {"ok": True, "skew": s, "rtt": t1 - t0,
            "detail": f"{s:+.2f} s from this clock (round trip {t1 - t0:.2f} s, error at most +/-{(t1 - t0) / 2:.2f} s)"}


def level(s):
    if s is None:
        return "WARN"
    a = abs(s)
    return "STOP" if a > STOP_S else "WARN" if a > WARN_S else "OK"


def check(devices, hello_for, run=None, now=time.time):
    """-> {"local", "peers": [{device, level, ...}], "level"}. devices: names; hello_for(name) -> hello answer."""
    loc = local(run)
    peers = []
    for d in devices:
        r = skew(lambda d=d: hello_for(d), now)
        peers.append(dict(r, device=d, level=level(r["skew"])))
    worst = max([{"OK": 0, "WARN": 1, "STOP": 2}[p["level"]] for p in peers] + [0 if loc["synced"] is not False else 1])
    return {"local": loc, "peers": peers, "level": ("OK", "WARN", "STOP")[worst]}


def lines(r):
    loc = r["local"]
    synced = {True: "yes", False: "NO", None: "unknown"}[loc["synced"]]
    out = [f"TIME {r['level']}  (WARN > {WARN_S:g} s, STOP > {STOP_S:g} s for measuring runs)",
           f"  this computer  synced={synced}  {loc['detail']}"
           + (f"  fix: {loc['fix']}" if loc.get("fix") else "")]
    for p in r["peers"]:
        out.append(f"  {p['device']:<14} {p['level']:<4}  {p['detail']}")
    if not r["peers"]:
        out.append("  no peer on the cable (fieldkit peers link devices)")
    return out
