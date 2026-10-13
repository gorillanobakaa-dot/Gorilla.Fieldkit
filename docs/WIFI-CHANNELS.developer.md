# channelsurvey: design, scoring model and the two faults it was built to fix

`fieldkit/wifi/channelsurvey.py`. Single file, standard library only, 2026-10-13.

## Constraints it was built to

| Constraint | Consequence |
|---|---|
| Transplantable to any OS | stdlib only, no third-party imports, enforced by a test |
| Must work from a terminal | CLI is the primary interface; the GUI is a wrapper over the same functions |
| Must not assume the host machine | all capability is read, never inferred; profiles are opt-in only |
| Clients are unobservable | stated in output rather than silently omitted |

The stdlib-only rule is enforced, not merely intended:
`test_stdlib_only_so_it_transplants` walks the AST and asserts every `import`
against an allow-list. `tkinter` is imported inside `gui()` so that a machine
without it still runs the CLI.

## The scoring model

For each candidate channel, take the 20 MHz we would occupy as `(mhz-10, mhz+10)`
and intersect it with every observed network's own span, derived from its centre
frequency and width:

```
fraction = overlap_MHz / 20
if 0.05 < fraction < 0.95:
    fraction *= PARTIAL_OVERLAP_PENALTY   # 2.0
score += nuisance * fraction
```

`nuisance` is signal strength (0-100) scaled up for wide networks, 1.5x for
40 MHz on 2.4 GHz, 1.2x elsewhere. A 40 MHz carrier on 2.4 GHz is penalised
harder because it consumes most of a band with only three clear channels.

### Why partial overlap is multiplied, not divided

**This is the central correction and the reason the module exists.**

Fractional weighting is intuitive and wrong. Co-channel interference is managed
by CSMA/CA: both radios decode each other's preamble, defer, and transmit in
turn. Throughput halves but the channel remains orderly.

Adjacent-channel interference defeats carrier sense. The interfering signal is
above the noise floor but not decodable, so neither station defers. Both
transmit, both frames fail CRC, both retransmit, and retransmissions collide.
The effective cost is superlinear in the overlap fraction.

`PARTIAL_OVERLAP_PENALTY = 2.0` is a **deliberate underestimate of a
superlinear effect**, chosen as a single defensible constant rather than a fitted
curve, because the input (a scan's signal percentage) does not justify more
precision. It is `model inference`, not measured on this hardware.

The predecessor module (`fieldkit/wifi/survey.py`, now quarantined in
`Agents.Work.Trash/fieldkit-wifi-survey-superseded-26-10-13/`) used
`weight = (reach + 1 - gap) / (reach + 1)`, i.e. monotonically decreasing with
distance. On live air that scored channel 9 at 45 against channel 11 at 81 and
recommended channel 9, while the same module's own prose explained why adjacent
overlap is worse than co-channel. The tool contradicted itself across two
functions.

### The margin rule

```
WORTH_LEAVING_1_6_11 = 2.5
```

Scoring alone still recommended channel 9 over 11 on a 1.4x margin. A single
scan's error bar is wider than that, and the non-overlapping property of 1/6/11
is time-invariant while the score is not. So `recommend()` returns the lowest
scorer only if it beats the best of 1/6/11 by this factor; otherwise it returns
the conventional channel and sets `margin_too_thin`, which `rationale()` then
explains explicitly rather than hiding.

Rationale: **do not trade a permanent structural advantage for a marginal
measured one.** This is judgement a scoring loop does not contain, and it is the
difference between a number generator and advice.

## Two notes, not one

`ChannelScore` carries `legal_note` and `overlap_note` as **separate** fields.

They were originally one field, assigned in an `if/elif` chain, and `rationale()`
rendered it under the sentence "The catch with channel N:" followed by a
hardcoded paragraph about US regulatory domains. On channel 9 this emitted a
flat falsehood: that channel 9 is UK-legal and US-illegal.

`test_12_and_13_carry_a_legality_note_and_others_do_not` asserts that no channel
outside `UK_EU_ONLY_24` can carry the string `ILLEGAL`.

**Generalisation:** a single string field reused for two kinds of fact will be
rendered into the wrong sentence eventually. The type system was carrying less
information than the prose assumed.

## Capability and regulation

`local_rules()` reads `iw reg get` for the regulatory domain and `iw phy` for the
channel list the card will actually tune. `card_report()` surfaces the same, with
`read: False` and a populated `problem` when it cannot, never a fabricated
default. `card_lines()` prints `UNKNOWN` in that case.

The fallback when `iw` is absent is `range(1, 12)`, the universally legal set,
chosen as the conservative intersection rather than the local machine's actual
capability.

Regulatory domain `00` is reported with the passive-scan consequence for 12/13
and the `iw reg set GB` remedy, because `00` is the conservative world default
rather than a real country and this is invisible otherwise.

## Profiles

`channel_profiles/*.json`, loaded **only** by explicit name.

```json
{"description": "...", "worth_leaving_1_6_11": 1.3, "avoid": [6], "note": "..."}
```

Keys are validated against `DEFAULTS`; unknown keys are a refusal, not a warning.
A profile may adjust judgement constants and exclude channels. It may **not**
declare capability: if a profile claimed channel 13 and `iw phy` disagrees, the
card wins, because measurement outranks configuration.

**Auto-detection is banned by design**, documented as a TRAP block in the source.
Matching on hostname, MAC prefix or driver name and loading silently would apply
the author's unusual 1x1 single-band 2.4-GHz-only tuning to a normal dual-band
laptop. When a profile is active it is announced in `text_report()`,
`rationale()` and the JSON `profile` field.

`test_defaults_are_machine_neutral` asserts the no-profile path equals the module
constants and differs from every shipped profile, so neutrality is a test
rather than a promise.

## Scan backends

| OS | Command | Notes |
|---|---|---|
| Linux | `nmcli -t -f SSID,CHAN,FREQ,SIGNAL,SECURITY dev wifi list` | falls back to `iw dev X scan` |
| macOS | `airport -s` | falls back to `system_profiler -json SPAirPortDataType`; airport is removed on recent versions |
| Windows | `netsh wlan show networks mode=bssid` | present since Vista |
| FreeBSD | `ifconfig wlanN list scan` | |

`_tool()` searches `/usr/sbin`, `/sbin`, `/usr/local/sbin` and the Apple80211
framework path, because those are not on a normal user's `PATH` on Debian or
macOS.

### nmcli field parsing

An SSID may contain a colon, which `nmcli -t` escapes as `\:`. Splitting left to
right shifts every subsequent field and `int()` is then called on `"2462 MHz"`.
`scan_linux()` uses `rsplit(":", 4)` and unescapes afterwards, so the name
absorbs any colons. Covered by
`test_nmcli_ssid_with_a_colon_is_parsed_from_the_right`.

dBm is mapped to a 0-100 quality as `2 * (dbm + 100)`, clamped, matching
NetworkManager's own mapping, so the `iw` and `nmcli` paths are comparable.

## Bugs the test suite found before any user did

1. **`ZeroDivisionError` on an empty channel.** `conv.score / best.score` with a
   score of 0. A score of 0 means nothing overlaps, i.e. the **best possible
   result**, and it is the normal case outside a city. Fixed with `ratio()` and
   `say_ratio()`, which return infinity and the phrase "completely clear
   against". This would have crashed for every rural user.

2. **`TclError` building the GUI.** `scrolledtext.ScrolledText` re-parents itself
   into a `Frame` it creates, so `widget.master` is that internal frame and not
   the notebook page. `pack(before=...)` across that boundary fails. Page frames
   are now held explicitly. No unit test would have caught this; only
   constructing the widget tree does, which is why
   `test_gui_builds_every_tab_without_entering_mainloop` exists and monkeypatches
   `mainloop` to a single `update()` and `destroy()`.

3. **A wrong assertion in the test itself.** The first version asserted channel 2
   overlaps channel 6. It does not: they are exactly 20 MHz apart at 20 MHz
   width, so the edges touch and no more. That adjacency-without-overlap is
   precisely the property 1/6/11 are selected for, and getting it wrong in a
   test would have locked the error in.

## Corrections made after the first version shipped

Each of these was a real defect found by use, not by review. They are recorded
because the pattern matters more than the individual fix.

### The distance model was anchored on an invented constant

`RSSI_AT_1M = -40.0` was hardcoded. The correct figure is derived:

```
FSPL(1 m, dB) = 20*log10(f_MHz) - 27.55     ~40 dB at 2.4 GHz, ~47 dB at 5 GHz
reference     = TX EIRP (14 to 20 dBm) - FSPL
              = about -20 to -26 dBm at 2.4 GHz
```

The hardcoded value was **20 dB out**, a factor of a hundred in power, which
compressed every estimate roughly 5x too close. A measured -34 dBm was reported
as "0.5 to 0.7 metres" for an access point across a room.

**Found by ground truth from outside the tool**: the owner photographed the room
and later measured it as 8 ThinkPad L15 widths, 8 x 360.2 mm = 2.88 m. No
internal consistency check would have caught it, because the model was
self-consistent and wrong.

**Generalisation:** a constant with no derivation and no citation is a guess
with a variable name. Derive it or cite it, or it will be wrong by an order of
magnitude and nothing will notice.

### The path loss exponent could not be a fixed range either

Once the reference was right, -85 dBm produced "43 to 883 metres", because the
model still allowed an open-air exponent for a signal that had obviously
travelled through a building. `PATH_LOSS_BY_SIGNAL` now selects plausible
exponents by signal strength, and below `TOO_WEAK_TO_GUESS` (-80 dBm) it returns
no figure at all. A number nobody can rely on is worse than a refusal.

### Your own access point was counted as interference against itself

`analyse()` scored every visible network against every candidate channel,
including the user's own hub. On live air, channel 13 scored 158 of which **89
was the owner's own access point sitting on channel 13**.

This penalises whichever channel you are already on, every time, and biases the
tool toward recommending a move. The AP being placed is now excluded from
**every** candidate channel, because wherever it moves, it moves with it. A
second AP you own is NOT excluded: it is staying put and is real competition.

**Found by making the tool show its own arithmetic** (`--score CH`). The feature
that explains a number is also the feature that audits it.

### The two entry points disagreed

The own-AP rule was added to the standalone `main()` but not to `cmd_wifi` in
`cli.py`, so `python3 channelsurvey.py` and `fieldkit wifi channels` printed
different scores for the same air. Both now share the rule, with a comment
saying why they must not diverge.

### The `__main__` guard drifted into the middle of the file

Functions appended below `if __name__ == "__main__":` exist when the module is
imported but **not** when it is run as a script, so `--rate` raised `NameError`
from the command line while working perfectly through `fieldkit`. Now enforced
by `test_the_entry_point_is_the_last_thing_in_the_file`.

### The saturation check went quiet exactly when it mattered

`watch()` warned only on the mean. A real run of `97,83,83,99,99,81,92,82`
averages 89.5, ducked under a threshold of 90, and the warning vanished while
three of eight samples were pegged at 99. It now checks the top of the range
(`CLIPPING`) and the spread (`TOO_NOISY`) independently.

**Generalisation:** a guard keyed on an average will fail on the distribution it
exists to catch.

### The output was correct and unusable

The engineering table printed the same jargon line ten times, opened with
"regulatory domain: 00", and put the answer at the bottom. `plain_report()` is
now the default: answer first, a bar chart instead of a score column, each
warning once, and `--technical` for the old view. Tests assert that no warning
repeats and that the word "carrier sense" cannot reach the plain view.

### 12 and 13 were framed only as a hazard

The tool warned that they are US-illegal but never said they are, for a UK or EU
household, spectrum almost nobody is permitted to compete for. `cfg["devices"]`
takes `mixed` (the cautious default) or `local` (the person states every device
here can use them). It is **never inferred**, because it is a fact about
someone's house that no scan can establish. By default the tool offers the
choice explicitly rather than deciding for them.

## Calibration

`calibrate(points)` fits both unknowns from `(metres, dBm)` pairs by least
squares on `rssi = ref - 10*n*log10(d)`.

It **refuses** fewer than two distinct distances. One measurement cannot
separate transmit power from fade: -34 dBm at 2.88 m yields n=1.70 for a weak
transmitter and n=3.00 for a strong one, from "better than open air" to "a
furnished room". It warns at exactly two points that a line through two points
cannot be tested for fit, and at three or more reports the RMS residual.

Every result carries `scope`: valid for one access point in one building, not a
property of wifi.

## Device enumeration

`local_devices()` sweeps the local subnet to populate the neighbour table, then
reads it. Cross-platform via `ip neigh` with an `arp -a` fallback. Vendor
resolution reads whichever local OUI file exists; an unresolved prefix is
printed rather than swallowed, because the prefix is actionable and "unknown" is
not. The locally-administered bit is surfaced as "private address" with an
explanation that it is a privacy feature.

Scope is stated in the output: your own network only. A neighbour's attached
devices are unobservable without monitor mode, and the tool says so rather than
implying the picture is complete.

## Rate decoding

`RATE_TABLE` maps negotiated PHY rates to (standard, width, streams). Only
certain values are achievable, so the rate is a code that pins the radio
configuration exactly. Used to establish that a hub's 5 GHz radio still had both
chains working (2401 Mbit/s = Wi-Fi 6, 160 MHz, 2 streams) without any access to
the hub.

Always annotated as a capability, never a throughput, and a test asserts that
the phrase "not throughput" appears on every decode.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | survey produced |
| 2 | could not scan, or a named profile was invalid; stderr explains which |
| 3 | `wifi card` could not read the radio |
| 4 | `--gui` requested but tkinter is absent |

## Verification

```bash
.venv/bin/python -m pytest tests/test_wifi_channelsurvey.py -q     # 28 passed
python3 fieldkit/wifi/channelsurvey.py --json | python3 -m json.tool >/dev/null
```

On Windows:

```powershell
python -m pytest tests\test_wifi_channelsurvey.py -q
python fieldkit\wifi\channelsurvey.py --json
```

## Claims and their provenance

- Channel centre frequencies, widths, and the 1/6/11 non-overlap property:
  `stated in input` (802.11 channel plan).
- 12/13 UK-legal and US-illegal; 14 Japan-only and no OFDM: `stated in input`.
- Carrier sense failing under adjacent-channel interference: `stated in input`
  (CSMA/CA behaviour).
- `PARTIAL_OVERLAP_PENALTY = 2.0` and `WORTH_LEAVING_1_6_11 = 2.5`:
  **`model inference`.** Defensible constants, not fitted to measured data. They
  are named constants with reasoning in comments so they can be argued with.
- The live scan figures quoted above (channel 9 at 45 vs channel 11 at 81, and
  the later 102 vs 143): **measured** on this machine on 2026-10-13, 20-24
  networks visible, and specific to that location and moment.

## Profiles: what ships and what stays local

Two directories, searched local-first so a private profile shadows a shipped one
of the same name:

```
fieldkit/wifi/channel_profiles/     published: a CLASS of hardware
state/channel_profiles/             gitignored: facts about one address
```

The original `vaio.json` mixed the two. It described a hardware class (a 1x1
single-band card has no 5 GHz escape hatch, so a smaller margin is worth
chasing) AND a household (`"UK sited, second router on 6"` plus a laptop model).
Separately each is harmless; together, and alongside the hub model named in an
explain topic, they narrow to one dwelling.

It is now `single-band-1x1.json`, which names no machine and excludes no
channel, plus a local `home.json` that nobody but its owner sees. Quarantined
original in `Agents.Work.Trash/fieldkit-vaio-profile-26-10-13/`.

Enforced by tests: no shipped profile may contain a machine name, a country, a
possessive, or an `avoid` list, since which channel a neighbour occupies is a
fact about one address. Local profiles may set every field a shipped one can, so
nothing is lost by moving private tuning out of the repo.
