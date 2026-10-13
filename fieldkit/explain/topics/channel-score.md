# What the channel score means, in plain words

## It is a weight, not a measurement

The score is **not** megabits, **not** decibels, and **not** "percent busy". It
has no units at all.

It is the answer to one question: **how much competition for the air would I
have on this channel?** Built by adding up every network that lands on it,
counting the loud ones more than the distant ones, and counting a partial
overlap double.

So it is a **league table position**, not a reading off an instrument. The only
thing you can do with it is compare it against the other channels **in the same
scan**. A score of 145 tonight and 145 next week do not mean the same thing,
because the neighbours will have changed.

## What the comparison actually tells you

**Lower is better, and the ratio is what matters, not the gap.**

```
channel 13 scores  61
channel 11 scores 105
```

That is **1.7 times** less competition on 13, not "44 points better". The
difference between 61 and 105 matters; the difference between 561 and 605 would
not, even though both are 44.

Roughly what the ratios feel like in daily use:

```
under 1.5x    you will not notice. Do not move for this.
2x to 3x      noticeable in the evening, when everyone is streaming
over 3x       obvious. Pages hang, video drops quality, calls stutter
```

## It predicts WAITING, not slowness

This is the part most people have backwards.

Wifi is one conversation in a room where everyone must take turns. Before a
device transmits it listens, and if someone else is talking it waits. So a busy
channel does **not** make your connection slower in the sense of a weaker
signal. **It makes you queue.**

Your link speed stays exactly the same. You simply get fewer turns, so less of
your data gets through per second. That is why a congested channel feels like
stuttering and pausing rather than a steady slowdown, and why it is much worse
at eight in the evening than at six in the morning.

A loud neighbour counts for more than a distant one because you can hear them,
so you politely wait for them. A distant one you cannot hear does not stop you.

## Why a partial overlap counts double

Channels on 2.4 GHz are five units apart but twenty units wide, so they
overlap. A network two channels away is covering half of your space.

You might think half the overlap is half the problem. **It is worse than a full
overlap.**

- On the **same** channel, two radios hear each other properly and take turns.
  Slower, but orderly.
- **Half** overlapping, neither can make out the other. Each hears only noise.
  Both talk at once, both messages are ruined, both repeat, and the repeats
  collide too.

That is the whole reason channels 1, 6 and 11 exist: they are the only three far
enough apart not to tread on each other. Anything marked `x2` in the breakdown
is being charged for this.

## Your own hub does not count against itself

If you are deciding where to put **your own** access point, your own hub is not
competition. Wherever you move it, **it moves with you.**

Counting it would penalise whichever channel you are already on, every time, and
make the tool keep recommending that you move. This genuinely happened: channel
13 scored 158, of which 89 was the owner's own hub sitting on channel 13. With
it correctly excluded the same channel scored 61.

A **second** access point you own is different. If it is staying where it is, it
is real competition and must still count.

## Seeing the arithmetic

The tool will show you exactly how a score was built:

```
fieldkit wifi channels --score 13
```

You get one line per network: what it is, how loud, how much of your channel it
covers, whether it was charged double, and what it contributed. If a number
looks wrong, that is where you find out why.
