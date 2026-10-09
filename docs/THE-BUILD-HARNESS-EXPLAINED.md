# The build harness, explained

Fieldkit's build harness (`fieldkit build-harness`) builds Gorilla Unleashed, a Firefox with every call home cut out
of its source code, on one Windows laptop, and refuses to call anything finished until it has measured it on the
browser people actually download. This page explains, for anyone curious, what this large machine does, why it
exists, who else does similar work, and how much it does. Numbers are as of 9 October 2026 and come from the
harness's own records.

---

## What the harness is, for someone outside IT

Think of an aircraft factory, not a garage. The harness is three things bolted together.

1. **The recipe book, with a notary.** Every change made to Firefox is written down as a recipe step (a "patch").
   Before every release the harness takes Mozilla's untouched Firefox, applies all 479 steps, and proves the result
   is identical, to the last byte, to the source that was compiled. Anyone can repeat this, so nobody has to take
   the maintainer's word for what is inside.
2. **The production line, with inspectors.** It builds the browser without overheating the laptop (it holds the
   processor below a temperature cap and proves its temperature sensor actually responds before trusting it). Then
   inspectors examine the *finished* browser, not the plans: does it contact Mozilla (read from the browser's own
   network log)? Does the built-in ad blocker block? Are the removed pages really gone? Are the menus readable? Does
   Help > About say which build it is? A four-hour leak test watches every connection, name lookup, file and process
   with several independent witnesses at once.
3. **The auditor.** Every product decision is written down with its reason, its cost and a check; a build that
   breaks a decision fails. Every sentence in the public documents (7,513 of them) is held against the real build:
   1,239 are proven by a check, a false one blocks the release, and the 6,274 with no check yet are listed as
   unproven, never counted as proven. The release page must explain every change since the last
   release, in plain words and in technical words.

## Why this browser needs it

Gorilla's whole promise is something nobody can see: **"it never calls home."** You cannot look at a browser and see
it *not* sending something; only measurement proves an absence. That has already mattered: Gorilla 155.0.1 was found
contacting **eight Mozilla hosts** while it was believed to be clean. Without the harness, that would have been a
false promise published to people who trust it.

**On 9 October 2026 alone, it caught three things that would otherwise have shipped:**
- **Files left in.** The pages the source had removed still had their files inside the packaged browser. The source
  was right; the packaging step left them in.
- **False public sentences.** 87 sentences in the public documents were false about the installed build. All were
  fixed or proven the same day: 0 left.
- **A blind check.** One of the harness's own checks could not see what it was checking and would have passed
  anything. Every check of that kind must now prove it can see before its "pass" counts.

## Why the effort is not wasted

Without a harness, the same bug is paid for again and again, because nothing remembers it. Here, every mistake
becomes a permanent check that runs on every build at no extra cost. The work buys a guard, not a one-off fix.

## The honest part

- **The risk is real.** A harness can grow fat and slow. On 9 October the checks after installation took 31 minutes,
  because they repeated, on byte-identical files, what the checks after the build had proved 20 minutes earlier.
  The maintainer spotted it. Since then each check runs once, on the package that ships, and the install inherits
  the result only when every one of its files is proven identical to that package (see "Prove once" below).
- **Most of the code is written by an AI.** The maintainer does not write or read most of the C++. What makes that
  safe is that the AI is not trusted either: it had to build the inspectors, and the inspectors fail closed. A check
  that cannot run counts as a failure, never as a pass.

In one sentence: **the harness is a test-pilot programme for a browser, with an AI as the engineering crew, and
the harness as the flight-test instrumentation.**

### Prove once

| Checks that read only the browser's own files | Checks that depend on the machine, the network or the install |
|---|---|
| visual (icons, page layouts), menus and the tab outline, every about: page and the removed ones, the About stamp, Satellite mode | profiles, settings, removed parts, start-up, network egress, ad blocking, leaks, decisions, public claims, the installed build's identity, the maintainer's own scripts |
| run **once**, after the build, on the zip that ships (1,212 seconds on build 29) | run after **every** install |
| after an install: carried over only when all 50 installed files are byte-identical (SHA-256) to that zip, which takes about 2 seconds to prove; otherwise the row fails and everything runs again | |

---

## Is this reinventing the wheel?

Partly. Every piece has a respected original somewhere, but no single organisation known to us builds the whole
thing as one harness on one machine. Large organisations split this work across many teams and server farms.

### Who does the same kind of work

| What this harness does | Who does it at scale | How it differs |
|---|---|---|
| Audit every Firefox change for privacy leaks before shipping | **Tor Project and Mullvad.** Every Firefox ESR move is formally audited: the move to ESR 128 reviewed and resolved more than 200 Firefox issues, and Mullvad audits each Firefox version's changes | The closest match to the leak gate and the hidden-pages review. Done by a paid team over months, on ESR only |
| Prove the download equals the published recipe | **Tor Project** reproducible builds (presented at CCC in 2014); **Debian**'s reproducible-builds project | Same idea as the replay proof. They go further: two independent builds must produce byte-identical binaries. Here the source is proven; the binary is not yet reproduced independently |
| Install the package, then test it | **Debian** piuparts (install, upgrade, remove) and autopkgtest (tests on the installed package) | The same idea as post-install |
| Static checks on a package | **Debian** lintian | Close to the pre-build gate |
| Build and test a browser on every change | **Mozilla**: mach, Taskcluster, Treeherder, memory tracking ("Are We Slim Yet"), performance suites | Hundreds of machines and paid staff. Their tests check that Firefox *works*, not that it *never calls home* |
| Drive the real screen and keyboard to test | **SUSE and Fedora**: openQA | Like the address-bar and visual checks |
| Record decisions and check public claims against the build | **Aviation and space**: DO-178C (avionics software certification) and NASA's requirements traceability | The decision register and claims audit are a small civilian version. Rare outside safety-critical industry |

The maintainer's own audit of 29 September 2026 found no tested, AI-drivable Firefox-on-Windows build harness from
any reputable organisation. Mozilla's AI material for building Firefox is Markdown notes, with no working tooling.

### What nobody else does, and why

- **Fan control while compiling.** Mozilla builds in data centres with proper cooling. Gorilla is built on a
  ThinkPad that has reset itself twice from heat, so the harness has to manage temperature.
- **Desktop icons and the Windows icon cache.** Companies do not ship to one desktop and then check it. This
  harness rebuilds the icon cache after an install and checks every desktop icon.
- **One person and an AI covering every role.** Tor's audit alone is a team's job. The harness is how one person can
  hold that standard without the staff.

### Where we could borrow instead of invent

1. **Debian's diffoscope**, to show *what* differs between two builds, file by file. The replay proof only says
   identical or not.
2. **Tor's ESR audit method**: they publish an audit checklist per Firefox version. Reading it before each Firefox
   upgrade would show what to look for.
3. **in-toto / SLSA**: standard records saying "this build came from this source by these steps", which outsiders
   can verify with standard tools. The harness's record is its own format.

The wheel exists, but in pieces, owned by organisations with budgets and server farms. Each of them does far more
in its own field than this harness does; it covers, at laptop scale, parts of what they do.

---

## How much it does

Counted from the harness's own records and logs; nothing below is estimated.

### What the harness is made of

| | Count |
|---|---|
| Build-harness commands | **64** |
| Fieldkit tool groups (office, privacy, thermal, build-harness, ...) | **23** |
| Python modules in the build harness | **60** (17,433 lines) |
| Ready-made browser probes (scripts run inside a throwaway copy of the browser) | **22** |
| Tests of the harness itself | **1,364** |

### One build-and-install cycle: about 3,400 individual checks

Each with a yes/no verdict backed by evidence.

| Step | Checks |
|---|---|
| Claims: sentences in the public documents proven by a check against the build | 1,239 |
| Visual: icons and page layouts (529 static + 112 in the running browser) | 641 |
| Replay: published patches re-applied to Mozilla's untouched source | 479 |
| Settings: every setting the port makes, in the shipped browser | 366 |
| Decisions (300) and the final locked block (55 settings) | 355 |
| Removed parts that must not be in the package | 153 |
| about: pages read, removed addresses typed, removed files opened | 87 |
| Satellite mode cases (9 ordinary + 8 call sites) | 17 |
| Menu and Settings items checked for readability | 11 |
| Plus about 80 summary rows | ≈80 |

Not counted above: the other 6,274 public sentences, which have no check yet. The audit lists each one as
unproven, and the strict release check stays FAIL until every sentence is proven. Writing those checks is open
work.

The four-hour leak gate is separate: about **16,000 observations** per run (every connection, name lookup, file and
process, judged against 19 rules), run once per release.

### Since the last release (6 October 2026, 14:10, to 9 October 2026)

| | Count |
|---|---|
| Build runs (9 produced a verified package) | 13 |
| Installs | 7 |
| Post-install runs | 14 |
| The maintainer's proof scripts run | 178 times |
| Replay proofs | 31, re-applying **14,531** patches |
| Harness test executions (the full suite before each of 51 changes) | **62,117** |
| Leak-gate runs | 0, not yet run for this release |

The 117,597 leak-gate observations on the 157.0 release page belong to that release, not this one.

Not counted, because nothing records them: test runs started by hand between changes, and checks done by eye.

---

Sources for the comparison:
[TechRadar: Tor Browser major update (ESR 128 audit, 200+ issues)](https://www.techradar.com/pro/security/tor-browser-releases-major-service-update-with-privacy-security-and-usability-boost) ·
[Mullvad Browser 13.0 release (per-version ESR audit tickets)](https://github.com/mullvad/mullvad-browser/releases/tag/13.0) ·
[Tom's Guide: Tor Browser 14.0](https://www.tomsguide.com/computing/online-security/tor-project-releases-tor-browser-14-0-what-you-need-to-know) ·
[Wikipedia: Reproducible builds](https://en.wikipedia.org/wiki/Reproducible_builds)
