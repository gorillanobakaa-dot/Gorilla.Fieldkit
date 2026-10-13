# fieldkit.wifi: 2.4 GHz channel selection, card capability, and link measurement

> Generated 2026-10-13 | Source: `wifi`

---

## Purpose

Answers two separable questions about a wireless link, and keeps them separate on purpose. channelsurvey.py decides which 2.4 GHz channel is least contended and then argues the case, including the argument against its own recommendation, so a reader can disagree with the judgement instead of only the number. doctor.py decides whether the channel is the bottleneck at all, which it frequently is not: an old single-stream adapter is its own ceiling and no router setting moves it. Trust level is read-only throughout. The module issues scan and capability queries to the host operating system and never mutates radio, routing or configuration state. Every judgement constant is named and carries its reasoning in-source, because the recommendation is a judgement and an unexplained constant is a guess with a variable name. The whole of channelsurvey.py is one file using nothing outside the standard library, so it can be copied to any machine with Python 3.8 or later and run there unchanged.

## Known Alternatives Considered

Not available in the source material. The predecessor module fieldkit/wifi/survey.py was quarantined instead of deleted, with its own note recording why: its weighting was monotonically decreasing with channel distance, which scores adjacent-channel interference as cheaper than co-channel, and it had no margin rule. Its DEVICE_HINTS table was carried across.

## Architecture

- **Pattern:** Pure functions over a dataclass, with platform dispatch at the single scan() boundary. No global state except module constants. CLI and GUI call identical analysis functions; the GUI is a thin tkinter wrapper and shares no logic of its own.
- **Trust boundary:** Everything returned by a scan is attacker-controlled: SSIDs are arbitrary bytes chosen by strangers. They are never interpolated into a shell string, never used as a regular expression, and never split by position. nmcli -t output is parsed right to left because the first field may contain the separator. Numeric fields are validated before int().
- **Attack surface:** Subprocess invocations of nmcli, iw, arp, ip, ping, netsh, airport, ifconfig, system_profiler. All take fixed argv lists; no shell is ever invoked. The only externally-influenced value reaching a subprocess is an IP address in _sweep(), constructed from a validated subnet prefix. Local profile JSON is key-validated against DEFAULTS and rejects unknown keys.
- **Dependencies:** `argparse`, `json`, `platform`, `re`, `shutil`, `subprocess`, `sys`, `textwrap`, `os`, `time`, `math`, `dataclasses`, `concurrent.futures`, `tkinter (imported inside gui() only)`

## Flags & Configuration

| Name | Type | Default | Effect | Notes |
|------|------|---------|--------|-------|
| `--band` | `string` | `2.4` | Which band to analyse. | 5 GHz is implemented but untested against real 5 GHz air; the development machine has no 5 GHz radio. |
| `--avoid` | `string` | `` | Channels excluded from recommendation. | For a second AP you own that is staying put. Distinct from --mine. |
| `--mine` | `string (repeatable)` | `auto-detected` | The AP being placed; excluded from every candidate channel. | Defaults to the currently associated SSID via current_network(). Not the same as --avoid: this one moves with you. |
| `--count-mine` | `bool` | `false` | Restores the old biased behaviour of counting your own AP. | Present so the bias can be demonstrated, not because it is ever correct. |
| `--devices` | `mixed|local` | `mixed` | 'local' asserts every device present can use channels 12 and 13. | NEVER inferred. It is a fact about a building, not about radio. |
| `--profile` | `string` | `none` | Applies a named tuning profile. | Never automatic. Local profiles in state/channel_profiles shadow shipped ones by name. |
| `--technical` | `bool` | `false` | Full engineering table instead of plain_report(). | The plain view is the default deliberately. |
| `--score` | `int` | `none` | Prints the per-network arithmetic behind one channel's score. | This is how the own-AP bug was found; it audits as well as explains. |
| `--case` | `int` | `none` | Argues the case for a chosen channel against the recommendation. | Includes the local-knowledge argument the tool cannot evaluate itself. |
| `--watch` | `string` | `none` | Samples one network repeatedly for before/after tests. | Refuses saturated or noisy vantage points. |
| `--rate` | `float` | `none` | Decodes a negotiated PHY rate into standard, width and streams. | Pure arithmetic; needs no scan and works on any platform. |
| `--calibrate` | `string (repeatable)` | `none` | metres:dBm pairs; fits reference and path loss exponent. | Refuses fewer than two distinct distances. |
| `--devices-here` | `bool` | `false` | Enumerates devices on the local subnet. | Sweeps with ping to populate the neighbour table; --no-sweep reads it passively. |
| `--gui` | `bool` | `false` | Opens the tkinter window. | Exit 4 with an actionable message if tkinter is absent. |

## API Surface

| Symbol | Description | Side Effects |
|--------|-------------|--------------|
| `scan()` | Platform-dispatched scan; second element explains any failure. | Runs a scan command; on Linux triggers an nmcli rescan. |
| `analyse()` | Scores every candidate channel with overlap, width and strength weighting. | Calls local_rules() which runs iw if rules is None. |
| `recommend()` | Applies the margin rule; returns best, conventional, lowest and margin_too_thin. | None. |
| `rationale()` | The argued case, including the argument against the recommendation. | None. |
| `case_for()` | Argues a user-chosen channel against the recommendation, naming what only the user can know. | None. |
| `score_breakdown()` | Per-network contributions to one channel's score. | None. |
| `watch()` | Repeated sampling with spread and usability warnings. | Runs samples scans; sleeper is injectable for tests. |
| `decode_rate()` | PHY rate to (standard, width, streams, band). | None. |
| `distance_hint()` | A distance RANGE with explicit caveats, or a refusal. | None. |
| `calibrate()` | Least-squares fit of reference and exponent from (metres, dBm) pairs. | None. |
| `local_devices()` | Devices on the local subnet with vendor and randomised-MAC flags. | Pings every address on the subnet using a bounded thread pool; counts not measured for timing. |
| `card_report()` | Bands and channels this radio can tune, read from iw phy. | Runs iw; read=False with a populated problem when it cannot. |

## Kill Switches

### `load_profile()`
- **Condition:** No --profile argument given
- **Effect:** Neutral DEFAULTS are used; no profile is ever auto-applied.
- reversible
- Documented as a TRAP block. Matching on hostname or driver and loading silently would apply one unusual machine's tuning to a normal one.

### `scan()`
- **Condition:** Platform has no backend, or the backend returns nothing
- **Effect:** Returns (list, problem) where problem explains why; callers print it and exit 2.
- reversible
- Never returns an empty list silently.

### `distance_hint()`
- **Condition:** dBm below TOO_WEAK_TO_GUESS (-80)
- **Effect:** Returns near_m and far_m as None with an explanation.
- reversible
- The honest spread there spans tens to hundreds of metres.

### `calibrate()`
- **Condition:** Fewer than two distinct distances
- **Effect:** Returns an error dict with no fitted values.
- reversible
- The system is underdetermined; any number returned would be an assumption.

### `watch()`
- **Condition:** mean >= SATURATED, any sample >= CLIPPING, or spread > TOO_NOISY
- **Effect:** usable_for_comparison False plus named warnings.
- reversible
- Checks the top of the range, not only the mean.

### `analyse()`
- **Condition:** Channel in JAPAN_ONLY_24, or outside rules['usable_24']
- **Effect:** usable=False; excluded from recommendation.
- **not reversible**
- Channel 14 is never recommended under any configuration.

## Performance

- **CPU:** Negligible. Scoring is O(channels x networks), at most 14 x ~40. Not measured.
- **MEMORY:** One dataclass per visible network. Not measured.
- **IO:** One scan per invocation; watch() runs one per sample. local_devices() with sweep=True pings every address on the subnet through a bounded thread pool, each with a per-ping timeout. Wall-clock cost not measured.
- **NOTES:** The 30x cost difference between ASCII and box-drawing width measurement is why the plain report uses '#' for bars.

## Security

- **Remote execution:** None. No network listener, no code loaded from data, no eval. Every subprocess takes a fixed argv list and no shell.
- **Data handling:** Nothing is persisted or transmitted. Scan results live only in memory for the process lifetime. Local profiles are read from a gitignored directory and key-validated.
- **Attack surface:** SSIDs are the only attacker-controlled input and are treated as opaque data throughout: never shell-interpolated, never compiled as a pattern, never split by position.
- **Notes:** Shipped profiles are tested to contain no machine name, country or avoid list, so a published profile cannot leak a household.

## Error Conditions

| Error | Cause | Remedy |
|-------|-------|--------|
| `exit 2, 'COULD NOT SCAN: ...'` | No backend for this OS, or the backend returned nothing. | Enable the radio; on macOS grant Location Services. The message distinguishes the cases. |
| `exit 2, 'no profile named X'` | --profile named a profile that exists in neither directory. | --list-profiles. |
| `exit 2, 'profile X sets unknown keys'` | A profile JSON contains a key not in DEFAULTS. | Remove it. Unknown keys are a refusal, not a warning, so a typo cannot silently do nothing. |
| `exit 3, wifi card UNKNOWN` | iw absent or returned nothing. | Install iw. The survey still works; only the capability check is missing. |
| `exit 4, no GUI available` | tkinter is packaged separately on this distribution. | sudo apt install python3-tk. The CLI is unaffected and the message says so. |
| `'no standard configuration produces this rate'` | --rate given a value absent from RATE_TABLE. | Check the figure; it may be a vendor extension or read mid-negotiation. |

## Tasks

### Run the unit tests for this module

Before changing any scoring constant or adding a platform backend.

**Prerequisites:**
- A virtualenv with pytest
- Xvfb for the GUI test, else it skips with a written reason

**Step 1:** .venv/bin/python -m pytest tests/test_wifi_channelsurvey.py tests/test_wifi.py -q
  - Expected: All tests in both files pass; the count is printed by pytest.
**Step 2:** Confirm the GUI test did not skip: add -v and look for test_gui_builds_every_tab_without_entering_mainloop.
  - Expected: It passes on its own Xvfb display and never draws on the real one.

**After this task:** Overlap maths, margin rule, regulatory handling, profile isolation, saturation detection and the distance model are all exercised.

### Verify the module still transplants

The single-file, stdlib-only property is a design requirement, not an accident.

**Prerequisites:**
- A directory outside the repository

**Step 1:** cp fieldkit/wifi/channelsurvey.py /tmp/t/ && cp -r fieldkit/wifi/channel_profiles /tmp/t/
  - Expected: Two items copied.
**Step 2:** cd /tmp/t && python3 -I channelsurvey.py --why
  - Expected: A full argued recommendation. -I disables site-packages and the current directory, so any third-party import would fail here.
**Step 3:** .venv/bin/python -m pytest -k stdlib_only -q
  - Expected: Passes. It walks the AST and asserts every import against an allow-list.

**After this task:** The transplant claim is tested instead of asserted.

### Change a scoring constant safely

PARTIAL_OVERLAP_PENALTY and WORTH_LEAVING_1_6_11 are judgement, and both are deliberately arguable.

**Prerequisites:**
- Read the TRAP comment above each constant first

**Step 1:** Edit the constant, keeping its reasoning comment accurate.
  - Expected: The comment still describes what the number does.
**Step 2:** Run the suite.
  - Expected: test_partial_overlap_is_charged_double_not_half and test_thin_margin_keeps_you_on_1_6_11 constrain the direction, not the exact value.
**Step 3:** Run --score CH against live air before and after.
  - Expected: The arithmetic changes as intended and the recommendation changes for a reason you can read.

**After this task:** The change is defensible from the source alone.

## Troubleshooting

**Symptom:** Different scores from `python3 channelsurvey.py` and `fieldkit wifi channels`.
**Cause:** The two entry points have diverged. This happened once: the own-AP exclusion was added to main() and not to cmd_wifi.
**Remedy:** Both must build cfg and mine identically. There is a comment in cli.py saying so.
**Verify:** Run both with --json and diff the channels array.

**Symptom:** A recommendation that contradicts the tool's own prose.
**Cause:** The scorer and the explanation have drifted apart, which is the exact failure the predecessor module had.
**Remedy:** Use --score CH to see the arithmetic. If it disagrees with warn_about(), one of them is wrong.
**Verify:** test_partial_overlap_is_charged_double_not_half.

**Symptom:** ZeroDivisionError or an absurd ratio.
**Cause:** A channel scored 0, meaning nothing overlaps it, which is the best possible result.
**Remedy:** Use ratio() and say_ratio() instead of dividing directly.
**Verify:** test_an_empty_channel_does_not_crash.

**Symptom:** TclError when building the GUI.
**Cause:** scrolledtext.ScrolledText re-parents itself into a Frame it creates, so widget.master is that internal frame, not the notebook page.
**Remedy:** Hold page frames explicitly; never recover a parent from a child.
**Verify:** test_gui_builds_every_tab_without_entering_mainloop.

**Symptom:** NameError running the file as a script but not as a module.
**Cause:** A function was appended below the __main__ guard. Importing executes the whole file; running calls main() first.
**Remedy:** Keep the guard last.
**Verify:** test_the_entry_point_is_the_last_thing_in_the_file.

## Technical Debt

🟠 **MEDIUM** — Windows, macOS and FreeBSD scan backends are written but have never been executed. → Run scan() on each platform and capture real output as a test fixture. 'OS agnostic' is currently a design claim for three of the four platforms.
🟠 **MEDIUM** — 5 GHz analysis is implemented but untested against real 5 GHz air. → Run --band 5 on a dual-band adapter and check the channel candidate list and width handling.
🟡 **LOW** — The GUI exposes decode_rate but not watch, calibrate or devices. → Add tabs, or document that the window is a subset of the CLI.
🟡 **LOW** — PARTIAL_OVERLAP_PENALTY and WORTH_LEAVING_1_6_11 are reasoned but unmeasured. → They are deliberately single defensible constants instead of a fitted curve, because the input is a scan's signal percentage. Fitting them would need throughput measurements the tool does not take.
🟡 **LOW** — local_devices() sweeps with ping, which some hosts drop. → A device that ignores ICMP and has not recently spoken will be missed. Document, or add an ARP-level probe.

## Impact If Removed

fieldkit wifi channels, card, gui, watch and rate stop existing, and the CLI parser fails at import of the wifi subcommand. doctor.py is independent and would survive. Nothing else in Fieldkit imports channelsurvey. The explain topic channel-score would describe a tool that is not there.

## Claim Sources

| Claim | Basis | Evidence |
|-------|-------|----------|
| Partial overlap is charged double | 📄 stated in input | PARTIAL_OVERLAP_PENALTY = 2.0 |
| A non-1/6/11 channel must beat the best of them by 2.5x | 📄 stated in input | WORTH_LEAVING_1_6_11 = 2.5 |
| The one-metre reference is derived from free-space path loss | 📄 stated in input | FSPL(dB) = 20*log10(frequency in MHz) - 27.55 |
| The hardcoded reference was 20 dB wrong | 📄 stated in input | The hardcoded value was **20 dB out**, a factor of a hundred in power |
| Below -80 dBm no distance is returned | 📄 stated in input | TOO_WEAK_TO_GUESS = -80.0 |
| The maintainer's own AP contributed 89 of channel 13's score of 158 | 📄 stated in input | channel 13 scored 158 of which **89 was the maintainer's own access point sitting on channel 13** |
| No shell is ever invoked | 🤖 model inference | *(none — model judgment)* |
| Nothing is persisted or transmitted | 🤖 model inference | *(none — model judgment)* |
| Resource use is negligible | 🤖 model inference | *(none — model judgment)* |
| The non-Linux backends have never been run | 📄 stated in input | written but never run |


---
**How to verify this document:**
`📄 stated in input` — the model's phrasing of something your source text said.
Find the matching line in the original to verify.
`🤖 model inference` — the model's own judgment or synthesis. Treat as opinion,
not measurement. Re-run on the same input and check whether specific numbers
stay consistent between runs.

*Auto-generated DITA-structured developer documentation.*