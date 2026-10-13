# Fieldkit v0.3.0, for developers

## Scope

`v0.2.0..v0.3.0` is 22 commits. The suite is run by the pre-commit hook on every commit (the whole suite plus
`fieldkit privacy scan . --git`) and by `.github/workflows/tests.yml` on `windows-latest` and `ubuntu-latest` for
every pull request. The release workflow publishes only after the same suite passes on both runners for the tagged
commit.

Measured on the build machine (Sony VAIO SVE, i7-3632QM, Debian, Python 3.13): **2029 passed, 56 skipped, 0
failed in 8m08s**. The skips are platform-gated, mostly Windows-only paths; none is a disabled test.

New modules: `fieldkit/wifi/channelsurvey.py`, `fieldkit/net/{route,router,cellular}.py`,
`fieldkit/core/names.py`, `fieldkit/explain/`, `fieldkit/voice/`, `fieldkit/watchdog/`.

## fieldkit.wifi: channel selection that shows its arithmetic

`channelsurvey.py` is 2140 lines, one file, standard library only, Python 3.8 or later. It can be copied to a
machine with no Fieldkit install and run unchanged, which is deliberate: the audience includes people who will be
handed one file.

Pattern: pure functions over a dataclass, with platform dispatch at the single `scan()` boundary. No global state
except module constants. The CLI and the tkinter GUI call identical analysis functions; the GUI holds no logic of
its own. `tkinter` is imported inside `gui()` only, so a machine without it loses the window and nothing else
(exit 4 with the package name to install).

Commands: `fieldkit wifi {doctor,scan,channels,card,upgrade,gui,watch,rate,devices,calibrate}`, plus `--score CH`,
`--case CH`, `--check CH`, `--networks`, `--devices {mixed,local}`, `--mine SSID`, `--count-mine`, `--profile`,
`--technical`, `--band {2.4,5}`, `--avoid`, `--at METRES:DBM`, `--no-sweep`.

`devices` and `calibrate` were reachable only from the standalone file until this release. Writing the
plain-language track found it: the track described `fieldkit wifi devices` and the command did not exist. That is
the second divergence between these two front doors, after the own-AP rule, which made them print different scores
for the same air. `test_both_front_doors_offer_the_same_things` now asserts the parity.

**Every judgement constant is named and carries its reasoning in-source**, because an unexplained constant is a
guess with a variable name:

| Constant | Value | Why |
|---|---|---|
| `PARTIAL_OVERLAP_PENALTY` | `2.0` | A half-overlap defeats carrier sense. Co-channel radios defer to each other; partially overlapping ones cannot decode each other's preambles and transmit over the top. Scoring overlap as a smooth function of channel distance gets this backwards, which is what the quarantined predecessor did. |
| `WORTH_LEAVING_1_6_11` | `2.5` | The margin rule. A non-overlapping channel is a permanent structural advantage; a measured score is a snapshot of one afternoon. A thin win does not justify leaving 1, 6 or 11. |
| `SATURATED` / `CLIPPING` / `TOO_NOISY` | `90` / `95` / `12` | `watch()` refuses a saturated vantage point. The check is on the top of the range and the spread, not the mean: a real run of `97,83,83,99,99,81,92,82` averages 89.5 and ducks under a mean threshold while three samples are pegged. |
| `TX_EIRP_RANGE` | `(14.0, 20.0)` dBm | A weak access point to the legal maximum. Distance is estimated across the range, which is why the answer is a range. |
| `TOO_WEAK_TO_GUESS` | `-80.0` dBm | Below this the honest spread is tens to hundreds of metres, so `distance_hint()` returns `None` with an explanation instead of a figure. |

**Trust boundary.** Everything a scan returns is attacker-controlled: an SSID is arbitrary bytes chosen by a
stranger. SSIDs are never interpolated into a shell string, never compiled as a regular expression and never split
by position. `nmcli -t` output is parsed right to left, because the first field can contain the separator. Numeric
fields are validated before `int()`. Every subprocess takes a fixed argv list; no shell is ever invoked. The only
externally influenced value reaching a subprocess is an IP address in `_sweep()`, built from a validated subnet
prefix.

**Kill switches**, all reversible except the last:

- `load_profile()`: with no `--profile`, neutral `DEFAULTS` apply. No profile is ever auto-selected. The in-source
  TRAP block records why: matching on hostname or driver and loading silently would impose one unusual machine's
  tuning on a normal one. A VAIO profile exists and must be asked for by name.
- `scan()`: returns `(list, problem)`. An empty list is never returned silently; the problem distinguishes "no
  backend for this OS" from "the backend returned nothing".
- `distance_hint()` and `calibrate()`: refuse rather than return an underdetermined figure. `calibrate()` needs two
  distinct distances; one reading cannot separate distance from attenuation.
- `analyse()`: channel 14 is never recommended under any configuration.

**Two bugs worth recording, both found by the tool auditing itself.**

`--score` exists because of the first. The scorer counted the operator's own access point as interference against
itself: 89 points of channel 13's score of 158 came from the radio being placed. `--mine` now defaults to the
currently associated SSID via `current_network()`, and `--count-mine` restores the biased behaviour so the bias can
be demonstrated rather than argued about.

The second was a hardcoded `RSSI_AT_1M = -40.0`, roughly 20 dB wrong, which made every distance estimate wrong by a
large factor. The reference is now derived from free-space path loss, `FSPL(dB) = 20*log10(f_MHz) - 27.55`, with the
path loss exponent selected by signal strength (2.0 to 3.0 open air, up to 4.5 through walls). It was caught by a
user measuring a real distance in ThinkPad widths and comparing.

Tests: `tests/test_wifi_channelsurvey.py`, 96 tests. The GUI test runs under a private Xvfb display, because it
previously drew on the operator's real screen.

## fieldkit.net: establish the path before measuring it

Three independent modules of pure functions sharing no state, each returning a dict with a paired `lines()`
renderer so the CLI never formats domain data itself.

`route.py` (300 lines). `gateways()` returns every default route with interface, metric and a `wins` flag, sorted
by metric; `gateway_warning()` produces a sentence when more than one router claims the default. This exists
because of a measured failure on the development machine: a cable to 192.168.1.1 at metric 900 lost to wifi to
192.168.0.1 at metric 600, so every byte went out over a different hub than the one that was plugged in, and a
throughput measurement down that path would have carried the authority of a number while being wrong.
`unsupported_here()` makes every state-reading action refuse on non-Linux with an explanation and exit 4. These
modules previously swallowed the missing-binary error and returned empty results, so `gateways()` read as "no
default route". The band arithmetic still works everywhere and the refusal says so.

`router.py` (285 lines). `identify()` is an HTTP GET to one address on the local network; the page is untrusted
remote content, pattern-matched for vocabulary and asset names, never executed, never deserialised into objects,
never used to build a request path. `risk()` reads the device's own JavaScript for its lockout policy before any
credential goes anywhere near it. On the hub examined the policy was five failures then 120 seconds per attempt,
and reading it turned an automation plan into a decision to ask the owner for a screenshot. No credential is ever
held, submitted or logged.

`cellular.py` (557 lines). `read_status()` parses a hub status page into bands, RSRP and SINR with grading.
`antenna_spec()` returns required coverage, count, mounting and a cable loss budget; it refuses without `--bands`
rather than producing a specification from no band data. The band string is untrusted text: every token is looked
up in `BANDS` and unrecognised tokens are reported as unknown instead of skipped. It is also treated as unordered,
because carrier order on a hub's status page varies between firmware revisions and carries no reliable meaning.
`--cable` defaults to `RG174`, the thin cable supplied with most kits, deliberately, because that is what people
will use, and a long enough run of it loses more than the aerial gains.

`compare()` transfers real data using `curl --interface`, so it measures without altering routing. It announces
the transfer first. This is listed below as a known weakness: the default byte count should be small enough to be
free on a metered connection.

Tests: `tests/test_net_route.py` (20), `tests/test_net_cellular.py` (16).

## fieldkit.core.names: parse names chosen by someone else

144 lines, extracted because a neighbour's SSID is `:(` and a colon is the `nmcli -t` field separator. nmcli
escapes it as `\:`, so the fix is to unescape before matching and to split from the right. `fieldkit/watchdog`
had the same bug and is fixed in the same release.

Tests: `tests/test_core_names.py`, 17 tests over brackets, punctuation, emoji, right-to-left text and
command-shaped names. The contract is that nothing crashes and nothing is silently mangled.

## fieldkit.voice: a neural voice, wired in at the system level

piper plus `en_US-amy-medium` (60 MB, fetched once), installed by `fieldkit voice install --apply` and registered
as a speech-dispatcher generic module, so every program on the machine that speaks inherits it: the herald, the
laptop alerts, desktop notifications, Orca and Claude Code's own `speechSynthesis`. espeak-ng stays registered as
a fallback so the machine is never mute.

Settled configuration, reached by auditioning eleven models on the actual alert texts:

```
voice             en_US-amy-medium
pace              --length-scale 1.06
pitch             -2.5 semitones, rubberband -F (formants preserved)
sentence silence  --sentence-silence 0.1
audio             sox norm -1, nothing else
```

Four findings are recorded as numbered notes in `fieldkit/voice/__init__.py`, because each one is a trap the next
person will walk into:

- **`sox pitch` is a WSOLA time-stretch plus a resample.** Splice artefacts accumulate, so a long sentence degrades
  while a short phrase sounds fine, and it moves formants, which is how an accent change gets synthesised by
  accident. `rubberband -F` preserves formants. Tune on the sentences the program will actually say, not on test
  phrases.
- **`< file` is not a pipe.** `rubberband` makes two passes and seeks backwards, so it cannot read a pipe. Tested as
  `rubberband - - < in.wav` it looks pipe-safe, because a shell redirect gives seekable stdin. Through a real pipe
  it silently truncated 70700 bytes to 4184. The wrapper uses a temporary file, and a test asserts it stays that
  way. Generalisation: when a tool must live in a pipeline, prove it with `cat x |`, never with `< x`.
- **The audio chain must never silence an alert.** With no `rubberband`, or no writable temporary file, the wrapper
  plays the plain voice. A higher-pitched voice beats no voice when the point of the thing is to speak while the
  laptop is offline. A test asserts the fallback path exists.
- **A calm voice is correct for an emergency.** Written down specifically so a later reader does not "fix" it to
  sound urgent.

`speakable.py` fixes text before anything says it. Hyphens are corrected automatically (a compound becomes a space,
a punctuation dash becomes a comma), because every kind of dash, not only `U+002D`, makes piper insert a halting
pause mid-word. Homophone risks and over-long sentences are reported rather than rewritten. `-40 dBm` and `--json`
survive unchanged.

Alert texts were shortened rather than sped up:

```
before  "The processor is at 91 degrees. That is dangerous. Save your work and close heavy programs."   8.58s
after   "91 degrees. Save your work now."                                                              3.39s
```

No change of pace. Less than half the time, purely by removing words that were not information.

`FIELDKIT_VOICE_SILENT=1` stops the suite speaking aloud while it runs.

Tests: `tests/test_voice.py` (15), `tests/test_voice_speakable.py` (24).

## fieldkit.explain: answers at no model cost

Topics rendered from files that ship with the package. No model call, no network. Eight topics, including
`hostile-names.md` and its Romanian translation. Tests: `tests/test_explain.py`, 12 tests, written after a
`pytest -k explain` run matched unrelated tests in `buildh` and was briefly mistaken for coverage.

## Also in this release

- `fieldkit privacy scan` reports files examined as well as findings. "0 findings" and "0 findings because it never
  ran" were indistinguishable, and that ambiguity was acted on twice in one session. The count now comes back from
  `privacy.scan_path(..., examined=[])`, because only the scanner knows which files it opened: the first version of
  this fix counted the CLI's own path arguments and printed "out of 1 file(s) scanned" for a folder of five.
- `fieldkit/release.py` had two definitions of `check()`. The second wins at import, so the first never ran; it was
  half of `check()` spliced to half of `prove()` and would have raised `NameError` on `runs`, `platform` and `host`
  if it ever had. Four helpers beside it were byte-identical second copies. Nothing failed, because the behaviour
  was correct; the dead copy was simply the one a reader finds first. Removed, and
  `test_no_module_defines_the_same_function_twice` now parses every file under `fieldkit/` and asserts no top-level
  name occurs twice.
- The dual-track glossary check matched a term followed by an em-dash and only that, so removing em-dashes from published
  text (banned here) silently dropped the glossary count to zero and failed the gate. It now accepts a colon or a
  hyphen as well.
- `tests/test_buildh_herald.py` no longer depends on the ambient `FIELDKIT_VOICE_SILENT`. Two tests there pass an
  injected runner and failed when the variable was exported, because the switch is a hard gate that returns before
  the injection point. An autouse fixture clears it; the tests that want it set it themselves. A suite whose result
  depends on the shell it was started from is not a suite.
- `fieldkit shot` refuses to attribute a fault to a layer it has not identified.
- `kits/videokit`: `_xfade_join` renders video and audio in separate passes when `audio and len(files) >= 4`.
  Bisection: 2 and 3 inputs are correct, 4 and above fail; each filter chain alone is correct at 8 inputs. The fault
  is in combining them in one graph, not in `acrossfade`, which was the first and wrong diagnosis.
- `hooks/pre-commit` searches `$VIRTUAL_ENV/bin/python`, `./.venv/bin/python`, `python3`, `python`, and verifies
  each candidate can import pytest, so "could not run the tests" is no longer reported as "tests failed".
- `fieldkit peers`: messages between AI sessions on one computer, using Gorilla OpenCode's protocol (#13).
- Dual-track documentation for `wifi` and `net`, both tracks complete, in `docs/dual-track/`.

## Known weaknesses

Carried from the dual-track documents, unedited, because a limitation discovered later is worth less than one
stated now.

- **MEDIUM.** The Windows, macOS and FreeBSD scan backends are written and have never been executed. "OS agnostic"
  is a design claim for three of the four platforms. Fix: run `scan()` on each and capture real output as a
  fixture.
- **MEDIUM.** 5 GHz analysis is implemented and untested against real 5 GHz air; the development machine has no
  5 GHz radio.
- **MEDIUM.** `router.probe()` and `risk()` have no direct unit tests, and they are the parts that touch a device
  and reason about its login behaviour. Fix: capture a fixture page and assert the parsing.
- **MEDIUM.** `net compare()` transfers real data on a possibly metered connection. It announces this, but a
  smaller default byte count would be safer for the intended audience.
- **LOW.** `PARTIAL_OVERLAP_PENALTY` and `WORTH_LEAVING_1_6_11` are reasoned and unmeasured. They are deliberately
  single defensible constants instead of a fitted curve, because the input is a scan's signal percentage. Fitting
  them would need throughput measurements the tool does not take.
- **LOW.** Wall and cable loss constants are order-of-magnitude estimates, marked as inference in the output.
  Measuring them would need equipment the toolkit does not assume.
- **LOW.** `BANDS` covers United Kingdom carriers only. Unknown tokens are reported rather than dropped, so the gap
  is visible.
- **LOW.** `local_devices()` sweeps with ping. A device that ignores ICMP and has not recently spoken will be
  missed.
- **LOW.** The GUI exposes `decode_rate` but not `watch`, `calibrate` or `devices`.
