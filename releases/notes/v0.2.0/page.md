# Fieldkit v0.2.0

## What this is

Fieldkit is a set of tested tools that let a small AI model, running on your own laptop, do real work safely:
build software and prove the result, check a student's assignment before it is submitted, keep backups, look
after the laptop itself. Every tool has a card that says what it changes; an AI may only run what the card allows,
and a person approves anything that changes the system. It runs on Windows 11 and on Linux.

## Should you download it?

Yes, if you use Fieldkit v0.1.0: this release is 109 changes later and replaces it. Yes, if you are a student in
the United Kingdom, the United States, Spain, Portugal, Italy, Germany or Romania and want your work checked before
you hand it in. Yes, if you build software and want proof that the program you publish is the one your source makes.
No, if you only want a chat program: Fieldkit is the toolbox an AI uses, not the AI.

## Why this matters

A program you download should be the program its published source builds, and now that can be checked: Fieldkit
builds it a second time, somewhere else, and compares every byte. A student should not lose marks to a missing
citation, a stray dash or their name left in the file, and should never lose the only copy of their work to a
cleanup tool. Both are what this release is about. It is free, and stays free.
---
# In plain language: everything in this release

This is the complete explanation, not a summary of one. Nothing below is behind a link.

<!-- plain-language track: in full, on this page -->

### The program you download is the program the source builds

Anyone can publish a program and say "this is built from that source". Until now nothing here proved it. Now
Fieldkit builds the program a second time, in a different folder, with a different clock and different settings, and
compares the two results byte by byte. If they are the same, nobody slipped anything in along the way. If they
differ, it does not just say "different": it opens the package and points at the exact file and the reason (a folder
name baked in, a date, a file order).

- Gorilla OpenCode: both programs, for Linux and for Windows, came out identical.
- The custom Linux kernel: its packages came out identical, after three small fixes the comparison itself found.
- The Gorilla Firefox build: the tool is ready, but a Firefox build takes hours on the build laptop, so it has not
  been proven yet. We say so rather than claim it.

### Help for students, in seven countries

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

### Your backups are never touched

`fieldkit backup usb` copies your newest backup to a USB stick and reads it back to prove the copy is good. It never
deletes, moves or overwrites anything already on the stick. If the stick is full, it stops and tells you how much room
it needs; you decide what to delete. A reminder tells you when the last copy on a stick is getting old.

### Looking after the laptop

- A processor that the firmware locks at a slow speed (a known fault on some laptops) is measured, unlocked and
  measured again.
- A voice tells you when an AI assistant has finished, or is waiting for you to approve something.
- Low sound on Windows is diagnosed, fixed safely, and can be undone.
- Everyday cooling: a quiet, balanced or performance profile that slows the processor a little when it gets hot.
- Sharp desktop icons, the TV over HDMI (with the graphics-driver repair it exists for), and the Windows 11
  settings framework are all part of Fieldkit now.

### Research you can check

When an AI researches something for you, every page it used is kept, and every claim it makes is checked against the
page it says it came from. A claim with no page behind it is marked, not passed along.

### What it cannot do yet

- The Firefox build has not been reproduced; that needs a second full build on the build laptop.
- The kernel was reproduced with a small configuration, not yet with the full one.
- The Windows laptop tools were tested with simulated Windows answers; they still need to be tried on a real laptop.

---

# For developers: how it works, and how to check it

Written for someone who will audit, fork or change the code. It covers the same release as the plain-language part above; neither is a summary of the other.

<!-- developer track: in full, on this page -->

### Scope

v0.1.0..v0.2.0 is 109 commits, merged as PRs #1 to #11. Every PR ran the whole suite on `windows-latest` and
`ubuntu-latest` (`.github/workflows/tests.yml`), and every commit passed the pre-commit hook (the full suite plus
`fieldkit privacy scan . --git`). This release is published by `.github/workflows/release.yml` only after the same
suite passes on both runners for the tagged commit.

### Reproducible binaries

- `fieldkit repro compare A B [--root RA RB]` (`fieldkit/build/repro.py`): files or folders byte for byte. Archives are
  opened recursively (ar/.deb, tar in gz/bz2/xz/zst, zip, so omni.ja too) and a finding names the deepest member that
  differs. Causes recognised: a build root or the home, user or host name present in one side only, a date or time near
  the first differing byte, member order, uid/gid/mtime in headers, PE COFF link time and RSDS GUID (`/Brepro`).
- Go (`go-app.yaml`, `gorilla-opencode.yaml`): a `repro` stage between `build` and `checksums` rebuilds every target
  from a `copytree` of the source in another folder with its own `GOCACHE` and `TMPDIR`, `TZ=Pacific/Kiritimati`,
  `LC_ALL=C`, the verified `GOMODCACHE`, and the build record's ldflags. Gorilla OpenCode b0ce82f: both binaries
  identical.
- Kernel (`debian-kernel.yaml`): a `repro` stage copies the tree without build products (top-level only for names
  such as `debian` and `vmlinux`, which also exist as sources lower down), runs `mrproper`, restores `.config` and
  the build stamp, and runs `bindeb-pkg` with the first build's `LOCALVERSION`. `build_env` now pins
  `SOURCE_DATE_EPOCH` to the top Makefile's mtime (so dpkg-deb clamps every built file), puts a `date` shim on PATH
  (changelog dates), and adds `-ffile-prefix-map=<tree>=.` to `HOSTCFLAGS` (objtool's DWARF). Proven on 7.2.9 with
  tinyconfig plus modules: linux-image and linux-headers identical; the RPM's vmlinuz still equals the .deb's.
- Firefox: `fieldkit build-harness repro TASK [--run|--compare]` (`fieldkit/buildh/reprobuild.py`): a git worktree of
  HEAD (HEAD is already replay-proven), a mozconfig with every spelling of the objdir rewritten, the pinned
  `MOZ_BUILD_DATE`, `mach build` and `mach package` under the thermal governor, then `repro.compare` on the zip and
  the installer. Refuses without a pin, without room, or when a target folder exists that it did not create.

### Academic (`fieldkit academic`)

`check` (gates in `academic/check.py`), `brief`, `date`, `record` (`grades` per locale; Germany's scale runs the
other way), `portfolio`, `local` (copy, SHA-256 every file, rename the cloud original), `preflight` (real documents
built and measured for each locale: page size, page breaks, empty author fields, the header, the language tag),
`menu` and `shortcut`. `build` no longer leaves `[Module Code]` in the header when no module is set; `check` found it.
Not carried over from the original: randomised "Accessed" dates (an access date is the real one; `accessed` stamps
it), and a migration of one student's old folder layout.

### Backups, laptop, thermal

- `fieldkit backup usb|remind` (`core/usbcopy.py`): `.part` then rename, read back (SHA-256, `testzip`), a `.sha256`
  beside it, an existing name never overwritten (`_2`), a full stick refused; the test asserts every pre-existing
  file on the stick is byte-identical afterwards.
- `fieldkit laptop cpu|alerts|sound|volume|video|wallpaper|rules`: BD PROCHOT guard (typeperf against the base clock,
  event 37, an optional unlock tool, `governor.set_cap(100)`, a logon task; on Linux cpufreq and `thermal_throttle`
  counters, read-only); voice alerts from Claude Code's Stop/Notification hooks and Antigravity transcripts (merged
  into `~/.claude/settings.json` with a backup); Windows Core Audio through `-EncodedCommand` PowerShell with an undo
  file written before any change; `wpctl`/`pactl` on Linux.
- `fieldkit thermal profile|monitor|autostart` (`thermal/profiles.py`) and an `hwinfo` provider in
  `thermal/sensors.py`, still subject to `prove_live`.

### Also since v0.1.0

Research evidence records and `fieldkit evidence` (#8); the owner's other repositories gathered, reviewed and carded,
`fieldkit win11` (#8); `fieldkit hdmi` with the elevated GPU-driver repair (#9); dual-track and speaker-loudness fixes
carded (#10); methods turned into tested commands (#7); Gorilla OpenCode MCP wiring and the Go pipeline (#6); academic
documents and templates (#4, #5); `fieldkit icons` (#3); CV and job-hunt tools and the privacy gate (#2); Linux
parity, kernel migration, packaging and the release gate (#1). Each PR's description, at
https://github.com/gorillanobakaa-dot/Gorilla.Fieldkit/pulls?q=is%3Apr+is%3Amerged, lists its tests.

### Known limits

Firefox not yet reproduced (needs the build machine); the kernel proof used a small config; the `laptop` Windows paths
are tested against simulated `run`/`ps` answers only; the 109-commit range is summarised here by PR, not commit by
commit.
