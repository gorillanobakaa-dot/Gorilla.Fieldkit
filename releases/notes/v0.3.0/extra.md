# What it actually looks like

Real runs on the machine this was built on, at full size. Click any picture for the original.

The network names are replaced, and so is the unique half of every hardware address. Those belong to the
neighbours, and a name broadcast down one street is not the same thing as a name indexed by a search engine for
ever. The tool does this itself, with `--anonymise`, and **it changes no measurement**: signal, channel, width and
the scores are exactly as measured. One test asserts the scores come out identical either way, another asserts that
no real name survives into a sanitised report. On your own machine, without the flag, you see the real names, which
is the whole point of it at home.

## The survey, as it prints

[![The sanitised channel survey: it recommends channel 11, says the user is already on 13 and that the two are level tonight so there is no reason to move, then a bar chart of how busy each channel is, who would be competing on each, and the warnings about channels 12 and 13 being illegal in the United States](https://raw.githubusercontent.com/gorillanobakaa-dot/Gorilla.Fieldkit/v0.3.0/docs/screenshots/gallery/wifi-channels-terminal.png)](https://raw.githubusercontent.com/gorillanobakaa-dot/Gorilla.Fieldkit/v0.3.0/docs/screenshots/gallery/wifi-channels-terminal.png)

It recommends channel 11 and then argues against acting on it, because tonight channel 13 scores the same. That is
the honest answer, and most tools will not give it.

## The arithmetic, so you can check it

[![The score breakdown for channel 11: three networks listed by label with the cost each contributed, 34, 24 and 19, totalling 77, and a note that the number has no units and means something only against the other channels in the same scan](https://raw.githubusercontent.com/gorillanobakaa-dot/Gorilla.Fieldkit/v0.3.0/docs/screenshots/gallery/wifi-score-arithmetic.png)](https://raw.githubusercontent.com/gorillanobakaa-dot/Gorilla.Fieldkit/v0.3.0/docs/screenshots/gallery/wifi-score-arithmetic.png)

Every number in the main table comes apart like this. The total at the bottom is the number in the table.

## The window, for anyone who would rather click

[![The wifi channel survey window at 1000 by 680 pixels: a band selector, an exclude-channels box, a profile selector and a Scan button across the top, tabs for Channels, Networks, Why, Overlap check, This radio and Decode a rate, and the full channel table with scores and overlap warnings](https://raw.githubusercontent.com/gorillanobakaa-dot/Gorilla.Fieldkit/v0.3.0/docs/screenshots/gallery/wifi-channels-gui.png)](https://raw.githubusercontent.com/gorillanobakaa-dot/Gorilla.Fieldkit/v0.3.0/docs/screenshots/gallery/wifi-channels-gui.png)

The window and the terminal call the same functions. Neither is a reduced version of the other.

## A connection speed, decoded

[![2401 Mbit/s decoded as Wi-Fi 6, 160 MHz wide, 2 spatial streams, on 5 or 6 GHz, with the notes that two working streams mean two antenna chains are connected and that this is a negotiated capability and not a throughput](https://raw.githubusercontent.com/gorillanobakaa-dot/Gorilla.Fieldkit/v0.3.0/docs/screenshots/gallery/wifi-rate-decoded.png)](https://raw.githubusercontent.com/gorillanobakaa-dot/Gorilla.Fieldkit/v0.3.0/docs/screenshots/gallery/wifi-rate-decoded.png)

## An aerial specification, and the cable that would have ruined it

[![The antenna specification for bands B3, B3, B7, n78 and n78: must cover 1805 to 3800 MHz, buy two not one, mount them cross-polarised, and a refusal of the supplied thin cable because 10 dB of loss over five metres exceeds the gain of almost any affordable aerial](https://raw.githubusercontent.com/gorillanobakaa-dot/Gorilla.Fieldkit/v0.3.0/docs/screenshots/gallery/net-antenna-spec.png)](https://raw.githubusercontent.com/gorillanobakaa-dot/Gorilla.Fieldkit/v0.3.0/docs/screenshots/gallery/net-antenna-spec.png)

The refusal in the middle is the point of the tool. Nobody selling aerials mentions the cable.

---

# The same runs, as text

So they can be read by a screen reader, searched, and pasted into a question somewhere. Sanitised the same way.

## What a channel overlaps, and why a partial overlap is the worse case

`fieldkit wifi channels --check 9`

```
HEADS UP: channel 9 overlaps channels 8, 10, 7, 11, 6, 12.
  More than half-overlapped, so effectively the same channel: 8 (75%), 10 (75%), 7 (50%), 11 (50%)
  Anything on those channels is in your way and you are in theirs.
  Neither side can hear the other well enough to take turns politely,
  which is worse than sharing a channel outright: on the SAME channel
  radios queue for each other. PARTLY overlapping, they talk over each
  other and both retransmit. That is why 1, 6 and 11 exist.

  Who is actually sitting on those overlapping channels right now:
    ch 6    74%  unknown              neighbour 1
    ch 6    42%  unknown              neighbour 2
    ch 6    19%  unknown              neighbour 10
    ch 11   39%  unknown              neighbour 3
    ch 11   32%  unknown              neighbour 6
    ch 11   14%  unknown              neighbour 11
```

The second half is the part anybody can act on: who is actually sitting on those channels right now, and how loud.

## An aerial specification

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

Five metres of the thin coax that comes in the box costs about 10 dB, which is more than an affordable aerial will
give back, so the purchase nets out negative.

## A connection speed, decoded

`fieldkit wifi rate --mbps 2401`

```
2401 Mbit/s is Wi-Fi 6 (802.11ax), 160 MHz wide, 2 spatial stream(s), on 5 or 6 GHz

  2 spatial streams are working, so 2 antenna chains on that radio are connected and functioning. A dead chain would have halved this.

  160 MHz channels do not exist on 2.4 GHz, so this link is definitely on 5 or 6 GHz.

  This is the NEGOTIATED CAPABILITY, not throughput. Real data moved is typically 40-60% of it, and less with distance or interference. Never quote it as a speed.
```
