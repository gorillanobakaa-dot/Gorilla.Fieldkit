# How an antenna actually works, and what it is not

## An antenna is a piece of metal. It is not a radio.

This is the single most useful thing to understand, and it clears up most of the
confusion people have when buying them.

An antenna is **passive**. It has no power supply, no chip, no firmware and no
intelligence. It is a shaped conductor that converts between a signal travelling
inside a cable and a wave travelling through the air. That is the whole job.

Everything clever happens in the **radio** at the other end of the cable: the
chipset, its firmware and its driver. The antenna only decides **which
directions** that radio can hear and shout in.

## It always does both, and identically

People ask whether an antenna transmits or receives. It is always **both**, and
this is not a design choice, it is physics.

The **reciprocity theorem** says an antenna's pattern is exactly the same in
both directions. If it hears 7 dB better towards the north, it also shouts 7 dB
better towards the north. There is no such thing as a receive-only or
transmit-only antenna. The same metal does both at once, with the same shape.

So "will this antenna help me receive?" and "will it help me transmit?" are the
same question with the same answer, always.

## The famous ALFA abilities are NOT in the antenna

This trips up a lot of people, and it is worth being blunt about.

ALFA AWUS adapters are well known for doing things ordinary wifi hardware will
not: listening to all traffic rather than just your own, and injecting frames.
Those abilities come **entirely from the USB adapter's chipset and its Linux
driver**, which are unusually permissive. Realtek and Atheros parts, with
drivers that expose monitor mode and injection.

**None of that lives in the antenna.** The antenna that came in the box is a
plain omnidirectional whip. Screwing it onto a router gives you a plain
omnidirectional whip on a router. It does not give the router any of the
adapter's abilities, because those abilities were never in the metal.

The antennas are good antennas. They are just antennas.

## One box, several radios, and the ports only reach one of them

A modern hub contains **separate radios** that have nothing to do with each
other:

```
2.4 GHz wifi radio    talks to your laptop and your kettle
5 GHz wifi radio      talks to newer devices, faster, shorter range
cellular modem        talks to the mobile network's mast
```

Each one has its own antennas. **External antenna sockets are wired to ONE of
these.** Sockets wired to the wifi radio cannot do anything for the mobile
signal, and sockets wired to the modem cannot do anything for wifi. There is no
shared pool.

So an antenna on a wifi port improves **the network your hub broadcasts**. It
does not improve, and cannot improve, the hub's connection out to the mast.
Telling the two apart is what the connectors guide is for.

## Never leave a MIMO port empty

If a hub has four sockets, they are not four copies of one connection. They are
separate chains, usually two per radio, and the radio uses the **difference**
between its chains to carry more than one stream at once.

**An empty socket is a dead chain.** The radio either loses that stream outright
or radiates badly from an unterminated stub. Either way you can lose around half
the throughput on that radio, which is far more than any antenna gain recovers.

Fit all of them, or at the very least keep each radio's pair complete. Removing
one antenna from a pair is usually worse than having none of them fitted at all,
because the internal antennas are disconnected the moment you screw something
into the socket.

## Why mixing a panel with whips is a bad idea

A flat panel antenna is **directional**: it concentrates its pattern into a cone,
typically a few tens of degrees wide. A whip is **omnidirectional**: a flattened
doughnut covering all compass directions at once.

Put one of each on the same radio and the two chains now see completely
different parts of the room. MIMO wants chains that cover **similar ground by
different paths**, not chains pointing at different places. A panel aimed at a
wall is an antenna receiving a wall, and it drags its chain down while the whip
beside it works normally.

So: **all the antennas on one radio should be the same type.** Mixed sets are a
common and expensive mistake.

## Why you usually cannot tell whether a change helped

Two rules, and breaking either makes the result worthless:

1. **Change ONE thing at a time.** Move the hub, or swap the antennas, never
   both. If two things changed, no measurement can tell you which one mattered,
   and you have spent the effort for nothing.
2. **Measure where the meter still responds.** Close to the hub, the signal pegs
   at maximum and the link rate hits the ceiling of your network card. At that
   point a genuine improvement and no improvement at all look identical. Test
   from the furthest room, or from a device that is visibly struggling.

A device sitting at its own hardware maximum cannot show you an improvement in
anything upstream of it. It is already saying yes to everything.
