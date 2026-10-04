"""Link profiles the relay emulates, the browser mode levels (--profile), and the link arithmetic.

A link has a download rate, an upload rate, a round-trip time and a loss rate. The relay (relay.py) puts every byte
through one shared bottleneck per direction: a byte waits for the bytes before it (serialisation at the link rate),
then travels for half the round trip. Loss is modelled as a retransmission stall: a chunk that contains a lost
segment arrives one round trip later (fast retransmit after duplicate acknowledgements), and the bytes behind it on
the same connection wait for it (TCP delivers in order). TCP's congestion-window cut after a loss is NOT
modelled, so throughput under loss is optimistic compared with real TCP without a performance-enhancing proxy.

The mode levels are the study's section 7.3 bundles written as user.js prefs into the throwaway profile. Build 16
has no `gorilla.linkmode` switch yet, so `satellite` and `slow` are emulations of what S1 would set, recorded as such.
"""
import math
import random
from dataclasses import asdict, dataclass

MSS = 1448                      # TCP payload bytes per segment (1500 MTU, IPv4, timestamps)


@dataclass(frozen=True)
class Link:
    name: str
    title: str
    down_bps: float             # bits per second, origin -> browser
    up_bps: float               # bits per second, browser -> origin
    rtt_ms: float
    loss: float = 0.0           # probability that one segment is lost

    @property
    def rtt(self):
        return self.rtt_ms / 1000.0

    def as_dict(self):
        d = asdict(self)
        d["down_mbps"] = round(self.down_bps / 1e6, 4)
        d["up_mbps"] = round(self.up_bps / 1e6, 4)
        return d


LINKS = {
    "broadband": Link("broadband", "Broadband (50 Mbit/s down, 10 up, 20 ms)", 50e6, 10e6, 20, 0.0),
    "starlink": Link("starlink", "Starlink-like LEO (100 Mbit/s down, 15 up, 40 ms, 0.5% loss)", 100e6, 15e6, 40, 0.005),
    "geo": Link("geo", "GEO satellite (10 Mbit/s each way, 600 ms, 1% loss)", 10e6, 10e6, 600, 0.01),
    "austere": Link("austere", "Austere (5 KB/s each way, 700 ms)", 40e3, 40e3, 700, 0.0),
}
DEFAULT_LINKS = ("broadband", "starlink", "geo", "austere")


def pick_links(spec=None):
    """'broadband,geo' -> [Link]; None -> all four. An unknown name is refused (ValueError)."""
    names = [n.strip() for n in spec.split(",") if n.strip()] if spec else list(DEFAULT_LINKS)
    bad = [n for n in names if n not in LINKS]
    if bad:
        raise ValueError(f"unknown link profile(s) {bad}; known: {', '.join(LINKS)}")
    return [LINKS[n] for n in names]


# Browser mode levels (study section 7.3). Values are the study's; S4/S7/S9/S13/S16 need code or a decision and
# are not emulated (listed in NOT_EMULATED so the result says so).
SATELLITE = {
    "media.autoplay.default": 5,                       # S2
    "media.preload.default": 1,                        # S3
    "media.preload.auto": 2,
    "network.http.connection-timeout": 60,             # S10 (GEO-like delay values)
    "network.http.tls-handshake-timeout": 60,
    "network.http.response.timeout": 300,
    "network.http.connection-retry-timeout": 1000,
    "network.dnsCacheExpiration": 600,                 # S12 (the pref part; F1 is a source change)
    "network.dnsCacheExpirationGracePeriod": 3600,
    "network.http.http3.enable": True,                 # S14
}
SLOW = dict(SATELLITE, **{
    "permissions.default.image": 2,                    # S5 (the pref form of S4/S5)
    "gfx.downloadable_fonts.enabled": False,           # S6
    "network.http.max-persistent-connections-per-server": 6,   # S11
})
MODES = {
    "normal": {},
    # the REAL switch (Gorilla.Satellite mode in Settings): GorillaLinkMode applies the level itself, so the bench
    # measures the shipped code, not a copy of its values (2026-10-04)
    "satellite": {"gorilla.linkmode": 1},
    # JavaScript stays on: the bench page reports its own timings with a script (2026-10-04: with D-157-33 no-JS
    # every timing came back UNMEASURED). The no-JS saving is measured on real sites (probe realsite-weight).
    "slow": {"gorilla.linkmode": 2, "gorilla.linkmode.no_javascript": False},
    # the study's emulations, kept for builds without the switch
    "satellite-emulated": SATELLITE,
    "slow-emulated": SLOW,
    # RAM decision (study F3, F7; D-157-30: the lighter one wins if it is not slower)
    "upstream-buffers": {"network.buffer.cache.size": 32768, "network.buffer.cache.count": 24},
    "upstream-memcache": {"browser.cache.memory.capacity": -1},
    "upstream-both": {"network.buffer.cache.size": 32768, "network.buffer.cache.count": 24,
                      "browser.cache.memory.capacity": -1},
}
NOT_EMULATED = {
    "normal": [], "satellite": [], "slow": ["S9 text-first reader (not built)"],
    "satellite-emulated": ["S13 persistent cache"],
    "slow-emulated": ["S4 uBlock Origin switches (no pref)", "S7 Save-Data", "S9 text-first reader (code)",
                      "S13 persistent cache", "S16 uBO list pause (uBO setting)"],
    "upstream-buffers": [], "upstream-memcache": [], "upstream-both": [],
}


def mode_prefs(name):
    if name not in MODES:
        raise ValueError(f"unknown --profile {name!r}; known: {', '.join(MODES)}")
    return dict(MODES[name])


class Direction:
    """One direction of the link: a shared serialiser (rate) plus a delay line (half the RTT) plus loss stalls.

    `schedule(n, now)` -> the time the n bytes arrive at the far side. Deterministic for a given seed."""

    def __init__(self, rate_bps, rtt_s, loss=0.0, seed=1):
        self.rate = float(rate_bps) / 8.0          # bytes per second
        self.owd = rtt_s / 2.0
        self.rtt = rtt_s
        self.loss = loss
        self.rng = random.Random(seed)
        self.next_free = 0.0
        self.losses = 0
        self.bytes = 0

    def chunk_size(self):
        """How many bytes the relay reads at once: about 5 ms of link time, at least one segment, at most 64 KiB."""
        return int(min(65536, max(MSS, self.rate * 0.005)))

    def queue_limit(self):
        """How far ahead (seconds of link time) the bottleneck queue may run before the relay stops reading."""
        return max(self.rtt, 0.05)

    def backlog(self, now):
        return max(0.0, self.next_free - now)

    def schedule(self, n, now):
        start = max(now, self.next_free)
        self.next_free = start + n / self.rate
        self.bytes += n
        arrive = self.next_free + self.owd
        if self.loss > 0:
            segs = max(1, math.ceil(n / MSS))
            if self.rng.random() < 1.0 - (1.0 - self.loss) ** segs:
                self.losses += 1
                arrive += self.rtt
        return arrive


def transfer_size(rate_bps, seconds=8.0, lo=32 * 1024, hi=64 * 1024 * 1024):
    """Bytes for a throughput test that lasts about `seconds` at the nominal rate, clamped."""
    return int(min(hi, max(lo, rate_bps / 8.0 * seconds)))
