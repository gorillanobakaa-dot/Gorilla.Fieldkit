## What this is

Fieldkit is a set of tested tools that let a small AI model, running on your own laptop, do real work safely: build
software and prove the result, keep backups, look after the laptop. This release is about two things: a tool made for
one operating system now never starts on the other one, and a small model can find the right tool and run it without
guessing.

## Should you download it?

Yes, if you use Fieldkit with a small or local model (Gemma, a hosted Gemini Flash): the tools it is offered are only
the ones that work on your system, ranked the same way Gorilla OpenCode ranks them, offline tools first.

Yes, if you build Firefox (Gorilla Unleashed) on Windows or Linux: the release path no longer needs an all-nighter,
and nothing goes to the network while the leak gate measures the machine.

Yes, if you use Fieldkit v0.4.0: this is 49 changes later and replaces it.

## Why this matters

A tool started on the wrong system does not do half its job: it crashes, and a small model then spends the owner's
money guessing why. Every tool already said where it runs; now the harness reads that before anything starts, and
says what to use instead.
