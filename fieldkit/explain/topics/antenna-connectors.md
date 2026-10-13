# Telling antenna connectors apart from a photograph

## The rule that decides everything

**Look at the CENTRE of the socket, not at the thread.** The thread tells you
almost nothing, because the two families that matter use the same thread and
screw together perfectly while making no electrical contact.

For a socket mounted on a device (a panel-mount connector):

```
EXTERNAL thread + centre SOCKET (a dark hole)   =  SMA female      -> cellular
EXTERNAL thread + centre PIN    (a gold dot)    =  RP-SMA female   -> wifi
no thread, small push-fit nub                   =  TS9 or MHF/u.FL -> cellular
```

**RP-SMA means "reverse polarity".** The thread is identical to SMA; only the
centre contact is swapped. It exists for a regulatory reason: it was meant to
stop people fitting bigger aerials to wifi gear. In practice it is now simply
the convention for wifi, and SMA is the convention for cellular.

So the centre contact is a strong hint about **which radio the port is wired
to**, because manufacturers follow the convention:

- **Pin in the socket (RP-SMA)** on a consumer device: almost always the **wifi**
  radio.
- **Hole in the socket (SMA)**, or a tiny push-fit **TS9**: almost always the
  **cellular modem**.

It is a convention, not a law, so confirm it before spending real money. But it
is the right starting assumption and it is free to check from a photograph.

## Why this matters more than it sounds

The two connectors **mate mechanically**. An RP-SMA plug screws into an SMA
socket and feels completely solid. Pin meets pin, or hole meets hole, and
**nothing connects**. You get a confident-looking installation and almost no
signal, with nothing visible to diagnose.

This is also why "the antenna fitted, so it must be the right one" is not
evidence. Fit proves the thread matched. It proves nothing about polarity, about
frequency coverage, or about which radio is on the other end.

## Counting the ports

- **Two ports** usually means one radio with 2x2 MIMO.
- **Four ports** usually means either one radio at 4x4, or two radios at 2x2
  each, typically 2.4 GHz on one pair and 5 GHz on the other.

**If the pairs serve different bands, fitting four identical single-band
antennas is a downgrade.** Two of them will be feeding a band they cannot
receive. A 2.4 GHz-only antenna on a 5 GHz chain is close to an open circuit.

For a four-port device of unknown wiring, buy **dual-band (2.4 and 5 GHz)**
antennas, which work whichever pair is which.

## How to settle it by measurement rather than argument

A photograph can identify the connector. It cannot tell you which radio is
behind it. Only a measurement can, and it takes two minutes:

1. From another device, measure the hub's own wifi signal in one fixed spot.
2. Change the antennas. Measure again, same spot, within a few minutes.
3. Separately, read the hub's cellular RSRP and band list before and after.

**Whichever number moves is the radio those ports are wired to.** If the wifi
number moves and the cellular numbers do not, they are wifi ports, and no
cellular antenna will ever help there.

Be careful with the cellular reading in particular: a mobile signal drifts on
its own by several decibels, so a single before-and-after pair proves nothing.
Take several readings each way before believing a change.
