#!/usr/bin/env python3
"""Which wifi channel is actually free, and why: survey, overlap warnings, rationale.

Runs three ways, from one file, with NOTHING but the Python standard library:

    python3 channelsurvey.py                 the survey, on screen
    python3 channelsurvey.py --gui           a window you can click
    python3 channelsurvey.py --json          for another program to read

Works on Linux, macOS, Windows and FreeBSD. Copy this single file onto any
machine with Python 3.8 or newer and it runs. No pip, no wheels, no install.

# WHY THIS EXISTS

Written 2026-10-13 after answering "which channel should I use" four separate
times in one evening by hand, each time rebuilding the same reasoning. The
answer needs four things and getting any of them wrong gives the wrong channel:

  1. OVERLAP. 2.4 GHz channels sit 5 MHz apart and are 20 MHz wide, so a network
     on channel 4 interferes with BOTH 1 and 6. Counting exact matches only is
     the commonest mistake in every "wifi analyser" app.
  2. STRENGTH. Six distant networks hurt less than one loud neighbour.
  3. WIDTH. A 40 MHz network occupies two channels' worth of air.
  4. LEGALITY AND CAPABILITY. Channel 13 is legal in the UK and illegal in the
     US; channel 14 is Japan only and 802.11b only. And there is no point
     recommending a channel your own card cannot tune.

It also ARGUES ITS CASE, because the owner picked channel 13 against the
conventional "always use 1, 6 or 11" advice and was right to, and a tool that
cannot explain that is just another number generator.
"""
from __future__ import annotations

import argparse
import json
import textwrap
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field, asdict

VERSION = "1.0.0"

# --------------------------------------------------------------------------
# Channel knowledge. The part everyone gets wrong.
# --------------------------------------------------------------------------
# 2.4 GHz: centre frequency per channel. 5 MHz apart, 20 MHz wide, so adjacent
# channels OVERLAP. Only 1, 6 and 11 are mutually clear.
CH24 = {n: 2407 + 5 * n for n in range(1, 14)}
CH24[14] = 2484                      # Japan, and 802.11b only

NON_OVERLAPPING_24 = (1, 6, 11)
UK_EU_ONLY_24 = (12, 13)             # legal here, invisible to US-market kit
JAPAN_ONLY_24 = (14,)                # and NO-OFDM: 11 Mbps ceiling

# PARTIAL overlap is WORSE than sharing a channel outright, and this is the bit
# every "wifi analyser" app on every app store gets backwards.
#
# On the SAME channel, two radios hear each other's preamble, so carrier sense
# works and they take turns. It is slower, but orderly.
#
# PARTLY overlapped, each one's transmission is energy the other cannot decode.
# Carrier sense fails. Both transmit at once, both frames are corrupted, both
# retransmit, and the retransmissions collide too. That is the ENTIRE reason the
# 1/6/11 convention exists: not to be tidy, but because a half-overlap is worse
# than a full one.
#
# So a 50% overlap must not score as half the interference. It scores as roughly
# double. Without this the tool recommends channel 9 over channel 11 and then,
# two tabs away, explains why that is wrong.
PARTIAL_OVERLAP_PENALTY = 2.0

# How much better a non-1/6/11 channel must be before it is worth recommending.
#
# A score is ONE SNAPSHOT of the air. The non-overlapping property of 1, 6 and
# 11 is PERMANENT. So a 1.4x paper advantage for channel 9 is not worth taking:
# the neighbour who moves tonight erases it, and you are left on a channel that
# is structurally worse forever. A 3x advantage is worth taking, because no
# plausible shift in the neighbours closes that gap.
#
# This is the judgement an experienced person applies and a scoring loop does
# not. Without it the tool confidently recommends channel 9 on a margin thinner
# than the error bar of its own measurement.
WORTH_LEAVING_1_6_11 = 2.5

# 5 GHz channels are 20 MHz apart and do NOT overlap at 20 MHz width, which is
# why the whole problem is easier up there.
CH5_START = 36


def freq_of(channel: int, band: str) -> int:
    if band.startswith("2"):
        return CH24.get(channel, 0)
    return 5000 + 5 * channel


def band_of(mhz) -> str:
    try:
        mhz = int(mhz)
    except (TypeError, ValueError):
        return "unknown"
    if 2300 <= mhz <= 2600:
        return "2.4"
    if 4900 <= mhz <= 5900:
        return "5"
    if 5925 <= mhz <= 7125:
        return "6"
    return "unknown"


# --------------------------------------------------------------------------
# What KIND of thing is on the air
# --------------------------------------------------------------------------
# A hint from the network name, never an identification. The point is "there are
# chatty low-rate devices here", which matters because a doorbell that wakes up
# twice an hour holds the air far longer per byte than its signal suggests.
#
# IMPORTANT AND OFTEN MISSOLD: a wifi scan sees ACCESS POINTS, not clients. The
# Alexa, the TV and the fridge connected to a neighbour's router are INVISIBLE
# here; they are using the air but broadcasting no beacon. Reading them needs
# monitor mode, which most cards and all of Windows will not give you. Any app
# claiming to show you "all devices on channel 6" from a normal scan is lying.
DEVICE_HINTS = [
    (r"\b(echo|alexa|amazon)\b", "voice assistant"),
    (r"\b(chromecast|googlehome|nest|google-home)\b", "streaming or voice"),
    (r"\b(roku|firetv|fire-tv|bravia|samsung|lg|philips|hisense|tcl)\b", "smart TV"),
    (r"\b(ring|blink|arlo|eufy|camera|cam|ipcam|cctv)\b", "camera or doorbell"),
    (r"\b(hp|canon|epson|brother|printer|scan)\b", "printer"),
    (r"\b(hue|tplink|tapo|kasa|shelly|sonoff|smartplug|bulb)\b", "smart home"),
    (r"\b(fridge|washer|dishwasher|oven)\b", "appliance"),
    (r"\b(deco|velop|orbi|eero|mesh|-ext|repeater|extender)\b", "mesh or extender"),
    (r"\b(bt|sky|virgin|vm|ee|three|talktalk|plusnet)\b", "ISP-supplied router"),
    (r"\b(guest)\b", "guest network"),
]


def classify(ssid):
    """A hint at what is on the air, from the name. Never an identification."""
    if not ssid or ssid == "<hidden>":
        return "hidden"
    s = ssid.lower()
    for pattern, label in DEVICE_HINTS:
        if re.search(pattern, s):
            return label
    return "unknown"


# --------------------------------------------------------------------------
# What is on the air
# --------------------------------------------------------------------------
@dataclass
class Network:
    ssid: str
    channel: int
    mhz: int
    band: str
    signal: int                     # 0-100, higher is stronger
    width_mhz: int = 20
    bssid: str = ""
    security: str = ""

    @property
    def hidden(self) -> bool:
        return not self.ssid or self.ssid == "<hidden>"

    @property
    def span(self) -> tuple:
        """The frequency range this network actually occupies, in MHz."""
        half = self.width_mhz / 2
        return (self.mhz - half, self.mhz + half)

    @property
    def nuisance(self) -> int:
        """How much trouble this one network causes, 0-100.

        Not just signal: a wide, loud network is far worse than a narrow distant
        one, and a 40 MHz network on 2.4 GHz is antisocial because it eats most
        of a band that only has three clear channels.
        """
        n = self.signal
        if self.width_mhz >= 40 and self.band.startswith("2"):
            n = min(100, int(n * 1.5))
        elif self.width_mhz >= 40:
            n = min(100, int(n * 1.2))
        return n


# --------------------------------------------------------------------------
# Scanning, per platform. Each returns [] rather than raising.
# --------------------------------------------------------------------------
def _run(args, timeout=45):
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout
    except Exception:
        return 1, ""


def _tool(name):
    """Find a binary even in /usr/sbin, which is not on a normal user's PATH."""
    p = shutil.which(name)
    if p:
        return p
    for d in ("/usr/sbin", "/sbin", "/usr/local/sbin",
              "/System/Library/PrivateFrameworks/Apple80211.framework/Versions/Current/Resources"):
        import os
        c = os.path.join(d, name)
        if os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


def scan_linux():
    """nmcli, which nearly every desktop Linux has. Falls back to iw."""
    nm = _tool("nmcli")
    out = []
    if nm:
        _run([nm, "dev", "wifi", "rescan"], timeout=20)
        rc, text = _run([nm, "-t", "-f", "SSID,CHAN,FREQ,SIGNAL,SECURITY", "dev", "wifi", "list"])
        for line in text.splitlines():
            # An SSID MAY CONTAIN A COLON and nmcli escapes it as "\:". Splitting
            # left to right shifts every field and you end up calling int() on
            # "2462 MHz". Split from the RIGHT; the name is whatever is left.
            parts = line.rsplit(":", 4)
            if len(parts) < 5:
                continue
            ssid, chan, freq, sig, sec = parts
            ssid = re.sub(r"\\(.)", r"\1", ssid)
            mhz = re.sub(r"[^0-9]", "", freq)
            if not chan.isdigit() or not mhz:
                continue
            try:
                signal = int(sig)
            except ValueError:
                continue
            out.append(Network(ssid or "<hidden>", int(chan), int(mhz),
                               band_of(mhz), signal, security=sec))
    if out:
        return out
    iw = _tool("iw")
    if not iw:
        return []
    rc, text = _run([iw, "dev"])
    m = re.search(r"Interface\s+(\S+)", text)
    if not m:
        return []
    rc, text = _run([iw, "dev", m.group(1), "scan"], timeout=60)
    cur = {}
    for line in text.splitlines():
        if line.startswith("BSS "):
            if cur.get("mhz"):
                out.append(_from_iw(cur))
            cur = {"bssid": line.split()[1].split("(")[0]}
        elif "freq:" in line:
            cur["mhz"] = int(float(line.split("freq:")[1].strip()))
        elif "SSID:" in line:
            cur["ssid"] = line.split("SSID:", 1)[1].strip()
        elif "signal:" in line:
            cur["dbm"] = float(re.findall(r"-?\d+\.?\d*", line)[0])
    if cur.get("mhz"):
        out.append(_from_iw(cur))
    return out


def _from_iw(d):
    mhz = d["mhz"]
    band = band_of(mhz)
    ch = round((mhz - 2407) / 5) if band == "2.4" else round((mhz - 5000) / 5)
    # dBm to a 0-100 quality, the same mapping NetworkManager uses
    dbm = d.get("dbm", -100)
    sig = max(0, min(100, int(2 * (dbm + 100))))
    return Network(d.get("ssid") or "<hidden>", ch, mhz, band, sig, bssid=d.get("bssid", ""))


def scan_macos():
    """airport on older macOS, system_profiler on newer where airport was removed."""
    out = []
    ap = _tool("airport")
    if ap:
        rc, text = _run([ap, "-s"], timeout=40)
        for line in text.splitlines()[1:]:
            m = re.match(r"\s*(.+?)\s+([0-9a-f:]{17})\s+(-?\d+)\s+(\d+)", line, re.I)
            if not m:
                continue
            ssid, bssid, dbm, ch = m.group(1).strip(), m.group(2), int(m.group(3)), int(m.group(4))
            band = "2.4" if ch <= 14 else "5"
            out.append(Network(ssid or "<hidden>", ch, freq_of(ch, band), band,
                               max(0, min(100, 2 * (dbm + 100))), bssid=bssid))
        if out:
            return out
    sp = _tool("system_profiler")
    if not sp:
        return []
    rc, text = _run([sp, "-json", "SPAirPortDataType"], timeout=60)
    try:
        data = json.loads(text)
    except Exception:
        return []

    def walk(node):
        if isinstance(node, dict):
            name = node.get("_name")
            ch = node.get("spairport_network_channel")
            sig = node.get("spairport_signal_noise")
            if name and ch:
                m = re.search(r"(\d+)", str(ch))
                band = "2.4" if m and int(m.group(1)) <= 14 else "5"
                dbm = -70
                if sig:
                    mm = re.search(r"(-?\d+)", str(sig))
                    if mm:
                        dbm = int(mm.group(1))
                if m:
                    out.append(Network(name, int(m.group(1)),
                                       freq_of(int(m.group(1)), band), band,
                                       max(0, min(100, 2 * (dbm + 100)))))
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(data)
    return out


def scan_windows():
    """netsh, present on every Windows since Vista."""
    ns = _tool("netsh")
    if not ns:
        return []
    rc, text = _run([ns, "wlan", "show", "networks", "mode=bssid"], timeout=60)
    out, ssid = [], None
    sig = 0
    for line in text.splitlines():
        m = re.match(r"\s*SSID\s+\d+\s*:\s*(.*)", line)
        if m:
            ssid = m.group(1).strip() or "<hidden>"
            continue
        m = re.match(r"\s*Signal\s*:\s*(\d+)%", line)
        if m:
            sig = int(m.group(1))
            continue
        m = re.match(r"\s*Channel\s*:\s*(\d+)", line)
        if m and ssid is not None:
            ch = int(m.group(1))
            band = "2.4" if ch <= 14 else "5"
            out.append(Network(ssid, ch, freq_of(ch, band), band, sig))
    return out


def scan_freebsd():
    """ifconfig list scan."""
    ifc = _tool("ifconfig")
    if not ifc:
        return []
    rc, text = _run([ifc])
    m = re.search(r"^(wlan\d+):", text, re.M)
    if not m:
        return []
    rc, text = _run([ifc, m.group(1), "list", "scan"], timeout=60)
    out = []
    for line in text.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 5:
            continue
        try:
            ch = int(parts[2])
            dbm = int(parts[4].split(":")[0])
        except (ValueError, IndexError):
            continue
        band = "2.4" if ch <= 14 else "5"
        out.append(Network(parts[0], ch, freq_of(ch, band), band,
                           max(0, min(100, 2 * (dbm + 100)))))
    return out


def scan():
    """-> (networks, problem). An empty list with no problem means a quiet area."""
    sysname = platform.system().lower()
    fn = {"linux": scan_linux, "darwin": scan_macos,
          "windows": scan_windows, "freebsd": scan_freebsd}.get(sysname)
    if not fn:
        return [], f"no scanner for {platform.system()}"
    nets = fn()
    if not nets:
        # Honest degradation: say WHY rather than showing an empty window.
        return [], ("no networks returned. Either the wifi radio is off, this machine has no "
                    "wifi, or the scan needs permission (macOS requires Location Services for "
                    "wifi scanning; Linux may need the radio enabled with `nmcli radio wifi on`)")
    return nets, None


# --------------------------------------------------------------------------
# Profiles: someone else's tuning, applied ONLY when asked for by name
# --------------------------------------------------------------------------
# ============================================================================
# TRAP: NEVER AUTO-APPLY A PROFILE.
# ============================================================================
# It is tempting to match a profile on hostname, MAC prefix or card driver and
# load it silently. Do not. The machine this tool was written on is a 2012
# laptop with a 1x1 2.4-GHz-only card and a Japanese regulatory history, which
# is an unusual combination. Any value tuned for it is WRONG on a normal laptop,
# and silently wrong is the worst kind.
#
# So: the default profile is NO profile. Everything the tool decides by default
# comes either from radio physics (the overlap maths), from regulation (the
# channel tables) or from READING THE LOCAL CARD. A profile can only ever be
# added by a human typing its name, and when one is active the tool says so
# loudly in every output, including the JSON.
#
# A profile may adjust judgement constants and exclude channels. It may NOT
# invent capability: if a profile claims the card can do channel 13 and `iw phy`
# says otherwise, the card wins. Measurement beats configuration, always.
# ============================================================================
import os

PROFILE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "channel_profiles")

# A SECOND place for profiles that must never be published.
#
# A shipped profile describes a CLASS of hardware, which helps anybody with that
# class. A profile that also records "second router on channel 6" describes one
# household, and together with a laptop model and a country that is a
# fingerprint of one dwelling. Useful locally, nobody else's business.
#
# So local profiles live under state/, which is gitignored, and they win over a
# shipped profile of the same name. Nothing is lost: everything a shipped
# profile can set, a local one can set too.
LOCAL_PROFILE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "state", "channel_profiles")

# ============================================================================
# TRAP: YOUR OWN ACCESS POINT IS NOT INTERFERENCE TO ITSELF.
# ============================================================================
# Found 2026-10-13 by making the tool show its own arithmetic. Channel 13 scored
# 158, of which 89 came from the owner's OWN hub sitting on channel 13.
#
# That is wrong, and wrong in a direction that matters: when you are deciding
# where to PUT your access point, counting it against its current channel
# penalises exactly the channel you are on, every time. The tool then
# recommends moving, for a gain that is partly an artefact of its own scoring.
#
# Here it was decisive. Removing the owner's own hub took channel 13 from 158 to
# about 69 against roughly 174 for channel 11: a 2.5x margin instead of 1.1x,
# which crosses the threshold and flips the recommendation.
#
# It is excluded from EVERY candidate channel, not just its current one, because
# wherever you move it, IT MOVES WITH YOU. It is never a competitor to itself.
#
# IMPORTANT LIMIT: --mine means "the access point I am placing". If you own a
# SECOND access point in the same building, that one is real interference and
# must stay counted, because it is staying where it is. Do not list it under
# --mine; use --avoid to stop its channel being recommended.
# ============================================================================

DEFAULTS = {
    "partial_overlap_penalty": PARTIAL_OVERLAP_PENALTY,
    "worth_leaving_1_6_11": WORTH_LEAVING_1_6_11,
    "avoid": [],
    "devices": "mixed",
    "note": "",
}

# What the person knows about their own hardware, which no scan can establish.
#
#   "mixed"  the cautious default. Assume SOMETHING in the building is US-market
#            or has no region set, so channels 12 and 13 carry a real risk: such
#            a device does not get a slower connection there, it gets NO NETWORK
#            AT ALL and cannot even see it.
#
#   "local"  the person has stated that every device here can legally use 12 and
#            13 (UK, EU and most of the world outside the US). The risk is then
#            ZERO, and 12 and 13 stop being a hazard and become what they really
#            are: spectrum almost nobody is allowed to compete with you for.
#
# Added 2026-10-13 because the tool only ever framed 12 and 13 as a danger and
# never as the opportunity, and gave the person no way to say what they know.
# Only they can answer this, so only they may set it. It is never guessed.
DEVICE_KNOWLEDGE = ("mixed", "local")


def profile_dirs():
    """Local profiles first, so a private one overrides a shipped one by name."""
    return [d for d in (LOCAL_PROFILE_DIR, PROFILE_DIR) if os.path.isdir(d)]


def list_profiles():
    names = []
    for d in profile_dirs():
        for f in sorted(os.listdir(d)):
            if f.endswith(".json") and f[:-5] not in names:
                names.append(f[:-5])
    return sorted(names)


def profile_path(name):
    for d in profile_dirs():
        p = os.path.join(d, f"{name}.json")
        if os.path.exists(p):
            return p
    return None


def load_profile(name):
    """-> (settings, problem). No name means the defaults, which suit any machine."""
    cfg = dict(DEFAULTS)
    cfg["name"] = None
    if not name:
        return cfg, None
    path = profile_path(name)
    if not path:
        return cfg, f"no profile named {name!r}. Available: {', '.join(list_profiles()) or 'none'}"
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except Exception as e:
        return cfg, f"profile {name!r} could not be read: {e}"
    unknown = [k for k in raw if k not in DEFAULTS and k != "description"]
    if unknown:
        return cfg, f"profile {name!r} sets unknown keys: {', '.join(unknown)}"
    cfg.update({k: v for k, v in raw.items() if k in DEFAULTS})
    cfg["name"] = name
    cfg["description"] = raw.get("description", "")
    cfg["local"] = path.startswith(LOCAL_PROFILE_DIR)
    return cfg, None


# --------------------------------------------------------------------------
# Regulatory and capability: what may you even use here
# --------------------------------------------------------------------------
def local_rules():
    """Which channels are legal and tunable on THIS machine."""
    info = {"regdomain": None, "usable_24": [], "note": ""}
    iw = _tool("iw")
    if iw:
        rc, text = _run([iw, "reg", "get"])
        m = re.search(r"country\s+(\S\S)", text)
        if m:
            info["regdomain"] = m.group(1)
        rc, text = _run([iw, "phy"])
        chans = re.findall(r"\*\s*2(\d{3})\.0 MHz \[(\d+)\]", text)
        info["usable_24"] = sorted({int(c) for _f, c in chans})
    if not info["usable_24"]:
        info["usable_24"] = list(range(1, 12))
        info["note"] = "could not read the card; assuming the universally safe 1-11"
    if info["regdomain"] == "00":
        info["note"] = ("regulatory domain is 00, the conservative world default. Channels 12 "
                        "and 13 are passive-scan only: the card listens but never probes, so "
                        "joining can be slower. `iw reg set GB` in the UK fixes it.")
    return info


def card_report():
    """What THIS machine's radio can actually do. Measured, or openly unknown.

    Exists so the tool never recommends a channel or a band the card cannot
    tune. It reports "could not read" rather than guessing, because a guessed
    capability is how a tool ends up confidently advising something impossible.
    """
    out = {"platform": platform.system(), "bands": [], "channels_24": [],
           "channels_5": [], "driver": None, "read": False, "problem": None}
    iw = _tool("iw")
    if not iw:
        out["problem"] = ("no `iw` on this system, so the card's own limits cannot be read. "
                          "The survey still works; it just cannot tell you whether your "
                          "radio can tune the channel it recommends.")
        return out
    rc, text = _run([iw, "phy"])
    if rc != 0 or not text:
        out["problem"] = "`iw phy` returned nothing (it may need root on some systems)"
        return out
    out["read"] = True
    for band_num, label in ((1, "2.4"), (2, "5"), (4, "6")):
        if re.search(rf"Band {band_num}:", text):
            out["bands"].append(label)
    for f, c in re.findall(r"\*\s*(\d{4})\.0 MHz \[(\d+)\]", text):
        ch, mhz = int(c), int(f)
        if band_of(mhz) == "2.4":
            out["channels_24"].append(ch)
        elif band_of(mhz) == "5":
            out["channels_5"].append(ch)
    out["channels_24"] = sorted(set(out["channels_24"]))
    out["channels_5"] = sorted(set(out["channels_5"]))
    return out


def card_lines(c):
    L = [f"radio capability on this machine ({c['platform']})"]
    if not c["read"]:
        L += ["", "  UNKNOWN: " + (c["problem"] or "could not read"), "",
              "  That is reported rather than guessed. The channel advice below is still",
              "  valid radio-wise; only the 'can your card do it' check is missing."]
        return "\n".join(L)
    L.append("")
    L.append(f"  bands:        {', '.join(c['bands']) or 'none detected'} GHz")
    L.append(f"  2.4 channels: {', '.join(map(str, c['channels_24'])) or 'none'}")
    if c["channels_5"]:
        L.append(f"  5 channels:   {len(c['channels_5'])} available")
    else:
        L.append("  5 channels:   none. This radio is 2.4 GHz only.")
    if 13 in c["channels_24"]:
        L.append("")
        L.append("  This card CAN tune channel 13. Many cannot, so do not assume it of")
        L.append("  the other devices in the building; the hub is only half the question.")
    L.append("")
    L.append("  Read from the card itself, not assumed from the model name.")
    return "\n".join(L)


# --------------------------------------------------------------------------
# The analysis
# --------------------------------------------------------------------------
@dataclass
class ChannelScore:
    channel: int
    mhz: int
    score: int = 0
    on_channel: list = field(default_factory=list)
    overlapping: list = field(default_factory=list)
    legal_note: str = ""        # a legal or compatibility catch
    overlap_note: str = ""      # a radio catch. NOT the same thing.
    partial: int = 0            # how many networks partly overlap us
    yours: bool = False         # your own AP is here, and was NOT counted
    opportunity: bool = False   # quiet FOR A REASON that does not apply to you
    usable: bool = True


def analyse(nets, band="2.4", avoid=(), rules=None, cfg=None, mine=()):
    cfg = cfg or dict(DEFAULTS)
    rules = rules or local_rules()
    avoid = set(avoid) | set(cfg.get("avoid") or ())
    mine = {m for m in (mine or ()) if m}
    nets = [n for n in nets if n.band == band]
    channels = list(CH24) if band == "2.4" else sorted({n.channel for n in nets}) or [36]
    rows = []
    for ch in channels:
        mhz = freq_of(ch, band)
        row = ChannelScore(ch, mhz)
        lo, hi = mhz - 10, mhz + 10              # the 20 MHz we would occupy
        for n in nets:
            # the AP being placed is never its own competitor, on ANY candidate
            # channel, because it moves with you (see the TRAP above)
            if n.ssid in mine or n.bssid.lower() in {m.lower() for m in mine}:
                row.yours = True
                continue
            nlo, nhi = n.span
            if nhi <= lo or nlo >= hi:
                continue                          # no frequency overlap at all
            # how much of our 20 MHz does this network cover
            covered = min(hi, nhi) - max(lo, nlo)
            fraction = max(0.0, min(1.0, covered / 20.0))
            if 0.05 < fraction < 0.95:
                fraction *= cfg["partial_overlap_penalty"]   # see the constant
                row.partial += 1
            row.score += n.nuisance * fraction
            (row.on_channel if n.channel == ch else row.overlapping).append(n)
        row.score = round(row.score)

        if band == "2.4":
            if ch in JAPAN_ONLY_24:
                row.usable = False
                row.legal_note = "Japan only, and 802.11b only: 11 Mbps ceiling. Never use."
            elif ch in UK_EU_ONLY_24:
                if cfg.get("devices") == "local":
                    row.legal_note = ("you have stated every device here can use this channel, "
                                      "so the usual compatibility risk does not apply. It is "
                                      "quiet because US-market kit is not allowed here, which "
                                      "makes it capacity almost nobody competes for.")
                    row.opportunity = True
                else:
                    row.legal_note = ("legal in the UK and EU, ILLEGAL in the US. Devices with "
                                      "a US or unset regulatory domain cannot see it at all, "
                                      "which includes a lot of cheap IoT. Empty for that "
                                      "reason.")
            if ch not in NON_OVERLAPPING_24 and ch not in JAPAN_ONLY_24:
                row.overlap_note = ("not 1, 6 or 11: partly overlaps its neighbours, which "
                                    "breaks carrier sense (see --check)")
        if ch not in rules["usable_24"] and band == "2.4":
            row.usable = False
            row.legal_note = "your own card cannot tune this channel"
        if ch in avoid:
            row.usable = False
            row.legal_note = "excluded at your request"
        rows.append(row)
    return rows, rules


def ratio(worse, better):
    """How many times better. Guards the empty channel, which scores zero.

    A score of 0 means NOTHING overlaps that channel, which is the best possible
    result and must not be a crash. It happens constantly outside cities, so the
    naive division here was a bug waiting for a rural user.
    """
    if better <= 0:
        return float("inf") if worse > 0 else 1.0
    return worse / better


def say_ratio(worse, better):
    r = ratio(worse, better)
    return "completely clear against" if r == float("inf") else f"{r:.1f}x better than"


def recommend(rows, band="2.4", cfg=None):
    """Two answers: the conventional one, and the best one. They often differ."""
    cfg = cfg or dict(DEFAULTS)
    usable = [r for r in rows if r.usable]
    if not usable:
        return {"error": "no usable channel"}
    lowest = min(usable, key=lambda r: r.score)
    conventional = [r for r in usable if r.channel in NON_OVERLAPPING_24] if band == "2.4" else usable
    best_conv = min(conventional, key=lambda r: r.score) if conventional else lowest

    # The margin rule (see WORTH_LEAVING_1_6_11). A thin win does not justify
    # giving up a permanent structural advantage for a temporary measured one.
    best = lowest
    thin = False
    if band == "2.4" and lowest.channel not in NON_OVERLAPPING_24:
        if ratio(best_conv.score, lowest.score) < cfg["worth_leaving_1_6_11"]:
            best, thin = best_conv, True
    return {"best": best, "conventional": best_conv, "lowest": lowest,
            "margin_too_thin": thin,
            "they_differ": best.channel != best_conv.channel}


def rationale(rows, rec, rules, band="2.4", cfg=None):
    """Argue the case. A number without a reason is just another app."""
    cfg = cfg or dict(DEFAULTS)
    if "error" in rec:
        return [rec["error"]]
    best, conv, lowest = rec["best"], rec["conventional"], rec["lowest"]
    out = []
    out.append(f"The conventional advice is 'always use 1, 6 or 11', because on 2.4 GHz those "
               f"are the only three channels that do not overlap each other. Of those, "
               f"{conv.channel} is clearest here, scoring {conv.score}.")
    if rec["they_differ"]:
        out.append("")
        out.append(f"But that rule assumes EVERYONE follows it. Here they do not: there are "
                   f"networks sitting between the clear channels, so the clear channels are not "
                   f"actually clear. Channel {best.channel} scores {best.score}, which is "
                   f"{(conv.score / best.score):.1f}x less interference than channel "
                   f"{conv.channel}.")
        if best.opportunity:
            out.append("")
            out.append(f"On channel {best.channel}: {best.legal_note}")
        elif best.legal_note:
            out.append("")
            out.append(f"The catch with channel {best.channel}: {best.legal_note}")
            out.append("That is a decision about your devices, not a measurement. If everything "
                       "in the building is UK or EU market, it is free capacity nobody else is "
                       "using. If anything is US market or cheap IoT with an unset region, it "
                       "will not see the network at all. A scan cannot tell you which; you have "
                       "to look at your own kit.")
        if best.overlap_note:
            out.append("")
            out.append(f"Note that channel {best.channel} is not one of the three clear ones. It "
                       f"wins here anyway because the clear ones are occupied, and that is a real "
                       f"result, not a loophole: {best.partial} network(s) partly overlap it "
                       f"against {conv.partial} partly overlapping channel {conv.channel}. The "
                       f"scoring already charges a partial overlap double, because a half-overlap "
                       f"breaks carrier sense and is worse than sharing outright.")
    else:
        out.append("")
        if rec["margin_too_thin"]:
            out.append(f"Channel {lowest.channel} actually measures slightly quieter "
                       f"({lowest.score} against {conv.score}), and a lot of tools would tell you "
                       f"to go there. Do not. That margin is only "
                       f"{ratio(conv.score, lowest.score):.1f}x, which is thinner than the error bar "
                       f"on a single scan: one neighbour moving tonight erases it. Meanwhile the "
                       f"non-overlap property of 1, 6 and 11 is permanent. Take the permanent "
                       f"advantage over the temporary one.")
            out.append("")
            out.append(f"So: channel {best.channel}. It only becomes worth leaving 1, 6 or 11 at "
                       f"about {cfg['worth_leaving_1_6_11']}x, where no plausible shift in the "
                       f"neighbours closes the gap.")
        else:
            out.append(f"Here the conventional answer is also the best one: channel {best.channel}.")
    if rules.get("note"):
        out.append("")
        out.append("Also: " + rules["note"])
    if cfg.get("name"):
        out.append("")
        out.append(f"PROFILE {cfg['name']!r} IS ACTIVE, so the judgement above is tuned for that "
                   f"machine, not for yours. {cfg.get('description', '')}".rstrip())
        out.append("Run without --profile for the neutral answer, which suits any laptop.")
    return out


def case_for(rows, rec, channel, rules, cfg=None):
    """Argue the case for the channel the PERSON chose, against the recommendation.

    The tool recommends cautiously, because it does not know your devices. You
    do. This weighs your choice honestly, says where it is better, says where it
    is worse, and names the one fact only you can supply.
    """
    cfg = cfg or dict(DEFAULTS)
    mine = next((r for r in rows if r.channel == channel), None)
    if mine is None:
        return [f"channel {channel} is not a channel on this band"]
    if "error" in rec:
        return [rec["error"]]
    best = rec["best"]
    out = []

    if not mine.usable:
        return [f"Channel {channel} is not usable: {mine.legal_note}",
                f"Use channel {best.channel}."]

    if mine.channel == best.channel:
        out.append(f"You are on channel {channel}, which is also what the tool recommends. "
                   f"Nothing to argue about.")
        return out

    r = ratio(best.score, mine.score)
    if mine.score < best.score:
        out.append(f"YOUR CHOICE MEASURES BETTER. Channel {channel} scores {mine.score} against "
                   f"{best.score} for the recommended channel {best.channel}: "
                   f"{say_ratio(best.score, mine.score)} it, right now.")
        out.append("")
        out.append(f"The tool still recommends {best.channel}, and it is worth being clear WHY it "
                   f"disagrees with a measurement it took itself. It is not that your channel is "
                   f"worse. It is that the tool is being cautious ON YOUR BEHALF about two things "
                   f"it cannot see.")
    else:
        out.append(f"YOUR CHOICE MEASURES WORSE. Channel {channel} scores {mine.score} against "
                   f"{best.score} for channel {best.channel}, so you are carrying "
                   f"{say_ratio(mine.score, best.score)} the interference of the recommendation.")
        out.append("")
        out.append("That may still be the right call if the reasons below apply to you, but you "
                   "are paying for it rather than gaining.")

    out.append("")
    out.append("WHAT THE TOOL CANNOT SEE, AND YOU CAN:")
    out.append("")
    if mine.legal_note:
        out.append(f"  1. YOUR OWN DEVICES. {mine.legal_note}")
        out.append(f"     The tool has to assume somebody in the building owns US-market or "
                   f"cheap unregioned kit, because being wrong about that means a device gets NO "
                   f"NETWORK AT ALL, not a slower one. If you know every device here is UK or EU "
                   f"market, that risk is zero and the channel is simply free capacity nobody "
                   f"else is allowed to use. THAT IS A FACT ABOUT YOUR HOUSE, NOT ABOUT RADIO, "
                   f"and no scan can establish it.")
    else:
        out.append(f"  1. Channel {channel} carries no legal or compatibility catch, so this is "
                   f"purely about interference.")
    out.append("")
    out.append(f"  2. HOW LONG THIS WILL HOLD. A score is one moment. The tool prefers 1, 6 and "
               f"11 because their non-overlap is permanent while a measurement is not, and it "
               f"only abandons them at about {cfg['worth_leaving_1_6_11']}x. If you have watched "
               f"this band over weeks and the picture is stable, you have evidence the tool does "
               f"not: it has seen one scan.")
    out.append("")
    if mine.overlapping:
        others = sorted({n.channel for n in mine.overlapping})
        out.append(f"  3. WHAT YOU ARE ACTUALLY COLLIDING WITH. Channel {channel} overlaps "
                   f"{', '.join(map(str, others))}. Run --check {channel} for who is sitting "
                   f"there. If you control one of those access points yourself, move IT instead "
                   f"and the problem disappears.")
    else:
        out.append(f"  3. Nothing overlaps channel {channel} at all right now, which is the best "
                   f"possible position and the strongest part of your case.")
    out.append("")
    out.append("THE HONEST SUMMARY:")
    if mine.score < best.score:
        out.append(f"  A person who knows their own devices can beat this tool, and on these "
                   f"numbers you have. The tool gives the answer that is safe for a stranger. "
                   f"You are not a stranger to this building.")
    else:
        out.append(f"  On interference alone, channel {best.channel} is the better choice here. "
                   f"Keep {channel} only if one of the reasons above genuinely applies.")
    return out


def current_network():
    """What this machine is associated to right now, so it can be excluded."""
    nm = _tool("nmcli")
    if not nm:
        return None
    rc, text = _run([nm, "-t", "-f", "ACTIVE,SSID", "dev", "wifi"], timeout=15)
    for line in (text or "").splitlines():
        if line.startswith("yes:"):
            return re.sub(r"\\(.)", r"\1", line[4:])
    return None


def score_breakdown(nets, channel, band="2.4", cfg=None, mine=()):
    """Show the arithmetic behind a channel's score, line by line.

    The score is not a measurement in any unit. It is a WEIGHT: add up every
    network that lands on the 20 MHz you want, counting the loud ones more, the
    wide ones more, and the partly-overlapping ones double. It is meaningful
    ONLY against the other channels in the same scan, like a league table.
    """
    cfg = cfg or dict(DEFAULTS)
    mhz = freq_of(channel, band)
    lo, hi = mhz - 10, mhz + 10
    rows, total = [], 0.0
    mine = {m for m in (mine or ()) if m}
    for n in nets:
        if n.band != band:
            continue
        if n.ssid in mine or n.bssid.lower() in {m.lower() for m in mine}:
            continue
        nlo, nhi = n.span
        if nhi <= lo or nlo >= hi:
            continue
        covered = min(hi, nhi) - max(lo, nlo)
        frac = max(0.0, min(1.0, covered / 20.0))
        partial = 0.05 < frac < 0.95
        charged = frac * (cfg["partial_overlap_penalty"] if partial else 1.0)
        cost = n.nuisance * charged
        total += cost
        rows.append({"ssid": n.ssid, "channel": n.channel, "signal": n.signal,
                     "nuisance": n.nuisance, "covers_pct": round(frac * 100),
                     "partial": partial, "cost": round(cost)})
    rows.sort(key=lambda r: -r["cost"])
    return {"channel": channel, "mhz": mhz, "total": round(total), "contributors": rows,
            "penalty": cfg["partial_overlap_penalty"]}


def breakdown_lines(d):
    L = [f"How channel {d['channel']} ({d['mhz']} MHz) got its score of {d['total']}", "",
         "  Every network that lands on the 20 MHz this channel would use, counting",
         "  the loud ones more and charging a PARTIAL overlap double (see below).", "",
         "  cost  signal  covers  ch   network"]
    L.append("  " + "-" * 58)
    for r in d["contributors"]:
        mark = " x2" if r["partial"] else "   "
        L.append(f"  {r['cost']:<5} {r['signal']:<7} {r['covers_pct']:>3}%{mark} {r['channel']:<4} "
                 f"{r['ssid'][:28]}")
    if not d["contributors"]:
        L.append("  (nothing at all lands on this channel: the best possible result)")
    L += ["", f"  TOTAL {d['total']}", "",
          f"  'x2' means the network only PARTLY overlaps, which is charged "
          f"{d['penalty']}x because",
          "  a half-overlap breaks carrier sense and is worse than sharing outright.", "",
          "  THE NUMBER HAS NO UNITS. It is not Mbps, not dBm, not percent busy. It only",
          "  means something COMPARED WITH THE OTHER CHANNELS IN THE SAME SCAN. Half the",
          "  score means roughly half the competition for airtime, nothing more precise."]
    return L


def overlap_warning(rows, channel):
    """Name every channel that collides with this one, and by how much."""
    me = next((r for r in rows if r.channel == channel), None)
    if not me:
        return []
    out = []
    for other in rows:
        if other.channel == channel:
            continue
        gap = abs(other.mhz - me.mhz)
        if gap < 20:
            pct = int((20 - gap) / 20 * 100)
            out.append({"channel": other.channel, "overlap_pct": pct,
                        "mhz_apart": gap})
    return sorted(out, key=lambda x: -x["overlap_pct"])


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------
def loudness(signal):
    """Plain words for how hard a network is shouting, so nobody needs dBm."""
    if signal >= 70:
        return "SHOUTING"
    if signal >= 45:
        return "loud"
    if signal >= 25:
        return "talking"
    return "murmuring"


def neighbours_on(rows, channel, limit=8):
    """Who you would actually be sharing the air with on this channel.

    The score says HOW MUCH competition there is. It never said WHO, and that
    was the thing the person wanted most: a name, how loud it is, and whether
    it is sitting on top of you or only half over you. Added 2026-10-13.
    """
    row = next((r for r in rows if r.channel == channel), None)
    if row is None:
        return {"channel": channel, "error": f"channel {channel} is not on this band"}
    items = []
    for n in row.on_channel:
        items.append({"ssid": n.ssid or "<no name>", "signal": n.signal,
                      "loudness": loudness(n.signal), "channel": n.channel,
                      "where": "right on top of you", "kind": classify(n.ssid)})
    for n in row.overlapping:
        gap = abs(freq_of(channel, n.band) - n.mhz)
        pct = max(0, int((20 - gap) / 20 * 100))
        items.append({"ssid": n.ssid or "<no name>", "signal": n.signal,
                      "loudness": loudness(n.signal), "channel": n.channel,
                      "where": f"on channel {n.channel}, {pct}% over you",
                      "kind": classify(n.ssid)})
    items.sort(key=lambda i: -i["signal"])
    return {"channel": channel, "total": len(items), "shown": items[:limit],
            "more": max(0, len(items) - limit), "yours": row.yours}


def neighbour_lines(d, heading=None):
    if "error" in d:
        return [d["error"]]
    L = []
    if heading:
        L.append(heading)
    if not d["total"]:
        L.append(f"  Nobody at all. Channel {d['channel']} is completely clear right now.")
        return L
    L.append(f"  {d['total']} network(s) would be competing with you on channel {d['channel']}:")
    L.append("")
    for i in d["shown"]:
        kind = "" if i["kind"] in ("unknown", "hidden") else f"  [{i['kind']}]"
        L.append(f"    {i['loudness']:<10} {i['ssid'][:24]:<25} {i['where']}{kind}")
    if d["more"]:
        L.append(f"    ... and {d['more']} more")
    return L


def _wrap(text, indent="  ", cols=78):
    """Wrap prose to a width that fits a modest terminal without side-scrolling."""
    return textwrap.wrap(" ".join(text.split()), width=cols,
                         initial_indent=indent, subsequent_indent=indent) or [indent.rstrip()]


def plain_report(nets, rows, rec, rules, band="2.4", cfg=None, here=None, width=46):
    """The DEFAULT view: for somebody who did not ask for a lecture.

    Written 2026-10-13 after looking at the engineering table through a
    non-technical reader's eyes. It printed the same jargon line ten times, put
    the actual answer at the very bottom, and led with "regulatory domain: 00".
    Technically complete and practically useless.

    Rules this view follows:
      * the ANSWER first, in a sentence, before anything else
      * a picture, not a column of raw numbers
      * every warning said ONCE, at the end, and only if it applies
      * no word that a reader would have to look up, unless it is explained here
    """
    cfg = cfg or dict(DEFAULTS)
    usable = [r for r in rows if r.usable]
    on_air = len([n for n in nets if n.band == band])
    L = []
    L.append(f"WIFI CHANNELS{' ' * 12}{on_air} other networks are competing with you")
    L.append("")

    if "error" in rec:
        return "\n".join(L + ["  " + rec["error"]])
    best = rec["best"]

    L.append(f"  USE CHANNEL {best.channel}.")
    L.append("")
    if here is not None:
        mine = next((r for r in rows if r.channel == here), None)
        if here == best.channel:
            L.append(f"  You are already on channel {here}. Nothing to do.")
        elif mine and mine.score <= best.score:
            r = ratio(best.score, mine.score)
            if r < 1.1:
                L += _wrap(
                    f"You are on channel {here}, and it is level with channel {best.channel} "
                    f"right now: there is nothing between them tonight. Channel "
                    f"{best.channel} is still the safer long-term home, because it is one of "
                    f"the only three that never overlap anything, and that stays true when "
                    f"the neighbours change.")
                L.append("")
                L += _wrap(f"But on tonight's numbers there is no reason to move. Staying on "
                           f"{here} costs you nothing.")
            else:
                L += _wrap(
                    f"You are on channel {here}, which is actually the quietest right now. "
                    f"Channel {best.channel} is {r:.1f} times busier, but it is one of the "
                    f"only three channels that never overlap anything, and that stays true "
                    f"when the neighbours change. Tonight's quiet is not permanent.")
                L.append("")
                felt = ("you would not feel the difference either way" if r < 1.5
                        else "that is a real gap, so your choice is earning you something")
                L += _wrap(f"Honestly: at {r:.1f}x {felt}. Staying on {here} is a defensible "
                           f"choice if you know your own devices.")
        elif mine:
            r = ratio(mine.score, best.score)
            tail = ("That is enough to notice in the evening, so it is worth moving."
                    if r >= 2 else
                    "That is too small to feel. Move it if it is easy, but do not fret.")
            L += _wrap(f"You are on channel {here}, which is {r:.1f} times busier than channel "
                       f"{best.channel}. {tail}")
    L.append("")

    L.append("HOW BUSY EACH CHANNEL IS")
    L.append("  Lower is better. It means less waiting for your turn to talk.")
    L.append("")
    shown = sorted(usable, key=lambda r: r.score)[:6]
    worst = max((r.score for r in shown), default=1) or 1
    flagged = set()
    for r in shown:
        bar = "#" * max(1, round(r.score / worst * width))
        tags = []
        if r.channel == here:
            tags.append("you are here")
        if r.channel == best.channel:
            tags.append("recommended")
        if r.legal_note:
            tags.append("see note *")
            flagged.add("legal")
        if r.overlap_note:
            flagged.add("overlap")
        tag = ("   <- " + ", ".join(tags)) if tags else ""
        L.append(f"  channel {r.channel:<3} {r.score:>4}  {bar}{tag}")
    L.append("")

    notes = []
    if "legal" in flagged or any(r.channel in UK_EU_ONLY_24 for r in shown):
        if cfg.get("devices") == "local":
            notes.append(("*", "Channels 12 and 13 are legal across the UK and Europe and "
                               "banned in the United States, which is the ONLY reason they are "
                               "so quiet. You have confirmed every device here can use them, so "
                               "there is no catch for you: this is spectrum almost nobody is "
                               "allowed to compete with you for."))
        else:
            notes.append(("*", "Channels 12 and 13 are legal in the UK and Europe and ILLEGAL "
                               "in the United States. They look empty for that reason. Devices "
                               "built for the American market, and a lot of cheap smart-home "
                               "gadgets, cannot see them AT ALL. Not slower: invisible. Only "
                               "you know what you own."))
    if "overlap" in flagged:
        notes.append(("-", "Only channels 1, 6 and 11 never tread on each other. Every other "
                           "channel partly overlaps its neighbours, and a PARTIAL overlap is "
                           "worse than sharing a channel outright, because the two radios "
                           "cannot hear each other well enough to take turns."))
    if rules.get("note"):
        notes.append(("-", rules["note"]))
    if cfg.get("name"):
        notes.append(("!", f"Profile {cfg['name']!r} is active, so these judgements are tuned "
                           f"for another machine, not yours."))
    L.append("WHO YOU ARE SHARING THE AIR WITH")
    L.append("")
    seen_ch = []
    for ch, label in [(here, "where you are now"), (best.channel, "the recommendation")]:
        if ch is None or ch in seen_ch:
            continue
        seen_ch.append(ch)
        L += neighbour_lines(neighbours_on(rows, ch), f"  CHANNEL {ch}, {label}:")
        L.append("")
    L += _wrap("Remember this is the ROUTERS only. The Alexas, televisions, doorbells and "
               "smart plugs attached to them are using exactly the same air, but they answer "
               "rather than announce, so no ordinary scan can see them. Assume there are "
               "several times more things in here than this list shows.")
    L.append("")

    quiet_extra = [r for r in shown if r.channel in UK_EU_ONLY_24]
    if quiet_extra and cfg.get("devices") != "local":
        ch = min(quiet_extra, key=lambda r: r.score).channel
        notes.append(("?", f"YOU MAY HAVE A CHOICE HERE. Channel {ch} is among the quietest, and "
                           f"it is quiet for a reason that may not apply to you: it is legal "
                           f"across the UK and Europe but banned in the United States, so almost "
                           f"nobody uses it and American-market kit never will. If every device "
                           f"in your home is UK or European, that is free space nobody can "
                           f"compete with you for. Only you can know that, so the tool will not "
                           f"assume it. Say so with --devices local and it will count it "
                           f"properly."))
    elif quiet_extra:
        notes.append(("+", "You have told the tool every device here can use channels 12 and 13, "
                           "so they are counted as ordinary channels. They are quiet because "
                           "US-market hardware is not permitted on them, not because anything is "
                           "wrong with them."))
    if notes:
        L.append("WORTH KNOWING")
        for marker, text in notes:
            L.append("")
            body = _wrap(text, indent="      ")
            L.append(f"  {marker} " + body[0].lstrip())
            L += body[1:]
        L.append("")

    L.append("This is ONE MOMENT. Neighbours come and go all day, so check again at a")
    L.append("different hour before moving anything. It is also why the 'Auto' setting on")
    L.append("most hubs chooses badly: it looks once at startup and never again.")
    L.append("")
    L.append("  more detail:  --technical   --why   --score CH   --check CH   --case CH")
    return "\n".join(L)


def text_report(nets, rows, rec, rules, band="2.4", cfg=None):
    L = []
    L.append(f"wifi channel survey {VERSION}   {platform.system()}   band {band} GHz")
    L.append(f"{len(nets)} networks on the air")
    if cfg and cfg.get("name"):
        L.append(f"!! profile {cfg['name']!r} active: values tuned for ANOTHER machine")
    if rules.get("regdomain"):
        L.append(f"regulatory domain: {rules['regdomain']}")
    L.append("")
    L.append("  ch   MHz    score  on it  overlapping  worst neighbour")
    L.append("  " + "-" * 62)
    for r in rows:
        if not r.usable and "excluded" not in r.legal_note:
            mark = "  x"
        elif "error" not in rec and r.channel == rec["best"].channel:
            mark = "  <-- best"
        elif "error" not in rec and r.channel == rec["conventional"].channel:
            mark = "  (conventional)"
        else:
            mark = ""
        worst = max((n.nuisance for n in r.on_channel + r.overlapping), default=0)
        L.append(f"  {r.channel:<4} {r.mhz:<6} {r.score:<6} {len(r.on_channel):<6} "
                 f"{len(r.overlapping):<12} {worst}{mark}")
        if r.legal_note:
            L.append(f"       ! {r.legal_note}")
        if r.overlap_note:
            L.append(f"       ~ {r.overlap_note}")
    L.append("")
    L.append("WHY:")
    for line in rationale(rows, rec, rules, band, cfg):
        L.append("  " + line if line else "")
    L.append("")
    L.append("A survey is ONE MOMENT. Neighbours come and go all day; sample again at a")
    L.append("different hour before settling. This is also why 'Auto' on most hubs chooses")
    L.append("badly: it samples at boot and then never looks again.")
    return "\n".join(L)


def to_json(nets, rows, rec, rules, band, cfg=None):
    cfg = cfg or dict(DEFAULTS)
    return json.dumps({
        "version": VERSION, "platform": platform.system(), "band": band,
        "profile": cfg.get("name"), "card": card_report(),
        "regdomain": rules.get("regdomain"), "note": rules.get("note"),
        "networks": [asdict(n) for n in nets],
        "channels": [{"channel": r.channel, "mhz": r.mhz, "score": r.score,
                      "on_channel": len(r.on_channel), "overlapping": len(r.overlapping),
                      "usable": r.usable, "legal_note": r.legal_note,
                      "overlap_note": r.overlap_note, "partly_overlapping": r.partial,
                      "overlaps_with": overlap_warning(rows, r.channel)} for r in rows],
        "recommended": None if "error" in rec else rec["best"].channel,
        "recommended_conventional": None if "error" in rec else rec["conventional"].channel,
        "rationale": rationale(rows, rec, rules, band, cfg),
    }, indent=1)


def network_table(nets, band="2.4"):
    """Every network: where it is, how wide, how loud, how much trouble."""
    rows = [n for n in nets if n.band == band]
    rows.sort(key=lambda n: (-n.nuisance, n.channel))
    L = ["  ch   MHz    width  signal  nuisance  kind                  name"]
    L.append("  " + "-" * 74)
    for n in rows:
        L.append(f"  {n.channel:<4} {n.mhz:<6} {n.width_mhz:<6} {n.signal:<7} "
                 f"{n.nuisance:<9} {classify(n.ssid)[:20]:<21} {n.ssid[:24]}")
    if not rows:
        L.append("  (nothing on this band)")
    L.append("")
    L.append("  NOTE: a scan sees ACCESS POINTS only. The Alexas, TVs and fridges attached")
    L.append("  to those networks are using the air but broadcast no beacon, so they are")
    L.append("  invisible here. Any app claiming to list them from a normal scan is lying.")
    return "\n".join(L)


def warn_about(rows, channel):
    """The blunt warning: this channel collides with these, and you do not want that."""
    laps = overlap_warning(rows, channel)
    me = next((r for r in rows if r.channel == channel), None)
    if me is None:
        return [f"channel {channel} is not a 2.4 GHz channel"]
    out = []
    if not laps:
        out.append(f"Channel {channel} overlaps NOTHING. That is as good as it gets.")
    else:
        heavy = [l for l in laps if l["overlap_pct"] >= 50]
        names = ", ".join(str(l["channel"]) for l in laps)
        out.append(f"HEADS UP: channel {channel} overlaps channels {names}.")
        if heavy:
            out.append(f"  More than half-overlapped, so effectively the same channel: "
                       + ", ".join(f"{l['channel']} ({l['overlap_pct']}%)" for l in heavy))
        out.append("  Anything on those channels is in your way and you are in theirs.")
        out.append("  Neither side can hear the other well enough to take turns politely,")
        out.append("  which is worse than sharing a channel outright: on the SAME channel")
        out.append("  radios queue for each other. PARTLY overlapping, they talk over each")
        out.append("  other and both retransmit. That is why 1, 6 and 11 exist.")
    busy = [r for r in rows if r.channel in [l["channel"] for l in laps] and r.on_channel]
    if busy:
        out.append("")
        out.append("  Who is actually sitting on those overlapping channels right now:")
        for r in busy:
            for n in r.on_channel:
                out.append(f"    ch {r.channel:<3} {n.signal:>3}%  {classify(n.ssid):<20} {n.ssid}")
    if me.legal_note:
        out.append("")
        out.append(f"  And on legality: {me.legal_note}")
    return out


# --------------------------------------------------------------------------
# The window. tkinter, because it is in the standard library on every platform,
# so this file stays transplantable with no install step.
# --------------------------------------------------------------------------
def gui():
    try:
        import tkinter as tk
        from tkinter import ttk, scrolledtext
    except Exception as e:
        print(f"no GUI available ({e}).", file=sys.stderr)
        print("tkinter is part of Python but some Linux distributions package it", file=sys.stderr)
        print("separately. On Debian or Ubuntu: sudo apt install python3-tk", file=sys.stderr)
        print("Everything works without it, on the terminal. Run with no arguments.", file=sys.stderr)
        return 4

    state = {"nets": [], "rows": [], "rec": {}, "rules": {}, "band": "2.4",
             "cfg": dict(DEFAULTS)}   # no profile unless the user picks one

    root = tk.Tk()
    root.title("wifi channel survey")
    root.geometry("1000x680")           # fits a 1600x900 screen with room to spare

    top = ttk.Frame(root, padding=8)
    top.pack(fill="x")
    ttk.Label(top, text="band").pack(side="left")
    band = ttk.Combobox(top, values=["2.4", "5"], width=5, state="readonly")
    band.set("2.4")
    band.pack(side="left", padx=(4, 12))
    ttk.Label(top, text="exclude channels").pack(side="left")
    avoid = ttk.Entry(top, width=12)
    avoid.pack(side="left", padx=(4, 12))
    ttk.Label(top, text="profile").pack(side="left")
    prof = ttk.Combobox(top, values=["(none)"] + list_profiles(), width=10, state="readonly")
    prof.set("(none)")
    prof.pack(side="left", padx=(4, 12))
    status = ttk.Label(top, text="ready")
    scan_btn = ttk.Button(top, text="Scan")
    scan_btn.pack(side="left")
    status.pack(side="left", padx=12)

    nb = ttk.Notebook(root)
    nb.pack(fill="both", expand=True, padx=8, pady=8)

    # TRAP: scrolledtext.ScrolledText re-parents ITSELF into a Frame it creates,
    # so t.master is that internal frame, NOT the notebook page. Packing another
    # widget "before" it therefore fails with a confusing TclError. Keep the
    # page frames explicitly instead of recovering them from a child.
    pages, frames = {}, {}
    for name in ("Channels", "Networks", "Why", "Overlap check", "This radio", "Decode a rate"):
        f = ttk.Frame(nb)
        frames[name] = f
        if name == "Decode a rate":
            rr = ttk.Frame(f, padding=(4, 4))
            rr.pack(fill="x")
            ttk.Label(rr, text="the 'connection speed' your device reports, in Mbps").pack(
                side="left", padx=4)
            rate_in = ttk.Entry(rr, width=10)
            rate_in.insert(0, "2401")
            rate_in.pack(side="left", padx=4)
            rate_btn = ttk.Button(rr, text="Decode")
            rate_btn.pack(side="left")
        if name == "Overlap check":
            # the picker goes in FIRST, so it sits above the text with no
            # before= games
            ov = ttk.Frame(f, padding=(4, 4))
            ov.pack(fill="x")
            ttk.Label(ov, text="check channel").pack(side="left", padx=4)
            ovch = ttk.Combobox(ov, values=[str(c) for c in CH24], width=5, state="readonly")
            ovch.set("11")
            ovch.pack(side="left")
        t = scrolledtext.ScrolledText(f, wrap="none", font=("monospace", 10))
        t.pack(fill="both", expand=True)
        nb.add(f, text=name)
        pages[name] = t

    def show(page, text):
        pages[page].configure(state="normal")
        pages[page].delete("1.0", "end")
        pages[page].insert("1.0", text)
        pages[page].configure(state="disabled")

    def do_overlap(*_a):
        if not state["rows"]:
            show("Overlap check", "Scan first.")
            return
        try:
            ch = int(ovch.get())
        except ValueError:
            return
        show("Overlap check", "\n".join(warn_about(state["rows"], ch)))

    ovch.bind("<<ComboboxSelected>>", do_overlap)

    def do_rate(*_a):
        show("Decode a rate", "\n".join(rate_lines(decode_rate(rate_in.get()))))

    rate_btn.configure(command=do_rate)
    rate_in.bind("<Return>", do_rate)
    do_rate()

    def do_scan():
        status.configure(text="scanning, this takes a few seconds...")
        scan_btn.configure(state="disabled")
        root.update()
        try:
            nets, problem = scan()
            if problem:
                show("Channels", "COULD NOT SCAN\n\n" + problem)
                status.configure(text="scan failed")
                return
            b = band.get()
            pick = prof.get()
            state["cfg"], pp = load_profile(None if pick == "(none)" else pick)
            if pp:
                show("Channels", pp)
                return
            excl = {int(x) for x in re.findall(r"\d+", avoid.get())}
            rows, rules = analyse(nets, b, avoid=excl, cfg=state["cfg"])
            rec = recommend(rows, b, cfg=state["cfg"])
            state.update(nets=nets, rows=rows, rec=rec, rules=rules, band=b)
            show("Channels", text_report(nets, rows, rec, rules, b, state["cfg"]))
            show("Networks", network_table(nets, b))
            show("Why", "\n".join(rationale(rows, rec, rules, b, state["cfg"])))
            show("This radio", card_lines(card_report()))
            do_overlap()
            n = len([x for x in nets if x.band == b])
            best = "" if "error" in rec else f"   best: channel {rec['best'].channel}"
            status.configure(text=f"{n} networks on {b} GHz{best}")
        finally:
            scan_btn.configure(state="normal")

    scan_btn.configure(command=do_scan)
    show("Channels", "Press Scan.\n\nEverything this window shows is also available on the\n"
                     "terminal: run this file with --help.")
    root.after(300, do_scan)
    root.mainloop()
    return 0


# --------------------------------------------------------------------------
def main(argv=None):
    p = argparse.ArgumentParser(
        prog="channelsurvey",
        description="Which wifi channel is actually free, and why.",
        epilog="Standard library only. Copy this one file anywhere Python 3.8+ runs.")
    p.add_argument("--gui", action="store_true", help="open the clickable window")
    p.add_argument("--band", default="2.4", choices=["2.4", "5"], help="which band (default 2.4)")
    p.add_argument("--avoid", default="", help="channels to exclude, e.g. 6 or 1,6")
    p.add_argument("--json", action="store_true", help="machine-readable, for another tool")
    p.add_argument("--networks", action="store_true", help="list every network instead")
    p.add_argument("--devices-here", action="store_true",
                   help="how many devices are on YOUR OWN network, and what they are")
    p.add_argument("--calibrate", action="append", metavar="METRES:DBM", default=None,
                   help="a measurement at a KNOWN distance, e.g. --calibrate 2.88:-34. "
                        "Repeat at two or more different distances to fit this building")
    p.add_argument("--no-sweep", action="store_true",
                   help="with --devices-here: read the address table without waking anything")
    p.add_argument("--technical", action="store_true",
                   help="the full engineering table instead of the plain summary")
    p.add_argument("--check", type=int, metavar="CH",
                   help="what does channel CH overlap, and who is sitting there")
    p.add_argument("--why", action="store_true", help="the argued rationale only")
    p.add_argument("--mine", action="append", default=None, metavar="SSID",
                   help="your OWN network, excluded from the channel it would move to. "
                        "Repeatable. Detected automatically if you are connected to it")
    p.add_argument("--devices", choices=list(DEVICE_KNOWLEDGE), default=None,
                   help="'local' means YOU confirm every device here can use channels 12 and 13 "
                        "(UK, EU, most places outside the US). Then they stop being a risk and "
                        "become quiet spectrum nobody competes for. Never guessed")
    p.add_argument("--count-mine", action="store_true",
                   help="do NOT exclude your own network (the old, biased behaviour)")
    p.add_argument("--score", type=int, metavar="CH",
                   help="show the arithmetic behind one channel's score")
    p.add_argument("--case", type=int, metavar="CH",
                   help="argue the case for YOUR chosen channel against the recommendation")
    p.add_argument("--card", action="store_true",
                   help="what THIS machine's radio can do, read from the card")
    p.add_argument("--profile", metavar="NAME",
                   help="apply a named tuning profile. Never automatic: without this "
                        "flag the tool uses neutral values that suit any machine")
    p.add_argument("--list-profiles", action="store_true", help="what profiles exist")
    p.add_argument("--watch", metavar="SSID",
                   help="sample ONE network repeatedly, for before-and-after tests")
    p.add_argument("--bssid", metavar="MAC", help="watch a radio by MAC, when one name covers "
                                                  "both bands")
    p.add_argument("--samples", type=int, default=8, help="how many scans for --watch (8)")
    p.add_argument("--rate", type=float, metavar="MBPS",
                   help="decode a reported link rate into standard, width and streams")
    p.add_argument("--version", action="version", version=f"channelsurvey {VERSION}")
    a = p.parse_args(argv)

    if a.list_profiles:
        names = list_profiles()
        if not names:
            print("no profiles. The neutral defaults suit any machine.")
            return 0
        print("profiles (opt-in only, with --profile NAME):")
        for n in names:
            cfg, _ = load_profile(n)
            print(f"  {n:<12} {cfg.get('description', '')}")
        print("\nWithout --profile the tool uses neutral values read from your own card.")
        return 0

    cfg, prob = load_profile(a.profile)
    if a.devices:
        cfg["devices"] = a.devices
    if prob:
        print("PROFILE: " + prob, file=sys.stderr)
        return 2

    if a.card:
        print(card_lines(card_report()))
        return 0

    if a.calibrate:
        pts = []
        for item in a.calibrate:
            m = re.match(r"\s*([0-9.]+)\s*:\s*(-?[0-9.]+)\s*$", item)
            if not m:
                print(f"REFUSED: {item!r} is not METRES:DBM, e.g. 2.88:-34", file=sys.stderr)
                return 2
            pts.append((float(m.group(1)), float(m.group(2))))
        d = calibrate(pts)
        print(json.dumps(d, indent=1) if a.json else "\n".join(calibration_lines(d)))
        return 2 if "error" in d else 0

    if a.devices_here:
        devs = local_devices(sweep=not a.no_sweep)
        print(json.dumps(devs, indent=1) if a.json else "\n".join(device_lines(devs)))
        return 0

    if a.rate is not None:
        d = decode_rate(a.rate)
        print(json.dumps(d, indent=1) if a.json else "\n".join(rate_lines(d)))
        return 0 if "standard" in d else 2

    if a.watch or a.bssid:
        d = watch(a.watch, a.bssid, samples=a.samples)
        print(json.dumps(d, indent=1) if a.json else "\n".join(watch_lines(d)))
        return 2 if "error" in d else 0

    if a.gui:
        return gui()

    nets, problem = scan()
    if problem:
        print("COULD NOT SCAN: " + problem, file=sys.stderr)
        return 2

    excl = {int(x) for x in re.findall(r"\d+", a.avoid)}
    mine = set(a.mine or ())
    if not mine and not a.count_mine:
        here = current_network()
        if here:
            mine.add(here)
            if not a.json:
                print(f"[excluding your own network {here!r}: an access point does not "
                      f"interfere with itself. Use --count-mine to include it.]\n",
                      file=sys.stderr)
    if a.count_mine:
        mine = set()
    rows, rules = analyse(nets, a.band, avoid=excl, cfg=cfg, mine=mine)
    rec = recommend(rows, a.band, cfg=cfg)

    if a.json:
        print(to_json(nets, rows, rec, rules, a.band, cfg))
    elif a.score is not None:
        print("\n".join(breakdown_lines(score_breakdown(nets, a.score, a.band, cfg, mine))))
    elif a.case is not None:
        print("\n".join(case_for(rows, rec, a.case, rules, cfg)))
    elif a.check is not None:
        print("\n".join(warn_about(rows, a.check)))
    elif a.networks:
        print(network_table(nets, a.band))
    elif a.why:
        print("\n".join(rationale(rows, rec, rules, a.band, cfg)))
    elif a.technical:
        print(text_report(nets, rows, rec, rules, a.band, cfg))
    else:
        here = next((n.channel for n in nets if n.ssid in mine), None)
        print(plain_report(nets, rows, rec, rules, a.band, cfg, here))
    return 0




# ============================================================================
# WATCH ONE NETWORK: the instrument for before-and-after tests
# ============================================================================
# Built 2026-10-13 to answer "did changing the antennas do anything", after a
# cellular reading of -92 then -87 was treated as a 5 dB improvement when it was
# almost certainly drift. Two samples are not a measurement.
#
# TRAP 1: SATURATION. Next to the access point the signal pegs at 99-100% and
#   STOPS RESPONDING. Removing an antenna there can change nothing visible, and
#   the test then "proves" the port is dead when it is not. Measure from where
#   the signal is in the responsive middle, roughly 35-70%.
#
# TRAP 2: ONE SAMPLE IS NOISE. Wifi readings wander by several points with
#   nobody touching anything. Take many, and compare the SPREAD against the
#   difference you think you saw. A change smaller than the spread is not a
#   change.
#
# TRAP 3: THE SSID IS NOT THE RADIO. A hub can put the same name on 2.4 and
#   5 GHz. Watch the BSSID, which is per radio, when you need to be sure.
# ============================================================================
SATURATED = 90      # mean at or above this: the meter has stopped responding
CLIPPING = 95       # ANY sample at or above this: the top of the scale was hit
TOO_NOISY = 12      # spread wider than this: no realistic change could be seen
FLOOR = 15


def watch(ssid=None, bssid=None, samples=8, pause=3.0, sleeper=None):
    """Sample one network's signal repeatedly. -> the numbers AND their spread."""
    import time
    sleeper = sleeper or time.sleep
    seen = []
    for i in range(samples):
        nets, problem = scan()
        if problem:
            return {"error": problem}
        for n in nets:
            if bssid and n.bssid.lower() == bssid.lower():
                seen.append(n.signal)
                break
            if ssid is not None and n.ssid == ssid:
                seen.append(n.signal)
                break
        if i < samples - 1:
            sleeper(pause)
    if not seen:
        return {"error": f"never saw {bssid or ssid!r} in {samples} scans. Check the name "
                         f"exactly, including punctuation and spaces."}
    lo, hi = min(seen), max(seen)
    mean = sum(seen) / len(seen)
    out = {"target": bssid or ssid, "samples": seen, "seen": len(seen), "of": samples,
           "mean": round(mean, 1), "low": lo, "high": hi, "spread": hi - lo, "warnings": []}
    out["spread"] = hi - lo
    # Judge the TOP of the range, not only the average.
    #
    # Found 2026-10-13 by a real run: samples 97,83,83,99,99,81,92,82 averaged
    # 89.5, slipped under a mean-only threshold of 90, and the warning vanished
    # even though half the samples were pegged at the ceiling. A test that goes
    # quiet exactly when it is least reliable is worse than no test.
    if mean >= SATURATED or hi >= CLIPPING:
        where = (f"the average is {mean:.0f}%" if mean >= SATURATED
                 else f"the average is only {mean:.1f}%, but {sum(1 for x in seen if x >= CLIPPING)} "
                      f"of {len(seen)} samples hit {hi}%")
        out["warnings"].append(
            f"SATURATED: {where}, so the meter is clipping at the top of its scale. You are too "
            f"close to the access point. A before-and-after test HERE WILL SHOW NOTHING even if "
            f"the change is real. Move to where it reads roughly 35-70% and start again.")
    if out["spread"] > TOO_NOISY:
        out["warnings"].append(
            f"TOO NOISY: the reading moved {out['spread']} points while nothing was touched. "
            f"Any antenna change you could realistically make is smaller than that, so this spot "
            f"cannot answer the question whatever you do here.")
    if not out["warnings"] and mean <= FLOOR:
        out["warnings"].append(
            f"only {mean:.0f}%, which is near the noise floor where readings jump about. Move "
            f"closer until it reads roughly 35-70%.")
    out["usable_for_comparison"] = not out["warnings"]
    out["smallest_believable_change"] = max(hi - lo, 2)
    return out


def compare_watch(before, after):
    """Did it actually change, or is the difference inside the noise?"""
    if "error" in before or "error" in after:
        return {"error": before.get("error") or after.get("error")}
    diff = after["mean"] - before["mean"]
    noise = max(before["spread"], after["spread"], 2)
    d = {"before": before["mean"], "after": after["mean"], "difference": round(diff, 1),
         "noise": noise}
    if not (before.get("usable_for_comparison") and after.get("usable_for_comparison")):
        d["verdict"] = ("CANNOT SAY: at least one reading was taken where the meter does not "
                        "respond. Redo both from a spot reading roughly 35-70%.")
    elif abs(diff) <= noise:
        d["verdict"] = (f"NO REAL CHANGE. The difference ({diff:+.1f}) is inside the noise "
                        f"({noise}), so it proves nothing either way.")
    else:
        d["verdict"] = (f"REAL CHANGE: {diff:+.1f} points, bigger than the noise ({noise}). "
                        f"{'Better' if diff > 0 else 'WORSE'} after the change.")
    return d


def watch_lines(d):
    if "error" in d:
        return ["REFUSED: " + d["error"]]
    if "verdict" in d:
        return [f"before {d['before']}%   after {d['after']}%   difference {d['difference']:+}",
                f"noise in the readings: {d['noise']}", "", d["verdict"]]
    L = [f"{d['target']}: seen in {d['seen']} of {d['of']} scans",
         f"  samples: {', '.join(str(s) for s in d['samples'])}",
         f"  mean {d['mean']}%   range {d['low']}-{d['high']}%   spread {d['spread']}",
         f"  a change smaller than {d['smallest_believable_change']} points is not believable"]
    for w in d["warnings"]:
        L += ["", "  !! " + w]
    return L


# ============================================================================
# DECODE A LINK RATE: the number a phone shows tells you the whole radio setup
# ============================================================================
# A "connection speed" is not a measurement. It is the negotiated PHY rate, and
# it is a CODE: only certain values are possible, and each one pins down the
# standard, the channel width and the number of spatial streams exactly.
#
# That makes it the cheapest diagnostic there is. A phone showing 2401 Mbps has
# told you it is on 5 GHz, at 160 MHz width, with two working spatial streams,
# on Wi-Fi 6, without you opening anything.
#
# TRAP: it is a CAPABILITY, not throughput. Real data moved is typically 40-60%
# of it, less with distance and interference. Never quote it as a speed.
RATE_TABLE = {
    # rate: (standard, width MHz, streams, band hint)
    72.2: ("Wi-Fi 4 (802.11n)", 20, 1, "2.4 or 5"),
    144.4: ("Wi-Fi 4 (802.11n)", 20, 2, "2.4 or 5"),
    150.0: ("Wi-Fi 4 (802.11n)", 40, 1, "2.4 or 5"),
    300.0: ("Wi-Fi 4 (802.11n)", 40, 2, "2.4 or 5"),
    433.3: ("Wi-Fi 5 (802.11ac)", 80, 1, "5"),
    866.7: ("Wi-Fi 5 (802.11ac)", 80, 2, "5"),
    1733.0: ("Wi-Fi 5 (802.11ac)", 160, 2, "5"),
    286.8: ("Wi-Fi 6 (802.11ax)", 20, 2, "2.4 or 5"),
    573.5: ("Wi-Fi 6 (802.11ax)", 40, 2, "2.4 or 5"),
    600.4: ("Wi-Fi 6 (802.11ax)", 80, 1, "5"),
    1200.9: ("Wi-Fi 6 (802.11ax)", 80, 2, "5"),
    2401.0: ("Wi-Fi 6 (802.11ax)", 160, 2, "5 or 6"),
    4803.0: ("Wi-Fi 6 (802.11ax)", 160, 4, "5 or 6"),
}


def decode_rate(mbps, tolerance=0.02):
    """What radio configuration produces this link rate? -> the setup, or unknown."""
    try:
        mbps = float(mbps)
    except (TypeError, ValueError):
        return {"error": f"{mbps!r} is not a number"}
    best = None
    for rate, (std, width, ss, band) in RATE_TABLE.items():
        if abs(rate - mbps) <= max(rate * tolerance, 1.0):
            best = {"rate": mbps, "matched": rate, "standard": std, "width_mhz": width,
                    "streams": ss, "band": band}
            break
    if not best:
        return {"rate": mbps, "error": ("no standard configuration produces this rate. It may be "
                                        "a vendor extension, or a rate reported mid-negotiation.")}
    best["note"] = ("This is the NEGOTIATED CAPABILITY, not throughput. Real data moved is "
                    "typically 40-60% of it, and less with distance or interference. Never "
                    "quote it as a speed.")
    if best["streams"] >= 2:
        best["proves"] = (f"{best['streams']} spatial streams are working, so {best['streams']} "
                          f"antenna chains on that radio are connected and functioning. A dead "
                          f"chain would have halved this.")
    if best["width_mhz"] >= 80:
        best["band_proof"] = (f"{best['width_mhz']} MHz channels do not exist on 2.4 GHz, so this "
                              f"link is definitely on {best['band']} GHz.")
    return best


def rate_lines(d):
    if "error" in d and "standard" not in d:
        return [f"REFUSED: {d['error']}"]
    L = [f"{d['rate']:g} Mbit/s is {d['standard']}, {d['width_mhz']} MHz wide, "
         f"{d['streams']} spatial stream(s), on {d['band']} GHz"]
    for k in ("proves", "band_proof"):
        if d.get(k):
            L += ["", "  " + d[k]]
    L += ["", "  " + d["note"]]
    return L




# ============================================================================
# WHAT IS ON YOUR OWN NETWORK, AND ROUGHLY HOW FAR AWAY THINGS ARE
# ============================================================================
# Two different questions that people ask in the same breath, with very
# different answers available.
#
# "How many devices are on MY network" is answerable and reliable: they have
# addresses on your own subnet and they answer when spoken to.
#
# "How close is that thing" is answerable only VAGUELY, and the honesty about
# that matters more than the number. Signal strength falls off with distance,
# but it also falls off with walls, furniture, which way the aerial points, how
# much power the other end chose to use, and a person standing in the way. Two
# identical devices at the same distance routinely read 15 dB apart.
#
# So this never prints a single distance. It prints a RANGE, and says what it
# cannot know. A confident number here would be a lie that people would act on.
# ============================================================================

# The signal you would see at ONE METRE, which anchors every distance estimate.
#
# CORRECTED 2026-10-13. This was a hardcoded -40 dBm, invented rather than
# derived, and it was 20 dB wrong: a factor of a hundred in power, which pushed
# every distance estimate about five times too close. Caught when a measured
# -34 dBm was reported as "0.5 to 0.7 metres" and the person pointed out the
# access point was across the room, three or four metres away.
#
# Derived now instead of guessed. Free-space path loss at one metre is
#     FSPL(dB) = 20*log10(frequency in MHz) - 27.55
# which is about 40 dB at 2.4 GHz and about 47 dB at 5 GHz. An access point
# transmits somewhere between 14 and 20 dBm EIRP, so at one metre you should see
# roughly -20 to -26 dBm on 2.4 GHz.
#
# The transmit power is genuinely unknown, so it stays a RANGE and widens the
# answer honestly rather than pretending to a precision nobody has.
TX_EIRP_RANGE = (14.0, 20.0)       # dBm, a weak access point to a legal maximum

# Path loss exponent: 2.0 is open air, 3.0 a normal room, 4.0+ through walls.
#
# It CANNOT be a fixed range, because the plausible values depend on what you
# are reading. A very strong signal is almost certainly line of sight, so open
# air is likely and heavy obstruction is not. A very weak one is almost
# certainly obstructed, so assuming open air produces a nonsense like
# "43 to 883 metres", which is what the first version printed.
PATH_LOSS_BY_SIGNAL = (
    (-45, (2.0, 3.0)),      # strong: probably line of sight
    (-65, (2.5, 3.5)),      # middling: a room or two
    (-999, (3.0, 4.5)),     # weak: it got here through things
)

# Below this there is no useful answer. The spread swamps the estimate, and a
# number nobody can rely on is worse than an honest refusal.
TOO_WEAK_TO_GUESS = -80.0


def path_loss_for(dbm):
    for threshold, rng in PATH_LOSS_BY_SIGNAL:
        if dbm >= threshold:
            return rng
    return PATH_LOSS_BY_SIGNAL[-1][1]


def fspl_at_1m(mhz):
    """Free-space path loss at one metre, dB. Physics, not a fitted constant."""
    import math
    return 20 * math.log10(max(mhz, 1)) - 27.55


def reference_dbm(mhz=2437):
    """-> (weakest, strongest) signal you would expect at one metre."""
    loss = fspl_at_1m(mhz)
    lo, hi = TX_EIRP_RANGE
    return lo - loss, hi - loss


def signal_to_dbm(percent):
    """nmcli reports 0-100. Undo NetworkManager's own mapping to get dBm back."""
    try:
        return (float(percent) / 2.0) - 100.0
    except (TypeError, ValueError):
        return None


def distance_hint(signal_pct, dbm=None, mhz=2437):
    """-> a RANGE in metres, never a single figure, plus what we cannot know."""
    d = dbm if dbm is not None else signal_to_dbm(signal_pct)
    if d is None:
        return {"error": "no signal reading"}
    if d < TOO_WEAK_TO_GUESS:
        return {"dbm": round(d, 1), "near_m": None, "far_m": None,
                "plain": "too weak to estimate a distance from",
                "caveat": (f"below about {TOO_WEAK_TO_GUESS:.0f} dBm the possibilities range "
                           f"from tens of metres through walls to hundreds in the open, and "
                           f"nothing in the reading tells you which. A number here would be "
                           f"invented, so none is given.")}
    lo_n, hi_n = path_loss_for(d)
    weak_ref, strong_ref = reference_dbm(mhz)
    # shortest: assume a WEAK transmitter and heavy attenuation
    # longest:  assume a STRONG transmitter and open air
    near = 10 ** ((weak_ref - d) / (10 * hi_n))
    far = 10 ** ((strong_ref - d) / (10 * lo_n))
    near, far = min(near, far), max(near, far)
    out = {"dbm": round(d, 1), "near_m": round(near, 1), "far_m": round(far, 1)}
    if signal_pct is not None and dbm is None and float(signal_pct) >= 99:
        # the percentage scale tops out, so everything from "quite close" to
        # "touching it" reads 100 and the distance becomes a CEILING, not a value
        out["capped"] = ("the signal reading is at the top of its scale, so this distance is "
                         "an upper limit: it could be very much closer. Read the real dBm for "
                         "anything better.")
    # the words and the numbers must always agree: say the same range, then add
    # what it probably means in terms of the building
    span = f"roughly {near:.0f} to {far:.0f} metres"
    if d >= -40:
        out["plain"] = f"{span}, almost certainly the same room"
    elif d >= -55:
        out["plain"] = f"{span}, same room or the next one"
    elif d >= -70:
        out["plain"] = f"{span}, probably through a wall"
    else:
        out["plain"] = f"{span}, through several walls or a long way off"
    out["caveat"] = ("This is a GUESS from signal strength and it can be wrong by a factor of "
                     "two or three. Walls, furniture, which way the aerial points, how much "
                     "power the other end chose, and a person standing in between all change "
                     "it. Never use it to decide where somebody is.")
    return out


def _arp_table():
    """Everything this machine has recently spoken to. Cross-platform."""
    sysname = platform.system().lower()
    out = []
    if sysname == "linux":
        rc, text = _run(["ip", "neigh"])
        for line in (text or "").splitlines():
            m = re.match(r"(\S+)\s+dev\s+(\S+).*?lladdr\s+([0-9a-f:]{17})\s+(\w+)", line, re.I)
            if m:
                out.append({"ip": m.group(1), "iface": m.group(2),
                            "mac": m.group(3).lower(), "state": m.group(4)})
        if out:
            return out
    # arp -a exists on macOS, Windows, BSD and most Linux installs
    rc, text = _run(["arp", "-a"])
    for line in (text or "").splitlines():
        m = re.search(r"\(?(\d+\.\d+\.\d+\.\d+)\)?\s.*?([0-9a-f]{1,2}(?:[:-][0-9a-f]{1,2}){5})",
                      line, re.I)
        if m:
            mac = ":".join(p.zfill(2) for p in re.split(r"[:-]", m.group(2))).lower()
            out.append({"ip": m.group(1), "iface": None, "mac": mac, "state": "known"})
    return out


# Where a system might keep a manufacturer list. None of these is guaranteed, so
# an unknown vendor is reported as unknown rather than guessed at.
OUI_FILES = ("/usr/share/nmap/nmap-mac-prefixes", "/usr/share/ieee-data/oui.txt",
             "/var/lib/ieee-data/oui.txt", "/usr/share/misc/oui.txt",
             "/usr/share/wireshark/manuf")


def _oui_lookup(macs):
    """-> {oui: vendor} for the prefixes we can resolve from a local file."""
    want = {m.replace(":", "")[:6].upper() for m in macs}
    found = {}
    for path in OUI_FILES:
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    key = line[:8].replace(":", "").replace("-", "").upper()[:6]
                    if key in want and key not in found:
                        rest = line[6:].strip(" \t:-")
                        name = rest.split("\t")[-1].strip() if "\t" in rest else rest.strip()
                        if name:
                            found[key] = name[:40]
        except OSError:
            continue
        if len(found) == len(want):
            break
    return found


def _sweep(subnet, timeout=1, workers=48):
    """Wake every address on the subnet so the ARP table fills in.

    Only ever run against the network this machine is already a member of: these
    are your own devices on your own LAN, which is the question being asked.
    """
    import concurrent.futures
    sysname = platform.system().lower()
    flag = "-n" if sysname == "windows" else "-c"
    wait = ["-w", "1000"] if sysname == "windows" else ["-W", str(timeout)]

    def ping(ip):
        _run(["ping", flag, "1"] + wait + [ip], timeout=timeout + 2)

    hosts = [f"{subnet}.{i}" for i in range(1, 255)]
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(ping, hosts))


def local_devices(sweep=True):
    """How many things are on YOUR network, and what they are. -> a list.

    This is the reliable half of the question. These devices hold addresses on
    your own subnet and answer when spoken to, so the count is real, unlike the
    attached gadgets of a NEIGHBOUR'S network, which are invisible to any scan.
    """
    rc, text = _run(["ip", "-4", "addr"]) if platform.system().lower() == "linux" else (1, "")
    mine = set(re.findall(r"inet (\d+\.\d+\.\d+)\.\d+/\d+", text or ""))
    mine.discard("127.0.0")
    if sweep:
        for subnet in sorted(mine):
            _sweep(subnet)
    rows = [r for r in _arp_table() if r["mac"] != "00:00:00:00:00:00"]
    seen, uniq = set(), []
    for r in rows:
        if r["mac"] in seen:
            continue
        seen.add(r["mac"])
        uniq.append(r)
    vendors = _oui_lookup([r["mac"] for r in uniq])
    for r in uniq:
        key = r["mac"].replace(":", "")[:6].upper()
        r["vendor"] = vendors.get(key)
        r["randomised"] = bool(int(r["mac"][1], 16) & 0x2) if r["mac"][1] in "0123456789abcdef" \
            else False
    uniq.sort(key=lambda r: [int(p) for p in r["ip"].split(".")] if r["ip"].count(".") == 3
              else [999])
    return uniq


def device_lines(devs, subnets=None):
    L = [f"{len(devs)} device(s) on your own network", ""]
    if not devs:
        L.append("  None found. Either nothing else is connected, or this machine is not on")
        L.append("  a network it can sweep.")
        return L
    L.append("  address          manufacturer                      notes")
    L.append("  " + "-" * 68)
    for d in devs:
        notes = []
        if d.get("randomised"):
            notes.append("private address")
        if d.get("state", "").upper() in ("STALE", "FAILED"):
            notes.append("not answering now")
        # an unknown vendor still shows its prefix, so it CAN be looked up. A
        # blank "unknown" is a dead end; the prefix is the thing to search for.
        who = d.get("vendor") or f"unknown (prefix {d['mac'][:8].upper()})"
        L.append(f"  {d['ip']:<16} {who[:33]:<33} {', '.join(notes)}")
    L.append("")
    L.append("  'private address' means the device is deliberately hiding its real hardware")
    L.append("  identity, which modern phones do on purpose. It is a privacy feature, not a")
    L.append("  fault, and it means you cannot tell one visit from another by address alone.")
    L.append("")
    L.append("  This is YOUR network only. It says nothing about the neighbours', whose")
    L.append("  devices are using the same air and cannot be counted by anyone but them.")
    return L




# ============================================================================
# CALIBRATION: turning real measurements into a model for THIS building
# ============================================================================
# The generic model has two unknowns: how hard the transmitter is pushing (the
# reference at one metre) and how fast the signal fades here (the path loss
# exponent). One measurement cannot separate them.
#
# Demonstrated 2026-10-13. A measured -34 dBm at a known 2.88 m gives an
# exponent of 1.70 if the access point is weak and 3.00 if it is strong: from
# "better than open air" to "a furnished room". Same reading, same distance,
# opposite conclusions, because one equation cannot solve for two things.
#
# TWO readings at DIFFERENT known distances fix both. The signal falls linearly
# against log10(distance):
#
#     rssi = reference - 10 * n * log10(distance)
#
# so a straight-line fit gives the reference as the intercept and the exponent
# from the slope. Three or more points also tell you how well the straight line
# fits, which is the only honest measure of whether the model suits the room at
# all.
#
# A calibration is valid for ONE building and ONE access point. It is not a
# property of wifi. Carrying it elsewhere is how a measured number becomes a
# made-up one.
# ============================================================================


def calibrate(points):
    """Fit the reference and the path loss exponent from (metres, dBm) pairs.

    Refuses with fewer than two DISTINCT distances, because that problem has no
    unique answer and any figure returned would be an assumption wearing the
    clothes of a measurement.
    """
    import math
    clean = []
    for p in points:
        try:
            d, rssi = float(p[0]), float(p[1])
        except (TypeError, ValueError, IndexError):
            continue
        if d > 0:
            clean.append((d, rssi))
    if len(clean) < 2:
        return {"error": ("at least two measurements are needed. One reading cannot separate "
                          "how hard the transmitter pushes from how fast the signal fades: "
                          "both change the answer in the same direction.")}
    if len({round(d, 2) for d, _ in clean}) < 2:
        return {"error": ("the measurements must be at DIFFERENT distances. Several readings "
                          "from one spot tell you about the noise, not about the fade.")}

    xs = [math.log10(d) for d, _ in clean]
    ys = [r for _, r in clean]
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    if sxx == 0:
        return {"error": "all distances were identical after rounding"}
    slope = sxy / sxx
    ref = my - slope * mx
    exponent = -slope / 10.0

    resid = [y - (ref + slope * x) for x, y in zip(xs, ys)]
    rms = (sum(r * r for r in resid) / n) ** 0.5

    out = {"points": len(clean), "reference_dbm": round(ref, 1),
           "path_loss_exponent": round(exponent, 2), "fit_error_db": round(rms, 1),
           "warnings": []}
    if exponent < 1.6:
        out["warnings"].append(
            f"an exponent of {exponent:.2f} is below free space, which is not physically "
            f"sensible for a single path. Either a distance is wrong, or reflections are "
            f"adding up along a corridor, or the readings were taken while something moved.")
    if exponent > 5.5:
        out["warnings"].append(
            f"an exponent of {exponent:.2f} is extreme. Check that the distances are right and "
            f"that you did not measure through a floor or a metal door.")
    if n >= 3 and rms > 6:
        out["warnings"].append(
            f"the readings do not sit on a straight line (typically {rms:.1f} dB off). A simple "
            f"fade model does not describe this space well, so treat any distance from it as a "
            f"rough hint only.")
    if n == 2:
        out["warnings"].append(
            "two points define a line exactly, so there is no way to tell whether the line "
            "FITS. Take a third reading at a different distance to find out.")
    out["scope"] = ("valid for THIS access point in THIS building only. It is not a property of "
                    "wifi and must not be carried anywhere else.")
    return out


def calibration_lines(d):
    if "error" in d:
        return ["REFUSED: " + d["error"]]
    L = [f"fitted from {d['points']} measurement(s)", "",
         f"  signal at one metre : {d['reference_dbm']} dBm",
         f"  path loss exponent  : {d['path_loss_exponent']}  "
         f"(2.0 open air, 3.0 a room, 4.0+ through walls)",
         f"  readings sit within : {d['fit_error_db']} dB of the fitted line"]
    for w in d["warnings"]:
        L += ["", "  !! " + w]
    L += ["", "  " + d["scope"]]
    return L


# The entry point stays LAST, always. It was once left mid-file after new
# functions were appended below it, so running the file as a script called
# main() before those functions existed, while importing it as a module worked
# perfectly and hid the bug.
if __name__ == "__main__":
    sys.exit(main())
