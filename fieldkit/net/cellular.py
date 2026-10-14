"""Mobile signal, read honestly, and whether an antenna can possibly help.

    fieldkit net cellular read FILE    read a hub's signal page (screenshot or text)
    fieldkit net cellular antenna LO HI   can an antenna covering LO-HI MHz help?

# WHY THIS EXISTS

On 2026-10-13 the owner fitted two 9 dBi dipoles and a 7 dBi ALFA panel to an
EE 5G hub and asked whether they would improve reception. The honest answer came
from one line of the hub's status page:

    Band: B3+B3+B7+n78+n78

Five carriers across 1800, 2600 and 3500 MHz. The antennas cover 2400-2500 MHz,
which is wifi. Not one carrier falls inside that range; the closest is 120 MHz
off and the two that carry most of the capacity are 800 MHz off.

Nobody can do that comparison from memory, and getting it wrong costs money on
the wrong hardware. So it is a tool.

# ============================================================================
# WHAT PEOPLE GET WRONG HERE
# ============================================================================
#
# 1. "dBi" MEANS NOTHING WITHOUT A FREQUENCY. A 9 dBi antenna is 9 dBi at the
#    band it was designed for. Off that band the number is marketing.
#
# 2. A MISMATCHED ANTENNA IS WORSE THAN NO ANTENNA. It reflects rather than
#    receives, and if it displaced a correctly tuned internal one, the chain
#    got worse, not better.
#
# 3. RSRP IS LOUDNESS, SINR IS CLARITY, AND SINR USUALLY DECIDES YOUR SPEED.
#    Throughput swinging 10x on an unchanged RSRP is cell load and interference,
#    not signal strength. A hub that reports no SINR is hiding the useful number.
#
# 4. RSSI OF 0 dBm IS NOT A READING. It is the hub declining to answer. 0 dBm
#    would be a milliwatt arriving at the antenna, which is absurd.
#
# 5. CARRIER AGGREGATION MEANS SEVERAL BANDS AT ONCE, AND THE HIGH ONE USUALLY
#    CARRIES THE CAPACITY. Helping only the low band may change very little.
#
# 6. WIFI ANTENNAS ARE NOT CELLULAR ANTENNAS. The connectors fit. The physics
#    does not.
# ============================================================================
"""
import re

# Downlink frequencies in MHz for the bands a UK hub is likely to report.
BANDS = {
    "B1": (2110, 2170, "2100 MHz 4G"),
    "B3": (1805, 1880, "1800 MHz 4G"),
    "B7": (2620, 2690, "2600 MHz 4G"),
    "B8": (925, 960, "900 MHz 4G"),
    "B20": (791, 821, "800 MHz 4G, the long-range one"),
    "B28": (758, 803, "700 MHz 4G"),
    "B32": (1452, 1496, "1500 MHz, downlink only"),
    "B38": (2570, 2620, "2600 MHz TDD"),
    "B40": (2300, 2400, "2300 MHz TDD"),
    "n1": (2110, 2170, "2100 MHz 5G"),
    "n3": (1805, 1880, "1800 MHz 5G"),
    "n7": (2620, 2690, "2600 MHz 5G"),
    "n28": (758, 803, "700 MHz 5G"),
    "n40": (2300, 2400, "2300 MHz 5G"),
    "n77": (3300, 4200, "3700 MHz 5G NR"),
    "n78": (3300, 3800, "3500 MHz 5G NR, usually where the capacity is"),
}

# RSRP, dBm. Signal LOUDNESS.
RSRP_SCALE = [(-80, "excellent"), (-90, "good"), (-100, "fair"), (-110, "poor"), (-999, "very poor")]
# SINR, dB. Signal CLARITY, and usually what decides your speed.
SINR_SCALE = [(20, "excellent"), (13, "good"), (5, "fair"), (0, "poor"), (-99, "very poor")]


def grade(value, scale):
    if value is None:
        return "not reported"
    for threshold, label in scale:
        if value >= threshold:
            return label
    return scale[-1][1]


def parse_bands(text):
    """'B3+B3+B7+n78+n78' -> the carriers, in order, with duplicates kept."""
    if not text:
        return []
    return [b for b in re.findall(r"\b([Bb]\d{1,2}|n\d{1,3})\b", text)]


def read_status(text):
    """Pull the numbers out of a hub status page, whether typed, pasted or OCR'd."""
    def num(pattern):
        m = re.search(pattern, text, re.I)
        if not m:
            return None
        try:
            return float(m.group(1))
        except ValueError:
            return None

    bands_raw = None
    # [^\n] throughout: an earlier version used \s in the capture, which matches
    # NEWLINES, so "Band B3+B3+B7" swallowed the next two lines and parsed zero
    # carriers. Keep every character class newline-free when scraping a line.
    m = re.search(r"^\s*band[s]?\b[^\n:]*?[:\s]+([A-Za-z0-9+ ]+?)\s*$", text, re.I | re.M)
    if m:
        bands_raw = m.group(1).strip()

    rsrp = num(r"RSRP[^\-0-9]*(-?\d+)")
    rssi = num(r"RSSI[^\-0-9]*(-?\d+)")
    sinr = num(r"SINR[^\-0-9]*(-?\d+)")
    rsrq = num(r"RSRQ[^\-0-9]*(-?\d+)")

    notes = []
    if rssi == 0:
        # trap 4
        notes.append("RSSI reads 0 dBm, which is not a measurement: the hub is declining to "
                     "report it. Ignore it.")
        rssi = None
    if sinr is None:
        notes.append("NO SINR REPORTED, and SINR is usually what decides your speed. RSRP is "
                     "how loud the tower is; SINR is how clearly you hear it through everyone "
                     "else. Speed swinging on a steady RSRP is SINR and cell load moving.")

    net = re.search(r"^\s*Network\s*type[^\n:]*?[:\s]+([A-Za-z0-9]+)\s*$", text, re.I | re.M)
    name = re.search(r"^\s*Network\s*name[^\n:]*?[:\s]+([A-Za-z0-9 ]+?)\s*$", text, re.I | re.M)
    ipv4 = re.search(r"IPv4[^\n:]*[:\s]+(\d+\.\d+\.\d+\.\d+)", text, re.I)

    out = {"network": name.group(1) if name else None,
           "type": net.group(1) if net else None,
           "bands_raw": bands_raw, "carriers": parse_bands(bands_raw),
           "rsrp": rsrp, "rsrp_grade": grade(rsrp, RSRP_SCALE),
           "rsrq": rsrq, "sinr": sinr, "sinr_grade": grade(sinr, SINR_SCALE),
           "rssi": rssi, "notes": notes}

    if ipv4 and ipv4.group(1).startswith(("192.0.0.", "100.64.", "10.")):
        out["notes"].append(f"the WAN address {ipv4.group(1)} is a carrier NAT address, so there "
                            f"are no inbound connections: port forwarding and self-hosting will "
                            f"not work on this connection.")
    return out


def antenna_fit(carriers, ant_low, ant_high, label="this antenna"):
    """Can an antenna covering ant_low-ant_high MHz help the carriers in use?

    The question nobody can answer from memory, and the one that decides whether
    hardware is worth buying.
    """
    rows, usable = [], 0
    for c in carriers:
        key = c if c in BANDS else c.upper() if c.upper() in BANDS else c.lower()
        rng = BANDS.get(key)
        if not rng:
            rows.append({"carrier": c, "known": False, "verdict": "unknown band"})
            continue
        lo, hi, desc = rng
        if hi < ant_low:
            gap, verdict = ant_low - hi, "no, far below the antenna's range"
        elif lo > ant_high:
            gap = lo - ant_high
            verdict = "marginal" if gap <= 100 else "no, far above the antenna's range"
        else:
            gap, verdict = 0, "yes"
        if verdict in ("yes", "marginal"):
            usable += 1
        rows.append({"carrier": c, "known": True, "band_mhz": f"{lo}-{hi}", "what": desc,
                     "gap_mhz": gap, "verdict": verdict})

    total = len([r for r in rows if r.get("known")])
    if total == 0:
        # Nothing was parsed, so nothing was checked. Reporting "covers every
        # carrier" here would be a vacuous pass: silence and success must never
        # look alike. Refuse instead.
        return {"antenna_mhz": f"{ant_low}-{ant_high}", "carriers": rows,
                "usable": 0, "total": 0,
                "summary": ("CANNOT SAY: no recognised carriers were read from the hub. "
                            "Nothing was compared, so this is not a pass. Check the band line "
                            "was captured, e.g. 'Band B3+B3+B7+n78+n78'.")}
    if usable == 0:
        summary = (f"{label} covers {ant_low}-{ant_high} MHz and NOT ONE of the {total} carriers "
                   f"in use falls inside it. It cannot help, and a mismatched antenna reflects "
                   f"signal rather than receiving it. If it displaced a correctly tuned internal "
                   f"antenna, reception is now WORSE.")
    elif usable < total:
        summary = (f"{label} matches {usable} of {total} carriers. Check whether the ones it "
                   f"misses are the high bands, because those usually carry the capacity.")
    else:
        summary = f"{label} covers every carrier in use."
    return {"antenna_mhz": f"{ant_low}-{ant_high}", "carriers": rows,
            "usable": usable, "total": total, "summary": summary}


WIFI_24 = (2400, 2500)


def lines(d):
    if "carriers" in d and "antenna_mhz" in d:
        out = [f"antenna covers {d['antenna_mhz']} MHz", ""]
        for r in d["carriers"]:
            if not r.get("known"):
                out.append(f"  {r['carrier']:<6} unknown band")
                continue
            out.append(f"  {r['carrier']:<6} {r['band_mhz']:>10} MHz  "
                       f"{str(r['gap_mhz']) + ' MHz off':<14} {r['verdict']}")
        out += ["", d["summary"]]
        return out
    out = [f"network: {d.get('network')} {d.get('type') or ''}".rstrip(),
           f"bands:   {d.get('bands_raw') or 'not reported'}   "
           f"({len(d.get('carriers') or [])} carriers aggregated)",
           f"RSRP:    {d.get('rsrp')} dBm   {d.get('rsrp_grade')}   (how LOUD the tower is)",
           f"SINR:    {d.get('sinr')}        {d.get('sinr_grade')}   (how CLEARLY you hear it)"]
    for n in d.get("notes", []):
        out += ["", "  " + n]
    return out


def compare_change(before, after, what="the change"):
    """Did changing something actually help? Compare the RIGHT measurement.

    MEASURED 2026-10-13, and this is the whole reason the function exists. The
    owner fitted 2.4 GHz wifi antennas to a 5G hub, then removed them:

        RSRP        -92 -> -87 dBm      +5 dB, 3.2x the received power
        throughput  median 206 -> 167 Mbps   (six samples each, 20 min apart)

    The signal improved and the speed did not. Both are true. RSRP is an
    instantaneous property of YOUR radio link, so two readings minutes apart with
    one thing changed is a fair test. Throughput is SHARED with everyone on the
    cell, so a six-against-six comparison at different times measures the cell's
    mood, not your change.

    NEVER judge a cellular hardware change by a handful of speedtests. Judge it
    by RSRP and SINR, which is what this does.
    """
    out = {"what": what, "verdict": None, "rsrp_change": None, "sinr_change": None,
           "notes": []}
    if before.get("rsrp") is not None and after.get("rsrp") is not None:
        d = after["rsrp"] - before["rsrp"]
        out["rsrp_change"] = d
        out["power_ratio"] = round(10 ** (d / 10), 1)
        if abs(d) < 2:
            out["notes"].append(f"RSRP moved {d:+.0f} dB, which is inside normal drift. "
                                f"Not evidence either way.")
        else:
            out["notes"].append(f"RSRP moved {d:+.0f} dB, which is {10 ** (abs(d) / 10):.1f}x the "
                                f"received power. That is a real change.")
    if before.get("sinr") is not None and after.get("sinr") is not None:
        d = after["sinr"] - before["sinr"]
        out["sinr_change"] = d
        out["notes"].append(f"SINR moved {d:+.0f} dB. This is the one that usually decides speed.")
    elif after.get("sinr") is None:
        out["notes"].append("No SINR reported, so the measurement that best predicts throughput "
                            "is missing. RSRP alone can improve while speed does not.")

    if before.get("carriers") and before.get("carriers") != after.get("carriers"):
        out["notes"].append(f"THE BANDS CHANGED ({'+'.join(before['carriers'])} -> "
                            f"{'+'.join(after['carriers'])}), so this is not a controlled "
                            f"comparison: the hub moved to different carriers.")

    d = out["rsrp_change"]
    if d is None:
        out["verdict"] = "CANNOT SAY: no RSRP in one of the readings."
    elif d >= 2:
        out["verdict"] = (f"{what} IMPROVED reception by {d:+.0f} dB. Expect it to show as a "
                          f"better FLOOR when the cell is busy, not a higher ceiling when it "
                          f"is quiet.")
    elif d <= -2:
        out["verdict"] = (f"{what} made reception WORSE by {d:.0f} dB. Undo it.")
    else:
        out["verdict"] = f"{what} changed nothing measurable ({d:+.0f} dB)."

    out["never"] = ("Do not judge this with speedtests. Throughput on a shared cell varies by "
                    "10x on its own; it would take tens of samples over hours to see what RSRP "
                    "shows in two readings.")
    return out


# Which carriers survive degrading conditions, worst first. The modem drops the
# fragile ones first, so the COMPOSITION is a quality proxy on hubs that do not
# report SINR.
BAND_ROBUSTNESS = {"n78": 0, "n77": 0, "B7": 1, "n7": 1, "B38": 1, "B40": 1, "n40": 1,
                   "B1": 2, "n1": 2, "B3": 3, "n3": 3, "B8": 4, "B20": 5, "B28": 5, "n28": 5}

NO_SINR_MODELS = ("ee", )          # EE-branded consumer hubs: verified 2026-10-13


def quality_proxy(status, jitter_ms=None, baseline_carriers=None):
    """Estimate link quality on a hub that will not report SINR.

    WHY THIS EXISTS. On 2026-10-13 the owner's EE 5G hub was searched
    exhaustively: 118 KB of its own language file and every script contained
    ZERO mentions of sinr, snr, rsrq, cqi, rscp or "signal quality". The
    firmware has no string for them, so no screen can show them. RSRP and a
    broken RSSI are the only radio numbers it will ever give.

    So quality is inferred from three things the hub DOES give:

      1. WHICH CARRIERS ARE AGGREGATED. The modem keeps a high band only while
         the link can carry it. n78 (3500 MHz) is the first casualty when
         quality drops; B3 (1800 MHz) is the robust fallback. Losing the high
         bands IS the quality reading.
      2. RSRP, which separates "the tower is far" from "the tower is busy". A
         steady RSRP with collapsing throughput is congestion, not distance.
      3. JITTER, from fieldkit net kind. A busier cell schedules you less
         regularly.

    None of these is SINR. Together they answer the question SINR is usually
    asked for: is this me, or is this the cell?
    """
    carriers = status.get("carriers") or []
    if not carriers:
        return {"quality": "unknown", "why": "no carriers parsed; nothing to judge"}

    ranks = [BAND_ROBUSTNESS.get(c, BAND_ROBUSTNESS.get(c.upper(),
             BAND_ROBUSTNESS.get(c.lower(), 2))) for c in carriers]
    fragile = sum(1 for r in ranks if r <= 1)          # high bands still held
    notes = [f"{len(carriers)} carriers aggregated, {fragile} of them high-band "
             f"(the fragile ones the modem drops first)"]

    if fragile >= 3:
        q = "good"
        notes.append("the modem is holding several high bands, which it only does when the "
                     "link can carry them. This is the equivalent of a healthy SINR.")
    elif fragile >= 1:
        q = "fair"
        notes.append("some high bands held, some not. Conditions are middling.")
    else:
        q = "poor"
        notes.append("ONLY ROBUST LOW BANDS LEFT. The modem has dropped everything fragile, "
                     "which is what a low SINR looks like on a hub that will not report one. "
                     "Expect slow speeds and do not blame your own equipment.")

    if baseline_carriers:
        lost = [c for c in baseline_carriers if baseline_carriers.count(c) > carriers.count(c)]
        if lost:
            notes.append(f"CARRIERS LOST since the baseline: {', '.join(sorted(set(lost)))}. "
                         f"That is a real degradation, not noise.")

    rsrp = status.get("rsrp")
    if rsrp is not None:
        if rsrp > -95 and q == "poor":
            notes.append(f"RSRP is {rsrp:.0f} dBm, which is NOT weak. So this is the cell being "
                         f"busy or interfered with, not you being far from the tower. Moving the "
                         f"hub will not fix it; waiting will.")
        elif rsrp <= -100:
            notes.append(f"RSRP is {rsrp:.0f} dBm, which IS weak. Placement, a window, or a "
                         f"correctly tuned antenna would help here.")

    if jitter_ms is not None:
        notes.append(f"jitter {jitter_ms} ms: " +
                     ("the cell is scheduling you irregularly, which means it is busy"
                      if jitter_ms >= 5 else "scheduling is steady, so the cell is not congested"))

    return {"quality": q, "carriers": carriers, "high_bands_held": fragile,
            "rsrp": rsrp, "notes": notes,
            "instead_of_sinr": "this hub reports no SINR; the band composition is the proxy"}


# ============================================================================
# WHAT TO BUY: turning the measured bands into an antenna specification
# ============================================================================
# Written 2026-10-13. The owner asked which antennas to buy to get mobile signal
# through British solid-brick walls, having already proven by measurement that
# the wifi antennas he owned made it WORSE (RSRP -92 with them on, -87 with them
# off: removing them gained 5 dB).
#
# The spec is derivable from the bands the modem reports, so it should be
# computed, not remembered. The TRAPS below are the expensive part: every one of
# them is a way to spend money and end up with less signal than before.
#
# ----------------------------------------------------------------------------
# TRAP A: BUYING ONE ANTENNA KILLS MIMO AND CAN HALVE YOUR SPEED.
#   A modern hub uses at least 2x2 MIMO: two antennas receiving the SAME band,
#   and the modem exploits the difference between them to send two independent
#   data streams. Connect one antenna and the second chain gets nothing, so the
#   modem drops to a single stream. You can lose ~50% of throughput, which no
#   realistic gain figure recovers. ALWAYS BUY A PAIR, and connect both.
#
# TRAP B: TWO ANTENNAS SIDE BY SIDE, SAME WAY UP, ARE NOT MIMO EITHER.
#   MIMO needs the two signals to be DIFFERENT. Mount them cross-polarised
#   (one at +45, one at -45, or one vertical and one horizontal) and separate
#   them by as much space as you can manage. Touching, parallel and identical
#   gives two copies of the same thing and no second stream.
#
# TRAP C: CABLE LOSS AT 3500 MHz EATS THE GAIN YOU PAID FOR.
#   Loss rises with frequency. The thin coax supplied with most antenna kits
#   (RG174 and friends) loses roughly 1-2 dB per metre at n78 frequencies. A 5 m
#   supplied pigtail can therefore consume MORE than a 7 dBi antenna provides,
#   which is how an outdoor antenna ends up worse than sitting at a window.
#   Keep the run short, or use thick low-loss coax, and count the loss BEFORE
#   buying. This is the most commonly ignored number in the whole purchase.
#
# TRAP D: "9 dBi OMNI" IS A SQUASHED DOUGHNUT, NOT FREE SIGNAL.
#   An omnidirectional antenna gets gain by narrowing its vertical beam. It
#   helps only if the tower is near your horizon. If the mast is elevated, or
#   close and above you, a high-gain omni can be worse than a low-gain one.
#   Gain is never created, only redirected.
#
# TRAP E: HIGH-GAIN DIRECTIONAL MUST BE AIMED, AND YOU NEED THE AZIMUTH.
#   A 15 dBi panel may have a 30-degree beam. Pointed 45 degrees off the mast it
#   performs worse than the hub's internal antenna. Buying directional without
#   knowing the tower bearing is buying a worse antenna.
#
# TRAP F: HIGH BANDS DIE IN BRICK FIRST, AND THAT IS THE CAPACITY BAND.
#   Attenuation through masonry rises sharply with frequency, so n78 (3500 MHz)
#   is lost indoors long before B3 (1800 MHz) struggles. Since n78 is usually
#   where the speed is, the fix is a POSITION change, not a gain change: getting
#   the antenna outside or to a window beats any amount of indoor gain.
#
# TRAP G: WIFI CONNECTORS AND CELLULAR CONNECTORS LOOK IDENTICAL AND ARE NOT.
#   Wifi equipment generally uses RP-SMA (reverse polarity: the socket has a
#   pin). Cellular antennas generally ship SMA. They screw together happily and
#   make no electrical contact in the centre. Check the CENTRE of the port, not
#   the thread.
# ============================================================================

# Rough one-way attenuation through a single solid masonry wall, dB. These are
# order-of-magnitude figures for explaining WHY high bands fail indoors, not
# measurements of any particular wall. Marked as inference wherever printed.
WALL_LOSS_DB = [(1000, 6), (2000, 9), (2700, 12), (4000, 18)]

# Loss per metre at ~3.5 GHz for common coax, dB. The number buyers never check.
CABLE_LOSS_DB_PER_M = {
    "RG174 (thin, supplied with most kits)": 2.0,
    "RG58": 1.4,
    "LMR240 / RG8X": 0.75,
    "LMR400 / RG213 (thick)": 0.4,
}


def wall_loss(mhz):
    """Indicative masonry loss at this frequency. model inference, not measured."""
    for ceiling, db in WALL_LOSS_DB:
        if mhz <= ceiling:
            return db
    return WALL_LOSS_DB[-1][1]


def antenna_spec(carriers, cable_m=5.0, cable="RG174 (thin, supplied with most kits)"):
    """From the bands the modem actually reports, what antenna is required.

    Computed rather than recalled, so it stays correct when the carriers change.
    Returns the frequency range that must be covered, how many antennas, and the
    cable budget, with every trap that applies.
    """
    known, unknown = [], []
    for c in carriers:
        key = c if c in BANDS else c.upper() if c.upper() in BANDS else c.lower()
        if key in BANDS:
            lo, hi, desc = BANDS[key]
            known.append({"carrier": c, "low": lo, "high": hi, "what": desc})
        else:
            unknown.append(c)

    if not known:
        # Refuse rather than emit a plausible shopping list for no data. A spec
        # derived from nothing is the expensive kind of wrong.
        return {"error": ("CANNOT SPEC: no recognised bands. Read the hub's band line first "
                          "(e.g. 'B3+B3+B7+n78+n78'); without it any antenna recommendation "
                          "is a guess, and a mismatched antenna is worse than none."),
                "unknown": unknown}

    low = min(k["low"] for k in known)
    high = max(k["high"] for k in known)
    highest = max(known, key=lambda k: k["high"])

    # distinct bands, since B3+B3 is two carriers on ONE band
    bands = sorted({(k["low"], k["high"], k["what"]) for k in known})

    loss_per_m = CABLE_LOSS_DB_PER_M.get(cable, 2.0)
    cable_loss = round(loss_per_m * cable_m, 1)

    out = {
        "carriers_read": [k["carrier"] for k in known],
        "unknown_carriers": unknown,
        "distinct_bands": [{"mhz": f"{lo}-{hi}", "what": w} for lo, hi, w in bands],
        "must_cover_mhz": f"{low}-{high}",
        "buy_this": (f"a wideband cellular MIMO PAIR covering at least {low}-{high} MHz. "
                     f"Commercial wording is usually '4G/5G MIMO 698-3800 MHz', which covers "
                     f"this with headroom for bands you may fall back to."),
        "how_many": 2,
        "why_two": ("the hub uses MIMO: two antennas on the same band, carrying two independent "
                    "data streams. One antenna leaves a chain unfed and the modem falls back to a "
                    "single stream, which can cost half the throughput. No gain figure recovers "
                    "that."),
        "mounting": ("cross-polarised (one at +45, one at -45, or one vertical and one "
                     "horizontal) and as far apart as the cables allow. Two identical antennas "
                     "touching and parallel receive the same signal twice and give no second "
                     "stream."),
        "cable": {
            "assumed_run_m": cable_m, "assumed_type": cable,
            "loss_db_at_3500": cable_loss,
            "verdict": None,
            "alternatives": {k: round(v * cable_m, 1) for k, v in CABLE_LOSS_DB_PER_M.items()},
        },
        "highest_band": {"carrier": highest["carrier"], "mhz": f"{highest['low']}-{highest['high']}",
                         "what": highest["what"],
                         "indicative_wall_loss_db": wall_loss(highest["high"])},
        "notes": [],
        "provenance": ("bands: measured from the hub. Wall and cable loss figures: model "
                       "inference, order-of-magnitude, for explaining the trade-off."),
    }

    if cable_loss >= 7:
        out["cable"]["verdict"] = (f"REFUSE THIS CABLE. {cable_loss} dB of loss over {cable_m} m "
                                   f"exceeds the gain of almost any affordable antenna, so the "
                                   f"whole purchase nets out negative. Shorten the run or use "
                                   f"low-loss coax.")
    elif cable_loss >= 3:
        out["cable"]["verdict"] = (f"{cable_loss} dB lost in the cable, which will cancel much of "
                                   f"a 7-9 dBi antenna. Shorten it or go thicker.")
    else:
        out["cable"]["verdict"] = f"{cable_loss} dB is acceptable."

    out["notes"].append(
        f"POSITION BEATS GAIN. Your highest band is {highest['carrier']} at "
        f"{highest['low']}-{highest['high']} MHz, and masonry costs roughly "
        f"{wall_loss(highest['high'])} dB per wall at that frequency against about "
        f"{wall_loss(1800)} dB at 1800 MHz. That is why the fast band disappears indoors "
        f"first. Getting the antenna OUTSIDE, or onto the window glass facing the mast, is "
        f"worth more than any indoor gain upgrade and costs nothing to test.")
    out["notes"].append(
        "DIRECTIONAL ONLY IF YOU KNOW THE BEARING. A panel or Yagi beats an omni when aimed "
        "and loses to it when not. Find the mast bearing first; until then, buy omni or "
        "wideband panel and test by rotating.")
    out["notes"].append(
        "CHECK THE CONNECTOR CENTRE, NOT THE THREAD. Wifi gear is usually RP-SMA (pin in the "
        "socket), cellular antennas are usually SMA. They screw together and make no centre "
        "contact. Photograph the port and compare before ordering.")
    out["notes"].append(
        "MEASURE BEFORE AND AFTER, same spot, same minute, and compare RSRP AND the band list. "
        "An antenna that raises RSRP while dropping the high band has made things worse. On this "
        "hub, REMOVING mismatched wifi antennas gained 5 dB.")
    if unknown:
        out["notes"].append(f"Not recognised and therefore not covered by this spec: "
                            f"{', '.join(unknown)}. Add them to BANDS before trusting the range.")
    return out


def spec_lines(d):
    if "error" in d:
        return [d["error"]]
    L = [f"bands in use: {', '.join(d['carriers_read'])}", ""]
    for b in d["distinct_bands"]:
        L.append(f"  {b['mhz']:<12} MHz   {b['what']}")
    L += ["", f"MUST COVER: {d['must_cover_mhz']} MHz", "",
          f"BUY: {d['how_many']} of these, not one.", f"  {d['buy_this']}", "",
          f"WHY TWO: {d['why_two']}", "", f"MOUNTING: {d['mounting']}", "",
          f"CABLE ({d['cable']['assumed_run_m']} m of {d['cable']['assumed_type']}): "
          f"{d['cable']['verdict']}"]
    for k, v in d["cable"]["alternatives"].items():
        L.append(f"    {v:>5} dB   {k}")
    L.append("")
    for n in d["notes"]:
        L.append("  * " + n)
    L += ["", d["provenance"]]
    return L
