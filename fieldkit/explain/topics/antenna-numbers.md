# What the antenna numbers mean, and what they cost you

## dB: the unit that is not a quantity

**A decibel is a ratio, not an amount.** It compares two things, and it is
logarithmic, which is why the numbers look small and matter enormously.

```
 3 dB   = double          -3 dB   = half
 6 dB   = four times      -6 dB   = a quarter
10 dB   = ten times      -10 dB   = a tenth
20 dB   = a hundred      -20 dB   = a hundredth
```

So **10 dB of cable loss is throwing away nine tenths of your signal.** That is
the implication people miss: the number is small, the loss is catastrophic.

Because they are ratios, decibels **add up**. An antenna giving +7, a cable
costing -10 and a wall costing -18 leaves you at **-21 dB**: about one part in a
hundred and twenty-five of what you started with. Every element in the chain
either adds or subtracts, and the total is all that matters.

## dBm: an actual amount of power

Note the **m**. `dBm` is power relative to one milliwatt, so it *is* a real
quantity, and it is always negative for received radio.

```
-70 dBm   strong
-87 dBm   what your hub reads with the antennas off
-92 dBm   what it read with the wrong antennas on
-110 dBm  effectively nothing
```

**Closer to zero is better.** And because it is logarithmic, **-87 is five times
stronger than -92**, not five percent stronger. That five-decibel difference from
simply unscrewing the wrong antennas is a genuinely large change.

## dBi: gain, which is redirection, not amplification

An antenna has no power source. **It cannot create signal. It can only collect
it from some directions instead of others.**

`dBi` compares the antenna to a theoretical one that listens equally in every
direction. So **9 dBi means it is nine decibels better in its best direction**
and necessarily worse everywhere else.

- A **high-gain omni** gets its gain by flattening its pattern into a thin disc.
  Brilliant if the mast is on your horizon. Worse than a cheap antenna if the
  mast is above you, below you, or close by.
- A **directional panel or Yagi** concentrates into a cone, perhaps 30 degrees
  wide at 15 dBi. Aimed correctly it is transformative. Aimed 45 degrees off it
  is worse than the hub's own internal antenna.

**The implication: gain without aim is a downgrade.** Which is why the tool
refuses to recommend directional antennas until you know the mast bearing.

## RSRP: loudness, which is not the same as quality

RSRP is how **loud** the signal arrives, in dBm. It is the number hubs display,
because it is easy.

It is **not** what decides your speed. A loud signal arriving alongside loud
interference is useless. The number that decides speed is **SINR**, the ratio of
wanted signal to everything unwanted, and your hub does not report it: we read
118 KB of its firmware strings and found no mention of SINR anywhere.

**The implication: you cannot optimise what you cannot see**, so on this hub we
use the **band list** as a stand-in. Which leads to the most important number
here.

## The band list: the number that actually tells you the truth

Your hub reports something like:

```
B3 + B3 + B7 + n78 + n78
```

Each entry is a slice of radio spectrum being used at once, which is called
carrier aggregation. More slices, and higher slices, means more speed.

```
B3    1800 MHz    4G,  reliable, travels well, modest capacity
B7    2600 MHz    4G,  more capacity, less reach
n78   3500 MHz    5G,  where nearly all your speed comes from
```

**This list is a better health indicator than RSRP**, because a struggling modem
drops the fragile high bands first and keeps the robust low ones. So:

- **Band list shrinks or loses n78** → things got worse, even if RSRP improved.
- **Band list grows or gains n78** → things got better, even if RSRP looks flat.

**The implication, and it is the one that catches everyone: an antenna that
raises RSRP while dropping n78 has made your internet slower while making the
signal bar look better.** Always compare both.

## Why brick walls attack the band you need most

Radio loses more energy passing through masonry as frequency rises. Roughly, per
solid wall:

```
 900 MHz    about  6 dB   lost
1800 MHz    about  9 dB
2600 MHz    about 12 dB
3500 MHz    about 18 dB   (a factor of sixty)
```

*(Order-of-magnitude figures to show the shape of the problem, not measurements
of your particular wall.)*

A Victorian or Edwardian British house is often double-skin solid brick, nine
inches or more, with no cavity. Two of those between you and the mast costs
around 36 dB at 3500 MHz.

**The implication: n78 is dead indoors before B3 even struggles. The band
carrying your speed is precisely the band the wall destroys.** And no antenna
gain you can buy covers 36 dB.

So the fix is **geometry, not gain**. Move the antenna outside, or onto the
window glass facing the mast, and you remove the wall from the equation entirely.
That is worth more than any purchase and it is free to test.

## MIMO: why one antenna is a downgrade

Your hub does not use one antenna per band. It uses **two or more, and relies on
them hearing slightly different things.** From those differences it reconstructs
two independent data streams at the same time on the same frequency. Two streams,
roughly double the speed.

Connect **one** external antenna and the second chain is fed nothing. The modem
cannot build a second stream, so it falls back to one.

**The implication: you can lose fifty percent of throughput, and no gain figure
recovers it.** A +7 dBi antenna that costs you a MIMO stream leaves you worse off
than the internal antennas you removed. **Always buy and connect a pair.**

And a pair must be genuinely different to work:

- **Cross-polarise them.** One at +45 degrees, the other at -45. Or one vertical,
  one horizontal.
- **Separate them** by as much distance as the cables allow.

Two identical antennas, touching, both vertical, receive the same signal twice.
That is one stream with extra cable loss.

## Cable loss: the number on nobody's shopping list

Coax loses signal per metre, and the loss **rises with frequency**, so it is
worst exactly at 3500 MHz where you need help most.

Per metre at 3500 MHz, approximately:

```
RG174 (the thin stuff in most antenna kits)   2.0 dB/m
RG58                                          1.4 dB/m
LMR240 / RG8X                                 0.75 dB/m
LMR400 / RG213 (thick)                         0.4 dB/m
```

A typical kit ships five metres of RG174. That is **10 dB**, which is nine tenths
of your signal, gone inside the cable.

**The implication: a 7 dBi antenna on five metres of supplied thin coax is a
3 dB net LOSS. You paid money to receive less.** This is why an outdoor antenna
on a long thin cable so often performs worse than simply sitting the hub at a
window.

Keep the run short, or buy thick cable, and **do the addition before ordering.**

## The connector trap

Wifi equipment generally uses **RP-SMA**, where the socket has the pin. Cellular
antennas generally use **SMA**, where the plug has the pin.

They have the same thread. **They screw together perfectly and make no
electrical contact in the centre.** You get a solid mechanical fit and almost no
signal, with nothing to see.

**Check the centre of the port, not the thread.**

## How to tell whether you improved anything

Change one thing. Then, in the same spot and within the same couple of minutes,
read **both** numbers again:

1. **RSRP** in dBm, closer to zero is better.
2. **The band list**, and specifically whether `n78` is still there.

If RSRP improved and the band list shrank, **undo it.** The signal bar went up
and your internet got slower.

This is not theory. On this hub, screwing on a pair of 2.4 GHz wifi antennas
moved RSRP from **-87 to -92**: five decibels worse, a factor of three, because
not one of the bands in use (1800, 2600 and 3500 MHz) falls anywhere near the
2400-2500 MHz those antennas are built for. Taking them off was the upgrade.
