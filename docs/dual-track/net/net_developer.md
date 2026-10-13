# fieldkit.net: route attribution, WAN classification, router identification and antenna specification

> Generated 2026-10-13 | Source: `net`

---

## Purpose

Establishes what is true about a connection before anything measures it, because a measurement taken down the wrong path carries the authority of a number while being wrong. route.py attributes traffic to an interface and reports every default route, not only the winner. router.py identifies the box at a given address and reads its lockout policy before any credential is used. cellular.py converts a modem's reported carrier aggregation into an antenna specification, with a cable loss budget, and refuses to produce one from no band data. Trust level is read-only with one exception: prefer() and unprefer() can alter route metrics and require an explicit apply flag, printing the intended change otherwise. Nothing here writes to a router, submits a credential, or attempts a login.

## Known Alternatives Considered

Not available in the source material. The source records why behaviour-based WAN classification was chosen over asking the router: the hub's own pages do not always say, and latency behaviour cannot misreport itself. It also records why risk() reads the lockout policy instead of automating a login: on the hub examined, the policy was five failures then 120 seconds per attempt, and that reading turned the plan into a decision to ask for a screenshot.

## Architecture

- **Pattern:** Three independent modules of pure functions over subprocess and HTTP reads, sharing no state. Each returns a dict and has a paired lines() renderer, so the CLI never formats domain data itself.
- **Trust boundary:** Router pages are untrusted remote content. They are pattern-matched for vocabulary and asset names and never executed, deserialised into objects, or used to construct a request path. The modem band string is untrusted text; every token is looked up in the BANDS table and unrecognised tokens are reported as unknown instead of being skipped. No credential is ever held, submitted or logged. The band string is additionally treated as unordered: carrier order in a hub's status page varies between firmware revisions and carries no reliable meaning, so nothing is inferred from a token's position within it.
- **Attack surface:** Outbound HTTP GET to a gateway address on the local network, and subprocess calls to ip, nmcli, ping and curl with fixed argv lists. No listener, no shell, no authentication flow. The largest risk is operational instead of technical: a tool that probed a router carelessly could trigger its lockout, which is why risk() exists and why probe() is GET-only.
- **Dependencies:** `subprocess`, `re`, `shutil`, `statistics`, `json`, `urllib`, `platform`

## Flags & Configuration

| Name | Type | Default | Effect | Notes |
|------|------|---------|--------|-------|
| `route` | `action` | `n/a` | Which interface carries traffic to the probe host. | Warns when more than one default route exists. |
| `kind` | `action` | `n/a` | Classifies the WAN as fixed-line, cellular-or-shared, or uncertain. | Judged from jitter and spread, not from the router. |
| `compare` | `action` | `n/a` | Downloads the same file down each interface without changing configuration. | Uses curl --interface, so it measures without altering routing. |
| `router` | `action` | `n/a` | Identifies the device at an address. | --ip selects which box when several exist. |
| `risk` | `action` | `n/a` | Reads the lockout policy from the device's own scripts. | Run before any credential is used anywhere near it. |
| `antenna` | `action` | `n/a` | Produces an antenna specification from reported bands. | Refuses without --bands. |
| `--bands` | `string` | `none` | The modem's carrier list, e.g. B3+B3+B7+n78+n78. | Mandatory for antenna and cellular. Unrecognised tokens are named, not silently dropped. |
| `--cable-m` | `float` | `5.0` | Planned coaxial run in metres. | Drives the loss budget, which can make a purchase net-negative. |
| `--cable` | `string` | `RG174` | Coaxial type for the loss budget. | The default is the thin cable supplied with most kits, deliberately, because that is what people will use. |
| `--ip` | `string` | `the default gateway` | Which device to probe. | Essential when more than one default route exists. |

## API Surface

| Symbol | Description | Side Effects |
|--------|-------------|--------------|
| `gateways()` | Every default route with interface, metric and a wins flag, sorted by metric. | Runs ip route. |
| `gateway_warning()` | A sentence when more than one router exists, or when the OS is unsupported. | Calls gateways(). |
| `route_for()` | Which interface would carry traffic to a host, with link details. | Runs ip route get and reads sysfs. |
| `kind()` | WAN classification from jitter and spread, with the figures it judged from. | Sends pings. |
| `compare()` | Same download down each interface, no configuration change. | Transfers real data; this is deliberate and announced. |
| `identify()` | Manufacturer, server, brand hints and WAN kind for one device. | HTTP GET to that address. |
| `mac_for()` | The MAC of a specific address from the neighbour table. | Runs ip neigh. |
| `risk()` | Lockout policy and crypto handling read from the device's own scripts. | Fetches the device's JavaScript. No credential is sent. |
| `read_status()` | Parses a hub status page into bands, RSRP and SINR with grading. | None. |
| `antenna_fit()` | Whether an antenna's range covers the carriers in use. | None. |
| `antenna_spec()` | Required coverage, count, mounting and cable budget. | None. |
| `quality_proxy()` | A stand-in for SINR on hubs that do not report it, from band composition. | None. |

## Kill Switches

### `route.unsupported_here()`
- **Condition:** platform.system() is not Linux
- **Effect:** Every state-reading action refuses with an explanation; the CLI exits 4.
- reversible
- Added because these modules previously swallowed the missing-binary error and returned empty results, so gateways() read as 'no default route'. The band maths still works everywhere and the refusal says so.

### `route.prefer() / unprefer()`
- **Condition:** apply_it is False, which is the default
- **Effect:** Returns the plan and changes nothing.
- reversible
- The only write path in the package.

### `cellular.antenna_spec()`
- **Condition:** No recognised band tokens
- **Effect:** Returns an error dict with no specification.
- reversible
- A plausible shopping list derived from nothing is the expensive kind of wrong.

### `cellular.antenna_fit()`
- **Condition:** Zero carriers parsed
- **Effect:** Returns CANNOT SAY instead of a pass.
- reversible
- It once reported covering every carrier in use having parsed none: a vacuous pass.

### `shot verdict interlock`
- **Condition:** The WAN kind is unknown
- **Effect:** Downstream interpretation refuses to blame a layer.
- reversible
- kind() exists to feed this. The same numbers mean opposite things on the two line types.

### `router.probe()`
- **Condition:** always
- **Effect:** GET only; no form is ever submitted.
- **not reversible**
- There is no code path that posts a credential.

## Performance

- **CPU:** Negligible; parsing and arithmetic only. Not measured.
- **MEMORY:** A few dicts. Not measured.
- **IO:** route and router: one or two small local HTTP fetches and a few subprocess calls. kind(): a short ping burst. compare(): transfers real data by design, and says so.
- **NOTES:** No polling, no daemon, no background work. compare() is the one function with a real cost, because measuring a download honestly requires downloading something; its byte count is a parameter so it can be reduced on a metered line, and the function announces the transfer before making it.

## Security

- **Remote execution:** None. Router HTML and JavaScript are pattern-matched as text, never executed or deserialised.
- **Data handling:** No credential is held, submitted or logged. Nothing is persisted. Hub status text is parsed in memory.
- **Attack surface:** Outbound GET to a local gateway and fixed-argv subprocesses. No listener, no shell.
- **Notes:** risk() reads a device's lockout policy specifically so that neither a human nor an automated tool begins guessing against it. The operational risk of losing access to your own router is treated as the real one.

## Error Conditions

| Error | Cause | Remedy |
|-------|-------|--------|
| `exit 4, 'fieldkit net reads Linux network state'` | Running on a platform without ip and nmcli. | Use the antenna action, which is arithmetic. The refusal names it. |
| `exit 2, 'pass --bands with the hub's own band line'` | antenna or cellular invoked without band data. | Copy the band line from the hub's status page. |
| `'CANNOT SPEC: no recognised bands'` | The band string parsed to zero known tokens. | Check the format; unknown tokens are listed so the BANDS table can be extended. |
| `'CANNOT SAY: no recognised carriers were read'` | antenna_fit() received nothing it could compare. | Capture the band line. Silence and success must not look alike. |
| `kind: 'not enough replies'` | Fewer than four pings returned. | Check connectivity and retry; it refuses to classify instead of guessing. |
| `'REFUSE THIS CABLE'` | Computed coaxial loss exceeds any plausible antenna gain. | Shorten the run or use low-loss cable. This is advice, not an error. |

## Tasks

### Run the unit tests for this package

Before changing route parsing, the WAN thresholds, or the BANDS table.

**Prerequisites:**
- A virtualenv with pytest

**Step 1:** .venv/bin/python -m pytest tests/test_net_route.py tests/test_net_cellular.py -q
  - Expected: All pass. The count is printed by pytest.
**Step 2:** Confirm the dual-gateway and wrong-MAC cases are present.
  - Expected: Both are built around the real incidents: a probe reaching the wrong box, and one hub's manufacturer attributed to another.
**Step 3:** Confirm no test reaches a real router: check the test files for any address outside the documentation ranges.
  - Expected: Every router interaction is driven through an injected fake. A unit test that touched a live device could trip its lockout, which is the exact harm risk() exists to prevent, and a test suite must never be the thing that locks somebody out of their own hardware.

**After this task:** Route attribution, WAN classification, MAC resolution, antenna specification and every refusal path are exercised.

### Add a band to the BANDS table

A modem reports a carrier the table does not know, and it appears as unknown.

**Prerequisites:**
- The downlink frequency range from the 3GPP band definition

**Step 1:** Add the entry as (low_MHz, high_MHz, description).
  - Expected: test_every_band_entry_is_well_formed constrains the shape.
**Step 2:** Run antenna_spec() with the new token.
  - Expected: It appears in distinct_bands and widens must_cover_mhz as expected.

**After this task:** The band contributes to the coverage range instead of being reported as unknown.

### Verify the WAN classification against a known line

The jitter thresholds decide how every later measurement is interpreted, so they are worth checking against ground truth.

**Prerequisites:**
- A connection whose type you already know

**Step 1:** fieldkit net kind on a known fixed line.
  - Expected: fixed-line, with low jitter and spread.
**Step 2:** The same on a known mobile connection.
  - Expected: cellular-or-shared, and the explanation states that large throughput variation is normal there.
**Step 3:** If either misclassifies, read the figures it reports before touching the thresholds.
  - Expected: The decision is fully visible in min_ms, max_ms, jitter_ms and spread_ms.

**After this task:** The thresholds are confirmed against reality instead of assumed.

## Troubleshooting

**Symptom:** A tool identified the wrong device.
**Cause:** It followed the default route while you meant the other box.
**Remedy:** Pass --ip. gateway_warning() fires whenever more than one default route exists.
**Verify:** test_every_default_gateway_is_listed_not_just_the_winner.

**Symptom:** The MAC reported does not belong to the device probed.
**Cause:** The regression where identify() used the default gateway's MAC regardless of --ip.
**Remedy:** mac_for() resolves per address and returns None instead of a wrong value.
**Verify:** test_the_mac_belongs_to_the_box_probed_not_the_default_gateway.

**Symptom:** Empty results on Windows or macOS.
**Cause:** Historic: missing binaries were swallowed and empty lists returned, reading as real answers.
**Remedy:** unsupported_here() now refuses and says nothing was examined.
**Verify:** test_on_an_unsupported_os_it_refuses_instead_of_reporting_nothing.

**Symptom:** An antenna check passes with no carriers parsed.
**Cause:** The vacuous pass that shipped once.
**Remedy:** antenna_fit() returns CANNOT SAY when total is zero.
**Verify:** test_antenna_fit_refuses_when_nothing_was_parsed.

**Symptom:** A band regex matches across lines.
**Cause:** \s matches newline, so a pattern can span a status page.
**Remedy:** Anchor the band pattern within a line.
**Verify:** Status parsing tests.

## Technical Debt

🟠 **MEDIUM** — router.probe() and risk() have no direct unit tests. → They are the parts that touch a device and reason about its login behaviour, which is where testing matters most. Capture a fixture page and assert the parsing.
🟡 **LOW** — The WAN jitter thresholds are reasoned, not fitted. → They were chosen from one cellular hub and one fixed line. Collect figures from more connections before tightening them.
🟡 **LOW** — Wall and cable loss constants are order-of-magnitude estimates. → Marked as inference in the output. Measuring them would need equipment the toolkit does not assume.
🟡 **LOW** — BANDS covers UK carriers only. → Unknown tokens are reported instead of dropped, so the failure is visible. Extend as other regions appear.
🟠 **MEDIUM** — compare() transfers real data on a possibly metered connection. → It announces this, but a default byte count that small enough to be free would be safer for the intended audience.

## Impact If Removed

fieldkit net disappears from the CLI and the net group leaves docs coverage incomplete. More importantly the interlock in fieldkit.shot loses its supplier: verdict() requires a WAN kind and kind() is what establishes it, so screenshot-based diagnosis would have to refuse every time or go back to guessing. The antenna advice has no other source in the toolkit. The explain topics covering antenna numbers and connectors would also describe calculations with no implementation behind them, which is worse than describing nothing.

## Claim Sources

| Claim | Basis | Evidence |
|-------|-------|----------|
| Lower metric wins and a cable can carry nothing | 📄 stated in input | Gorilla.WIFI   metric 600     <- lower wins |
| Measured cellular jitter: min 13.6, max 31.5, mdev 5.5 ms | 📄 stated in input | min 13.6, max 31.5, mdev 5.5 ms |
| The lockout policy read was five failures then 120 seconds | 📄 stated in input | lock out after 5 failures, then 120 seconds per attempt |
| antenna_fit once reported a vacuous pass | 📄 stated in input | Reporting "covers every carrier" here would be a vacuous pass |
| identify() used the default gateway's MAC regardless of --ip | 📄 stated in input | identify() used to report the DEFAULT GATEWAY's MAC no matter which --ip was probed |
| Buying one antenna can cost half the throughput | 📄 stated in input | can cost half the throughput |
| No credential is ever submitted | 🤖 model inference | *(none — model judgment)* |
| Router content is never executed or deserialised | 🤖 model inference | *(none — model judgment)* |
| Resource use is negligible | 🤖 model inference | *(none — model judgment)* |
| Wall and cable loss figures are estimates | 📄 stated in input | model inference, order-of-magnitude, for explaining the trade-off |


---
**How to verify this document:**
`📄 stated in input` — the model's phrasing of something your source text said.
Find the matching line in the original to verify.
`🤖 model inference` — the model's own judgment or synthesis. Treat as opinion,
not measurement. Re-run on the same input and check whether specific numbers
stay consistent between runs.

*Auto-generated DITA-structured developer documentation.*