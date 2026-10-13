# Which wifi channel to use, and why most advice is wrong

## What it does

It listens to every wifi network around you, works out which channel is
genuinely the quietest, and then **argues its case** rather than just handing
you a number.

```bash
fieldkit wifi channels
```

You get a table of all thirteen 2.4 GHz channels, how busy each one is, what is
sitting on it, and a plain explanation of the choice. There is a window version
if you would rather click:

```bash
fieldkit wifi gui
```

## The thing nearly every wifi app gets backwards

Wifi channels on 2.4 GHz are **five units apart but twenty units wide**. They
overlap. Picture thirteen people standing in a row one metre apart, each
shouting loudly enough to be heard four metres away.

So a network on channel 4 is interfering with channel 1 **and** channel 6 at the
same time, even though it is on neither. Only channels **1, 6 and 11** are far
enough apart to not tread on each other at all. That is the whole reason you
have heard "use 1, 6 or 11".

Now the part the apps get wrong. You might assume a half-overlap is half a
problem. **It is worse than a full overlap.**

- On the **same** channel, two routers can hear each other. They take turns.
  Slower, but orderly, like two people sharing a phone line.
- **Half** overlapping, neither can quite make out the other, so each one hears
  only meaningless noise. Both talk at once. Both messages are ruined. Both
  repeat themselves. The repeats collide as well.

That is why this tool charges a partial overlap **double**, not half. An earlier
version of it got this wrong and confidently recommended channel 9, then
explained two screens later why channel 9 was a bad idea.

## The second thing: a measurement is not permanent

The tool might find that some odd channel measures slightly quieter tonight.
That is usually a trap.

A score is **one moment**. Your neighbours come home, start streaming, go on
holiday. The non-overlapping property of 1, 6 and 11 is **permanent physics**.

So a small win is not worth taking. If channel 9 is 1.2 times quieter than
channel 11 tonight, one neighbour moving erases that tomorrow and you are stuck
on a structurally worse channel forever. The tool will tell you to stay on 11
and say exactly why. It only recommends leaving 1, 6 or 11 at around **two and a
half times** better, where no realistic change in the neighbours closes the gap.

This is also why the **"Auto"** setting on most routers chooses badly: it looks
once when it starts up, and then never looks again.

## Channels 12, 13 and 14: free parking, with a catch

Channel 12 and 13 are **legal in the UK and Europe, and illegal in the United
States.** They often look gloriously empty for exactly that reason.

That can be a genuine win. It can also be a trap, and the tool is careful to say
which: **a device that cannot legally use channel 13 does not get a worse
connection, it gets no network at all.** It will not even see it. That includes a
lot of American-market kit and a lot of cheap smart-home gadgets that ship with
no region set.

So it is a decision about **your own devices**, not something a scan can answer
for you. The tool presents the trade-off and leaves it to you.

Channel 14 is Japan only, and on top of that it is restricted to the oldest,
slowest wifi standard. The tool refuses to recommend it ever.

## What it cannot see, and says so

**A wifi scan sees routers, not the things connected to them.**

Your neighbour's router announces itself constantly, so the tool sees it. Their
Alexa, their television and their smart fridge do not announce themselves: they
just talk to the router when they feel like it. They are using up the airwaves,
and they are **invisible** to any normal scan.

Reading those needs a special listening mode that most wifi cards will not do
and Windows will not allow at all. Any app claiming to show you every device on
a channel from an ordinary scan is lying to you.

The tool says this out loud on the network list, rather than letting you think
the picture is complete.

## It does not assume anything about your computer

Everything it decides comes from one of three places: **the physics** of how
channels overlap, **the law** about which channels are allowed, or **reading your
actual wifi card**.

```bash
fieldkit wifi card
```

That tells you which channels your radio can really tune, read from the card
itself rather than guessed from its model name. If it cannot read it, it says
"unknown" instead of inventing an answer.

There is a **profiles** system for machine-specific tuning, and it is **never
automatic**. A profile is only ever applied when you type its name, and when one
is active the tool says so on every screen. The default behaviour is neutral and
suits any laptop.

```bash
fieldkit wifi channels --list-profiles
fieldkit wifi channels --profile single-band-1x1   # tuned for a class of card
```

This matters because the machine the tool was written on is unusual: a 2012
laptop with a single-band radio and a Japanese regulatory history. Anything tuned
for that is wrong on a normal computer, and **silently wrong is the worst kind**.

## Checking a specific channel

If you have a channel in mind, ask about it directly:

```bash
fieldkit wifi channels --check 13
```

It names every channel that collides with it, says by how much, lists who is
actually sitting on those channels at this moment, and flags any legal catch.

## Who is actually in your airspace

The score says how much competition there is. This says **who**:

```bash
fieldkit wifi channels
```

Under the chart you get each network by name, how hard it is shouting in plain
words rather than decibels, and whether it is sitting on top of you or only half
over you:

```
  CHANNEL 13, where you are now:
    talking    SKYGVML1           on channel 11, 50% over you
    murmuring  AJ-2               on channel 11, 50% over you
```

Where a name gives away what something is, it says so: `[voice assistant]`,
`[smart TV]`, `[camera or doorbell]`. That is a hint from the name, never an
identification.

It also states plainly what it **cannot** see: the televisions, speakers and
doorbells attached to those networks are using the same air but answer rather
than announce, so no ordinary scan can count them. Assume several times more
than the list shows.

## How many devices are on your own network

```bash
fieldkit wifi channels --devices-here
```

This is the reliable half of "how many things are there". Devices on your own
network hold addresses on your own subnet and answer when spoken to, so the
count is real.

An unknown manufacturer still shows its address prefix, because a bare
"unknown" is a dead end and the prefix is the thing you can search for.
Addresses marked **private** are devices deliberately hiding their hardware
identity, which modern phones do on purpose. That is a privacy feature working
correctly, not a fault, and it means you cannot tell one visit from another by
address alone.

## Roughly how far away something is

The tool will estimate a distance from signal strength, and it will never give
you a single number:

```
  -34 dBm   roughly 2 to 5 metres, almost certainly the same room
  -70 dBm   roughly 9 to 46 metres, probably through a wall
  -85 dBm   too weak to estimate a distance from
```

Walls, furniture, which way an aerial points, how much power the other end
chose, and a person standing in between all change it. Two identical devices at
the same distance routinely read 15 dB apart. Below about -80 dBm it refuses
entirely, because the honest answer there spans tens of metres through walls to
hundreds in the open and nothing in the reading tells you which.

**Never use it to decide where somebody is.**

### Making it better for your own building

If you can measure some real distances, the tool will fit the model to your
space:

```bash
fieldkit wifi channels --calibrate 1.0:-34 --calibrate 2.9:-51 --calibrate 0.5:-28
```

Each one is `metres:dBm`. You need at least **two different distances**, because
one reading cannot separate how hard the transmitter pushes from how fast the
signal fades: both move the answer the same way. It refuses rather than guessing.

Give it **three**, because two points define a line exactly and so can never
tell you whether a line fits at all. The third is what reveals whether the model
suits your room, and the tool keeps saying so until you provide one.

A calibration is valid for one access point in one building. It is not a
property of wifi and must not be carried anywhere else.

## Testing whether a change helped

```bash
fieldkit wifi channels --watch ':(' --samples 8
```

It samples one network repeatedly and reports the **spread** as well as the
average, then refuses to be trusted where it cannot work:

- **Saturated**: close to the access point the meter pegs at the top of its
  scale and stops responding. A before-and-after test there shows nothing even
  when the change is real. It checks the top of the range, not just the average,
  because an average can duck under a threshold while half the samples are
  pegged.
- **Too noisy**: if the reading wanders more than any realistic change would
  produce, that spot cannot answer the question whatever you do.

Mind the quotes if your network name contains shell characters.

## Decoding the speed your device reports

```bash
fieldkit wifi channels --rate 2401
```

A "connection speed" is not a measurement, it is a negotiated code, and only
certain values are possible. Each one pins down the standard, the channel width
and the number of working antenna chains exactly:

```
2401 Mbit/s is Wi-Fi 6, 160 MHz wide, 2 spatial streams, on 5 GHz
  2 spatial streams are working, so 2 antenna chains are connected and
  functioning. A dead chain would have halved this.
```

That makes it the cheapest diagnostic there is: it will tell you whether
removing an antenna broke something without opening anything.

It is a **capability**, not throughput. Real data moved is typically 40 to 60
per cent of it.

## If you want the engineering view

```bash
fieldkit wifi channels --technical
```

The full table of every channel with raw scores, counts and notes. The default
view is deliberately the plain one.

## Running it somewhere else

It is a single file that uses nothing but what comes with Python. Copy
`fieldkit/wifi/channelsurvey.py` onto any computer with Python and run it:

```bash
python3 channelsurvey.py
python3 channelsurvey.py --gui
```

It knows how to ask Linux, macOS, Windows and FreeBSD for a wifi scan, and
picks the right one by itself. There is nothing to install.

On Windows:

```powershell
python channelsurvey.py
python channelsurvey.py --gui
```

If the window will not open on Linux, some distributions package Python's window
toolkit separately. The tool tells you the exact command, and everything still
works in the terminal without it.
