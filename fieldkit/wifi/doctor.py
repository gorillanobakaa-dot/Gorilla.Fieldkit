"""Read the link, the card and the air, and say what is actually limiting it."""
import os
import re
import shutil
import subprocess
import sys

# Tools that live in /usr/sbin on Debian and are therefore invisible to a plain
# shutil.which(). See trap 2 in __init__.py.
SBIN_DIRS = ("/usr/sbin", "/sbin", "/usr/local/sbin")


def tool(name):
    """Find a binary even when it is in /usr/sbin and not on PATH. -> path or None."""
    p = shutil.which(name)
    if p:
        return p
    for d in SBIN_DIRS:
        cand = os.path.join(d, name)
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return None


def run(args, timeout=25):
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout
    except Exception:
        return 1, ""


def band_of(mhz):
    """2412 -> '2.4 GHz'. The only honest way to name a band (trap 1)."""
    try:
        mhz = int(mhz)
    except (TypeError, ValueError):
        return "unknown"
    if 2300 <= mhz <= 2600:
        return "2.4 GHz"
    if 4900 <= mhz <= 5900:
        return "5 GHz"
    if 5925 <= mhz <= 7125:
        return "6 GHz"
    return "unknown"


def active_link():
    """The connection in use: ssid, channel, frequency, band, signal."""
    rc, out = run(["nmcli", "-t", "-f", "ACTIVE,SSID,CHAN,FREQ,RATE,SIGNAL", "dev", "wifi"])
    for line in out.splitlines():
        if not line.startswith("yes:"):
            continue
        # Same colon trap as scan(): ACTIVE first, then an SSID that may contain
        # colons, then four fixed fields. Peel the known ends, keep the middle.
        rest = line[4:]
        parts = rest.rsplit(":", 4)
        if len(parts) >= 5:
            ssid, chan, freq, rate, sig = parts[0], parts[1], parts[2], parts[3], parts[4]
            mhz = re.sub(r"[^0-9]", "", freq)
            return {"ssid": ssid.replace("\\:", ":"), "channel": chan, "mhz": mhz,
                    "band": band_of(mhz),
                    # nmcli's rate is a CAPABILITY, not throughput (trap 3). Named
                    # so nobody quotes it as a speed.
                    "advertised_rate": rate, "signal": sig}
    return {}


def card():
    """What the radio actually is, and what it can actually do."""
    info = {"driver": None, "pci_id": None, "name": None,
            "bands": [], "streams": None, "ht40": None, "tools_missing": []}

    # the device
    rc, out = run(["lspci", "-nn"])
    for line in out.splitlines():
        if re.search(r"Network controller|Wireless", line, re.I):
            info["name"] = line.split(": ", 1)[-1].strip()
            m = re.search(r"\[([0-9a-f]{4}):([0-9a-f]{4})\]", line)
            if m:
                info["pci_id"] = f"{m.group(1)}:{m.group(2)}"
            break

    for iface in os.listdir("/sys/class/net"):
        if os.path.isdir(f"/sys/class/net/{iface}/wireless"):
            try:
                info["driver"] = os.path.basename(
                    os.path.realpath(f"/sys/class/net/{iface}/device/driver"))
            except OSError:
                pass
            info["interface"] = iface
            break

    # THE authoritative band answer (trap 5)
    iw = tool("iw")
    if not iw:
        info["tools_missing"].append("iw (sudo apt install iw)")
        return info
    rc, out = run([iw, "phy"])
    info["bands"] = [f"Band {n}" for n in sorted(set(re.findall(r"^\s+Band (\d+):", out, re.M)))]
    # MCS indexes are the ONLY way to read the spatial streams (trap 4)
    m = re.search(r"HT TX/RX MCS rate indexes supported:\s*([0-9]+)-([0-9]+)", out)
    if m:
        top = int(m.group(2))
        info["streams"] = 1 if top <= 7 else 2 if top <= 15 else 3 if top <= 23 else 4
        info["mcs"] = f"{m.group(1)}-{m.group(2)}"
    info["ht40"] = "HT20/HT40" in out
    info["five_ghz_channels"] = len(re.findall(r"\*\s*5[0-9]{3}(?:\.[0-9])?\s*MHz", out))
    info["two_ghz_channels"] = len(re.findall(r"\*\s*2[0-9]{3}(?:\.[0-9])?\s*MHz", out))
    return info


def scan():
    """Every network in range, with the band named honestly."""
    rc, out = run(["nmcli", "-t", "-f", "SSID,CHAN,FREQ,SIGNAL", "dev", "wifi", "list"], timeout=40)
    nets = []
    for line in out.splitlines():
        # An SSID MAY CONTAIN A COLON, and nmcli -t escapes it as "\\:". Splitting
        # left to right therefore shifts every later field and you end up calling
        # int() on "2462 MHz". SSID is the only field that can contain one and it
        # comes first, so split from the RIGHT and whatever is left is the name.
        parts = line.rsplit(":", 3)
        if len(parts) < 4:
            continue
        ssid, chan, freq, sig = parts
        mhz = re.sub(r"[^0-9]", "", freq)
        try:
            signal = int(sig or 0)
        except ValueError:
            continue
        nets.append({"ssid": ssid.replace("\\:", ":") or "<hidden>", "channel": chan,
                     "mhz": mhz, "band": band_of(mhz), "signal": signal})
    return nets


def channel_advice(nets, mine=None):
    """Which 2.4 GHz channel is clearest, counting overlap.

    2.4 GHz channels overlap: 1, 6 and 11 are the only three that do not collide
    with each other, so a network on channel 4 interferes with both 1 and 6. A
    count of exact-channel matches understates the problem, which is why this
    weighs anything within 4 channels.
    """
    busy = {}
    for ch in (1, 6, 11):
        score, who = 0, []
        for n in nets:
            if n["band"] != "2.4 GHz":
                continue
            try:
                c = int(n["channel"])
            except ValueError:
                continue
            gap = abs(c - ch)
            if gap <= 4:
                # a strong neighbour hurts far more than a weak one, and an
                # exactly-overlapping one more than a partial one
                weight = (5 - gap) / 5.0
                score += n["signal"] * weight
                if gap == 0:
                    who.append(f"{n['ssid']} ({n['signal']})")
        busy[ch] = {"score": round(score), "same_channel": sorted(who, reverse=True)}
    best = min(busy, key=lambda c: busy[c]["score"])
    return {"per_channel": busy, "clearest": best, "current": mine}


def upgrade():
    """Can the card be swapped, and would it help? Says what it cannot know."""
    c = card()
    vendor = _dmi("sys_vendor")
    model = _dmi("product_name")
    bios = _dmi("bios_version")
    notes, blockers = [], []

    if c.get("streams") == 1:
        notes.append("the fitted card is 1x1 (single stream). A 2x2 replacement only doubles "
                     "throughput if the laptop has TWO antenna leads wired, which cannot be "
                     "read from software: the cover has to come off and the leads counted.")
    if len(c.get("bands", [])) <= 1:
        notes.append("the fitted card has one band (2.4 GHz). A dual-band replacement is the "
                     "only way to reach 5 GHz on this machine, internally.")

    # dmidecode needs root AND often reports nothing on consumer laptops (trap 7)
    dmi = tool("dmidecode")
    if not dmi:
        blockers.append("dmidecode not found (sudo apt install dmidecode)")
    elif os.geteuid() != 0:
        blockers.append("slot data needs root: sudo dmidecode -t slot")
    else:
        rc, out = run([dmi, "-t", "slot"])
        if "Designation" not in out:
            notes.append("the BIOS reports no expansion slots at all, which is normal on "
                         "consumer laptops and does NOT mean there is no slot.")

    return {"vendor": vendor, "model": model, "bios": bios, "card": c,
            "notes": notes, "cannot_determine_here": blockers + [
                "whether a BIOS whitelist rejects third-party cards (search the model and "
                "BIOS version; many laptops of this era have one)",
                "how many antenna leads are wired (physical inspection only)"]}


def _dmi(name):
    try:
        with open(f"/sys/class/dmi/id/{name}") as fh:
            return fh.read().strip()
    except OSError:
        return None


def doctor():
    """The whole picture, and a verdict naming the limiting factor."""
    if sys.platform == "win32":
        return {"platform": "windows", "status": "not-this-platform",
                "why": "this reads Linux interfaces (nmcli, iw, sysfs)", "ok": True}

    link, c = active_link(), card()
    nets = scan()
    findings, verdict = [], None

    if not link:
        return {"platform": "linux", "status": "not-connected", "card": c,
                "findings": ["not joined to any wifi network"], "ok": False}

    bands = c.get("bands", [])
    single_band = len(bands) <= 1

    # trap 1: the name is not the band
    if re.search(r"5\s*G", link["ssid"], re.I) and link["band"] == "2.4 GHz":
        findings.append(f"the network is called {link['ssid']!r} but you are on "
                        f"{link['mhz']} MHz, which is {link['band']}. The name means nothing.")

    if single_band and link["band"] == "2.4 GHz":
        verdict = ("THE CARD IS THE LIMIT. It has one band (2.4 GHz) and physically cannot "
                   "reach 5 GHz. No router setting, driver option or firmware changes that. "
                   "Real-world 2.4 GHz tops out around 40 to 70 Mbps.")
        findings.append(f"card bands: {', '.join(bands) or 'unknown'} (no 5 GHz radio)")
    elif link["band"] == "2.4 GHz":
        verdict = ("You are on 2.4 GHz but the card supports 5 GHz. Joining the 5 GHz network "
                   "is the single biggest change available.")

    if c.get("streams") == 1:
        findings.append("the card is 1x1 (MCS %s): one spatial stream, so 802.11n peaks at "
                        "150 Mbps and real throughput is far below that" % c.get("mcs", "0-7"))

    adv = channel_advice(nets, link.get("channel"))
    try:
        cur = int(link["channel"])
    except (ValueError, KeyError):
        cur = None
    if cur in adv["per_channel"] and adv["clearest"] != cur:
        findings.append(f"channel {cur} is busier than channel {adv['clearest']}; "
                        f"{len(adv['per_channel'][cur]['same_channel'])} networks share it")

    if c.get("tools_missing"):
        findings.append("could not read the card fully: " + "; ".join(c["tools_missing"]))

    return {"platform": "linux", "status": "ok", "link": link, "card": c,
            "networks_seen": len(nets), "channels": adv, "findings": findings,
            "verdict": verdict or "no single obvious limit found",
            "ok": single_band is False or link["band"] != "2.4 GHz"}


def lines(d):
    if d.get("status") == "not-this-platform":
        return [f"wifi: {d['why']}"]
    if d.get("status") == "not-connected":
        return ["wifi: not joined to any network"]
    L, c = d["link"], d["card"]
    out = [f"connected: {L['ssid']}  ch {L['channel']}  {L['mhz']} MHz  = {L['band']}",
           f"signal:    {L['signal']}%   (nmcli advertises {L['advertised_rate']}, which is a "
           f"capability and NOT a measured speed)",
           f"card:      {c.get('name') or 'unknown'}",
           f"           driver {c.get('driver')}  bands: {', '.join(c.get('bands', [])) or '?'}"
           f"  streams: {c.get('streams') or '?'}",
           ""]
    for f in d["findings"]:
        out.append(f"  - {f}")
    out += ["", "VERDICT: " + d["verdict"], ""]
    ch = d["channels"]
    out.append("2.4 GHz channels, lower is clearer:")
    for k, v in sorted(ch["per_channel"].items()):
        mark = "  <- you" if str(k) == str(ch.get("current")) else ""
        out.append(f"  ch {k:<3} congestion {v['score']:<5} {len(v['same_channel'])} on it{mark}")
    out.append(f"NEXT: clearest is channel {ch['clearest']}")
    return out


def exit_code(d):
    return 0 if d.get("ok") else 3
