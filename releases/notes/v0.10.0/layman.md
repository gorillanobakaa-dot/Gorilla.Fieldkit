# v0.10.0, in plain language

## One command builds a product, like Gentoo's Portage

`fieldkit emerge RECIPE` builds a product from start to finish: Gorilla Firefox for Windows and Linux, the kernel, Gorilla OpenCode, gorilla-size. Each step remembers what it was built from and is skipped when nothing changed. A step for the other operating system is sent to the other computer over the cable. When a step fails, a model is called only for that step, and its fix is kept as a commit so the next run needs no model. Small front-end fixes to an installed browser take one step (`fieldkit emerge repair`), not a full rebuild.

## Asked once, at the start, never in the middle

Every job lists what only the owner may decide and asks it before it starts (`fieldkit prepare`, the one-ask registry). In the middle of a job, a missing answer takes its fallback; the job never waits for a person. Owner-only steps the mandate already covers no longer stop anything.

## Releases: every operating system the same day, one page with all of them

A repository gets one release per operating system per 24 hours, so Linux, Windows, macOS (and later FreeBSD, Android, ChromeOS) can all publish the same day (owner, 2026-11-17). Each Firefox version also gets one batch release that carries every system's file and is the Latest on GitHub, so a person clicking "Latest" sees every build. A release is never created without its files table and its plain-language page, and Windows releases now verify the installer file (`.exe`/`.msi`) and run an installer check first.

## Models: the cheapest one that can do the job, and a choice that is yours

The harness knows which coding agents and models work right now on each computer (Claude, Codex, Gemini, Antigravity CLI `agy`, local models) and offers them as a menu; your choice is remembered per kind of job. When a model's quota is out, it is skipped until its reset, then offered again. A quota block now needs a real "limit reached" message: a finished job whose log only mentioned the word "budget" blocked Claude for two hours on 2026-11-17, and that can no longer happen. Every job has a stronger model it can ask for advice, and a failed job climbs to a stronger model before it is parked.

## Linux and Windows are equal

The commands that existed on one system only (63 of them) now run on both, are sent to the other computer, or say plainly why not. The leak gate, the installer test and the night jobs run on Linux too. GitHub CI tests every change and end-to-end tool flow on both Linux and Windows before it merges, and tests run there, not on the laptops.

## The net between the computers, Antigravity bridge, and folder transfers

Each session has its own ear, a supervisor wakes Zero when a note waits on a deaf session, and Zero's traffic is mirrored to Gorilla OpenCode. Antigravity GUI and `agy` CLI sessions join the net and talk across the cable (`fieldkit antigravity sessions|send --device`, `listen`, and project hooks) with replies fenced as untrusted data. Sending files over the cable (`fieldkit peers link put`) can now deliver directly to the receiver's owner-approved Desktop, Downloads, or Documents folder without extra copy-paste.

## Nothing is lost

A session cannot end or be archived while its work is uncommitted, unpushed or unmerged. A patch group the policy does not name fails the replay instead of disappearing. Every process Fieldkit starts is tracked and stopped, including the hundreds of empty console windows sessions used to leave behind on Windows.

## Learning from every job

While a job runs, the harness records what the agent did with age-based capture cleanup and scoped Codex and Claude session lookback. Afterwards it turns repeated steps into candidate tools, tries each one (tests, a mutation check, a privacy scan) and adopts only the ones that pass, one pull request per run.
