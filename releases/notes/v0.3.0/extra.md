# What it actually prints

Real output from this release, on the machine it was built on. One thing has been removed: every scan here also
listed the neighbours' network names, and those belong to real people, so they are not reproduced on a public page.
The tool shows them to you, on your screen, and stores and uploads nothing.

## A connection speed, decoded

`fieldkit wifi rate --mbps 2401`

```
2401 Mbit/s is Wi-Fi 6 (802.11ax), 160 MHz wide, 2 spatial stream(s), on 5 or 6 GHz

  2 spatial streams are working, so 2 antenna chains on that radio are connected and functioning. A dead chain would have halved this.

  160 MHz channels do not exist on 2.4 GHz, so this link is definitely on 5 or 6 GHz.

  This is the NEGOTIATED CAPABILITY, not throughput. Real data moved is typically 40-60% of it, and less with distance or interference. Never quote it as a speed.
```

## What a channel overlaps, and why partial overlap is the worse case

`fieldkit wifi channels --check 9`

```
HEADS UP: channel 9 overlaps channels 8, 10, 7, 11, 6, 12.
  More than half-overlapped, so effectively the same channel: 8 (75%), 10 (75%), 7 (50%), 11 (50%)
  Anything on those channels is in your way and you are in theirs.
  Neither side can hear the other well enough to take turns politely,
  which is worse than sharing a channel outright: on the SAME channel
  radios queue for each other. PARTLY overlapping, they talk over each
  other and both retransmit. That is why 1, 6 and 11 exist.

```

Below this point the real run names every network sitting on those channels, with its signal strength and what kind
of device it appears to be. Removed here, for the reason given above.

## An aerial specification, and the cable that would have ruined it

`fieldkit net antenna --bands B3+B3+B7+n78+n78 --cable-m 5`

```
bands in use: B3, B3, B7, n78, n78

  1805-1880    MHz   1800 MHz 4G
  2620-2690    MHz   2600 MHz 4G
  3300-3800    MHz   3500 MHz 5G NR, usually where the capacity is

MUST COVER: 1805-3800 MHz

BUY: 2 of these, not one.
  a wideband cellular MIMO PAIR covering at least 1805-3800 MHz. Commercial wording is usually '4G/5G MIMO 698-3800 MHz', which covers this with headroom for bands you may fall back to.

WHY TWO: the hub uses MIMO: two antennas on the same band, carrying two independent data streams. One antenna leaves a chain unfed and the modem falls back to a single stream, which can cost half the throughput. No gain figure recovers that.

MOUNTING: cross-polarised (one at +45, one at -45, or one vertical and one horizontal) and as far apart as the cables allow. Two identical antennas touching and parallel receive the same signal twice and give no second stream.

CABLE (5.0 m of RG174 (thin, supplied with most kits)): REFUSE THIS CABLE. 10.0 dB of loss over 5.0 m exceeds the gain of almost any affordable antenna, so the whole purchase nets out negative. Shorten the run or use low-loss coax.
     10.0 dB   RG174 (thin, supplied with most kits)
      7.0 dB   RG58
      3.8 dB   LMR240 / RG8X
      2.0 dB   LMR400 / RG213 (thick)

  * POSITION BEATS GAIN. Your highest band is n78 at 3300-3800 MHz, and masonry costs roughly 18 dB per wall at that frequency against about 9 dB at 1800 MHz. That is why the fast band disappears indoors first. Getting the antenna OUTSIDE, or onto the window glass facing the mast, is worth more than any indoor gain upgrade and costs nothing to test.
  * DIRECTIONAL ONLY IF YOU KNOW THE BEARING. A panel or Yagi beats an omni when aimed and loses to it when not. Find the mast bearing first; until then, buy omni or wideband panel and test by rotating.
  * CHECK THE CONNECTOR CENTRE, NOT THE THREAD. Wifi gear is usually RP-SMA (pin in the socket), cellular antennas are usually SMA. They screw together and make no centre contact. Photograph the port and compare before ordering.
  * MEASURE BEFORE AND AFTER, same spot, same minute, and compare RSRP AND the band list. An antenna that raises RSRP while dropping the high band has made things worse. On this hub, REMOVING mismatched wifi antennas gained 5 dB.

bands: measured from the hub. Wall and cable loss figures: model inference, order-of-magnitude, for explaining the trade-off.
```

The refusal in the middle is the point of the tool. Five metres of the thin coax that comes in the box costs about
10 dB, which is more than an affordable aerial will give back, so the purchase nets out negative. Nobody selling
aerials mentions that.
