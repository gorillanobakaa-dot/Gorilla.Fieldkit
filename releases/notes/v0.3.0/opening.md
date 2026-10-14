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
