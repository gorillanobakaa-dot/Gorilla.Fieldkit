# Which connection your computer is really using, what kind of line it is, and what aerial it needs — Plain Language Guide

> Generated 2026-10-13 from `net`

---

## Should You Run This?

Yes for the route and line-kind checks, before any measurement you intend to believe: they read only, take seconds, and prevent a whole class of confident wrong conclusions. Yes for the antenna calculator before spending money on aerials, because it will frequently tell you not to. Use the router check on routers you own or administer, and treat the risk check as mandatory instead of optional before any password goes near an unfamiliar device.

## Worst Case, Honestly

You lock yourself out of your own router. Many routers block further attempts after a few failed logins, sometimes for minutes at a time, and some escalate. This toolkit reads that policy and tells you about it precisely so that neither you nor any automated tool starts guessing; on one real hub the policy was five failures and then a two-minute wait per attempt. Knowing that first turned a plan to automate the login into a decision not to.

The other realistic harm is money. If you follow an antenna recommendation that was produced without reading your hub's actual bands, you can spend real money on hardware that physically cannot help, and a badly matched aerial reflects signal instead of receiving it, leaving you worse off than with nothing attached. That is why the tool refuses to produce a shopping list at all unless you give it the band figures from your own hub.

## What Data This Touches

Nothing leaves your machine except ordinary network traffic you could watch yourself. There is no account, no server of ours, and nothing is uploaded or stored anywhere.

The route check reads your own computer's routing table, which is local information your operating system already holds. The line-kind check sends a handful of pings to a public address and times the replies; those pings carry no information about you beyond the fact that your connection exists, which every website you visit already knows.

The router check reads pages your own router serves on your own home network. It only ever reads. It never submits a password, never changes a setting, and the part that looks at how a router handles failed logins does so to warn you off, not to try any.

The antenna calculator is pure arithmetic on numbers you type in. It touches no network at all and works with the machine unplugged.

## Before You Trust It

One of these tools reads pages from your router and another is about to influence a purchase. Both are worth a few minutes of checking first.

**Step 1:** Run the route check and compare it against what your own system settings show.
  - Look for: The interface it names as the winner should match the connection your computer says it is using.
**Step 2:** Unplug your ethernet cable, run it again, plug the cable back in and run it a third time.
  - Look for: The list of routes should change in the obvious way. If it does not react to a cable being physically removed, it is not reading live state.
**Step 3:** Run the antenna calculator with no band figures at all.
  - Look for: It must refuse. A tool that produces a confident shopping list from nothing is the one to distrust.
**Step 4:** Run the antenna calculator twice, once with a two-metre thick cable and once with five metres of thin cable.
  - Look for: The second should tell you to refuse that cable. If both answers look the same, it is not doing the arithmetic it claims.
**Step 5:** Watch your router's own admin page while the router check runs.
  - Look for: No setting should change, and no failed login attempt should appear in its log.

## The Big Picture

Your computer can be connected more than one way at once: wifi and a cable, or two routers in the same building. Only one of them actually carries your traffic, and it is not always the one you expect. Plug in a cable and your computer may carry on using wifi without telling you.

That single fact wrecks measurements. Somebody plugs in a cable, runs a speed test, sees the same number, and concludes the cable made no difference. The cable was perfect. The test went over wifi.

This part of the toolkit answers three questions. Which connection is really being used. What kind of line it is, because a mobile connection and a fixed line behave so differently that the same numbers mean opposite things. And, if your internet arrives over a mobile signal, what aerial would actually help, which is usually not the one you already own.

None of it changes anything on your machine by itself. Every answer here is read from state your computer already holds, or worked out with arithmetic you can check, and the one function that could alter a setting will not do so unless you ask it to in as many words.

## Key Concepts

| Name | What It Means | Real-World Comparison |
|------|--------------|------------------------|
| `Default route` | The path your computer sends traffic down when it has a choice. | A junction with two roads to the same town and a sign pointing down one of them. Both roads work. Only one is signposted. |
| `Metric` | A priority number on each route. The lowest wins. | Golf. The lowest score takes it, which catches people out because bigger sounds better. |
| `Cellular WAN` | Your internet arriving over a mobile network instead of a cable in the ground. | A shared minibus instead of your own car. You get there, but you wait for a seat, and how long you wait changes all day. |
| `Carrier aggregation` | A mobile connection using several slices of radio spectrum at once. | Several lanes of a motorway opened together. Lose a lane and everything still moves, slower. |
| `MIMO` | Using more than one aerial at once to carry more than one conversation at the same time. | Two people on two phone lines, instead of taking turns on one. |
| `Jitter` | How much the delay on a connection wobbles from moment to moment. | A steady drumbeat against someone tapping whenever they feel like it. The average can be identical and the experience completely different. |
| `Aerial gain` | How much better an aerial listens in its best direction, which it pays for by listening worse everywhere else. | Cupping your hand behind one ear. You hear that direction better and the rest of the room worse. Nothing was amplified; attention was moved somewhere. |
| `Lockout policy` | What your router does after wrong passwords: how many it tolerates, and how long it makes you wait afterwards. | A bank card that swallows itself after three wrong attempts. Worth knowing the number before you start guessing, not afterwards. |

## How It Works — Step by Step

### Step 1: Ask which road is signposted

It reads your computer's routing table and lists every route to the internet, not only the winning one, along with the priority number on each. This is where the surprise usually is: a cable at priority 900 loses to wifi at 600, and nothing warns you.

### Step 2: Name the box you are actually talking to

When it looks at a router it resolves the hardware address of that specific device. This sounds like a detail and is not: an earlier version reported the main gateway's details no matter which address you asked about, so one hub's manufacturer was attributed to the other and sent a whole diagnosis down the wrong path.

### Step 3: Work out what kind of line it is from how it behaves

It times a series of pings and looks at how much the delay wobbles. A fixed line is metronomic. A mobile connection schedules every packet, so the delay wanders. It judges by behaviour instead of asking the router, because routers do not always say, and behaviour cannot lie about itself.

### Step 4: Say what that changes

This is the point of knowing. On a mobile connection the download speed varying ten times over within minutes is completely normal and is not a fault. On a fixed line the same pattern means something is wrong. The identical numbers support opposite conclusions, so the tool states which world you are in before anything interprets a measurement.

### Step 5: Read the hub's own band list

A mobile hub reports which slices of spectrum it is using. From those the tool works out the exact frequency range an aerial must cover. With no band list it refuses to guess, because a plausible shopping list derived from nothing is the expensive kind of wrong.

### Step 6: Count the cable against the aerial

It works out how much signal the coaxial cable will swallow before anything reaches the hub, and will tell you to refuse a cable outright. The thin cable supplied with most antenna kits loses so much at high frequencies that a five-metre run can cost more than any affordable aerial provides, leaving the whole purchase a net loss.

### Step 7: Insist on a pair

Mobile hubs use several aerials at once and rely on the differences between them to carry more than one data stream. Fit one and a chain goes unfed, so the hub drops to a single stream. The tool always specifies a pair and explains why, because no gain figure recovers a lost stream.

### Step 8: Refuse when it cannot look

On an operating system without the tools it needs, it says that nothing was examined instead of returning an empty result. An empty result reads as a real answer, and 'you have no internet connection configured' is an alarming thing to say when the truth is 'I could not check'.

## Quirky Things Worth Knowing

### A plugged-in cable can carry nothing at all

It can link at full speed, show a light, and move none of your traffic, because wifi has a lower priority number. Everything looks right. This happened here, and had the measurement gone ahead it would have produced a confident wrong conclusion about the cable.

### Lower wins

Route priority is like golf. 600 beats 900. Most people read a bigger number as better and get it exactly backwards.

### On a mobile connection, wild speed variation is normal

One evening on one unchanged connection: 25.7, 29.8, 80.2, 122, 170, 204, 220, 235 and 417 megabits per second. Nothing was wrong. If you test twice and compare, you are measuring the weather.

### Your signal bar can improve while your internet gets worse

Hubs report loudness, not clarity. A struggling hub drops its fragile high-frequency slices first and keeps the resilient low ones, so the bar can rise while the fast part of your connection quietly disappears. Always read the band list, not only the bar.

### Buying one aerial can halve your speed

It is not that one is half as good as two. Feeding one chain and starving another makes the hub give up on its second data stream entirely.

### The cable can cost more than the aerial gives

Five metres of the thin coaxial cable that ships with most kits loses about ten decibels at 3.5 gigahertz, which is nine tenths of the signal. A seven-decibel aerial on that cable is a net loss. Almost nobody checks this before buying.

### Two aerials side by side, both upright, are not two aerials

They receive the same thing twice. The hub needs them to hear slightly different things, which is why they must be angled apart and separated.

## What This Means For You

### Battery, Processor & Memory

Negligible. The route and router checks read local information and fetch a few small pages from your own router. The line-kind check sends a short burst of pings. The antenna calculator is arithmetic. Nothing runs in the background. Not measured.

### Speed

The tools change nothing about your connection by themselves. They tell you what is true so that your own measurements mean something. The one real speed effect is indirect and large: a comparison run down the wrong interface produces a wrong answer, and these checks are what stop that happening.

### Your Privacy

Nothing is uploaded or stored. Pings go to a public address and reveal nothing beyond your connection existing. Router pages are read from your own home network and no credential is ever submitted.

### Your Internet

A handful of pings and a few small page fetches from your own router. The data used is far below anything that would register on a metered connection. The optional download comparison deliberately transfers real data and tells you so before it does.

## The Off Switch

**What it is:** Everything here reads by default. The one function that can change anything is the route preference, and it requires an explicit apply flag: without it, it prints the change it would make and does nothing. The change it makes is reversible with a single matching command, and the router check has no write path at all.

**Without it:** A diagnostic that silently reprioritised your connections would leave your computer in a state you did not choose and could not explain, which is worse than the problem it was asked to diagnose.

**Think of it like:** A mechanic who shows you the part and asks before replacing it, instead of handing you a bill and a changed car.

## How to use this

**Before you start:**
- A computer connected to a network
- For the antenna advice: the band line from your hub's status page, which looks like B3+B3+B7+n78+n78
- On Windows: PowerShell. Press the Windows key, type PowerShell, and press Enter. On Linux or macOS: your Terminal application.

**Step 1:** Find out which connection is really carrying your traffic:

```
fieldkit net route
```

Do this before any speed test, not after one that surprised you.
  - You should see: A list of every route to the internet with its priority number, and a clear statement of which one wins. If there is more than one, it says so loudly, because a probe of 'the router' reaches the winner and that may not be the box you plugged a cable into.
**Step 2:** Find out what kind of line you have:

```
fieldkit net kind
```
  - You should see: Either fixed-line or cellular, with the delay figures it judged from and a plain statement of what that means for interpreting any measurement you take next.
**Step 3:** Identify the box itself with `fieldkit net router`, adding `--ip` and the address if you have more than one.
  - You should see: Its manufacturer, what it serves, and whether its pages are full of mobile vocabulary, which reveals a mobile connection even when nothing says so directly.
**Step 4:** Before using any password on a router you do not know well, run `fieldkit net risk`.
  - You should see: What a failed login costs you on that specific device, read from its own pages. Read this before guessing anything.
**Step 5:** For aerials, copy the band line from your hub's status page and run it with the cable you actually plan to use:

```
fieldkit net antenna --bands "B3+B3+B7+n78+n78" --cable-m 2
```
  - You should see: The exact frequency range an aerial must cover, how many to buy, how to mount them, and the cable loss budget. Change the cable length and watch the verdict change; that number decides whether the purchase is worth anything.
**Step 6:** Read the notes at the bottom of the antenna output before ordering.
  - You should see: Seven traps, each of which is a way to spend money and end up with less signal. The most important is that position beats gain: moving the aerial outside or to a window is worth more than any upgrade and costs nothing to try.

## If Something Goes Wrong

**It says fieldkit net reads Linux network state, which this system does not have.**
You are on Windows or macOS. The route and router checks need tools that only exist on Linux.
What to do: Nothing to fix. It is telling you that nothing was examined, which is different from nothing being found. The antenna calculator is arithmetic and works on any system.

**The line kind comes back as uncertain.**
The delay figures sat between the two patterns, often because something else was using the connection while it measured.
What to do: Run it again when the line is quiet. It says uncertain instead of picking one, which is the honest answer.

**My speed test shows the same result with the cable plugged in.**
Almost certainly the test did not go down the cable. This is the exact situation the route check exists for.
What to do: Run the route check first. If wifi has the lower priority number, it is winning.

**The antenna tool refuses to tell me what to buy.**
You did not give it the band figures from your hub.
What to do: Find them on your hub's status page. Without them any recommendation would be a guess, and a mismatched aerial receives worse than none at all.

**It reports a manufacturer I do not recognise for my own router.**
Most routers are built by a contract manufacturer and badged by the company that sold it to you.
What to do: Normal. The hardware address identifies who built it, not whose name is on the front.

## Why a Developer Would Do This

A measurement taken down the wrong path is worse than no measurement, because it carries the authority of a number. Most of these tools exist to establish what is true before anything is measured, and the rest exist to stop a purchase being made on an assumption. Both are about refusing to produce confident answers from insufficient information.

## Why It Matters That You Can Read This

The uncomfortable half of this is the router check, because reading pages from your own router and reasoning about its login behaviour is exactly the shape of something unwelcome. Being able to read it is how you confirm what it does not do: it never submits a password, never writes a setting, and reads the lockout policy in order to advise against guessing. You can verify all three by reading one short file, and no amount of reassurance from a closed program is equal to that.

The antenna advice matters for a different reason. It is about to cost you money. Every figure it uses, including the cable losses and the wall losses, is in the source with a note saying which are measured and which are reasoned estimates. A shop cannot show you that.

## Glossary

**Gateway** — The device on your network that passes traffic out to the internet, usually your router.

**Interface** — One way your computer connects: a wifi adaptor, an ethernet socket, a mobile dongle.

**Routing table** — Your computer's own list of where to send traffic, held locally.

**Ping** — A tiny message sent to see whether something answers and how long it takes.

**Band** — A slice of radio spectrum a mobile network uses, identified by a code such as B3 or n78.

**RSRP** — How loud the mobile signal arrives, in decibels relative to a milliwatt. Closer to zero is stronger.

**Coaxial cable** — The thick round cable that connects an aerial, which loses signal along its length.

**Decibel** — A ratio, not an amount, and it multiplies: ten decibels lost is nine tenths of the signal gone.

**SMA and TS9** — Two kinds of aerial connector. They are not interchangeable, and one family screws into the other while making no electrical contact at all.

## Claim Sources

| Claim | Basis | Evidence |
|-------|-------|----------|
| A plugged cable carried nothing because wifi had a lower metric | 📄 stated in input | The cable came up at 1000 Mbps full duplex and carried NOTHING, because the wifi route had a lower metric |
| Lower metric wins | 📄 stated in input | metric 600     <- lower wins |
| Download varied 25.7 to 417 Mbps on one unchanged connection | 📄 stated in input | 25.7, 29.8, 80.2, 122, 170, 204, 220, 235 and 417 Mbps |
| One hub locked out after five failures with 120 seconds per attempt | 📄 stated in input | lock out after 5 failures, then 120 seconds per attempt |
| Five metres of thin coax loses about 10 dB at 3.5 GHz | 📄 stated in input | RG174 (thin, supplied with most kits)": 2.0 |
| Buying one antenna can cost half the throughput | 📄 stated in input | the modem falls back to a single stream, which can cost half the throughput |
| A mismatched antenna reflects instead of receives | 📄 stated in input | A MISMATCHED ANTENNA IS WORSE THAN NO ANTENNA. It reflects instead of |
| Nothing is uploaded or stored | 🤖 model inference | *(none — model judgment)* |
| Resource use is negligible | 🤖 model inference | *(none — model judgment)* |
| Wall and cable loss figures are estimates, not measurements of any particular wall | 📄 stated in input | model inference, order-of-magnitude, for explaining the trade-off |


---
**How to verify this document:**
`📄 stated in input` — the model's phrasing of something your source text said.
Find the matching line in the original to verify.
`🤖 model inference` — the model's own judgment or synthesis. Treat as opinion,
not measurement. Re-run on the same input and check whether specific numbers
stay consistent between runs.

*Human Track. Its Developer Track twin covers the same changes in technical detail. Neither is a simplified copy of the other — they are the same truth in two languages.*