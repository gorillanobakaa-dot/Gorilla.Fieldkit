# Fieldkit v0.2.0, in plain language

## The program you download is the program the source builds

Anyone can publish a program and say "this is built from that source". Until now nothing here proved it. Now
Fieldkit builds the program a second time, in a different folder, with a different clock and different settings, and
compares the two results byte by byte. If they are the same, nobody slipped anything in along the way. If they
differ, it does not just say "different": it opens the package and points at the exact file and the reason (a folder
name baked in, a date, a file order).

- Gorilla OpenCode: both programs, for Linux and for Windows, came out identical.
- The custom Linux kernel: its packages came out identical, after three small fixes the comparison itself found.
- The Gorilla Firefox build: the tool is ready, but a Firefox build takes hours on the build laptop, so it has not
  been proven yet. We say so rather than claim it.

## Help for students, in seven countries

Fieldkit now checks a piece of work the way a careful tutor would, before it is handed in, and in the rules of your
own country: page size, date form, spelling, referencing style, the grading scale.

- One command, `check`, runs every check in order and gives one answer: ready, or not ready and the one thing to fix
  first. The finished file goes into the "HERE IS YOUR WORK" folder only when nothing failed.
- It reads the assignment brief and tells you the type of work, the word count and what you must show. If a brief
  hides instructions meant to trip up AI tools, it shows them to you.
- It keeps your marks and what markers keep saying, each with the marker's own sentence, so you know what to watch
  for next time.
- A numbered menu, in English or Romanian, opens from a shortcut on your desktop, for when there is no AI to hand.
- Guides in English and Romanian explain every step.

This came from one student's own tools, made to work for anyone. His original was never changed.

## Your backups are never touched

`fieldkit backup usb` copies your newest backup to a USB stick and reads it back to prove the copy is good. It never
deletes, moves or overwrites anything already on the stick. If the stick is full, it stops and tells you how much room
it needs; you decide what to delete. A reminder tells you when the last copy on a stick is getting old.

## Looking after the laptop

- A processor that the firmware locks at a slow speed (a known fault on some laptops) is measured, unlocked and
  measured again.
- A voice tells you when an AI assistant has finished, or is waiting for you to approve something.
- Low sound on Windows is diagnosed, fixed safely, and can be undone.
- Everyday cooling: a quiet, balanced or performance profile that slows the processor a little when it gets hot.
- Sharp desktop icons, the TV over HDMI (with the graphics-driver repair it exists for), and the Windows 11
  settings framework are all part of Fieldkit now.

## Research you can check

When an AI researches something for you, every page it used is kept, and every claim it makes is checked against the
page it says it came from. A claim with no page behind it is marked, not passed along.

## What it cannot do yet

- The Firefox build has not been reproduced; that needs a second full build on the build laptop.
- The kernel was reproduced with a small configuration, not yet with the full one.
- The Windows laptop tools were tested with simulated Windows answers; they still need to be tried on a real laptop.
