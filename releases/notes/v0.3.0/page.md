# Fieldkit v0.3.0

## What this is

Fieldkit is a set of tested tools that let a small AI model, running on your own laptop, do real work safely:
build software and prove the result, check a student's assignment before it is submitted, keep backups, look after
the laptop itself. Every tool has a card that says what it changes; an AI may only run what the card allows, and a
person approves anything that changes the system. It runs on Windows 11 and on Linux.

This release adds the part that was missing: the ability to look at the connection the laptop is actually using,
explain it in words a person can act on, and say so out loud.

## Should you download it?

Yes, if your wifi is slow and you can change your router's channel. Fieldkit now listens to every network around
you, works out which channel is genuinely least crowded, and then argues the case instead of handing you a number.
It tells you plainly when the honest answer is "leave it alone", which it frequently is.

Yes, if your internet comes through a mobile hub and you are thinking about buying aerials. Fieldkit reads the
bands your hub is actually using and tells you what to buy, including when the cable you would run costs you more
signal than the aerial would gain.

Yes, if you already use Fieldkit v0.2.0: this is 22 changes later and replaces it.

No, if you only want a chat program. Fieldkit is the toolbox an AI uses, not the AI. And no, if your connection is
fine: nothing here will make a working network faster.

## Why this matters

Advice about wifi is handed out as a number with no reasoning attached, usually by someone selling something.
"Use channel 1." "Buy this aerial." You cannot check any of it, so you either obey or you ignore it, and both are
guesses. Every judgement this release makes shows its arithmetic, names the networks it counted, states which of
its numbers were measured and which were reasoned, and includes the argument against its own recommendation.

The second thing: a tool that only prints is no use to someone who is not looking at the screen. Fieldkit now
speaks through a proper neural voice on Linux, wired in at the system level so every program on the machine
inherits it, and it will tell you when the laptop has lost its connection or is cooking itself.

It is free, and stays free.

---
# In plain language: everything in this release

This is the complete explanation, not a summary of one. Nothing below is behind a link.

<!-- plain-language track: in full, on this page -->

### Which wifi channel to use, and the argument for it

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

### Being told honestly that the channel is not your problem

An old wifi adaptor is its own ceiling. If your laptop can only manage one radio stream at a time, no router
setting on earth will move it, and spending an evening changing channels will achieve nothing.

`fieldkit wifi card` reads what your own hardware can actually do, and says UNKNOWN rather than guessing when it
cannot read it. `fieldkit wifi rate --mbps 2401` takes the "connection speed" figure your phone or laptop shows you and
tells you what it means: which wifi generation, how wide a slice of air, how many streams. The 2401 in that example
is Wi-Fi 6 using a 160 MHz channel and two streams, which tells you the radio at the other end is healthy and the
problem is somewhere else.

### How close is that device, roughly

If you ask, the tool will estimate how far away a network is from how loud it sounds. It gives you a range and not
a figure, because a single figure would be a lie: the same signal strength means two metres through a wall or eight
metres across an open room. Below a certain strength it refuses to guess at all, because the honest spread there
runs from tens to hundreds of metres.

If you want a better estimate you can teach it your own building. Stand at two known distances, note the signal
strength, and give it the pairs; it works out how much your particular walls absorb. It refuses to do this from a
single measurement, because one reading cannot separate "far away" from "thick wall".

### Showing somebody your results without naming your neighbours

If you want to put a scan in a forum post, send it to whoever looks after your network, or just show it to a
friend, `--anonymise` replaces every network name with "neighbour 1", "neighbour 2" and so on, and cuts the unique
half off every hardware address. Your own network is labelled as yours, because half the report is about which one
that is.

Nothing else changes. Every signal strength, channel and score is exactly what was measured, so the picture is
still evidence rather than a mock-up. Your neighbours' names are already going out over the air to anyone within
range, which is not the same as being on a web page for ever.

Without the flag you see the real names, which is what makes the tool useful at home in the first place.

### How many devices are on your network

`fieldkit wifi devices` lists what is on your own home network, with the manufacturer where it can tell, and flags
the ones using a randomised address (modern phones do this on purpose, so one device can appear as several). It
only sees your own network, it says so in its own output instead of letting you believe otherwise, and some devices
simply do not answer and will be missed.

### Which connection you are really using

A plugged-in cable can carry nothing at all. This is not a theory: on the machine this was built on, an ethernet
cable was plugged into one hub while every byte of traffic quietly went out over wifi through a different one,
because of a number in a routing table nobody had looked at.

`fieldkit net route` says which connection is actually carrying your traffic, and warns you when there is more than
one router claiming the job. `fieldkit net kind` works out whether you are on a fixed line or something mobile, by
how the connection behaves rather than by asking the router, because a router does not always say and behaviour
cannot misreport itself. `fieldkit net compare` downloads the same file down each connection in turn and tells you
which is faster. That one moves real data, so it announces the fact first, in case you are paying by the megabyte.

### What aerial to buy for a mobile hub

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

### It can speak now

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

### Written answers that cost nothing

`fieldkit explain` prints an explanation of a topic from a file that ships with the program. No AI model is
involved, so it costs nothing to run and works with no internet connection at all. Eight topics so far, including
why some wifi advice is wrong, and why a network name containing punctuation breaks automated tools.

### Odd network names no longer break things

Someone on this street named their network `:(`. That is a legal name, and it broke tool after tool, because a
colon is also the character these tools use to separate one field from another. Fieldkit now parses names chosen by
other people without falling over, whatever is in them, and that is now covered by its own tests: brackets,
punctuation, emoji, right-to-left text, names that look like commands.

This is not a hypothetical tidy-up. It was found because a real neighbour's real network name made real output
wrong.

### Smaller things

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

### What this release does not do

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

---

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

---

# For developers: how it works, and how to check it

Written for someone who will audit, fork or change the code. It covers the same release as the plain-language part above; neither is a summary of the other.

<!-- developer track: in full, on this page -->

### Scope

`v0.2.0..v0.3.0` is 22 commits. The suite is run by the pre-commit hook on every commit (the whole suite plus
`fieldkit privacy scan . --git`) and by `.github/workflows/tests.yml` on `windows-latest` and `ubuntu-latest` for
every pull request. The release workflow publishes only after the same suite passes on both runners for the tagged
commit.

Measured on the build machine (Sony VAIO SVE, i7-3632QM, Debian, Python 3.13): **2029 passed, 56 skipped, 0
failed in 8m08s**. The skips are platform-gated, mostly Windows-only paths; none is a disabled test.

New modules: `fieldkit/wifi/channelsurvey.py`, `fieldkit/net/{route,router,cellular}.py`,
`fieldkit/core/names.py`, `fieldkit/explain/`, `fieldkit/voice/`, `fieldkit/watchdog/`.

### fieldkit.wifi: channel selection that shows its arithmetic

`channelsurvey.py` is 2140 lines, one file, standard library only, Python 3.8 or later. It can be copied to a
machine with no Fieldkit install and run unchanged, which is deliberate: the audience includes people who will be
handed one file.

Pattern: pure functions over a dataclass, with platform dispatch at the single `scan()` boundary. No global state
except module constants. The CLI and the tkinter GUI call identical analysis functions; the GUI holds no logic of
its own. `tkinter` is imported inside `gui()` only, so a machine without it loses the window and nothing else
(exit 4 with the package name to install).

Commands: `fieldkit wifi {doctor,scan,channels,card,upgrade,gui,watch,rate,devices,calibrate}`, plus `--score CH`,
`--case CH`, `--check CH`, `--networks`, `--devices {mixed,local}`, `--mine SSID`, `--count-mine`, `--profile`,
`--technical`, `--band {2.4,5}`, `--avoid`, `--at METRES:DBM`, `--no-sweep`.

`devices` and `calibrate` were reachable only from the standalone file until this release. Writing the
plain-language track found it: the track described `fieldkit wifi devices` and the command did not exist. That is
the second divergence between these two front doors, after the own-AP rule, which made them print different scores
for the same air. `test_both_front_doors_offer_the_same_things` now asserts the parity.

**Every judgement constant is named and carries its reasoning in-source**, because an unexplained constant is a
guess with a variable name:

| Constant | Value | Why |
|---|---|---|
| `PARTIAL_OVERLAP_PENALTY` | `2.0` | A half-overlap defeats carrier sense. Co-channel radios defer to each other; partially overlapping ones cannot decode each other's preambles and transmit over the top. Scoring overlap as a smooth function of channel distance gets this backwards, which is what the quarantined predecessor did. |
| `WORTH_LEAVING_1_6_11` | `2.5` | The margin rule. A non-overlapping channel is a permanent structural advantage; a measured score is a snapshot of one afternoon. A thin win does not justify leaving 1, 6 or 11. |
| `SATURATED` / `CLIPPING` / `TOO_NOISY` | `90` / `95` / `12` | `watch()` refuses a saturated vantage point. The check is on the top of the range and the spread, not the mean: a real run of `97,83,83,99,99,81,92,82` averages 89.5 and ducks under a mean threshold while three samples are pegged. |
| `TX_EIRP_RANGE` | `(14.0, 20.0)` dBm | A weak access point to the legal maximum. Distance is estimated across the range, which is why the answer is a range. |
| `TOO_WEAK_TO_GUESS` | `-80.0` dBm | Below this the honest spread is tens to hundreds of metres, so `distance_hint()` returns `None` with an explanation instead of a figure. |

**Sanitised output.** `--anonymise` (or `FIELDKIT_WIFI_ANONYMISE=1`) is applied inside `scan()`, at the one
boundary every view goes through, so the CLI, the JSON, the GUI and anything written later inherit it without
asking. `anonymise()` relabels SSIDs loudest first and keeps the mapping for the run, so the channel table, the
score breakdown and the overlap warning still agree about which neighbour is which; the BSSID keeps its OUI and
loses the device half. Nothing numeric is touched, and a test asserts `analyse()` returns identical scores either
way. `current_network()` returns the LABEL under sanitising, because the caller compares it with names in the scan
and an unmapped real name would match nothing, putting the own-AP bug straight back.

**Trust boundary.** Everything a scan returns is attacker-controlled: an SSID is arbitrary bytes chosen by a
stranger. SSIDs are never interpolated into a shell string, never compiled as a regular expression and never split
by position. `nmcli -t` output is parsed right to left, because the first field can contain the separator. Numeric
fields are validated before `int()`. Every subprocess takes a fixed argv list; no shell is ever invoked. The only
externally influenced value reaching a subprocess is an IP address in `_sweep()`, built from a validated subnet
prefix.

**Kill switches**, all reversible except the last:

- `load_profile()`: with no `--profile`, neutral `DEFAULTS` apply. No profile is ever auto-selected. The in-source
  TRAP block records why: matching on hostname or driver and loading silently would impose one unusual machine's
  tuning on a normal one. A VAIO profile exists and must be asked for by name.
- `scan()`: returns `(list, problem)`. An empty list is never returned silently; the problem distinguishes "no
  backend for this OS" from "the backend returned nothing".
- `distance_hint()` and `calibrate()`: refuse rather than return an underdetermined figure. `calibrate()` needs two
  distinct distances; one reading cannot separate distance from attenuation.
- `analyse()`: channel 14 is never recommended under any configuration.

**Two bugs worth recording, both found by the tool auditing itself.**

`--score` exists because of the first. The scorer counted the operator's own access point as interference against
itself: 89 points of channel 13's score of 158 came from the radio being placed. `--mine` now defaults to the
currently associated SSID via `current_network()`, and `--count-mine` restores the biased behaviour so the bias can
be demonstrated rather than argued about.

The second was a hardcoded `RSSI_AT_1M = -40.0`, roughly 20 dB wrong, which made every distance estimate wrong by a
large factor. The reference is now derived from free-space path loss, `FSPL(dB) = 20*log10(f_MHz) - 27.55`, with the
path loss exponent selected by signal strength (2.0 to 3.0 open air, up to 4.5 through walls). It was caught by a
user measuring a real distance in ThinkPad widths and comparing.

Tests: `tests/test_wifi_channelsurvey.py`, 96 tests. The GUI test runs under a private Xvfb display, because it
previously drew on the operator's real screen.

### fieldkit.net: establish the path before measuring it

Three independent modules of pure functions sharing no state, each returning a dict with a paired `lines()`
renderer so the CLI never formats domain data itself.

`route.py` (300 lines). `gateways()` returns every default route with interface, metric and a `wins` flag, sorted
by metric; `gateway_warning()` produces a sentence when more than one router claims the default. This exists
because of a measured failure on the development machine: a cable to 192.168.1.1 at metric 900 lost to wifi to
192.168.0.1 at metric 600, so every byte went out over a different hub than the one that was plugged in, and a
throughput measurement down that path would have carried the authority of a number while being wrong.
`unsupported_here()` makes every state-reading action refuse on non-Linux with an explanation and exit 4. These
modules previously swallowed the missing-binary error and returned empty results, so `gateways()` read as "no
default route". The band arithmetic still works everywhere and the refusal says so.

`router.py` (285 lines). `identify()` is an HTTP GET to one address on the local network; the page is untrusted
remote content, pattern-matched for vocabulary and asset names, never executed, never deserialised into objects,
never used to build a request path. `risk()` reads the device's own JavaScript for its lockout policy before any
credential goes anywhere near it. On the hub examined the policy was five failures then 120 seconds per attempt,
and reading it turned an automation plan into a decision to ask the owner for a screenshot. No credential is ever
held, submitted or logged.

`cellular.py` (557 lines). `read_status()` parses a hub status page into bands, RSRP and SINR with grading.
`antenna_spec()` returns required coverage, count, mounting and a cable loss budget; it refuses without `--bands`
rather than producing a specification from no band data. The band string is untrusted text: every token is looked
up in `BANDS` and unrecognised tokens are reported as unknown instead of skipped. It is also treated as unordered,
because carrier order on a hub's status page varies between firmware revisions and carries no reliable meaning.
`--cable` defaults to `RG174`, the thin cable supplied with most kits, deliberately, because that is what people
will use, and a long enough run of it loses more than the aerial gains.

`compare()` transfers real data using `curl --interface`, so it measures without altering routing. It announces
the transfer first. This is listed below as a known weakness: the default byte count should be small enough to be
free on a metered connection.

Tests: `tests/test_net_route.py` (20), `tests/test_net_cellular.py` (16).

### fieldkit.core.names: parse names chosen by someone else

144 lines, extracted because a neighbour's SSID is `:(` and a colon is the `nmcli -t` field separator. nmcli
escapes it as `\:`, so the fix is to unescape before matching and to split from the right. `fieldkit/watchdog`
had the same bug and is fixed in the same release.

Tests: `tests/test_core_names.py`, 17 tests over brackets, punctuation, emoji, right-to-left text and
command-shaped names. The contract is that nothing crashes and nothing is silently mangled.

### fieldkit.voice: a neural voice, wired in at the system level

piper plus `en_US-amy-medium` (60 MB, fetched once), installed by `fieldkit voice install --apply` and registered
as a speech-dispatcher generic module, so every program on the machine that speaks inherits it: the herald, the
laptop alerts, desktop notifications, Orca and Claude Code's own `speechSynthesis`. espeak-ng stays registered as
a fallback so the machine is never mute.

Settled configuration, reached by auditioning eleven models on the actual alert texts:

```
voice             en_US-amy-medium
pace              --length-scale 1.06
pitch             -2.5 semitones, rubberband -F (formants preserved)
sentence silence  --sentence-silence 0.1
audio             sox norm -1, nothing else
```

Four findings are recorded as numbered notes in `fieldkit/voice/__init__.py`, because each one is a trap the next
person will walk into:

- **`sox pitch` is a WSOLA time-stretch plus a resample.** Splice artefacts accumulate, so a long sentence degrades
  while a short phrase sounds fine, and it moves formants, which is how an accent change gets synthesised by
  accident. `rubberband -F` preserves formants. Tune on the sentences the program will actually say, not on test
  phrases.
- **`< file` is not a pipe.** `rubberband` makes two passes and seeks backwards, so it cannot read a pipe. Tested as
  `rubberband - - < in.wav` it looks pipe-safe, because a shell redirect gives seekable stdin. Through a real pipe
  it silently truncated 70700 bytes to 4184. The wrapper uses a temporary file, and a test asserts it stays that
  way. Generalisation: when a tool must live in a pipeline, prove it with `cat x |`, never with `< x`.
- **The audio chain must never silence an alert.** With no `rubberband`, or no writable temporary file, the wrapper
  plays the plain voice. A higher-pitched voice beats no voice when the point of the thing is to speak while the
  laptop is offline. A test asserts the fallback path exists.
- **A calm voice is correct for an emergency.** Written down specifically so a later reader does not "fix" it to
  sound urgent.

`speakable.py` fixes text before anything says it. Hyphens are corrected automatically (a compound becomes a space,
a punctuation dash becomes a comma), because every kind of dash, not only `U+002D`, makes piper insert a halting
pause mid-word. Homophone risks and over-long sentences are reported rather than rewritten. `-40 dBm` and `--json`
survive unchanged.

Alert texts were shortened rather than sped up:

```
before  "The processor is at 91 degrees. That is dangerous. Save your work and close heavy programs."   8.58s
after   "91 degrees. Save your work now."                                                              3.39s
```

No change of pace. Less than half the time, purely by removing words that were not information.

`FIELDKIT_VOICE_SILENT=1` stops the suite speaking aloud while it runs.

Tests: `tests/test_voice.py` (15), `tests/test_voice_speakable.py` (24).

### fieldkit.explain: answers at no model cost

Topics rendered from files that ship with the package. No model call, no network. Eight topics, including
`hostile-names.md` and its Romanian translation. Tests: `tests/test_explain.py`, 12 tests, written after a
`pytest -k explain` run matched unrelated tests in `buildh` and was briefly mistaken for coverage.

### Also in this release

- `fieldkit privacy scan` reports files examined as well as findings. "0 findings" and "0 findings because it never
  ran" were indistinguishable, and that ambiguity was acted on twice in one session. The count now comes back from
  `privacy.scan_path(..., examined=[])`, because only the scanner knows which files it opened: the first version of
  this fix counted the CLI's own path arguments and printed "out of 1 file(s) scanned" for a folder of five.
- `fieldkit/release.py` had two definitions of `check()`. The second wins at import, so the first never ran; it was
  half of `check()` spliced to half of `prove()` and would have raised `NameError` on `runs`, `platform` and `host`
  if it ever had. Four helpers beside it were byte-identical second copies. Nothing failed, because the behaviour
  was correct; the dead copy was simply the one a reader finds first. Removed, and
  `test_no_module_defines_the_same_function_twice` now parses every file under `fieldkit/` and asserts no top-level
  name occurs twice.
- The dual-track glossary check matched a term followed by an em-dash and only that, so removing em-dashes from published
  text (banned here) silently dropped the glossary count to zero and failed the gate. It now accepts a colon or a
  hyphen as well.
- `tests/test_buildh_herald.py` no longer depends on the ambient `FIELDKIT_VOICE_SILENT`. Two tests there pass an
  injected runner and failed when the variable was exported, because the switch is a hard gate that returns before
  the injection point. An autouse fixture clears it; the tests that want it set it themselves. A suite whose result
  depends on the shell it was started from is not a suite.
- `fieldkit shot` refuses to attribute a fault to a layer it has not identified.
- `kits/videokit`: `_xfade_join` renders video and audio in separate passes when `audio and len(files) >= 4`.
  Bisection: 2 and 3 inputs are correct, 4 and above fail; each filter chain alone is correct at 8 inputs. The fault
  is in combining them in one graph, not in `acrossfade`, which was the first and wrong diagnosis.
- `hooks/pre-commit` searches `$VIRTUAL_ENV/bin/python`, `./.venv/bin/python`, `python3`, `python`, and verifies
  each candidate can import pytest, so "could not run the tests" is no longer reported as "tests failed".
- `fieldkit peers`: messages between AI sessions on one computer, using Gorilla OpenCode's protocol (#13).
- Dual-track documentation for `wifi` and `net`, both tracks complete, in `docs/dual-track/`.

### Known weaknesses

Carried from the dual-track documents, unedited, because a limitation discovered later is worth less than one
stated now.

- **MEDIUM.** The Windows, macOS and FreeBSD scan backends are written and have never been executed. "OS agnostic"
  is a design claim for three of the four platforms. Fix: run `scan()` on each and capture real output as a
  fixture.
- **MEDIUM.** 5 GHz analysis is implemented and untested against real 5 GHz air; the development machine has no
  5 GHz radio.
- **MEDIUM.** `router.probe()` and `risk()` have no direct unit tests, and they are the parts that touch a device
  and reason about its login behaviour. Fix: capture a fixture page and assert the parsing.
- **MEDIUM.** `net compare()` transfers real data on a possibly metered connection. It announces this, but a
  smaller default byte count would be safer for the intended audience.
- **LOW.** `PARTIAL_OVERLAP_PENALTY` and `WORTH_LEAVING_1_6_11` are reasoned and unmeasured. They are deliberately
  single defensible constants instead of a fitted curve, because the input is a scan's signal percentage. Fitting
  them would need throughput measurements the tool does not take.
- **LOW.** Wall and cable loss constants are order-of-magnitude estimates, marked as inference in the output.
  Measuring them would need equipment the toolkit does not assume.
- **LOW.** `BANDS` covers United Kingdom carriers only. Unknown tokens are reported rather than dropped, so the gap
  is visible.
- **LOW.** `local_devices()` sweeps with ping. A device that ignores ICMP and has not recently spoken will be
  missed.
- **LOW.** The GUI exposes `decode_rate` but not `watch`, `calibrate` or `devices`.
