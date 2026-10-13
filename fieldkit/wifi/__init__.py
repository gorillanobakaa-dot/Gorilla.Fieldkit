"""Why the wifi is slow, answered from the hardware rather than from guesses.

    fieldkit wifi doctor     the link, the card's real limits, and the verdict
    fieldkit wifi scan       who else is on your channel, and which is clearest
    fieldkit wifi upgrade    can this machine's card be replaced, and is it worth it

# WHY THIS EXISTS

On 2026-10-13 the owner measured 44 Mbps over wifi and expected about 417. Three
hours of router settings would have found nothing, because the cause was not the
router: his card has no 5 GHz radio at all, and 44 Mbps is roughly what real
2.4 GHz gives. The network he was joined to is NAMED "5G-Hub-MH3S" and runs on
channel 6, which is 2.4 GHz.

Nothing about that is unusual. It is the single most common wifi complaint there
is, and the answer is almost always in two numbers that take one second to read.
"""

# ============================================================================
# THE TRAPS. EVERY ONE OF THESE WAS HIT FOR REAL ON 2026-10-13.
# ============================================================================
#
# 1. AN SSID IS NOT A BAND. A network called "5G-Hub", "FRITZ!Box 5GHz" or
#    "SKY-5G" tells you nothing. Read the FREQUENCY. Below 2500 MHz is 2.4 GHz,
#    above 5000 MHz is 5 GHz. The owner was on 2437 MHz on a network with 5G in
#    its name.
#
# 2. iw, dmidecode, ethtool AND rfkill LIVE IN /usr/sbin, WHICH IS NOT ON A
#    NORMAL USER'S PATH ON DEBIAN. `shutil.which("iw")` returns None on a
#    machine that has iw. A doctor written that way tells the user to install
#    packages they already have. Look in /usr/sbin and /sbin too.
#
# 3. nmcli's RATE IS A CAPABILITY, NOT A MEASUREMENT. It reported "540 Mbit/s"
#    on a 1x1 802.11n card whose theoretical ceiling is 150. Never quote it as
#    throughput.
#
# 4. MCS INDEXES TELL YOU THE SPATIAL STREAMS, AND NOTHING ELSE DOES.
#       0-7   = 1 stream      8-15 = 2 streams      16-23 = 3 streams
#    This decides whether a 2x2 card upgrade can help at all.
#
# 5. THE BAND LIST FROM `iw phy` IS THE AUTHORITATIVE ANSWER. "Band 1:" with no
#    "Band 2:" means the card physically cannot do 5 GHz, and no router setting,
#    driver option or firmware will change it.
#
# 6. COMPARE UPLOAD AGAINST DOWNLOAD BEFORE BLAMING THE WIFI. A wifi bottleneck
#    throttles BOTH directions. The owner's tests went 44 to 417 on download
#    while upload stayed at 16 to 21 in every single run, which says the
#    download was above a ceiling the upload never reached. That one comparison
#    identifies the layer at fault before any other work.
#
# 7. dmidecode NEEDS ROOT, and on consumer laptops it often reports NO SLOTS at
#    all. Sony's SVE14 returns an empty Type 9 table. Absence of slot data is
#    not absence of a slot.
#
# 8. A SCAN SEEING NO 5 GHz NETWORKS IS EVIDENCE, NOT PROOF. In a dense street
#    it is strong evidence the card cannot see the band, but read the PHY to be
#    sure. A quiet area could genuinely have none.
# ============================================================================

from .doctor import doctor, lines, exit_code, scan, channel_advice, upgrade  # noqa: E402

__all__ = ["doctor", "lines", "exit_code", "scan", "channel_advice", "upgrade"]
