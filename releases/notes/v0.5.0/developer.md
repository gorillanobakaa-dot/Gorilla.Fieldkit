# Fieldkit v0.5.0, developer notes

49 changes since v0.4.0 (2026-10-16). Grouped by area; each line names the module.

## Platform gate (PR #77, 2026-10-20)
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

## Thermal monitor on Linux
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

## Finding and running tools (small models)
- `agent.discover` ranks like Gorilla OpenCode's matcher (`internal/fieldkit/match.go`): rare words weigh more,
  offline tools before online ones, two-letter words kept, 8 results. Answers lead with the fact.
- Recipes (`desk/recipes.py`, `recipes.yaml`): how something was done before, found with the tools, read in full,
  never run.
- MCP: read-only tools carry `readOnlyHint`; card effects use the schema's words only.
- Tool router: ten more hand-rolled habits refused with the harness tool that replaces them.
- Cards for every build-harness action and method (a test holds it), every gorilla-size 0.3.0 mode, `bh-ui-check`
  public, `bh-release-page`.

## Firefox build and release (Gorilla Unleashed)
- `release-publish`, `night-pack`, `installer-test`: the release path in checked steps; `prepare` takes `check=CMD`.
- Leak gate: owner-approved fixes A, B, D, E, F; a quiet machine on Windows and Linux for the whole gate; nothing goes
  to the network while it measures (`release-cover` and `publish` refuse); `rejudge` is owner-terminal only.
- `owner_terminal`: refuses an AI agent's shell (environment markers, agent ancestors, no terminal path to the desktop).
- `fieldkit canary`: every check proves it can fail, and a second witness reads the shipped artifact. themecheck 1.1.0.
- decisions: `mozconfig_lacks` (an option a platform deliberately does not set; Windows keeps RLBox).
- 158 on Windows: the patch set proven per platform; an install keeps the saved logins.
- Autopilot, runbook, ETA, theme and search tools; owner-only build-harness actions are a hard line.

## Privacy, sweep, shipping
- privacy: a push is checked commit by commit (a tag on a pushed commit checks nothing); a git checkout scan covers
  what git would publish (`--all` for every file); Google OAuth secrets are a pattern.
- telemetry scan: libraries on by default (chromadb) are flagged unless the file turns them off.
- Offline guard (`core/offline.py`) for every canary check, test child and the pre-commit hook.
- `fieldkit stranger`: download a project as someone else would and see whether it works (METHODS #20).
- `adopt`/`sweep`: captured loose scripts count as adopted; adopt copies, only `--finish` recycles, never deletes.
- `fieldkit size` (gorilla-size); `ship prune`; `ship release` waits for CI on the merge commit.
- Tests import the Fieldkit under test (PYTHONPATH from conftest) and run with no git identity, as on the runners.

## Upgrading
`pip install` the new version or `git pull` in your checkout. After changing a card's `platforms` or entry, run
`python -m fieldkit.core.platform_gate --write`, or `tests/test_platform_gate.py` fails.
