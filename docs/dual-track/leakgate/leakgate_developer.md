# `fieldkit.leakgate`: fail-closed leak, telemetry and network-behaviour release gate for Gorilla Firefox

> Generated 2026-10-02 | Source: `leakgate`

---

## Purpose

`fieldkit.leakgate` is the release gate for Gorilla Firefox builds. It runs 13 scenarios against throwaway copies of a build zip, collects observations from independent sensors, judges every observation against an approved allowlist under 18 fail-closed policies, and runs static audits of the source tree, the packaged files, the PE imports and `Cargo.lock`. `FINAL_RESULT` is `PASS` only when all 18 policies pass. The primary rule, from `__init__.py`: no allowlist entry means unexpected and FAIL, an unapproved entry means pending and FAIL, and a policy whose required sensors did not collect means FAIL, never PASS by absence. Approval has one code path: `allow.approve()`, which calls `task.owner_terminal()` itself and takes no parameter that can override it; `tests/test_leakgate.py::test_no_approval_path_exists_without_the_owner_terminal_check` AST-scans every module of the package and fails if any other function writes an `approval` key or is named `approve*`. Trust level: the gate is the evidence the maintainer's publish step reads (it writes `state/leakgate_result.json` under the maintainer's Gorilla Firefox root), so a false PASS here becomes a published privacy claim. The Windows sensors are in use; the Linux network-namespace runner in `linux.py` is declared authoritative but is untested on Linux as of 2026-10-02.

## Known Alternatives Considered

The source documents three choices against alternatives. (1) A disposable VM or Linux network namespace is the specification's environment; the Windows run is an accepted compromise, and `gate.py` says: "On this Windows laptop the environment is not the spec's disposable VM". Wire DNS that no browser sensor saw is therefore reported as `UNATTRIBUTED_WIRE_DNS` and fails only for vendor or tracker names. (2) Observation-driven allowlisting is rejected for hosts: `allow.propose()` says "destinations and DNS names are never proposed from observation: an unexpected host stays unexpected until the owner writes it in". (3) Model approval is rejected: "a model proposes entries, it never approves them". A chat approval route for dispositions was also rejected and deleted: "a chat route (`approve_from_chat`) that skipped the terminal check was removed with the unused `build()`". The `NET_API` regex records a rejected broader match: "a global fetch( only: `keywords.fetch(`, `this.#fetch(` and `async fetch({` methods are not network calls (02 Oct: 9 false hits)". The measurements add the motivating case for multiple sensors: an earlier DNS-cache check passed Gorilla 155.0.1, while the browser's own HTTP log showed 8 Mozilla hosts contacted.

## Architecture

- **Pattern:** Pipeline: build copies -> local fixtures (test server, DoH resolver, certificate servers, optional firewall rule) -> scenario x mode x repetition runs -> sensor events (`{sensor, scenario, mode, kind, value, port, detail}`) -> `judge()` per policy with required-sensor checks -> post-run checks (poisoned fallbacks, certificates, plaintext, shutdown, TLS versions, regression, reproducibility, allowlist schema) -> static audits -> result files. Sensors never judge; `judge()` never collects.
- **Trust boundary:** Trusted: `allow.json` entries whose `approval` is set, `dispositions.json` entries whose `approval` is truthy, `baseline.json`, the test definition's own hosts per scenario (`SCENARIOS[i][4]`), and the build zip handed over by the build job. Not trusted: every observation from the browser, the browser's own logs (cross-checked against proxy, socket table and packets), and any proposal written by a model. Allowlist approval trust rests on `task.owner_terminal()`, which is `sys.stdin.isatty() and sys.stdout.isatty()`, called inside `allow.approve()` with no override parameter. Disposition approvals have no writer in Fieldkit: the audits only read them, so their trust rests on write access to the file.
- **Attack surface:** Local only. The test server, certificate servers, DoH server and mitmproxy bind `127.0.0.1` (Linux: `10.77.0.1` on the veth). The DoH server forwards allowlisted names to the system resolver via `socket.getaddrinfo`. `firewall_block()` interpolates the executable path into a PowerShell command inside single quotes. `allow.json`, `dispositions.json` and `baseline.json` are read as trusted input; anyone with write access to the maintainer's root can change the verdict.
- **Dependencies:** `mitmproxy (mitmdump)`, `dpkt`, `cryptography`, `pefile`, `pktmon (Windows, elevated)`, `PowerShell Get-CimInstance / Get-NetTCPConnection / Get-NetUDPEndpoint / New-NetFirewallRule`, `fieldkit.buildh.task`, `fieldkit.buildh.proof (VENDOR_HOST, AD_HOSTS)`, `Linux: ip, nft, dnsmasq, tcpdump, strace, ss (required); netsniff-ng, tshark, zeek, suricata, conntrack (optional)`

## Flags & Configuration

| Name | Type | Default | Effect | Notes |
|------|------|---------|--------|-------|
| `--release` | `bool` | `false` | Sets `repeat = max(repeat, 3)`, `quick = False` (full durations and watch windows), adds the `poisoned` mode for `POISONED` scenarios; `leakgate-baseline` accepts only a result with `release_run` true. | Does not itself enforce elevation; without packets the network, DNS, shutdown, WebRTC, IPv6 and TLS policies fail. |
| `--repeat` | `int` | `1` | Repetitions per scenario and mode. | Below three, `REPRODUCIBILITY_POLICY` fails. |
| `--soak` | `int` | `none` | Overrides the `startup-idle` duration in seconds. | CLI help cites the specification values 1800 or 3600. |
| `--firewall` | `bool` | `false` | When elevated, adds an outbound Windows Firewall block for the direct copy's `firefox.exe` to all non-loopback ranges; removed in the `finally` block. | Ignored when not elevated. A hard kill leaves rule `leakgate-<run>` in place. |
| `--only` | `string` | `all` | Comma-separated scenario names to run. | CLI help text describes it as a post-install option only; `leakgate` reads it too. |
| `QUICK` | `dict` | `{'startup-idle': 120, 'workers': 45}` | Quick-mode durations; watch windows are capped at 10 s in quick mode. | Quick mode always adds a `SHUTDOWN_POLICY` failure. |
| `NECKO` | `string` | `nsHttp:3,nsHostResolver:5,nsSocketTransport:4,timestamp` | `MOZ_LOG` modules for the direct mode. | Parsed with `URL_RX`, `RES_RX`, `SOCK_RX`; a log format change in Firefox silently reduces events, but the required-sensor check fails if `necko-http` or `necko-dns` produce none. |

## API Surface

| Symbol | Description | Side Effects |
|--------|-------------|--------------|
| `gate.run()` | Runs the whole gate and writes the run folder. | Creates build copies, starts local servers and mitmdump, optional firewall rule, Linux namespace; writes `test-results.json`, `events.jsonl`, `build-manifest.json`, audit JSON files, `*.observed`, `*.new`. |
| `gate.judge()` | Applies the fail-closed policies to events. | Resolves observed names with `socket.getaddrinfo` to attribute `dest-ip` events. |
| `gate.save_baseline()` | Writes per-host request counts as `baseline.json`. | Overwrites `leakgate/baseline.json`. |
| `gate.stop_started()` | Stops a `Popen` this gate started, by PID only. No-op if `p.poll()` is not None. On `win32`: `taskkill /PID <pid> /T /F`. Elsewhere: `terminate()`, `wait(timeout=wait)`, then `kill()` and a second wait on `TimeoutExpired`. | Terminates one process (and, on Windows, its children). |
| `gate.ensure_ca()` | Creates `mitm-conf/mitmproxy-ca-cert.pem` by starting mitmdump once (polling up to 30 s), then `stop_started()`. mitmdump is taken from `PATH`, else `Scripts/mitmdump.exe` beside the Python interpreter on Windows or `mitmdump` beside it elsewhere. | Writes the run CA (including its private key) under the run folder. |
| `allow.verdict()` | Matches one observation with `fnmatch` globs, scenario and port. | none |
| `allow.problems()` | Schema check: missing exception-process fields and duplicate ids. | none |
| `allow.propose()` | Adds unapproved entries for `process`, `file`, `file-system`, `udp`, `listener` observations. | Writes `allow.json`. |
| `allow.approve()` | Maintainer-only approval at a real terminal; the only function in the package that writes an `approval`. Calls `task.owner_terminal()` unconditionally; `ids` may contain `*proposed*` to approve every unapproved entry. | Raises `task.Refused` without a TTY; otherwise prints each entry and writes `allow.json`. |
| `audit.static_audit()` | Inventories network-capable source files in 25 components. | Reads the source tree. |
| `audit.binary_audit()` | Diffs hostnames embedded in `omni.ja`, `browser/omni.ja` and `xul.dll` against the previous release. | none |
| `audit.executable_audit()` | PE import audit for network DLLs; new executables since N-1. | none |
| `audit.dependency_audit()` | New, removed and network-capable crates from `Cargo.lock`. | none |
| `dispositions.classify()` | Assigns a disposition category by longest `REASONS` prefix, lock marker, Remote Settings-only API or packaging. With `shipped_names()`, all that remains of the module after `build()` and `approve_from_chat()` were removed; neither has a caller in `fieldkit/`. | none |
| `extras.DohServer` | RFC 8484 resolver on `127.0.0.1`; allowlisted names answered, others NXDOMAIN; logs every query. | Starts an HTTPS server thread; forwards allowed names to the system resolver. |
| `extras.CertServers` | Four local HTTPS servers: valid, expired, wronghost, selfsigned. | Writes certificate and key files; starts servers. |
| `sensors.run_one()` | One scenario in one mode on Windows. | Starts Firefox, mitmdump, pktmon; kills only its own PIDs with `taskkill /T`. |
| `linux.selftest()` | Reports root and tool availability before a Linux run. | none |
| `linux.run_one()` | Linux twin inside namespace `lg` with nftables DROP, dnsmasq, tcpdump, strace. | Requires root; starts dnsmasq, tcpdump, optional netsniff-ng; reads `journalctl -k`. |

## Kill Switches

### `gate.judge() required-sensor table `need``
- **Condition:** A policy's required sensor produced no event
- **Effect:** The policy fails with `required sensor(s) did not collect`
- reversible
- Core fail-closed mechanism.

### `allow.approve() / task.owner_terminal()`
- **Condition:** stdin or stdout is not a TTY
- **Effect:** Raises `task.Refused`; nothing is approved
- reversible
- Also guards `leakgate-baseline` in `buildh/cli.py`. The former `terminal=` override parameter is gone: passing it raises `TypeError` before anything is written (asserted in the tests).

### `extras.firewall_block() / firewall_unblock()`
- **Condition:** `--firewall` and elevated
- **Effect:** Outbound block for the direct copy; unblocked in `finally`
- reversible
- Not removed after a hard kill or power loss.

### `buildh/cli.py leakgate exit code`
- **Condition:** `FINAL_RESULT` is `FAIL`
- **Effect:** Returns 3; `state/leakgate_result.json` records the failure for the publish step
- reversible
- Returns 0 only on PASS.

## Dead Code

- **`dispositions.classify() / shipped_names()`** — No caller in `fieldkit/`; `gate.run()` only reads an existing `dispositions.json`. Their former caller `build()` was removed. (risk: Low to remove, but they are the only code that assigns disposition categories; wire them to a CLI action or delete them.)
- **`Unused imports`** — pyflakes (2026-10-02): `json` in `audit.py` and `extras.py`; `tempfile` in `extras.py` and `sensors.py`; `hashlib` in `sensors.py`; `os` in `gate.py`. `dispositions.py` is clean since `json` and `time` went with the removed functions. (risk: None.)
- **`extras.graceful_close() docstring`** — Says it returns True when the root process exits; it returns the number of windows posted. (risk: None; misleading for callers.)
- **`linux.TOOLS optional entries `tshark`, `conntrack``** — Reported by `selftest()` but never invoked. (risk: None.)

## Performance

- **CPU:** Not measured.
- **MEMORY:** Not measured. `fs_snapshot()` holds size and mtime for every file under the watched Mozilla directories; `extras.tls_details()` and `parse_pcap()` read full captures.
- **IO:** Not measured. Each run unpacks the build zip three times and copies the zip into the run folder; release runs keep pcaps per scenario and mode.
- **NOTES:** Wall-clock time is not measured. Scripted durations: 13 scenarios, release `startup-idle` 300 s plus 60 s watch, quick 120 s plus 10 s; each scenario runs in two to four modes and `repeat` times. `snapshot_processes()` spawns a PowerShell `Get-CimInstance` every 2 s plus once per 2 s in the post-kill watch.

## Security

- **Remote execution:** None received. The gate executes the build under test, mitmdump with a generated addon file, PowerShell, pktmon and (Linux) root tools. `firewall_block()` builds a PowerShell command string from the executable path; a path containing a single quote breaks quoting.
- **Data handling:** Stores decrypted request headers and bodies (`mitm-*.raw`), necko logs, socket and process snapshots, and on Windows a whole-host pktmon capture that includes other programs' traffic. A run CA private key (`mitmproxy-ca.pem`) is written to the run folder and installed only into the proxied and DNS-controlled copies through `policies.json`.
- **Attack surface:** Loopback servers for the duration of a run. The DoH server resolves allowlisted names through the host resolver. Approval files and the baseline are trusted input.
- **Notes:** Approval security is a TTY check, not an identity check. There is a single approval writer, `allow.approve()`, with no override parameter, and a structural test fails the suite if a second one appears in the package. Disposition approvals have no writer and no shape check: `audit.py` treats any truthy `approval` value as approved. `gate.run()` on Linux monkeypatches `sensors.build_copy` at module level. Helper processes are stopped by PID (`stop_started()`, and `taskkill /PID` in `sensors.run_one()`), never by name.

## Error Conditions

| Error | Cause | Remedy |
|-------|-------|--------|
| `required sensor(s) did not collect: [...]` | A sensor named in `need` produced no event (for example mitmdump never started, or the necko log is empty). | Run the scenario with `--only` and check the artifacts listed in `artifacts.json` for that run. |
| `packet-level sensor not run (needs an elevated shell): wire traffic unverified` | `sensors.is_admin()` returned false. | Run from an elevated PowerShell. |
| `ran N time(s); the spec requires at least 3 identical runs` | `repeat < 3`. | Use `--release` or `--repeat 3`. |
| `no approved baseline (leakgate/baseline.json): run leakgate-baseline after the first approved release PASS` | No `baseline.json`. | Not available in the source material for the first release; after a release PASS, run `leakgate-baseline`. |
| `no previous release to diff against` | No backup manifest with a different installed version was found under the backups folder. | Install the previous release through the harness so a backup zip exists. |
| `no previous Cargo.lock to compare with` | `n_minus_1_tree` missing, or the hard-coded sibling repository `155.0.1` is absent. | Provide the N-1 tree; see technical debt. |
| `the certificate page never reported` | The `/certs` page did not POST `/result`. | Check the certificate server ports and the run's CA. |
| `proxy did not take` | In `page` proxied mode, no mitm event contained the real page's host. | Check mitmdump starts and the proxy policy is in `distribution/policies.json` of the proxied copy. |
| `approval is the owner's, at a real terminal; an agent's shell has none` | `owner_terminal()` false. | The maintainer runs `leakgate-approve` interactively. |
| `leakgate (linux): not root or missing required tools [...]` | `linux.selftest()` not ok. | Run as root with `ip`, `nft`, `dnsmasq`, `tcpdump`, `strace`, `ss`, `mitmdump` installed. |
| `nft: <stderr>` | nftables rejected the namespace ruleset. | Check kernel nftables support; `netns_down()` runs in `finally`. |
| `TypeError: approve() got an unexpected keyword argument 'terminal'` | A caller written for the old signature passes `terminal=`. | Remove the argument; there is no replacement, by design. |

## Tasks

### Run the unit tests for this group

Before you change `judge()` or `allow.py`, confirm the fail-closed behaviour still holds.

**Prerequisites:**
- Python with `pytest`
- Working directory: the Fieldkit repository root

**Step 1:** Run the tests:

```powershell
python -m pytest tests/test_leakgate.py -q
```

  - Expected: - **Pass:** every test passes. They cover allowed/pending/unexpected verdicts, refusal of approval without a terminal and `TypeError` for the removed `terminal=` argument, fail-closed judging, failure on missing packets and too few repeats, the AST scan for any second approval path, and `ensure_ca()` stopping only its own mitmdump on `linux` and `win32`.
  - **Fail:** any failure means a fail-closed or approval guarantee changed.

**After this task:** The verdict, approval and required-sensor rules behave as documented.

### Run a quick gate on the current build job

Use during porting to list what still fails. A quick run cannot PASS.

**Prerequisites:**
- A build job with `build-result.json` and a zip artifact
- `mitmdump`, `dpkt`, `cryptography`, `pefile` installed
- `leakgate/allow.json` under the maintainer's Gorilla Firefox root

**Step 1:** Run one scenario as a smoke test:

```powershell
fieldkit build-harness leakgate --only canary
```

  - Expected: - **Pass:** `canary` runs in `direct`, `proxied` and `dns-controlled`, the audits run, and 18 policy lines print with `FINAL_RESULT FAIL` (reproducibility and shutdown fail in quick mode).
**Step 2:** Run all scenarios:

```powershell
fieldkit build-harness leakgate
```

  - Expected: - **Pass:** exit code 3 with per-policy reasons; the run folder path prints after `FINAL_RESULT`.
**Step 3:** Propose entries for observed local kinds:

```powershell
fieldkit build-harness leakgate-propose
```

  - Expected: - **Pass:** `proposed N entr(ies)`; new entries have `approval: null` and `OBSERVED - owner to name` placeholders.

**After this task:** `allow.json` contains unapproved proposals; nothing passes until the maintainer approves them.

### Approve entries and run a release gate

The maintainer's final check before publishing.

**Prerequisites:**
- An interactive terminal (not an agent shell)
- An elevated PowerShell for the release run
- A backup of the previous release and the N-1 tree

**Step 1:** Approve all unapproved entries (each is printed first):

```powershell
fieldkit build-harness leakgate-approve
```

  - Expected: - **Pass:** `approved N: [...]`.
  - **Fail:** `task.Refused` when not at a TTY.
**Step 2:** Approve specific ids instead (the first argument is the job name, for example `firefox-157.0`):

```powershell
fieldkit build-harness leakgate-approve firefox-157.0 <entry-id>
```

  - Expected: - **Pass:** only the named ids are approved.
**Step 3:** Run the release gate from an elevated shell:

```powershell
fieldkit build-harness leakgate --release --firewall
```

  - Expected: - **Pass:** `packets ON`, `repeat 3`, `release durations`, exit code 0 and `FINAL_RESULT PASS`.
  - **Fail:** exit code 3; read `WHY` in `test-results.json`.
**Step 4:** Save the regression baseline:

```powershell
fieldkit build-harness leakgate-baseline
```

  - Expected: - **Pass:** `baseline saved: .../leakgate/baseline.json`.
  - **Fail:** `a baseline is taken only from a release-mode PASS`.

**After this task:** `state/leakgate_result.json` records a release PASS keyed by build id, and the next release is compared with the saved baseline.

### Prepare a first Linux run

The Linux runner is untested. The `ensure_ca()` blocker is fixed and unit-tested with a fake `Popen`, but no real Linux run is recorded; check prerequisites before any run.

**Prerequisites:**
- Debian, root
- `ip`, `nft`, `dnsmasq`, `tcpdump`, `strace`, `ss`, `mitmdump`

**Step 1:** Run the self-test from Python (the `fieldkit leakgate-linux selftest` command named in the `linux.py` docstring is not registered in `fieldkit/cli.py`):

```powershell
python -c "from fieldkit.leakgate import linux; print(linux.selftest())"
```

  - Expected: - **Pass:** `'ok': True` and an empty `missing_required` list.
**Step 2:** As root on Debian, from the Fieldkit folder, run one scenario:

```bash
fieldkit build-harness leakgate --only canary
```

  - Expected: - **Pass:** not available in the source material; no Linux run is recorded.
  - **Fail:** expected on a first run: `linux.run_one()` emits no `mitm`, `necko-http`, `necko-dns`, `process-tree` or `filesystem` events, so the policies that require them fail with `required sensor(s) did not collect`. Do not pass `--firewall` on Linux (see technical debt).

**After this task:** Not available in the source material: no Linux run has been recorded.

## Troubleshooting

**Symptom:** `PROXY_POLICY` FAIL with `direct connection while a proxy was pinned`
**Cause:** The socket sampler saw a non-loopback TCP connection from the proxied or poisoned copy.
**Remedy:** Check `UNEXPECTED_DESTINATIONS` for the address and find the component that bypasses the proxy.
**Verify:** Re-run with `--only <scenario>` and confirm no `proxy-bypass` event in `events.jsonl`.

**Symptom:** `UNATTRIBUTED_WIRE_DNS` lists many names
**Cause:** Other programs on the Windows host resolved names during the capture window.
**Remedy:** Expected on Windows; only vendor or tracker names fail. Use the Linux runner for an isolated result once it is tested.
**Verify:** `DNS_POLICY` reasons contain no `bypass?` entries.

**Symptom:** `TELEMETRY_POLICY` FAIL from the filesystem sensor
**Cause:** A file matching `TELEMETRY_FILES` (for example `datareporting/*` or `*glean*`) appeared in the profile.
**Remedy:** Find the writer in the source audit inventory and cut it.
**Verify:** `UNEXPECTED_TELEMETRY` is empty in a new run.

**Symptom:** `REPRODUCIBILITY_POLICY` FAIL with `host set differs between runs`
**Cause:** Direct-mode host sets differ across repetitions (time-based or randomised fetches).
**Remedy:** Inspect the listed hosts and `periodicity.json` for regular intervals.
**Verify:** Three repetitions give identical host sets.

**Symptom:** A `leakgate-*` firewall rule remains after a crash
**Cause:** The process died before the `finally` block.
**Remedy:** `Remove-NetFirewallRule -DisplayName 'leakgate-<run>'` in an elevated shell.
**Verify:** `Get-NetFirewallRule -DisplayName 'leakgate-*'` returns nothing.

## Technical Debt

🔴 **HIGH** — Linux runner untested. The former first blocker, `gate.ensure_ca()` calling `taskkill`, is fixed: it uses `stop_started()` and a POSIX mitmdump fallback, covered by a unit test with a fake `Popen` only → Run `linux.selftest()` and one scenario on Debian as root; record the result in MEASUREMENTS.md.
🔴 **HIGH** — `linux.run_one()` starts no mitmdump, writes no necko events, takes no process-tree or filesystem events, and ignores `graceful` and `poison` → Port the mitm and filesystem sensors; otherwise `PROXY_POLICY`, `CANARY_POLICY`, `TELEMETRY_POLICY`, `PROCESS_POLICY` and `FILESYSTEM_POLICY` fail for missing sensors on Linux.
🟠 **MEDIUM** — `linux.run_one()` re-reads the shared `dns.log` every run, so earlier queries are attributed to later scenarios → Use a log file per run.
🟠 **MEDIUM** — `fieldkit leakgate-linux selftest` is documented but not registered → Add the action to `fieldkit/cli.py` or correct the docstring.
🟠 **MEDIUM** — Hard-coded values: `REAL_PAGE` URL, the `page` proxy check that looks for the real page's host name in mitm events, `configure_record()` objdir `C:/gfobj` and a mozconfig path under the home folder, the N-1 `Cargo.lock` taken from a sibling `155.0.1` repository, `release: "157.0"` in proposals → Move these into the task metadata or settings.
🟠 **MEDIUM** — First-release baseline: `REGRESSION_POLICY` fails without `baseline.json`, and a baseline is saved only from a release PASS → Document or implement the bootstrap path.
🟠 **MEDIUM** — No writer and no schema for disposition approvals: `build()` and `approve_from_chat()` were removed, and `audit.py` accepts any truthy `approval` in `dispositions.json` → Add a disposition approval action in `buildh/cli.py` that calls `task.owner_terminal()` and records `by`, `at` and `how`, and make `audit.py` require that shape (as `allow.problems()` does for the allowlist).
🟡 **LOW** — `firewall_block()` interpolates a path into a PowerShell string → Pass the path as a separate argument or escape single quotes.
🟡 **LOW** — `--firewall` on Linux: `packets` is always true there, so `extras.firewall_block()` runs `powershell` and would raise `FileNotFoundError` → Skip the firewall step on Linux, where the namespace nftables DROP already enforces egress.
🟡 **LOW** — Unused imports reported by pyflakes → Remove them.

## Impact If Removed

`fieldkit build-harness leakgate`, `leakgate-approve`, `leakgate-propose` and `leakgate-baseline` fail with an import error in `fieldkit/buildh/cli.py`. The maintainer's publish step loses `state/leakgate_result.json`, so no build has fail-closed evidence that it makes no unapproved connections. The source inventory of 103 network-capable files in 25 components and its OCSP decision left to the maintainer (from the measurements) can no longer be regenerated or checked against a build.

## Claim Sources

| Claim | Basis | Evidence |
|-------|-------|----------|
| Missing sensors fail the policy | 📄 stated in input | never PASS by absence |
| Release runs require packets and three repetitions | 📄 stated in input | A release run (--release) requires the packet sensor (elevated shell) and 3 repetitions. |
| Hosts are never proposed from observation | 📄 stated in input | an unexpected host stays unexpected until the owner writes it in |
| The Linux runner is untested | 📄 stated in input | UNTESTED ON LINUX AS OF 2026-10-02 |
| Windows is not the specification's environment | 📄 stated in input | the environment is not the spec's disposable VM |
| 103 files in 25 components, OCSP left to the maintainer | 📄 stated in input | 103 network-capable source files in 25 components, each with a disposition; 1 left as an owner decision (OCSP) |
| An earlier DNS-cache check missed 8 Mozilla hosts | 📄 stated in input | the earlier DNS-cache check had passed it |
| A quick run cannot reach PASS | 🤖 model inference | *(none — model judgment)* |
| Linux runs fail several policies for missing sensors | 🤖 model inference | *(none — model judgment)* |
| `leakgate-linux selftest` is not a registered command | 🤖 model inference | *(none — model judgment)* |
| The Windows packet capture includes other programs' traffic | 🤖 model inference | *(none — model judgment)* |
| Approval security is a TTY check, not an identity check | 🤖 model inference | *(none — model judgment)* |
| `allow.approve()` has no terminal override | 📄 stated in input | There is no parameter that stands in for the terminal check |
| The chat approval route and `build()` were removed | 📄 stated in input | a chat route (`approve_from_chat`) that skipped the terminal check was removed with the unused `build()` |
| `stop_started()` uses taskkill only on Windows | 📄 stated in input | taskkill exists only on Windows: the Linux runner terminates its own child and kills it if it lingers |
| `classify()` and `shipped_names()` have no caller in `fieldkit/` | 🤖 model inference | *(none — model judgment)* |
| Disposition approvals have no writer and no shape check | 🤖 model inference | *(none — model judgment)* |
| `--firewall` would raise on Linux | 🤖 model inference | *(none — model judgment)* |


---
**How to verify this document:**
`📄 stated in input` — the model's phrasing of something your source text said.
Find the matching line in the original to verify.
`🤖 model inference` — the model's own judgment or synthesis. Treat as opinion,
not measurement. Re-run on the same input and check whether specific numbers
stay consistent between runs.

*Auto-generated DITA-structured developer documentation.*