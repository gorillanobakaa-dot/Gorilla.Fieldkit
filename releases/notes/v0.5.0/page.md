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

---
# In plain language: everything in this release

This is the complete explanation, not a summary of one. Nothing below is behind a link.

<!-- plain-language track: in full, on this page -->

### A tool for the other system never starts

Every Fieldkit tool says which computers it works on: Windows, Linux or both. Until now nothing checked that before a
tool ran, so a Windows tool started on Linux simply crashed. Now, before anything starts, Fieldkit compares the tool
with the computer it is on. If they do not match, it stops, says why, and names what to use on this computer instead.
A model looking for a tool is only shown the tools that work here.

The picture below is a real terminal on a Linux laptop: two Windows tools are refused with the reason, and the
cooling monitor starts.

[![A real terminal on the Linux VAIO: fieldkit hdmi status and fieldkit build-harness build-run are refused as Windows-only with exit 2 and the Linux alternative; fieldkit thermal monitor finds the coretemp sensor and the intel_pstate cap](https://raw.githubusercontent.com/gorillanobakaa-dot/Gorilla.Fieldkit/v0.5.0/docs/screenshots/platform-gate-and-linux-thermal-monitor.png)](https://raw.githubusercontent.com/gorillanobakaa-dot/Gorilla.Fieldkit/v0.5.0/docs/screenshots/platform-gate-and-linux-thermal-monitor.png)

The laptop was busy when this was taken (Fieldkit's own test suite was running), so its idle reading was already
69 C. Because of that the sensor rose less than 10 C under load and was graded "surface", although coretemp is the
processor itself; that grading rule was written for Windows sensors (see the developer notes).

### The cooling monitor works on Linux

`fieldkit thermal monitor` watches the processor's temperature while something heavy runs, such as a browser build,
and slows the processor a little when it gets too hot. It worked only on Windows. On Linux it now reads the
processor's own temperature sensor and uses Linux's own speed limit. When it is not allowed to change the speed, it
says so plainly instead of pretending. It can stop by itself when the build it watches ends.

### A small model finds the right tool

Fieldkit has more than a thousand tool descriptions. A small model is now shown the best few for what it asked, in
the same order Gorilla OpenCode uses, with tools that work offline first. "Recipes" (how something was done before)
are found the same way, to read, never to run.

### Releasing the browser without staying up all night

The Firefox release path is now a few commands that check themselves: preparing the release, packing it overnight,
and testing the Windows installer. While the leak gate measures what the browser sends over the network, nothing
else is allowed to go online, so the measurement stays clean.

### Nothing private leaves the computer

A push to GitHub is checked commit by commit for private files, names and passwords before it leaves. The checks now
cover exactly what git would publish, and a scan finds programs that send usage data on by default.

### Download it as a stranger would

`fieldkit stranger` downloads a project the way someone else would, into an empty folder, and checks that it works
there. Several things that only worked on the owner's computer were found and fixed this way.

---

# For developers: how it works, and how to check it

Written for someone who will audit, fork or change the code. It covers the same release as the plain-language part above; neither is a summary of the other.

<!-- developer track: in full, on this page -->

### Platform gate (PR #77, 2026-10-20)
- `core/host.py: platform_refusal(name, platforms, why)`: the one rule. `this_platform()` returns windows/linux/macos;
  `platform_ok` accepts `macos`.
- `core/platform_gate.py`: `cli.main` refuses a command whose card is for another system before any parser or tool
  code runs: exit 2, `{"ok": false, "refused": "platform", "why": ...}` with `--json`, the card's `platforms_why` and a
  NEXT line. `--help` always passes. A group is refused only when every card under it is for another system.
- The CLI reads `desk/platform_index.json` (275 commands), generated from the PUBLIC cards only:
  `python -m fieldkit.core.platform_gate --write`. `all_cards()` takes 3.6 s from cache, so the gate does not load
  cards. Private `local/tools.yaml` cards get their own index in `state/platform_index.local.json`, rebuilt on mtime.
  `tests/test_platform_gate.py` fails when the index and the cards disagree or a private card reaches the shipped file.
- `agent.run` raises `Refused`; `agent.discover` skips other-system cards; `describe` adds `runs_here`,
  `this_system`, `not_here`.
- Three tests that ran Linux-only commands on Windows now assert the refusal there (`test_audio`,
  `test_next_lines`) or set the gate aside (`test_buildh_cli_fixes`, which tests a message).

### Thermal monitor on Linux
- `thermal/sensors.py: providers(platform)`: Windows readers on win32, `coretemp` (hwmon coretemp "Package id 0",
  k10temp/zenpower "Tctl"/"Tdie") and `x86_pkg_temp` (thermal zone) on Linux, none elsewhere. `_ps()` returns "" off
  Windows: PowerShell is never started there.
- `thermal/governor.py`: `read_cap`/`set_cap` use intel_pstate `max_perf_pct`, else cpufreq `scaling_max_freq`;
  written directly or through `sudo -n tee` (never a prompt); `set_cap` returns whether it took. `can_cap()` writes
  the current value back to itself to test.
- `thermal/profiles.py: monitor()` reports its sensor and cap at start; with no usable cap it says
  `CANNOT COOL, only watches and speaks` and never claims a cap. `--until-pid` stops it with a build. New card
  `thermal-monitor` (windows, linux).
- Known: `sensors.grade()` calls a source "surface" when it rises less than 10 C under load. On a machine that is
  already busy, Linux coretemp (the die) can be graded "surface" (seen in the release screenshot: idle 69 C). The
  grade only changes the label and the length of the proof, not the cap.
- Tests: 18 new; each guard mutation-checked (removing it fails a test).

### Finding and running tools (small models)
- `agent.discover` ranks like Gorilla OpenCode's matcher (`internal/fieldkit/match.go`): rare words weigh more,
  offline tools before online ones, two-letter words kept, 8 results. Answers lead with the fact.
- Recipes (`desk/recipes.py`, `recipes.yaml`): how something was done before, found with the tools, read in full,
  never run.
- MCP: read-only tools carry `readOnlyHint`; card effects use the schema's words only.
- Tool router: ten more hand-rolled habits refused with the harness tool that replaces them.
- Cards for every build-harness action and method (a test holds it), every gorilla-size 0.3.0 mode, `bh-ui-check`
  public, `bh-release-page`.

### Firefox build and release (Gorilla Unleashed)
- `release-publish`, `night-pack`, `installer-test`: the release path in checked steps; `prepare` takes `check=CMD`.
- Leak gate: owner-approved fixes A, B, D, E, F; a quiet machine on Windows and Linux for the whole gate; nothing goes
  to the network while it measures (`release-cover` and `publish` refuse); `rejudge` is owner-terminal only.
- `owner_terminal`: refuses an AI agent's shell (environment markers, agent ancestors, no terminal path to the desktop).
- `fieldkit canary`: every check proves it can fail, and a second witness reads the shipped artifact. themecheck 1.1.0.
- decisions: `mozconfig_lacks` (an option a platform deliberately does not set; Windows keeps RLBox).
- 158 on Windows: the patch set proven per platform; an install keeps the saved logins.
- Autopilot, runbook, ETA, theme and search tools; owner-only build-harness actions are a hard line.

### Privacy, sweep, shipping
- privacy: a push is checked commit by commit (a tag on a pushed commit checks nothing); a git checkout scan covers
  what git would publish (`--all` for every file); Google OAuth secrets are a pattern.
- telemetry scan: libraries on by default (chromadb) are flagged unless the file turns them off.
- Offline guard (`core/offline.py`) for every canary check, test child and the pre-commit hook.
- `fieldkit stranger`: download a project as someone else would and see whether it works (METHODS #20).
- `adopt`/`sweep`: captured loose scripts count as adopted; adopt copies, only `--finish` recycles, never deletes.
- `fieldkit size` (gorilla-size); `ship prune`; `ship release` waits for CI on the merge commit.
- Tests import the Fieldkit under test (PYTHONPATH from conftest) and run with no git identity, as on the runners.

### Upgrading
`pip install` the new version or `git pull` in your checkout. After changing a card's `platforms` or entry, run
`python -m fieldkit.core.platform_gate --write`, or `tests/test_platform_gate.py` fails.
