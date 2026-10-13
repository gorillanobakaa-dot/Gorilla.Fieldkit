"""Which interface is actually carrying your traffic, and what kind of line it is.

    fieldkit net route            which interface the internet actually goes out of
    fieldkit net compare          the same download down EACH interface, no config change
    fieldkit net prefer IFACE     make one interface win, reversibly
    fieldkit net kind             fixed line or cellular? it changes every other answer

# WHY THIS EXISTS

On 2026-10-13 the owner plugged in an ethernet cable to compare it against wifi.
The cable came up at 1000 Mbps full duplex and carried NOTHING, because the wifi
route had a lower metric:

    Gorilla.WIFI   metric 600     <- lower wins
    enp3s0         metric 900

Any speedtest run then would have gone over wifi, shown the same 44 Mbps, and
"proved" the cable made no difference. The cable was perfect. The measurement
would have been worthless.

# ============================================================================
# THE TRAPS
# ============================================================================
#
# 1. PLUGGING IN A CABLE DOES NOT MOVE YOUR TRAFFIC. Both interfaces can be up,
#    connected and holding a default route at once. The LOWEST metric wins.
#    `ip route get 1.1.1.1` is the only honest answer to "which one am I using".
#
# 2. "carrier: 1" AND "speed: 1000" MEAN THE CABLE IS FINE, NOT THAT IT IS USED.
#    They describe the link, not the routing.
#
# 3. A 100 Mbps NEGOTIATION ON A GIGABIT PORT is the other classic cable fault,
#    usually a damaged pair or a cheap cable. Read /sys/class/net/IF/speed and
#    say so, because it looks exactly like a slow line.
#
# 4. CELLULAR WANS INVALIDATE MOST SPEED REASONING. A 5G or 4G hub varies by
#    more than 10x within minutes on an unchanged cable, because you share the
#    cell and the tower reschedules you constantly. Measured here on one
#    evening: 25.7, 29.8, 80.2, 122, 170, 204, 220, 235 and 417 Mbps. Any rule
#    of the form "it changed, so something is wrong" is false on cellular.
#    ALWAYS establish the WAN kind before concluding anything about speed.
#
# 5. JITTER TELLS YOU THE KIND WITHOUT ASKING THE ROUTER. A fixed line sits
#    within about 1 ms of itself. This hub measured min 13.6, max 31.5, mdev
#    5.5 ms, because every packet waits for a scheduling slot.
# ============================================================================
"""

from .route import (route_for, interfaces, compare, prefer, unprefer,  # noqa: E402
                    kind, lines, exit_code)

__all__ = ["route_for", "interfaces", "compare", "prefer", "unprefer",
           "kind", "lines", "exit_code"]
