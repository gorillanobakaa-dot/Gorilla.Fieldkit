# Fieldkit v0.3.0, in plain language

## Which wifi channel to use, and the argument for it

Your wifi shares the air with every neighbour's wifi. On the older 2.4 GHz band there are thirteen channels in the
United Kingdom, they overlap each other, and only three of them never tread on anything: 1, 6 and 11. If you are
sitting on a crowded channel your connection does not get weaker, it gets slower, because your devices spend their
time waiting for a turn to speak.

Run `fieldkit wifi channels`. It listens to every network it can hear, scores each channel, and recommends one.
Then it does the part that matters:

- It names who is sharing your air, by network name, and says how hard each one is shouting.
- It shows the arithmetic. `fieldkit wifi channels --score 6` lists every network that contributed to channel 6's
  score and what each one added, and the total at the bottom matches the number in the main table. You can check it
  by hand.
- It argues against itself. `fieldkit wifi channels --case 9` takes a channel you have chosen, compares it with the
  recommendation, and tells you what you know that the tool does not.
- It tells you when the difference is too small to feel, and says to leave the router alone. This is the most common
  honest answer and the tool gives it rather than inventing a reason to act.

There is a window as well as a terminal, if you would rather click: `fieldkit wifi gui`.

**Two warnings it gives by default.** Channels 12 and 13 are legal here and illegal in the United States, so an
American-market smart plug or speaker will not get a slower connection on channel 13, it will not see your network
at all. The tool assumes you might own such a thing unless you tell it otherwise. And a half-overlapping channel is
worse than sharing a channel outright, which is the opposite of what common sense suggests: two radios on the same
channel can hear each other and take turns politely, while two radios half on top of each other cannot hear each
other properly and simply talk over the top. The tool scores it accordingly and explains why.

## Being told honestly that the channel is not your problem

An old wifi adaptor is its own ceiling. If your laptop can only manage one radio stream at a time, no router
setting on earth will move it, and spending an evening changing channels will achieve nothing.

`fieldkit wifi card` reads what your own hardware can actually do, and says UNKNOWN rather than guessing when it
cannot read it. `fieldkit wifi rate --mbps 2401` takes the "connection speed" figure your phone or laptop shows you and
tells you what it means: which wifi generation, how wide a slice of air, how many streams. The 2401 in that example
is Wi-Fi 6 using a 160 MHz channel and two streams, which tells you the radio at the other end is healthy and the
problem is somewhere else.

## How close is that device, roughly

If you ask, the tool will estimate how far away a network is from how loud it sounds. It gives you a range and not
a figure, because a single figure would be a lie: the same signal strength means two metres through a wall or eight
metres across an open room. Below a certain strength it refuses to guess at all, because the honest spread there
runs from tens to hundreds of metres.

If you want a better estimate you can teach it your own building. Stand at two known distances, note the signal
strength, and give it the pairs; it works out how much your particular walls absorb. It refuses to do this from a
single measurement, because one reading cannot separate "far away" from "thick wall".

## Showing somebody your results without naming your neighbours

If you want to put a scan in a forum post, send it to whoever looks after your network, or just show it to a
friend, `--anonymise` replaces every network name with "neighbour 1", "neighbour 2" and so on, and cuts the unique
half off every hardware address. Your own network is labelled as yours, because half the report is about which one
that is.

Nothing else changes. Every signal strength, channel and score is exactly what was measured, so the picture is
still evidence rather than a mock-up. Your neighbours' names are already going out over the air to anyone within
range, which is not the same as being on a web page for ever.

Without the flag you see the real names, which is what makes the tool useful at home in the first place.

## How many devices are on your network

`fieldkit wifi devices` lists what is on your own home network, with the manufacturer where it can tell, and flags
the ones using a randomised address (modern phones do this on purpose, so one device can appear as several). It
only sees your own network, it says so in its own output instead of letting you believe otherwise, and some devices
simply do not answer and will be missed.

## Which connection you are really using

A plugged-in cable can carry nothing at all. This is not a theory: on the machine this was built on, an ethernet
cable was plugged into one hub while every byte of traffic quietly went out over wifi through a different one,
because of a number in a routing table nobody had looked at.

`fieldkit net route` says which connection is actually carrying your traffic, and warns you when there is more than
one router claiming the job. `fieldkit net kind` works out whether you are on a fixed line or something mobile, by
how the connection behaves rather than by asking the router, because a router does not always say and behaviour
cannot misreport itself. `fieldkit net compare` downloads the same file down each connection in turn and tells you
which is faster. That one moves real data, so it announces the fact first, in case you are paying by the megabyte.

## What aerial to buy for a mobile hub

If your internet arrives by mobile signal through thick walls, aerials can help. They can also do nothing, and you
can spend money making it worse.

Give the tool the bands your hub reports and it tells you what the aerial has to cover, how many you need, where to
put it, and what the cable will cost you. That last part is the one the shops do not mention: the thin cable
supplied with most aerial kits loses signal along its length, and a long enough run of it can lose more than the
aerial gains. The tool budgets for it, using that thin cable as the default, deliberately, because that is the
cable people will actually use.

It also explains the two numbers your hub shows. One is how loud the signal arrives, the other is how clear it is,
and they are not the same thing. A loud signal in a noisy place is useless. Aerials help with the first; moving the
hub helps with the second.

And the connectors: there are two families that look almost identical, screw together perfectly, and make no
electrical contact at all. The tool names which one your hub needs.

## It can speak now

Fieldkit speaks through a proper neural voice on Linux, installed with one command. It is wired in at the system
level, so every program on the machine that speaks uses it, not only Fieldkit.

The voice model is about 60 MB and is fetched once. That is a deliberate figure: it is a download an old laptop on
a slow connection can manage, and there are phone apps bigger than that.

Two things it says without being asked. If the laptop loses its connection it says so out loud, because a message
on a screen is no use when the thing that would deliver the message is the thing that broke. If the processor gets
dangerously hot it tells you to save your work.

Both sentences are short on purpose. "The laptop is offline. Switch the hotspot on." takes under four seconds. The
longer, more polite version took nearly nine, said nothing extra, and in an emergency those five seconds are the
whole point. The voice stays calm and measured rather than urgent, which is not an oversight: there is real research
showing a calm voice is understood better under stress, and the aviation industry settled this argument decades ago.

## Written answers that cost nothing

`fieldkit explain` prints an explanation of a topic from a file that ships with the program. No AI model is
involved, so it costs nothing to run and works with no internet connection at all. Eight topics so far, including
why some wifi advice is wrong, and why a network name containing punctuation breaks automated tools.

## Odd network names no longer break things

Someone on this street named their network `:(`. That is a legal name, and it broke tool after tool, because a
colon is also the character these tools use to separate one field from another. Fieldkit now parses names chosen by
other people without falling over, whatever is in them, and that is now covered by its own tests: brackets,
punctuation, emoji, right-to-left text, names that look like commands.

This is not a hypothetical tidy-up. It was found because a real neighbour's real network name made real output
wrong.

## Smaller things

- The privacy check now says how many files it examined, not only how many problems it found. "0 findings" and
  "0 findings in 0 files because it never ran" look identical otherwise, and the difference matters. The first
  attempt at this fix had the same flaw in miniature: it reported "1 file scanned" for a folder of five, because it
  counted the folders it was given rather than the files it read. Fixed, and tested.
- The release machinery contained a second copy of one of its own functions, left by a bad paste. Python accepts
  that silently and uses the last copy, so the first was a decoy: anybody editing it would have changed nothing.
  Nothing was broken, which is exactly why nobody had noticed. There is now a check that looks for this across the
  whole program.
- The screenshot tool refuses to blame a part of the system it has not identified, rather than guessing.
- The video tool renders picture and sound separately once there are more than three clips, which fixes joins that
  were silently coming out wrong.
- The test suite no longer speaks out loud while it runs, and no longer draws windows on your screen.
- The commit check now says "could not run the tests" when it could not run them, instead of "tests failed". Those
  are different problems with different fixes.

## What this release does not do

Said plainly, because a list of limitations is the part that is usually left out:

- The wifi scanning code for Windows, macOS and FreeBSD is written, and has never been run on those systems. It is
  a design claim there, not a tested one. On Linux it is tested.
- The 5 GHz analysis is written and has never been tested against real 5 GHz air, because the machine it was built
  on has no 5 GHz radio.
- The distance estimate, the wall losses and the cable losses are reasoned from physics, not measured with
  equipment. Everything of that kind is marked as an inference in the output, so you can tell which numbers were
  measured and which were worked out.
- The mobile band table covers United Kingdom carriers. Elsewhere, unknown bands are reported as unknown rather
  than quietly dropped, so you can see the gap.
- The voice is Linux only. On Windows the machine's existing voice is used.
