# Which wifi channel to use, who else is on it, and why your wifi is slow. Plain Language Guide

> Generated 2026-10-13 from `wifi`

---

## Should You Run This?

Yes, if your wifi is slow or unreliable and you can change your router's channel. It reads only, changes nothing, sends nothing anywhere, and will honestly tell you when the answer is 'leave it alone', which is frequently. Do not bother if your connection is fine, and do not expect it to help if the slowness is your internet line instead of your wifi: it measures the air in your home, not the connection into it.

## Worst Case, Honestly

You move your router to the channel it recommends, and a device in your home stops working entirely. This is a real risk on channels 12 and 13, which are legal in the UK and Europe and illegal in the United States: an American-market smart plug or speaker does not get a slower connection on channel 13, it cannot see the network at all. The tool warns about this by default and will not assume otherwise unless you explicitly tell it every device you own can use those channels. The second, smaller harm is wasted effort: acting on a difference the tool has told you is too small to feel, and spending an evening reconfiguring a router for no gain.

## What Data This Touches

Nothing leaves your machine. There is no account, no server, no telemetry, and no internet connection is used at any point in the survey. The tool asks your own operating system two questions: what wifi networks can you hear, and what can this computer's own wifi card do. It prints the answers on your screen and forgets them when it exits.

The network names it shows belong to your neighbours, and they are already being broadcast publicly to everyone within range; your phone sees the same list every time you open its wifi settings. The tool does not store them, upload them, or write them to any file.

The optional device listing covers only your own home network, and only the addresses your own computer has already spoken to. It cannot see inside anybody else's network, and it says so in its own output instead of letting you believe otherwise.

## Before You Trust It

This tool is about to tell you to reconfigure your router. Before you do that, it is worth spending two minutes confirming it is reading your actual surroundings and not inventing them.

**Step 1:** Run `fieldkit wifi channels --networks` and read the list of network names.
  - Look for: You should recognise your own network and some neighbours. If the list is empty or full of names you have never seen, something is wrong.
**Step 2:** Run 'fieldkit wifi card'.
  - Look for: It should describe your actual wifi hardware. If it cannot read it, it will say UNKNOWN instead of guessing, which is the correct behaviour.
**Step 3:** Run 'fieldkit wifi channels --score 6' to see the arithmetic behind one channel.
  - Look for: Every network contributing to the score is listed by name with what it added. The total at the bottom should match the number in the main table.
**Step 4:** Run the whole thing again twenty minutes later.
  - Look for: The numbers will move a little. If the recommendation swings wildly between runs, the air is too unsettled to act on and the tool should be telling you that.

## The Big Picture

Your wifi shares the air with every neighbour's wifi. On the 2.4 GHz band there are thirteen channels, they overlap each other, and only three of them never tread on anything. If you are sitting on a crowded one, your connection does not get weaker: it gets slower, because your devices spend their time waiting for a turn to speak.

This tool listens to every network around you, works out which channel is genuinely quietest, and then argues its case instead of handing you a number. It names who is sharing your air, says how hard each one is shouting, and tells you plainly when the answer does not matter enough to be worth acting on.

It also answers the other half of the question: whether the slowness is the air at all. Often it is not. Often it is the wifi card inside your own computer, which no router setting can change. The tool reads that card and says so.

## Key Concepts

| Name | What It Means | Real-World Comparison |
|------|--------------|------------------------|
| `Channel` | One of thirteen slots your wifi can sit in on the 2.4 GHz band. | Thirteen people standing in a row one metre apart, each shouting loudly enough to be heard four metres away. They are not in separate rooms. |
| `Overlap` | Channels are spaced closer together than they are wide, so neighbouring channels sit on top of each other. | Two radio stations whose signals bleed into each other, so you hear both and understand neither. |
| `Score` | A made-up number showing how much competition a channel has. Lower is better. It has no units and only means something compared with the other channels in the same scan. | A league table position. Being third tells you something; the number 3 by itself tells you nothing. |
| `Spatial stream` | A separate conversation your wifi can hold at the same time as another one, using more than one aerial. | Two people talking at once on two phone lines, instead of taking turns on one. |
| `dBm` | How loud a signal arrives. Always a negative number, and closer to zero is stronger. | Like temperature below freezing: minus 5 is warmer than minus 20. |
| `Access point` | The thing broadcasting a wifi network: your router, hub, or a range extender. | The person doing the talking in a crowded room. The tool can hear every talker, but not the people quietly listening to them. |

## How It Works, Step by Step

### Step 1: Ask the operating system what is on the air

It runs the same scan your laptop does when you click the wifi icon, and reads back a list of networks with their names, channels and signal strengths. On Linux it uses the standard network tools; it knows the equivalent commands for Windows, macOS and FreeBSD and picks the right one by itself.

### Step 2: Work out who lands on each channel

For each of the thirteen channels it asks: if I sat here, which networks would be in my way? Because channels overlap, the answer includes networks that are not on that channel at all. A network two channels away is covering half your space.

### Step 3: Charge a partial overlap double

This is the part almost every wifi app gets backwards. If two routers share a channel exactly, they can hear each other and take turns, which is orderly. If they only half overlap, neither can make out the other, so both talk at once, both messages are ruined, and both repeat themselves. A half overlap is worse than sharing outright, so it counts for more, not less.

### Step 4: Leave your own router out of it

Your own hub is not competition for itself. Wherever you move it, it moves with you. Counting it would make whichever channel you are already on look bad, every single time, and push you to keep moving. It is excluded automatically, and the tool says so.

### Step 5: Apply judgement, not arithmetic

The lowest number does not always win. Channels 1, 6 and 11 never overlap anything, and that is permanent, while a quiet evening is not. So an odd channel has to be about two and a half times better before the tool recommends leaving the safe three. Otherwise it tells you to stay put and explains why.

### Step 6: Check the law and your own hardware

It reads what your own wifi card can actually tune, instead of assuming. It refuses channel 14 outright, which is Japan-only and stuck at ancient speeds. And it flags channels 12 and 13 as a decision only you can make, because it concerns the devices in your house and no scan can see those.

### Step 7: Say who is actually there

Instead of only a number, it lists the networks sharing your air by name, how hard each is shouting in plain words, and whether it is directly on top of you or half over you.

### Step 8: Say what it cannot know

Two facts decide the answer and neither can be measured from outside: whether every device in your home can legally use channels 12 and 13, and how long tonight's quiet will last. The tool asks you for the first and refuses to assume it, because an American-market gadget on channel 13 does not get a slower connection, it gets no network at all. For the second it tells you honestly that one scan is one moment, and that this is also why the Auto setting on most routers picks badly: it looks once at startup and never again.

### Step 9: Show its own arithmetic on request

Ask it to explain any channel's number and it prints every network that contributed, what each one added, and whether it was charged double for a partial overlap. This is not decoration. It is how a real mistake in the tool was found: it had been counting the maintainer's own router as interference against the very channel that router was sitting on, which quietly pushed every recommendation away from wherever you already were.

## Quirky Things Worth Knowing

### A half overlap is worse than a full one

Everyone assumes half the overlap means half the problem. It is the opposite. On the same channel the two routers hear each other and queue politely. Half overlapping, each hears only noise it cannot decode, so both transmit at once and both have to repeat. That single fact is the entire reason channels 1, 6 and 11 exist.

### The tool will tell you to ignore its own recommendation

If the difference is small, it says so in plain words: at 1.2 times you would not feel it either way, so staying where you are costs nothing. A tool that always says 'move' is selling you work.

### A busy channel does not weaken your signal, it makes you queue

Your connection speed stays the same. You get fewer turns to use it. That is why congestion feels like stuttering and pausing instead of a steady slowdown, and why it is much worse at eight in the evening than six in the morning.

### It cannot see the Alexas, televisions and doorbells

A wifi scan sees routers, which announce themselves constantly. The gadgets connected to them only speak when spoken to, so they are invisible, even though they are using the same air. Any app claiming to list every device on a channel is lying. This tool says so on the page instead of letting you believe the picture is complete.

### Channel 13 is empty for a reason that may not apply to you

It is quiet because it is illegal in the United States, so almost nothing uses it. For a household where every device is British or European that is free space nobody can compete with you for. For a household with one American-market gadget it is a device that vanishes.

### Your laptop may be too close to measure anything

Near the router the signal meter pegs at the top of its scale and stops responding, and your wifi card may already be running at its maximum speed. At that point a genuine improvement and no improvement look identical. The tool refuses to be trusted there and tells you to move further away.

## What This Means For You

### Battery, Processor & Memory

Negligible. A scan takes a few seconds and the analysis is simple arithmetic over a list of at most a few dozen networks. Nothing runs in the background and nothing stays resident after the command finishes. Not measured precisely.

### Speed

The tool itself changes nothing about your connection. It only tells you what to change. Whether acting on it makes anything faster depends entirely on how congested your current channel is: if the tool reports a difference under about 1.5 times, expect to feel nothing.

### Your Privacy

Nothing is uploaded, stored or transmitted. The network names on screen are already public broadcasts from your neighbours' routers. The device listing covers only your own network.

### Your Internet

None. The tool never connects to the internet. The device listing briefly pings addresses on your own home network to wake them up, which does not leave your house.

## The Off Switch

**What it is:** The tool changes nothing by itself. It only reads and reports, so there is no setting it can leave switched on. The nearest thing to an off switch is the profiles system: a profile is a set of tuning choices made for one particular machine, and it is NEVER applied unless you type its name. Run the tool with no profile and you get neutral values that suit any computer.

**Without it:** If profiles applied themselves automatically, the tool would silently use settings tuned for an unusual 2012 laptop on your modern one, and be confidently wrong in a way you could not see.

**Think of it like:** A pair of prescription glasses left on a desk. Useful to the person they were made for, harmful to anyone else who puts them on, and the only safe rule is that nobody wears them by accident.

## How to use this

**Before you start:**
- A computer with a wifi adaptor switched on
- Permission to change your router's settings, if you intend to act on the answer
- On Windows: PowerShell. Press the Windows key, type PowerShell, and press Enter. On Linux or macOS: your Terminal application.

**Step 1:** Open a terminal and run the survey:

```
fieldkit wifi channels
```

On Windows, open PowerShell first (press the Windows key, type PowerShell, press Enter) and run `python -m fieldkit wifi channels` instead.
  - You should see: A recommendation in the first few lines, a bar chart of how busy each channel is, a list of who is sharing your air, and any warnings that apply to you.
**Step 2:** Read the first paragraph before anything else. It is written to tell you whether moving is worth the bother at all, and it will often say no. The tool reports how much better the recommended channel is as a multiple, and tells you plainly when that multiple is too small to feel.
  - You should see: A sentence either recommending a move, or saying that staying put costs you nothing tonight.
**Step 3:** Look at the section headed WHO YOU ARE SHARING THE AIR WITH. It names each competing network, how hard it is shouting in plain words, and whether it sits directly on top of your channel or only half over it.
  - You should see: A short list of names. Remember this covers routers only; the televisions and speakers attached to them are invisible to any ordinary scan, and the tool says so underneath.
**Step 4:** If channels 12 or 13 look attractive and you are confident every device in your home is British or European, run it again adding `--devices local`. If you are not confident, do not.
  - You should see: The compatibility warning disappears and those channels are treated as ordinary ones, because the risk no longer applies to you.
**Step 5:** If you have a second router of your own, tell the tool which channel it occupies with `--avoid 6`, replacing 6 with the real number.
  - You should see: That channel is no longer recommended, so your two routers are not put in each other's way.
**Step 6:** Before changing anything, run `fieldkit wifi doctor` as well.
  - You should see: It tells you whether the channel is even your problem. If your wifi card is old and single-band, it is the ceiling, and no channel change will help.
**Step 7:** Change the channel in your router's settings page, then run the survey again an hour later and on another day.
  - You should see: The numbers will have moved. One scan is one moment, and a channel chosen at three in the morning is often wrong by seven in the evening.

## If Something Goes Wrong

**It says it could not scan.**
The wifi radio is switched off, this machine has no wifi, or the operating system needs permission. On macOS, wifi scanning requires Location Services to be enabled.
What to do: Switch the wifi radio on and try again. The message tells you which of these it thinks is wrong instead of showing an empty screen.

**The recommendation changes every time I run it.**
Your neighbours' use of the air genuinely changes minute by minute. One scan is one moment.
What to do: Run it at several different times of day before changing anything. If it keeps moving, the differences are not big enough to matter.

**I moved to the recommended channel and nothing got better.**
Either the difference was small, which the tool would have told you, or the channel was never your problem.
What to do: Run 'fieldkit wifi doctor'. If your card is one stream and 2.4 GHz only, it is the ceiling and no channel helps.

**A device stopped connecting after I moved to channel 12 or 13.**
That device cannot legally use those channels. It is not broken and it is not a weak signal; the network is invisible to it.
What to do: Move back to 11 or below. This is the exact risk the tool warns about by default.

**The window will not open.**
Some Linux distributions package Python's window toolkit separately.
What to do: Install it with 'sudo apt install python3-tk'. Everything works in the terminal without it, and the tool tells you so instead of failing silently.

## Why a Developer Would Do This

A number without an argument cannot be checked. Most wifi analyser apps hand you a channel and no reasoning, so you cannot tell a good recommendation from a bad one, and the common error in them is invisible. Writing out the case, including the parts that argue against the recommendation, is what lets a person who cannot read code still judge whether to act.

## Why It Matters That You Can Read This

The recommendation this tool makes is a judgement, not a measurement, and you can read exactly how that judgement was made. Two numbers decide it: how much a partial overlap is penalised, and how much better an unusual channel must be before it is worth leaving the safe three. Both are named constants with the reasoning written beside them in the source, so you can disagree with them. Closed wifi apps give you a number with no argument, and almost all of them get the partial-overlap rule backwards, which you would have no way of discovering. This one shows you its arithmetic line by line on request, which is how a mistake in it was actually found: the tool was counting the maintainer's own router as interference against itself.

## Glossary

**2.4 GHz**: The older, slower, longer-range wifi band, where all the crowding happens.

**5 GHz**: The newer, faster, shorter-range band, with far more room and far less interference.

**Access point**: The thing broadcasting a wifi network, usually your router or hub.

**BSSID**: The unique hardware address of one radio inside a router, as opposed to the name you see.

**Carrier sense**: How wifi devices listen before speaking so they do not talk over each other.

**MIMO**: Using several aerials at once to carry more than one conversation simultaneously.

**Regulatory domain**: The country setting that decides which channels your hardware is allowed to use.

**Saturation**: When a meter has reached the top of its scale and stops responding to further change.

## Claim Sources

| Claim | Basis | Evidence |
|-------|-------|----------|
| Only channels 1, 6 and 11 do not overlap each other on 2.4 GHz | 📄 stated in input | NON_OVERLAPPING_24 = (1, 6, 11) |
| A partial overlap is penalised double | 📄 stated in input | PARTIAL_OVERLAP_PENALTY = 2.0 |
| An unusual channel must be about 2.5 times better before it is recommended | 📄 stated in input | WORTH_LEAVING_1_6_11 = 2.5 |
| Channel 14 is Japan only and 802.11b only | 📄 stated in input | JAPAN_ONLY_24 = (14,)                # and NO-OFDM: 11 Mbps ceiling |
| Channels 12 and 13 are legal in the UK and EU and illegal in the US | 📄 stated in input | UK_EU_ONLY_24 = (12, 13)             # legal here, invisible to US-market kit |
| Nothing is uploaded or stored | 🤖 model inference | *(none, model judgment)* |
| A scan sees access points only, not their attached devices | 📄 stated in input | a wifi scan sees ACCESS POINTS, not clients |
| Profiles are never applied automatically | 📄 stated in input | TRAP: NEVER AUTO-APPLY A PROFILE. |
| Resource use is negligible | 🤖 model inference | *(none, model judgment)* |


---
**How to verify this document:**
`📄 stated in input`: the model's phrasing of something your source text said.
Find the matching line in the original to verify.
`🤖 model inference`: the model's own judgment or synthesis. Treat as opinion,
not measurement. Re-run on the same input and check whether specific numbers
stay consistent between runs.

*Human Track. Its Developer Track twin covers the same changes in technical detail. Neither is a simplified copy of the other: they are the same truth in two languages.*