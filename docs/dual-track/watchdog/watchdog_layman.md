# The watchdog: keeping the laptop online

## What it does

It checks every few seconds whether the laptop can still reach the internet.

If it cannot:

- after twenty seconds, it switches the wifi back on and rejoins the network it
  was last on, by itself
- after ninety seconds, it says out loud: *"Yo Gorilla. The laptop is offline and
  cannot get back on the internet. Claude needs your help. Please switch on the
  phone hotspot."*
- when it comes back, it says so once, and stops

Everything it does is written to a log file, so you can see afterwards what
happened and when.

## Why it exists

On the 23rd of September 2026, while testing the Portable Network Hub, an AI
session kept taking the laptop off its only internet connection: the owner's
phone hotspot. It was switching the wifi radio off on purpose, to measure how
the hub behaved in the conditions it is built for, where there is no internet at
all.

The owner had no idea the laptop needed help getting back online.

And the AI could not tell him, because the only way it had to say anything was
the connection it had just broken.

So this is deliberately **not** an AI tool. It is a simple loop that opens a
network connection, and if it cannot, fixes it or shouts. No cleverness, nothing
to go wrong, and it keeps working when everything else cannot.

## What changed in October 2026

It worked, but only on Windows, and it lived as a loose file in a folder of
scripts rather than inside the harness.

Now it is one piece of code that runs on both Windows and Linux. The Windows
side behaves exactly as it always did, on purpose, because it works and there
was no reason to touch it. The Linux side is new.

It also stopped carrying its own speaking code. It now asks the machine to speak,
which means it automatically uses whatever voice the computer is set up to use.
On Linux that is now a proper modern voice rather than a 1995 robot, and the
watchdog did not need changing to get it.

## Using it

```
fieldkit watchdog status             are we online, on which network
fieldkit watchdog test               hear the alarm once
fieldkit watchdog pause 10           stay quiet for ten minutes
fieldkit watchdog install --apply    start it automatically at login
```

The pause matters. If you are deliberately testing something offline, you do not
want the laptop shouting at you about it.

## If your machine has no sound

It still runs. A machine that cannot speak is still worth having a watchdog on,
because the watchdog's first job is to fix the connection by itself, and the
shouting is only what happens when it cannot.
