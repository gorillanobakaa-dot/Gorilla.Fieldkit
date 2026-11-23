## What this is

Fieldkit v0.10.14 makes the two laptops work as one: the Linux computer now passes the same checks as the Windows one, and the harness reaches the other computer over the direct cable first, with SSH only as a fallback. It also stops false "quota reached" alarms that halted AI jobs for no reason.

## Should you download it?

Yes, if you run Fieldkit on two computers joined by a cable, or on Linux. Windows-only users get the quota fix.

## Why this matters

Before this release, an AI job could stop because a process number or a test name contained "429" or the word "overloaded", and a budget pause was mistaken for a quota error. Each false stop cost a human restart. Linux runs also failed checks that only made sense on Windows.
