# Fieldkit builds, releases and thermal: `fieldkit/build/`, `fieldkit/release.py`, `fieldkit/thermal/`

> Generated 2026-10-02 | Source: `builds-releases-thermal`

---

## Purpose

This group (1,240 lines of Python) turns past build and release failures into deterministic checks. `build/triage.py` names a build failure from a log with regex signatures kept as YAML data, and reports UNRECOGNISED with a paste-ready stub instead of guessing. `build/refcheck.py` proves every named file, link or local import exists. `build/lifecycle.py` runs install, upgrade and uninstall under snapshots and reports leftovers; since 2026-10-02 it fails closed when a snapshot part was not captured (`not_compared` from `core.snapshot.diff`), so an unread part can no longer pass as clean. `build/kernel.py` holds the Debian kernel pipeline stages and the tested helpers behind them (64-character `uname` guard, config fragment apply and verify, `sha256sums.asc` check). `release.py` is a publish gate: published bytes must equal tested bytes, and every claim found in the release notes needs a passing proof; there is no `--force`. `thermal/sensors.py` ranks Windows CPU temperature sources by proof under load, and `thermal/governor.py` moves `PROCTHROTTLEMAX` through `powercfg` during a build and kills it on a dead sensor or at 95 C; `fieldkit thermal watch` now wraps its loop in `try/finally`, so Ctrl+C stops and joins the governor, whose own `finally` restores the original cap. Trust level: triage, refcheck and `thermal status` are read-only. `lifecycle`, `release check`, the kernel stages and the governor execute commands or change system state, and trust their spec or pipeline file completely.

## Known Alternatives Considered

Triage: Red Hat/Fedora Log Detective reads failed build logs with a model; this module takes the idea and does it deterministically, "regex signatures kept as data ... no model involved". Thermal: Linux uses `intel_pstate` plus `thermald` in the kernel and reads the die through `coretemp`/`hwmon`; "Windows has no such API" and otherwise "leaves it to the firmware trip", so the governor reimplements the loop in user space with `powercfg`. Release: a `--force` flag is rejected by design ("no --force: a failing gate means fix the release or fix the check"). Release export: `git archive` with `core.autocrlf=false` instead of the working tree, because autocrlf made identical code report as "published != tested". Kernel: `fragment_from_injector()` reads `MANDATORY_FLAGS` with `ast.literal_eval` instead of importing the injector, "never executing it".

## Architecture

- **Pattern:** Pure functions returning result dicts plus `lines()` formatters for a short human/small-model answer; pipeline stage functions `stage_*(ctx, ...)` called by the `core.pipeline` engine; one `threading.Thread` subclass (`Governor`) with injectable clock, sleep, sensor, perf and cap functions for tests.
- **Trust boundary:** Trusted: spec YAML files (`releases/*.yaml`, lifecycle specs), pipeline YAML, signature YAML, and the `.config`/fragment files; commands in them run unsandboxed. Untrusted and only parsed: build logs (regex only, capped at 200 error lines and 400 characters per line), Markdown and Python files (`ast.parse`, never imported), the kernel injector (`ast.literal_eval`), the downloaded kernel tarball (sha256 checked; `tarfile.extractall(filter="data")`), and GitHub responses (hash-compared). Sensor values are untrusted until `prove_live()` passes.
- **Attack surface:** A hostile spec file achieves command execution through `tests`, `proof.command` (`release.py`) or `install/upgrade/uninstall.cmd` (`lifecycle.py`). A hostile signature YAML can supply a catastrophic-backtracking regex (denial of service on large logs). A hostile kernel `.config` fragment changes what kernel is built. A process that writes `C:\ProgramData\TPFanControl\status.txt` or opens a window titled `TPFanControl...` with a `Fan: ... Switch:` edit control can feed the governor fake temperatures, although `prove_live()` requires the value to move under load first.
- **Dependencies:** `ast`, `csv`, `datetime`, `fnmatch`, `hashlib`, `json`, `re`, `shutil`, `subprocess`, `tarfile`, `tempfile`, `threading`, `urllib.request`, `zipfile`, `ctypes (Windows only, sensors)`, `fieldkit.core.settings`, `fieldkit.core.snapshot`, `fieldkit.core.proc.Runner`, `fieldkit.core.privacy`, `fieldkit.core.host`, `external: git, gh, powercfg, powershell, taskkill, make, dpkg-query`

## Flags & Configuration

| Name | Type | Default | Effect | Notes |
|------|------|---------|--------|-------|
| `fieldkit triage LOG --set` | `string` | `auto` | Signature set: `auto` (every set), `firefox-windows`, `debian-kernel`, `debian-packaging`. | Exit 0 only on verdict `known`; `success`, `unrecognised` and `no-error-lines` all exit 3. The `triage.py` docstring says `fieldkit build triage`; the real subcommand is `fieldkit triage`. |
| `fieldkit refcheck RULE TARGET` | `string` | `none` | `manifest`, `markdown`, `python` or `regex`. | Exit 3 when anything is missing. |
| `--base` | `string` | `manifest's folder` | refcheck manifest: folder the listed paths resolve against. | Ignored by the other rules. |
| `--pattern` | `string` | `none` | refcheck regex: pattern whose group 1 names a path. | Required for `regex`; the CLI exits with a message otherwise. |
| `--glob` | `string` | `*` | refcheck regex: which files under TARGET to scan (recursive). | Targets resolve against the file's folder, then TARGET. |
| `fieldkit release check|prove SPEC` | `string` | `none` | `check` runs every gate; `prove` runs the tag's tests here and writes `evidence/<name>-<tag>-<platform>.json` next to the spec. | No `--force` exists. Exit 3 on DO NOT PUBLISH or on a failed prove. |
| `fieldkit lifecycle SPEC --approve` | `bool` | `false` | Without it `lifecycle.run()` raises `PermissionError` and nothing runs. | Exit 3 when not CLEAN, including when any snapshot part is `not_compared` (printed as `NOT COMPARED: <reason>`). |
| `fieldkit kernel localversion --base --tags --year` | `string/list/int` | `--year 2` | Prints the `LOCALVERSION` and `uname` that fit in 64 characters, and which tags were dropped. | Pure calculation; no build. |
| `fieldkit kernel fragment INJECTOR --out` | `string` | `stdout` | Extracts `MANDATORY_FLAGS` to YAML `{flags: ...}`. | Needs PyYAML for `--out`. |
| `fieldkit thermal status|prove|watch` | `string` | `none` | `status` reads every provider plus cap and perf; `prove` runs `sensors.best(prove=True)`; `watch` runs a `Governor` whose kill callback only prints. | `watch` still calls the real `set_cap()` and changes the power scheme. The watch loop in `cli.cmd_thermal()` sits in `try/finally: gov.stop(); gov.join(timeout=15)`, so a `KeyboardInterrupt` restores the cap before it propagates (Python prints a traceback). |
| `--seconds` | `int` | `60` | thermal watch duration. | The proof test runs before this time starts. |
| `--target` | `float` | `75.0` | thermal watch target in C. | Step-down above target + 3; step-up below target - 6 for `RELAX_S`. |

## API Surface

| Symbol | Description | Side Effects |
|--------|-------------|--------------|
| `triage.triage_text()` | Extract error lines, match signatures, return verdict known|success|unrecognised|no-error-lines. | Reads signature YAML. |
| `triage.triage_file()` | `triage_text` on a file read with `errors='replace'`. | Reads the log. |
| `triage.extract_errors()` | Distinct error lines in first-seen order, first matching extractor wins. | none |
| `triage.load_signatures()` | Load and compile a set; raises if `id`, `pattern`, `cause` or `fix` is missing. | Reads YAML. |
| `refcheck.check_manifest()` | Each non-comment line must exist under base. | none |
| `refcheck.check_markdown()` | Relative Markdown links under root must resolve. | none |
| `refcheck.check_python()` | Absolute imports of root's own top-level packages must resolve. | none |
| `refcheck.check_regex()` | Group 1 of each match must exist. | none |
| `lifecycle.run()` | Snapshot, install/upgrade/uninstall, verify, leftovers diff. A part with `not_compared` becomes a leftover entry with that reason, forcing `clean` false. | Runs spec commands; writes logs under `state/lifecycle/<name>`. |
| `lifecycle.lines()` | Verdict, one line per step with verify lines, then per part `LEFTOVERS in <part>:`, an optional `NOT COMPARED: <reason>` line, and at most 20 `+` added and 20 `~` changed keys. | none |
| `kernel.localversion()` | Build a LOCALVERSION that fits; drops feature tags from the end. | none; raises ValueError if prefix plus timestamp cannot fit. |
| `kernel.apply_fragment()` | Set flags in `.config`, idempotent. | Rewrites `.config`. |
| `kernel.verify_fragment()` | Flags whose value differs after `olddefconfig`; absent counts as `n`. | none |
| `kernel.fragment_from_injector()` | `MANDATORY_FLAGS` via `ast.literal_eval`. | none |
| `kernel.stage_deps/fetch/extract/config/build/collect()` | Pipeline stages for `build/pipelines/debian-kernel.yaml`. | Network (fetch), disk, `make`, moves `.deb` files. |
| `release.check()` | All gates; verdict CLEAR or DO NOT PUBLISH. | Temp dir, runs spec commands, `gh` network calls. |
| `release.prove()` | Run tests on the exported tag here; write evidence JSON. | Writes evidence file containing `host()`. |
| `release.evidence_gate()` | Accept evidence only for matching platform, tree id and pass. | Runs `git rev-parse`. |
| `release.export_tag()` | `git archive` with `core.autocrlf=false`, extracted. | Writes under dest. |
| `sensors.best()` | First provider that reads and proves live; regrades surface sensors with a longer load. | Spawns `os.cpu_count()` busy Python processes. |
| `sensors.prove_live()` | Require a 2 C rise or spread under full load; append die/surface grade. | Spawns and kills load processes. |
| `sensors.grade()` | `die` when the rise is at least `DIE_RISE` (10.0). | none |
| `sensors.parse_status()` | Parse the TPFanControl `Switch:` status line. | none |
| `governor.Governor` | Daemon thread: hysteretic cap control and kill rules. | Changes `PROCTHROTTLEMAX` (AC and DC); optional CSV. |
| `governor.read_cap()/set_cap()` | Query or set `PROCTHROTTLEMAX` on `scheme_current`. | `set_cap` writes both AC and DC and re-activates the scheme. |

## Kill Switches

### ``governor.Governor.run()``
- **Condition:** `t >= KILL` (95.0 C)
- **Effect:** `_die()`: verdict set, cap to `FLOOR` (30), `kill(why)` called, loop ends.
- reversible
- `finally` restores `_orig_cap`.

### ``governor.Governor.run()``
- **Condition:** `dead_check` and 60 consecutive busy samples (`perf > 50`) and only one distinct value seen since start
- **Effect:** Build killed as a dead sensor.
- reversible
- `_seen` is never reset, so any movement since start disarms this check for the run.

### ``governor.Governor.run()``
- **Condition:** No reading for `STALE_S` (30 s)
- **Effect:** Build killed.
- reversible
- None

### ``governor.kill_tree(pid)``
- **Condition:** Called by the build caller
- **Effect:** `taskkill /PID pid /T /F`.
- **not reversible**
- Kills the whole process tree; unsaved objects can be left half-written.

### ``lifecycle.run()``
- **Condition:** `approve` is false
- **Effect:** Raises `PermissionError` before any snapshot or command.
- reversible
- None

### ``lifecycle.run()` loop`
- **Condition:** install or upgrade fails its exit code or verify
- **Effect:** Breaks; uninstall is not run.
- reversible
- Leftovers are then not computed (`after-uninstall` missing), and the verdict is NOT CLEAN.

### ``lifecycle.run()` leftovers loop`
- **Condition:** `snapshot.diff()` returns a part with `not_compared` (the part was stored as not captured before or after: query failed, timed out, or returned unreadable output)
- **Effect:** The part is recorded in `leftovers` with its `not_compared` reason even when `added` and `changed` are empty, so `clean` is false and the verdict is NOT CLEAN; `lines()` prints `NOT COMPARED: <reason>`.
- reversible
- Fail closed: "unread is not clean". Before 2026-10-02 an uncaptured part diffed as empty and the lifecycle could report CLEAN. Covered by `tests/test_lifecycle_not_compared.py`.

### ``release.check()``
- **Condition:** Any gate `ok` false
- **Effect:** Verdict DO NOT PUBLISH; CLI exit 3.
- reversible
- No override flag.

### ``kernel.stage_fetch()``
- **Condition:** sha256 mismatch
- **Effect:** Tarball renamed to `.xz.bad`; stage fails.
- reversible
- A file absent from `sha256sums.asc` fails without being renamed.

### ``cli.cmd_thermal()` (`watch`; outside this group's sources)`
- **Condition:** `KeyboardInterrupt` (Ctrl+C) or any exception in the watch loop
- **Effect:** `finally` calls `gov.stop()` (sets `_halt`, which also wakes `_sleep`) and `gov.join(timeout=15)`; the thread leaves its loop and `Governor.run()`'s `finally` writes `_orig_cap` back if `cap` differs.
- reversible
- Covered by `test_thermal_watch_stops_and_joins_the_governor_on_ctrl_c` (fake governor). If the thread is still inside a sensor or `perf_percent()` PowerShell call when the join times out, the daemon thread dies at interpreter exit and the restore can be skipped.

## Dead Code

- **``sensors.tpfan_window()`, first comment block inside the `if temp is not None` branch`** — Stale documentation instead of dead code: it says the function takes the hotter of two values, but the code returns `vec[0]` only. (risk: None if removed; keeping it misleads a maintainer.)
- **``governor.Governor` `max_cap` path`** — Not dead, but unreachable from `fieldkit thermal watch`, which never passes `max_cap`; only another caller can reach it. Which caller is not available in the source material of this group. (risk: Removing it would break that caller.)

## Performance

- **CPU:** `prove_live()` saturates `os.cpu_count()` cores for `settle + samples*interval` = 20 s by default; a surface-graded sensor gets a second run of 45 + 8*2 = 61 s. The governor itself samples every 3 s and spawns PowerShell for `perf_percent()` each sample; cost not measured.
- **MEMORY:** Triage reads the whole log into memory (`read_text`); memory use for large logs not measured.
- **IO:** `sha256_file()` streams in 1 MiB chunks. `release.check()` extracts a full tag archive to a temp dir and deletes it. Lifecycle snapshots hash watched paths; cost not measured.
- **NOTES:** The whole Fieldkit suite: 584 passed, 1 skipped, 2 xfailed in 194.89 s; per-module timings not measured.

## Security

- **Remote execution:** No network listener. Spec files are executable configuration: `release.py` and `lifecycle.py` run their commands with `subprocess` (list form, no shell). `_sh()` replaces `python`/`python3` with `sys.executable`.
- **Data handling:** `release.prove()` writes `host()` (OS, release, machine, CPU count, Python version) and test output tails to evidence JSON. `release.check()` runs `privacy.scan_path` on the exported tree before tests write into it. Thermal reads only temperatures and processor performance.
- **Attack surface:** Spec, pipeline and signature YAML; the TPFanControl status file and window text; kernel tarball (hash-verified against `sha256sums.asc` fetched over HTTPS, but the `.asc` signature itself is not verified with GPG).
- **Notes:** `set_cap()` changes a system power setting without elevation. Normal stop, a kill rule and Ctrl+C during `fieldkit thermal watch` all restore the original cap; closing the console, `taskkill`, power loss, or a governor thread that outlives the 15 s join can still leave it lowered. `tarfile.extractall(filter="data")` blocks path traversal on supported Python versions.

## Error Conditions

| Error | Cause | Remedy |
|-------|-------|--------|
| `a lifecycle run installs and removes software: it needs --approve from the owner` | `approve=False`. | Read the spec; pass `--approve`. |
| `LEFTOVERS in <part>: / NOT COMPARED: <reason>` | The snapshot query for that part failed, timed out or returned something unreadable before or after (`not_compared` from `snapshot.diff`). | Re-run when the host is idle, or drop the part from `watch.parts` and verify it by hand; the lifecycle stays NOT CLEAN until every listed part is compared. |
| `no signature set 'X'; have [...]` | Unknown `--set` name. | Use a name from `available_sets()` or `auto`. |
| `<file>: signature 'id' lacks 'fix'` | Signature YAML entry missing a required key. | Add `id`, `pattern`, `cause` and `fix`. |
| `refcheck regex needs --pattern with one capture group` | `regex` rule without `--pattern`. | Supply a pattern with group 1. |
| `tag vX not found in <local>` | Tag absent from the local checkout. | Create or fetch the tag. |
| `the spec declares no tests: nothing proves the release works` | Empty `tests:`. | Declare at least one test command. |
| `the notes make this claim and the spec has no proof for it` | Claim regex matched the notes; no `proof`. | Add a `command` or `evidence` proof, or remove the claim from the notes. |
| `no evidence from linux - run `fieldkit release prove` there` | Missing evidence file. | Run `fieldkit release prove` on that platform and copy the file back. |
| `linux: tested tree <id> is not vX (<id>)` | Evidence recorded for different content. | Re-run prove on the tagged tree. |
| `sha256 mismatch: expected ..., got ...; file moved aside` | Corrupt or tampered kernel tarball. | Delete `.xz.bad` and fetch again. |
| `N option(s) missing after olddefconfig: config change-set not present` | Dependencies off, so Kconfig dropped requested options. | Enable the dependencies in the fragment; inspect `missing_after_olddefconfig`. |
| `cannot fit prefix+timestamp in 64 chars` | Base version plus brand prefix plus timestamp exceed 64. | Shorten the prefix or use `--year 2`. |
| `NO LIVE SOURCE: None: ...` | No provider moved by `rise` under full load. | Start TPFanControl, or do not run a governed build on this machine. |
| `sensor stuck at X C since the start, 60 busy samples` | Frozen die sensor under load. | Re-prove sensors; inspect the provider. |

## Tasks

### Run the unit tests for this group

After any change to triage, refcheck, lifecycle, kernel, release or thermal.

**Prerequisites:**
- Fieldkit checkout
- pytest installed

**Step 1:** Run:

```bash
python -m pytest -q tests/test_triage.py tests/test_refcheck.py tests/test_lifecycle.py tests/test_lifecycle_not_compared.py tests/test_kernel.py tests/test_debian_kernel.py tests/test_release.py tests/test_thermal.py
```
  - Expected: All selected tests pass. The group's share of the 584-test suite is not measured separately.

**After this task:** Signatures load, gates behave as tested. The real `powercfg` and real sensors are not exercised by these tests.

### Add a signature for a new build failure

`fieldkit triage` returned UNRECOGNISED.

**Prerequisites:**
- The failing log

**Step 1:** Run:

```bash
fieldkit triage build.log --set auto
```
  - Expected: UNRECOGNISED with a `- id: CHANGEME` stub.
**Step 2:** Paste the stub under `signatures:` in `fieldkit/build/signatures/<set>.yaml`; fill `id`, `cause`, `fix`; tighten `pattern`.
  - Expected: YAML parses.
**Step 3:** Run:

```bash
fieldkit triage build.log --set <set>
```
  - Expected: `KNOWN <id>` and exit 0. `test_every_signature_set_loads` still passes (ids unique).

**After this task:** The failure is named on every future run.

### Prove a release across two platforms

Release notes claim Windows and Linux support.

**Prerequisites:**
- Tag pushed
- `gh` signed in
- Spec with `proof: {evidence: [windows, linux]}`

**Step 1:** On Debian: `fieldkit release prove releases/<project>.yaml`
  - Expected: `PASSED on linux ... evidence: .../evidence/<name>-<tag>-linux.json`.
**Step 2:** On Windows: `fieldkit release prove releases/<project>.yaml`
  - Expected: `PASSED on windows ...`.
**Step 3:** Copy the Linux evidence file into the Windows `releases/evidence/`; run `fieldkit release check releases/<project>.yaml`
  - Expected: `CLEAR` and `NEXT: publish.`; otherwise each FAIL with its reason.

**After this task:** Every claim in the notes has a passing proof for the same git tree id.

### Check sensor trust before a governed build

Before a long compile on a Windows laptop.

**Prerequisites:**
- Mains power
- TPFanControl running if available

**Step 1:** `fieldkit thermal status`
  - Expected: Five provider lines and `cap (PROCTHROTTLEMAX): N%`.
**Step 2:** `fieldkit thermal prove`
  - Expected: `PROVEN: <name>: idle ... [die sensor]` (exit 0); `NO LIVE SOURCE` exits 3.
**Step 3:** `fieldkit thermal status`
  - Expected: Cap unchanged from step 1 (`prove` does not touch it).

**After this task:** You know which provider the governor will use and its grade.

### Confirm Ctrl+C restores the cap on real hardware

The unit test uses a fake governor; this checks the real `powercfg` path once on the target laptop.

**Prerequisites:**
- Windows
- Mains power
- A provider that passes `fieldkit thermal prove`

**Step 1:** Record the cap:

```powershell
fieldkit thermal status
```
  - Expected: `cap (PROCTHROTTLEMAX): N%`.
**Step 2:** Start a watch, wait for the first `cap ... peak` lines, then press Ctrl+C once:

```powershell
fieldkit thermal watch --seconds 120 --target 75
```
  - Expected: A `KeyboardInterrupt` traceback; if the cap had moved, `thermal: cap: restored to N%` first.
**Step 3:** Read the cap again:

```powershell
fieldkit thermal status
```
  - Expected: The same `N%` as step 1.

**After this task:** The restore path works with the real `powercfg` on this machine. A DC value that differed from AC before the run is now equal to the AC value (known debt).

## Troubleshooting

**Symptom:** Processor stays slow after `fieldkit thermal watch`.
**Cause:** The process ended without `Governor.run()`'s `finally` running: console window closed, `taskkill`, power loss, or the governor thread still inside a PowerShell sensor call when `gov.join(timeout=15)` returned, so the daemon thread was killed at interpreter exit. Ctrl+C alone no longer causes this.
**Remedy:** `powercfg /setacvalueindex scheme_current sub_processor PROCTHROTTLEMAX 100`, the same with `/setdcvalueindex`, then `powercfg /setactive scheme_current`.
**Verify:** `fieldkit thermal status` shows the expected cap.

**Symptom:** `read_cap()` returns `None`; the governor assumes 100.
**Cause:** The parser looks for the English string `Current AC Power Setting Index`; a non-English Windows prints a translated label.
**Remedy:** Not available in the source material; parse by position or GUID.
**Verify:** `fieldkit thermal status` prints a number after `cap`.

**Symptom:** `release check` reports a file DIFFERENT although the code is the same.
**Cause:** Line-ending conversion; `export_tag()` already forces `core.autocrlf=false`, so remaining causes are a different tag or a modified published file.
**Remedy:** Compare `git rev-parse <tag>^{tree}` locally and the published file.
**Verify:** Gate prints `same sha256`.

**Symptom:** Triage verdict `success` exits 3.
**Cause:** `cmd_triage` returns 0 only for `known`.
**Remedy:** Treat `success` as success in calling scripts, or change the CLI.
**Verify:** `fieldkit triage LOG --json` and read `verdict`.

**Symptom:** A kernel `dpkg-checkbuilddeps: error:` line is labelled `[compiler]`.
**Cause:** `EXTRACTORS` order: the `compiler` regex (`error: `) matches before the `dpkg` one.
**Remedy:** Cosmetic; signatures still match. Reorder if the label matters.
**Verify:** Run triage on the `KERNEL_DEPS` text from `tests/test_triage.py`.

**Symptom:** `fieldkit lifecycle` is NOT CLEAN with a `NOT COMPARED:` line and no `+`/`~` keys.
**Cause:** A snapshot part was stored as not captured (the reason names the side, `before` or `after`, and the query failure).
**Remedy:** Re-run on an idle host; if a part is never capturable on that host, remove it from `watch.parts`.
**Verify:** The verdict line reads `CLEAN: installed, verified, removed, nothing left behind`.

## Technical Debt

🟡 **LOW** — `triage.py` docstring documents `fieldkit build triage`; the CLI subcommand is `fieldkit triage`. → Fix the docstring.
🟠 **MEDIUM** — `set_cap()` writes the same value to AC and DC; restore writes the AC value to both, overwriting a different battery cap. → Read and restore AC and DC separately.
🟠 **MEDIUM** — `read_cap()` depends on English `powercfg` output. → Parse the hex index by position under the `PROCTHROTTLEMAX` GUID.
🟠 **MEDIUM** — Kernel `sha256sums.asc` is used without GPG signature verification. → Verify the clearsigned file with the kernel.org autosigner key before trusting its hashes.
🟡 **LOW** — Stale comment block in `sensors.tpfan_window()` contradicts the code. → Delete the first comment block.
🟡 **LOW** — `release.check()` skips claims the notes do not contain, silently. → Report skipped claims as informational lines.
🟠 **MEDIUM** — Governor and sensor tests use fakes; the real `powercfg` and provider paths have no automated test. → Add an opt-in Windows integration test that sets and restores a cap.
🟡 **LOW** — `cmd_thermal` returns after `gov.join(timeout=15)` without checking `gov.is_alive()`; a governor thread blocked in a slow PowerShell call is then killed as a daemon at exit and the cap is not restored. → After the join, if the thread is alive, call `governor.set_cap()` with the cap read before `gov.start()`.
🟡 **LOW** — A `KeyboardInterrupt` from `thermal watch` escapes `cli.main()` as a traceback instead of a one-line message and a defined exit code. → Catch `KeyboardInterrupt` in `cmd_thermal` after the `finally` and return a distinct exit code.

## Impact If Removed

Removing `build/triage.py` breaks `fieldkit triage` and pipeline stage triage in `cli.py`. Removing `refcheck.py` or `lifecycle.py` removes those CLI commands. Removing `kernel.py` breaks every stage of `build/pipelines/debian-kernel.yaml`. Removing `release.py` removes the publish gate and its evidence workflow. Removing `thermal/` leaves Windows builds with only the firmware trip, the condition that reset the laptop at 05:28 on 2026-10-02; whether the build harness (`buildh`) imports `thermal` directly is not covered by this group's source.

## Claim Sources

| Claim | Basis | Evidence |
|-------|-------|----------|
| Triage is deterministic, no model | 📄 stated in input | regex signatures kept as data in build/signatures/<set>.yaml, no model involved |
| Release has no force override | 📄 stated in input | no --force: a failing gate means fix the release or fix the check |
| Autocrlf caused false published != tested | 📄 stated in input | identical code is reported as "published != tested" (found 2026-09-30) |
| Injector is never executed | 📄 stated in input | read MANDATORY_FLAGS out of an existing kernel_config_injector.py with the ast module (never executing it) |
| olddefconfig drops options silently | 📄 stated in input | olddefconfig also silently drops options whose dependencies are off |
| Reset came from a stuck ACPI zone | 📄 stated in input | sat at 41.85 C for 386 samples |
| Windows lacks a die temperature API | 📄 stated in input | Windows has no such API |
| Original cap restored on normal stop | 📄 stated in input | The original cap is restored on stop, whatever happened. |
| Privacy scan runs before tests | 📄 stated in input | Scan BEFORE the tests run |
| Restore overwrites a distinct DC cap | 🤖 model inference | *(none — model judgment)* |
| read_cap is locale-dependent | 🤖 model inference | *(none — model judgment)* |
| Signature YAML regex can cause ReDoS | 🤖 model inference | *(none — model judgment)* |
| Real powercfg path untested | 🤖 model inference | *(none — model judgment)* |
| Proof load durations 20 s and 61 s | 🤖 model inference | *(none — model judgment)* |
| tpfan_window comment is stale | 🤖 model inference | *(none — model judgment)* |
| Lifecycle fails closed on a not-compared part | 📄 stated in input | unread is not clean: fail closed |
| Ctrl+C in thermal watch stops and joins the governor (cli.py and tests/test_thermal.py, outside this group's prep sources) | 🤖 model inference | *(none — model judgment)* |
| A governor thread outliving the join can skip the restore | 🤖 model inference | *(none — model judgment)* |
| KeyboardInterrupt escapes cli.main as a traceback | 🤖 model inference | *(none — model judgment)* |


---
**How to verify this document:**
`📄 stated in input` — the model's phrasing of something your source text said.
Find the matching line in the original to verify.
`🤖 model inference` — the model's own judgment or synthesis. Treat as opinion,
not measurement. Re-run on the same input and check whether specific numbers
stay consistent between runs.

*Auto-generated DITA-structured developer documentation.*