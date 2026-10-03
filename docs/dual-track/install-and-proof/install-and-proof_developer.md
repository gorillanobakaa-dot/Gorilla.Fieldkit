# install-and-proof: backup, zip install, restore and production proof of an installed Gorilla Firefox (`fieldkit/buildh/install.py`, `proof.py`, `leaks.py`, `capture.py`, `throwaway.py`)

> Generated 2026-10-02 | Source: `install-and-proof`

---

## Purpose

This group closes the gap between a green build and a browser that keeps its promises. `install.py` replaces an installed Gorilla Firefox with the build that `build-verify` hashed: it resolves the target (`--install-dir`, or the single uninstall entry whose `DisplayName` or install-folder basename contains `gorilla`, case-insensitive; none or several -> refused with a `NEXT:` hint), backs up the install directory and the profiles directory to `Documents/Gorilla.Firefox.Backups/<version>-<stamp>/` with a `manifest.json` (entries, bytes, SHA-256, and the profiles `source` path), refuses a package whose hash differs from `build-result.json`, empties the install directory, unpacks the hashed zip with a containment check per member, writes a nonce-bearing marker `gorilla-install.json`, deletes every profile's `startupCache`, verifies `application.ini`, `firefox.exe --version` and the marker (zip route) or the uninstall entry (NSIS route), and journals the result. `--restore` puts back the install zip and, when the backup has one, `profiles.zip`, after checking every hash, the profiles source path and that no Firefox runs; the current profiles folder is renamed aside, never deleted. `proof.py` and `leaks.py` judge the INSTALLED browser, never the source: shipped prefs against the port's intent, excised components absent from both `omni.ja` archives, a clean headless start, the browser's own `nsHttp` log free of Mozilla hosts, ad and tracker hosts absent on a news front page, and what a local leak page learns. `install.post_install` runs those rows and then four scripts from the maintainer's `working scripts` folder, one of which (`verify_address_bar`) takes the keyboard and runs only with `--drive`; it fails closed (every row carries `status` ok/FAIL/SKIPPED and SKIPPED is never OK). `capture.py` is a separate, deeper network witness (`mitmproxy`, resolver log, `pktmon`) on a throwaway copy unpacked from the hashed zip. `throwaway.py` (new) owns every headless check's temporary profile: created with a known prefix and a marker file, logs copied out by `keep()`, deleted by `discard()` only when the folder provably belongs to it. Trust level: the code runs with the invoking user's rights, deletes a directory tree (`shutil.rmtree`) chosen from the registry (Gorilla-named entries only) or `--install-dir` (trusted verbatim), renames the profiles directory on a profile restore, and starts the installed browser repeatedly. Linux branches exist in path helpers but process control is Windows-only; Linux behaviour is untested.

## Known Alternatives Considered

Rejected alternatives documented in the source. (1) The packaged NSIS installer, run silently: `install_from_zip` exists because, per its docstring, "the NSIS installer, silent, into an empty directory with a stale per-machine uninstall entry around, went the elevated route, could not, and exited 0 having installed nothing." The installer path (`install()`) remains as a fallback when `build-result.json` has no zip. (2) Verifying a zip install through the uninstall registry entry: replaced by the install's own marker, because, per the module docstring, with the marker "a stale uninstall entry of an earlier installer can never pass it". (3) Selecting the target by `DisplayName` containing `Gorilla` or `Firefox`: replaced by `gorilla_installs()`, which matches `gorilla` only in the display name or the install folder's own basename, not the full path, because an account name containing the word would otherwise match every path under that home folder. (4) Judging egress from the system DNS cache against the socket table: the `proof.py` comment says that check "passed 155.0.1 while that build fetched Remote Settings, content-signature chains, settings attachments, the location service, push, the add-ons API, the system add-on updater and the connectivity probe" because "Shared Fastly/GCP addresses hide behind one IP"; the browser's own `nsHttp` log replaces it. (5) Substring matching for excised paths: replaced by path-prefix matching after it "caught xml/, mathml/ and uBlock's _locales/ml/". (6) Basename matching of deleted modules: narrowed after it flagged toolkit's `TelemetryUtils.sys.mjs` and `services-settings/Utils.sys.mjs`. (7) Marionette for the leak page: `leaks.py` states "No site, no keyboard, no Marionette"; the page measures itself and POSTs JSON back.

## Architecture

- **Pattern:** Linear, journaled pipeline (`run`: hash check -> resolve target -> backup -> contained unpack + marker -> cache clear -> verify rows -> `task.journal`) plus a row-based proof runner (`post_install`): each check is a function that obtains a profile from `throwaway.profile(prefix)`, starts the installed `firefox.exe` headless on it, kills exactly that process tree with `taskkill /PID <pid> /T /F`, copies evidence logs out with `throwaway.keep()`, calls `throwaway.discard()`, and returns a dict `{check, ok, evidence, bad, log}`. `post_install` maps each row to a result with `status` in {ok, FAIL, SKIPPED} and returns `ok` only when every status is `ok`. The CLI (`fieldkit build-harness install|post-install|capture`) prints one line per row and exits 0 or 3; refusals carry a `next` hint that the CLI prints on its own line.
- **Trust boundary:** Trusted: `build-result.json` in the task state (hashes and artefact paths), the task's `workdir` git history (the root commit is taken as pristine upstream for pref intent), the maintainer's `DELETED_FILES` manifests via `firefox.manifest_deletions`, the four scripts in `Documents/Gorilla.firefox/working scripts` (executed with `sys.executable`, no hash check), a `--install-dir` value (used verbatim as the `rmtree` target), and, without it, the single uninstall entry (HKCU and HKLM) whose `DisplayName` or `InstallLocation` basename contains `gorilla` and holds `firefox.exe`. Verified, not trusted: the installer and zip (SHA-256 against `build-result.json`), each zip member's resolved path (must stay under the install dir), the marker after install (nonce and zip hash of this run), the backup zips on restore (SHA-256 against `manifest.json`), the restore's profiles target (must equal the manifest `source`, when recorded), and every folder `throwaway.discard()` is handed (parent is the system temp dir, known prefix, marker file present, not a symlink). Untrusted and only read: the browser's stdout/stderr, `nsHttp` and `nsHostResolver` logs, the leak page's POSTed JSON, `mitmproxy` flow records and `pktmon` frames.
- **Attack surface:** Local only. `leaks.measure` binds an `http.server.HTTPServer` on `127.0.0.1` at a random port for up to 45 s and `json.loads` any POST body without a size cap beyond `Content-Length`; any local process can post to it during that window and alter the leak verdict. `capture.mitm_run` runs `mitmdump` on `127.0.0.1` at a free port and writes `distribution/policies.json` that installs the mitmproxy CA into the throwaway copy only. The post-install scripts are executed from a user-writable folder without integrity checks, so whoever can write that folder controls code run by `post-install`. Whoever can write HKCU uninstall keys can still nominate an `rmtree` target, but only a folder whose entry or basename says Gorilla and that holds `firefox.exe`, and only when it is the sole such entry. A malicious zip member path (`..`, absolute) is refused by the containment check, but only after the old install dir has been removed. `throwaway.discard()` refuses any path it did not create, so a crafted `log` or profile path cannot steer a deletion.
- **Dependencies:** `configparser`, `hashlib`, `json`, `os`, `re`, `shutil`, `subprocess`, `sys`, `time`, `secrets`, `zipfile`, `pathlib`, `tempfile`, `threading`, `http.server`, `socket`, `ctypes`, `winreg`, `fieldkit.buildh.task`, `fieldkit.buildh.throwaway`, `fieldkit.buildh.firefox`, `fieldkit.buildh.verify`, `mitmdump (mitmproxy, external executable, capture only)`, `dpkt (capture packets pass only)`, `pktmon (Windows, elevated, capture packets pass only)`, `powershell (Get-Process, Get-CimInstance, Get-NetTCPConnection, Get-NetUDPEndpoint)`, `taskkill`, `git`

## Flags & Configuration

| Name | Type | Default | Effect | Notes |
|------|------|---------|--------|-------|
| `action` | `string` | `required` | `install`, `post-install` or `capture` select this group's code. | Positional, after `fieldkit build-harness`. |
| `task (positional or --task)` | `string` | `contents of `state/build-harness/CURRENT`` | Build job, for example `firefox-157.0`. | Refused with `no build job has been started` when neither is given and `CURRENT` is missing. |
| `--install-dir` | `string` | ``find_install()`: the single Gorilla-named uninstall entry, else refused` | Directory to back up, empty and unpack into (install), restore into (`--restore`), or test (post-install). | Required when no uninstall entry, or more than one, names a Gorilla install; the refusal prints `NEXT: pass --install-dir <the Gorilla install folder>`. Used verbatim: no Gorilla check is applied to a folder given here. |
| `--no-backup` | `bool` | `false` | Skips `backup()`; the old install directory is deleted with no copy. | Help text: "never the default". |
| `--restore` | `string (backup dir)` | `none` | Runs `restore()` instead of installing: hash-checks `install-<ver>.zip` and, if present, `profiles.zip`; checks the profiles target equals the manifest `source`; refuses while any Firefox runs (profiles only); empties the install dir and `extractall`s; then `restore_profiles()` renames the current profiles folder aside and unpacks the backup. | Nothing is journaled; no verify rows run; startup caches are not cleared. Without a target the CLI prints `RESTORE REFUSED: <why>` and the `NEXT:` line and exits 3. The profiles target is always `profiles_dir()` from the CLI; the `profiles` argument is not exposed. |
| `--drive` | `bool` | `false` | Allows checks in `DRIVES_WINDOW` (`verify_address_bar`) after a warning and a `DRIVE_COUNTDOWN` of 20 s in 5 s steps. | Without it the check is recorded with `status: SKIPPED` and the run is NOT OK. |
| `--only` | `string (comma list)` | `all` | post-install: restricts to named checks: `prefs`, `excised`, `startup`, `egress`, `adblock`, `leaks` and the four script names. | A name that is neither a proof row (`PROOF_CHECKS`) nor a `POST_INSTALL` script is recorded as SKIPPED (`no check has this name`), which makes the run NOT OK. Also read by `leakgate`; split on `,` without stripping spaces. |
| `--packets-only` | `bool` | `false` | capture: only the `pktmon` pass on a plain copy. | Prints `not elevated: run this in an administrator PowerShell` and exits 3 without elevation. |
| `use_installer (function argument)` | `bool` | `False` | `run()` uses the NSIS installer instead of the zip. | Not exposed on the CLI. |
| `MOZ_LOG / MOZ_LOG_FILE (env, set by the code)` | `string` | ``nsHttp:3,timestamp` (egress, adblock); `nsHostResolver:5,timestamp` (capture dns)` | Makes the browser write its own HTTP or resolver log into the throwaway profile. | Set per child process only. |

## API Surface

| Symbol | Description | Side Effects |
|--------|-------------|--------------|
| `install.run()` | Hash check, resolve target (or `no_target()`), backup, zip install with marker, cache clear, verify, journal. | Writes the backup; `rmtree` and recreates the install dir; deletes `startupCache` dirs; appends `backup` and `install` journal entries. |
| `install.backup()` | Zips install dir and profiles dir (ZIP_DEFLATED, level 6) and writes `manifest.json`, including `files.profiles.source`. | Creates `<version>-<YYYYmmdd-HHMMSS>/`; silently skips files that raise `OSError`. |
| `install.install_from_zip()` | Empties `install_dir`, unpacks the zip's single top-level folder into it with a per-member containment check, then writes `marker` as `gorilla-install.json`. | `shutil.rmtree(install_dir)`; members outside the first member's top folder are skipped; raises `task.Refused` on a member that escapes. |
| `install.install()` | NSIS route: `<installer> /S /InstallDirectoryPath=<dir>`, then a 3 s sleep. | `rmtree` when `fresh`; registry writes by the installer. |
| `install.clear_startup_caches()` | Deletes `startupCache` in every profile's local directory from `profiles.ini`. | `rmtree(..., ignore_errors=True)`. |
| `install.verify()` | Rows: `application.ini` Version, `firefox.exe --version`, then `marker_row` (zip route) or the uninstall entry location via `find_install()` (NSIS route). | Starts `firefox.exe --version` (60 s timeout). |
| `install.restore()` | All checks first (target present, target not running, install-zip hash; if the backup has profiles: source path, `profiles.zip` hash, no Firefox running), then unpack the install zip into an emptied dir and call `restore_profiles()`. | `rmtree` of the install dir; renames the profiles folder aside (kept). |
| `install.post_install()` | `caches_row`, `proof.rows`, `leaks.rows`, then `POST_INSTALL` scripts; unknown `--only` names, keyboard checks without `drive` and missing scripts become SKIPPED rows. Returns `{ok, target, results, skipped}`. | Starts the browser repeatedly; writes `state/build-harness/<task>/post-install/<name>.log`; journals `post_install`. |
| `install.startup_errors()` | Headless start, stderr scan with the narrower `STARTUP_ERROR` regex. | Throwaway profile `gstartup_*`, logs kept under `<temp>/fieldkit-logs/`; no caller in `fieldkit/` (tests call it). |
| `proof.prefs_row()` | Prefs added or changed versus the root commit in `all.js` and `firefox.js` must appear in `greprefs.js` (`omni.ja`) or `defaults/preferences/firefox.js` (`browser/omni.ja`) with the same value and `locked`/`sticky` flag. | Runs `git rev-list` and `git show`. |
| `proof.excised_row()` | No packaged file under `EXCISED_PACKAGED` (eight prefixes) or named like a deleted `.mjs`/`.jsm` that no survivor shares. | None. |
| `proof.startup_row()` | Headless start; fails on `STARTUP_BAD` lines. | Throwaway profile `gproof_*`, deleted; `stderr.txt`/`stdout.txt` kept, `log` points at the kept folder. |
| `proof.egress_row()` | `nsHttp:3` log; fails on any `VENDOR_HOST` match; lists unknown hosts. | Network: loads the page; throwaway profile `gegress_*`, deleted; `http.log*` kept, `log` points at the kept folder. |
| `proof.adblock_row()` | Page host must appear; none of the 16 `AD_HOSTS` may. | Network; throwaway profile `gadblock_*`, deleted; no log kept. |
| `proof.http_hosts()` | Hosts from `uri=`, `URI `, `spec=`, `BeginConnect ` lines; drops non-hostnames (wrapped lines, IP literals). | None. |
| `leaks.rows()` | Local leak page; fails on raw private ICE addresses, an unmasked WebGL renderer, or `getBattery`; a report row is always ok. | Local HTTP server; the page contacts `stun:stun.l.google.com:19302`; throwaway profile `gleaks_*`, deleted; no log kept. |
| `capture.rows()` | Per scenario (`idle` 120 s, `newtab` 60 s, `addons` 45 s, `page` 60 s): mitm, dns and, when elevated, packets rows. | Unpacks test copies; runs `mitmdump`; elevated: `pktmon` capture of all machine traffic to `.etl`/`.pcapng`. |
| `capture.judge()` | Shared classification using `VENDOR_HOST`, `LIST_HOSTS`, `AD_HOSTS` from `proof.py`. | None. |
| `install.gorilla_installs()` | De-duplicated install folders from `_uninstall_entries()` (or `entries`) whose display name or folder basename contains `gorilla` and that hold `firefox.exe`. | Reads HKCU and HKLM uninstall keys (Windows); returns `[]` elsewhere. |
| `install.find_install() / no_target()` | The one Gorilla install, else None; `no_target()` builds `{ok: False, why, next}` naming none or the ambiguous list. | Registry reads. |
| `install.new_marker() / marker_row()` | Marker with `secrets.token_hex(16)` nonce, zip name and SHA-256, version, task, time; the row passes only when the file on disk has this nonce and this zip hash. | `marker_row` reads `gorilla-install.json`. |
| `install.restore_profiles()` | Renames `target` to `<name>.before-restore-<YYYYmmdd-HHMMSS>`, unzips into a fresh `target`; on any exception removes the new folder and renames the kept one back, then re-raises. | Directory rename; `extractall`. |
| `install.any_firefox_running()` | `Get-Process firefox` (Windows) or `pgrep -x firefox`; an `OSError` counts as running. | Starts PowerShell or pgrep. |
| `throwaway.profile() / keep() / discard() / ours()` | Create a marked temp profile for one of `PREFIXES`; copy matching log files to `<temp>/fieldkit-logs/<profile name>/`; delete only a folder `ours()` accepts. | Creates and deletes folders in the system temp dir; `keep()` output is never deleted by the code. |

## Kill Switches

### ``install.running()` in `install_from_zip`, `install`, `restore`, `post_install``
- **Condition:** A `firefox.exe` process whose path equals `<install_dir>/firefox.exe`
- **Effect:** Raises `task.Refused`; nothing is deleted.
- reversible
- Path comparison is case-insensitive string equality; a browser started from a different path does not block. A profile restore additionally uses `any_firefox_running()`, which treats an `OSError` as running.

### ``install.clear_startup_caches()``
- **Condition:** Any process named `firefox`
- **Effect:** Raises `task.Refused` after the new build is already unpacked.
- reversible
- Propagates out of `run()` before `verify` and `task.journal`, so the journal has the backup entry but no install entry.

### ``install.run()` hash checks`
- **Condition:** SHA-256 of the installer or zip differs from `build-result.json`
- **Effect:** Returns `{ok: False, why: ...}` before any backup or deletion.
- reversible
- The installer is hashed even when the zip route is used, so a missing installer file raises `FileNotFoundError`.

### ``install.restore()``
- **Condition:** `install-<ver>.zip` SHA-256 differs from `manifest.json`
- **Effect:** Raises `task.Refused(... not restoring from a damaged backup)`.
- reversible
- Checked before `rmtree` of the install dir.

### ``install.DRIVES_WINDOW` / `DRIVE_COUNTDOWN` in `post_install``
- **Condition:** `verify_address_bar` selected
- **Effect:** Skipped unless `--drive`; with `--drive`, a printed warning ("will take the KEYBOARD and the FOREGROUND for about 70 s") and a 20 s countdown during which Ctrl+C aborts.
- reversible
- `sleep` is injectable for tests.

### ``capture.packet_run()` / `is_admin()``
- **Condition:** Not elevated
- **Effect:** Returns `None`; `capture.rows` adds a failing row naming `fieldkit build-harness capture <task> --packets-only`.
- reversible
- When elevated it first runs `pktmon stop`, which ends any capture already running on the machine.

### ``install.find_install()` / `no_target()``
- **Condition:** No `--install-dir`, and `gorilla_installs()` returns zero or more than one folder
- **Effect:** `run`, `post_install` and the CLI restore return or print the refusal with `next`; nothing is backed up or deleted.
- reversible
- An entry that only says Firefox never qualifies; duplicates of one folder (by `normcase(resolve())`) count once.

### ``install_from_zip` containment check`
- **Condition:** A member's resolved target is not `is_relative_to(dest.resolve())`
- **Effect:** Raises `task.Refused("... would land outside ...")`; the member is not written.
- **not reversible**
- Runs after `rmtree(dest)`; files unpacked before the bad member stay; recovery is `--restore`.

### ``install.restore()` profiles pre-checks`
- **Condition:** Manifest `source` differs from the target, `profiles.zip` hash mismatch, or any Firefox running
- **Effect:** Raises `task.Refused` before the install dir or the profiles folder is touched.
- reversible
- `restore_profiles()` rolls back on any exception during unpack: removes the half-written folder and renames the kept one back.

### ``install.post_install()` fail-closed result`
- **Condition:** Any result with `status` other than `ok` (FAIL or SKIPPED)
- **Effect:** `ok` is False, `skipped` lists the names, the CLI prints `POST-INSTALL NOT OK:` with one line per non-ok row and exits 3.
- reversible
- Replaces the earlier rule that ignored rows with `rc: None`.

### ``throwaway.discard()` / `ours()``
- **Condition:** Path not directly in `tempfile.gettempdir()`, no harness prefix, no `.fieldkit-throwaway` marker, a symlink, or not a directory
- **Effect:** Returns False; nothing is deleted.
- reversible
- `profile()` raises `ValueError` for an unknown prefix. Deletion retries `tries=3` with a 1 s sleep (injectable) because a just-killed browser can hold a file.

## Dead Code

- **``install.startup_errors()` and `install.STARTUP_ERROR``** — No caller in `fieldkit/` (only `tests/test_buildh_security_review.py` calls it, to prove it discards its profile); `proof.startup_row()` with the wider `STARTUP_BAD` regex replaces it. (risk: None if removed; keeping it invites the two regexes to drift.)
- **``install.BACKUPS``** — Unused; `backup()` uses `documents()`, which honours XDG on Linux, while this constant does not. (risk: None if removed.)
- **``install.install()` (NSIS route)`** — Reached only when `build-result.json` has no zip; `use_installer` is not exposed on the CLI. (risk: Removing it drops the only path for a build packaged without a zip.)

## Performance

- **CPU:** Not measured. Fixed browser lifetimes from the source: `startup_row` 25 s, `egress_row` 75 s, `adblock_row` 60 s, `leaks.measure` up to 45 s; scripts up to 900 s each; `verify_address_bar` announces about 70 s; capture 285 s (not measured: the sum of the scene times in the source) of scenarios per witness, with socket sampling via PowerShell about every 2 s. `throwaway.discard()` may add up to 2 s (not measured: two 1 s sleeps between its three tries) when a file is still held.
- **MEMORY:** Not measured. Zips are streamed (`shutil.copyfileobj`, 1 MiB hash chunks); the HTTP and resolver logs are read whole into memory.
- **IO:** Not measured. A full zip of the install dir and of the profiles dir per install; a full unpack; `pktmon` writes every frame (`--pkt-size 0`) to `.etl` then `.pcapng`.
- **NOTES:** Measured outcomes (MEASUREMENTS.md, build 11 of Firefox 157.0, BuildID 20261002161913): zero Mozilla or Firefox hosts in the browser's own HTTP log over 75 s on a real page; none of 16 ad and tracker domains reached on a news front page; no local IP address exposed over WebRTC; the address bar navigated in all four typed cases. Install and backup durations are printed per run but not recorded in MEASUREMENTS.md: not measured.

## Security

- **Remote execution:** None from the network. Local code execution paths: the four `POST_INSTALL` scripts from `Documents/Gorilla.firefox/working scripts`, run without hash or signature checks; `mitmdump` resolved via `PATH` or the interpreter's `Scripts` folder.
- **Data handling:** `profiles.zip` is an unencrypted full copy of every Firefox profile (browsing data) in `Documents`. A profile restore leaves the previous profiles folder as `<folder>.before-restore-<stamp>`, another full copy, never deleted by the code. Throwaway profiles (`gcap_`, `gleaks_`, `gproof_`, `gegress_`, `gadblock_`, `gstartup_`) are now deleted after each run by `throwaway.discard()`; evidence logs (`stderr.txt`/`stdout.txt` of the startup checks, `http.log*` of egress) are first copied to `<temp>/fieldkit-logs/<profile name>/`, which is never cleaned up and lists every URL the egress run requested. Profiles created by earlier code carry no marker and are not removed. The mitm addon writes method, host, path (300 chars), User-Agent, referer, cookie presence and up to 400 bytes of each request body to `mitm-<scenario>.jsonl`. The elevated packet pass captures the whole machine's traffic, not only the browser's, to disk. The zip-install marker `gorilla-install.json` stores the zip name and hash, version, task id and time in the install dir.
- **Attack surface:** `rmtree` target from `--install-dir` (unchecked) or from a sole Gorilla-named uninstall entry; unauthenticated localhost POST endpoint during `leaks.measure`; user-writable script folder executed by `post-install`; the mitmproxy CA is installed only into the throwaway copy via `distribution/policies.json` with a locked proxy. Zip members are contained (`resolve()` + `is_relative_to`); `restore()` uses `ZipFile.extractall`, which relies on Python's own member sanitising, not on this explicit check.
- **Notes:** Egress judgement is name-based: `http_hosts` keeps only hostnames with a letter TLD, so a request to a raw IP literal is not counted as vendor or unknown. The `nsHttp` log only sees traffic through necko; `capture.py`'s docstring says code can bypass it, which is why the DNS and packet witnesses exist. The leak test contacts a Google STUN server by design.

## Error Conditions

| Error | Cause | Remedy |
|-------|-------|--------|
| `Firefox is running from {install_dir}: close it first` | `running()` found `firefox.exe` from that directory. | Close the browser and rerun. |
| `Firefox is running: close it before the startup caches are cleared` | Any `firefox` process, raised after the unpack. | Close every Firefox and rerun `install` (a new backup is taken). |
| `the installer on disk is not the one build-verify hashed` | Installer SHA-256 differs from `build-result.json`. | Rerun `fieldkit build-harness build-verify`. |
| `the zip on disk is not the one build-verify hashed` | Zip SHA-256 differs; a later package step rewrote `dist/`. | Rerun `build-verify`; `capture` copies the zip first for this reason. |
| `{zip} does not match its manifest hash: not restoring from a damaged backup` | Backup zip altered or truncated. | Use an older backup directory. |
| `Firefox is running from {target}: the checks need to start it themselves` | `post_install` found the target browser running. | Close it. |
| `leaks: the local leak page reported back / no result in 45 s` | The page never POSTed (browser failed to start or script error). | Check `startup` row; rerun with `--only leaks`. |
| `{page} did NOT load ({n} hosts: offline or blocked page)` | Adblock row: page host absent from the log. | Rerun online. |
| `THE PROXY DID NOT TAKE (the page itself is missing)` | capture mitm `page` scenario: no flow for the page host. | Check `mitmdump` is installed and the CA file appeared within 30 s. |
| `not elevated: run this in an administrator PowerShell` | `--packets-only` without elevation. | Rerun in an elevated shell. |
| `FileNotFoundError on build-result.json` | `build-verify` never ran for this task. | Run `fieldkit build-harness build-verify <task>` first. |
| `no uninstall entry names a Gorilla install (an entry that only says Firefox is never picked)` | `gorilla_installs()` empty (no Gorilla-named entry, a zip-only install, or not Windows). | Pass `--install-dir`; the CLI prints `NEXT: pass --install-dir <the Gorilla install folder>`. |
| `{n} Gorilla installs are registered, not guessing which: [...]` | More than one distinct Gorilla install folder is registered. | Pass `--install-dir` with one of the listed folders. |
| `{zip}: entry {x!r} would land outside {dest}` | A zip member path resolves outside the install dir. | Do not use the zip; `--restore` the backup, rebuild and rerun `build-verify`. |
| `{pz} was taken from {src}, not {target}: nothing restored. ...` | The manifest `source` of `profiles.zip` differs from the profiles folder the restore would write. | Follow the printed manual steps: close every Firefox, rename the source folder, unzip into it. |
| `a Firefox is running and may hold the profiles: close every Firefox first` | `any_firefox_running()` returned True before a profile restore. | Close every Firefox and rerun the restore. |
| `[SKIPPED] {name}: no check has this name` | An `--only` name that is not in `PROOF_CHECKS` or `POST_INSTALL`. | Use a listed name; the line prints both lists. |

## Tasks

### Install a verified build with a backup

After `build-verify` passes and you want the build on this machine.

**Prerequisites:**
- `state/build-harness/<task>/build-result.json` exists with installer and zip hashes
- Every Firefox closed
- Windows (process checks use `powershell` and `taskkill`)

**Step 1:** Run:

```
fieldkit build-harness install firefox-157.0 --install-dir "<install dir>"
```

Omit `--install-dir` when exactly one Gorilla install is registered; otherwise the command refuses with a `NEXT:` line.
  - Expected: `installed now:`, two `backup:` lines, `install: <n> files from <zip>`, `startup cache cleared:` lines, four `[ok]` rows (application.ini, `--version`, `this install's own marker (gorilla-install.json) names this run and this zip`, startup caches), `INSTALL OK`, exit 0. Fail: `INSTALL NOT OK: <why or rc>` (plus a `NEXT:` line for a target refusal), exit 3.
**Step 2:** Open `Documents/Gorilla.Firefox.Backups/<version>-<stamp>/manifest.json`.
  - Expected: `files.install.entries` and `files.profiles.entries` are non-zero and match what you expect for the old install; `files.profiles.source` names the profiles folder.

**After this task:** The install dir holds exactly the zip's top folder plus `gorilla-install.json`; every `startupCache` is gone; the journal has `backup` and `install` entries.

### Roll back to a backup

The new build fails a proof or misbehaves; restores the browser and, when backed up, the profiles.

**Prerequisites:**
- Every Firefox closed (any Firefox blocks a profile restore)
- A backup directory with `manifest.json`
- The same Windows account the backup was taken under (the profiles `source` must match)

**Step 1:** Run:

```
fieldkit build-harness install firefox-157.0 --restore "<backup dir>" --install-dir "<install dir>"
```
  - Expected: `restored install-<ver>.zip into <dir>`, then `current profiles kept as <folder>.before-restore-<stamp>` and `restored profiles.zip into <folder>`, then `restored: {version: ...}` with the old version. Fail: `does not match its manifest hash`, `was taken from ..., not ...: nothing restored`, `a Firefox is running and may hold the profiles`, or `RESTORE REFUSED:` with `NEXT:` (exit 3).
**Step 2:** After confirming the restored profiles, remove the `.before-restore-` folder by hand if it is not needed.
  - Expected: Only the restored profiles folder remains; the tool never deletes the kept folder.

**After this task:** The old browser files and profiles are in place; startup caches are not cleared by restore and the restore is not journaled.

### Prove the installed browser

After every install, before trusting it.

**Prerequisites:**
- Online (egress and adblock load live pages)
- The target browser not running
- Optional: the maintainer's `working scripts` folder present

**Step 1:** Run:

```
fieldkit build-harness post-install firefox-157.0 --only prefs,excised,startup,egress,adblock,leaks,verify_installed_build,verify_no_phone_home,webrtc_selftest
```
  - Expected: `[ok]` for `profiles: no stale startup cache`, `prefs`, `excised`, `startup`, `egress`, `adblock`, three leak rows and the report row, and the three non-keyboard scripts, then `POST-INSTALL OK`, exit 0. Without `--only`, `verify_address_bar` is `[SKIPPED]` and the run ends `POST-INSTALL NOT OK:` / `SKIPPED: verify_address_bar (...)`, exit 3.
**Step 2:** With nobody at the keyboard, run:

```
fieldkit build-harness post-install firefox-157.0 --drive --only verify_address_bar
```
  - Expected: Warning line, countdown `20 s ... 15 s ... 10 s ... 5 s ...`, then `[ok] verify_address_bar`.
**Step 3:** Run:

```
fieldkit build-harness capture firefox-157.0
```

Then, in an elevated PowerShell:

```
fieldkit build-harness capture firefox-157.0 --packets-only
```
  - Expected: `CAPTURE OK: <dir>` with `capture.json`; elevated run prints one `packets` line per scenario.

**After this task:** Script logs under `state/build-harness/<task>/post-install/`, kept proof logs under `<temp>/fieldkit-logs/`, `capture-<stamp>/`; no throwaway profile left; journal entries `post_install` (with per-row status) and `capture`.

### Run this group's unit tests

After changing any of the five modules.

**Prerequisites:**
- Fieldkit checkout with its test dependencies

**Step 1:** Run:

```
python -m pytest tests/test_buildh_install.py tests/test_buildh_proof.py tests/test_buildh_leaks.py tests/test_buildh_capture.py tests/test_buildh_security_review.py
```
  - Expected: All pass. The whole suite last measured 584 passed, 1 skipped, 2 xfailed in 194.89 s before these fixes; this subset's own result is not recorded in MEASUREMENTS.md (not measured).

**After this task:** Covered with fakes: backup/restore hash refusal, profile restore source check, running-Firefox refusal and rollback, Gorilla-only target selection and ambiguity refusal, marker verification against a stale uninstall entry, zip member containment, fail-closed post-install (skipped, missing, unknown names), throwaway create/keep/discard and refusal of foreign folders, refuse-while-running, cache clearing, pref/excised/egress/adblock judgement, SNI parsing and leak verdicts. No test starts a real browser.

## Troubleshooting

**Symptom:** `[FAIL] this install's own marker (gorilla-install.json) names this run and this zip`
**Cause:** Evidence `marker missing` (not written or unreadable) or `marker from another run: <zip>` (the folder holds an older run's marker, so this run's unpack did not complete as expected).
**Remedy:** Rerun `install`; read the `install: <n> files` line.
**Verify:** The row's evidence reads `<zip> sha256 <prefix>..., nonce matches`.

**Symptom:** `POST-INSTALL NOT OK:` with `SKIPPED: verify_address_bar`
**Cause:** Fail-closed by design: the keyboard check did not run without `--drive`.
**Remedy:** Run the non-keyboard set with `--only`, then `--drive --only verify_address_bar` when unattended.
**Verify:** Both runs end `POST-INSTALL OK`.

**Symptom:** `INSTALL NOT OK: no uninstall entry names a Gorilla install ...` on a machine that has the browser
**Cause:** The browser was only ever unpacked from a zip (which writes no uninstall entry), or its entry and folder name do not contain `gorilla`.
**Remedy:** Pass `--install-dir`.
**Verify:** `installed now:` names the expected folder.

**Symptom:** `[FAIL] prefs: ... (overridden: firefox.js loads after greprefs.js and sets it again)`
**Cause:** The port changed a pref in `all.js` that upstream `firefox.js` also sets; `firefox.js` loads later and wins.
**Remedy:** Make the change in `browser/app/profile/firefox.js` too.
**Verify:** Rerun `post-install --only prefs`.

**Symptom:** `[FAIL] excised: ... packaged: [...]`
**Cause:** The build still packages a file of a removed component; the evidence names the archive and path.
**Remedy:** Remove the packaging entry, rebuild, repackage, `build-verify`, reinstall.
**Verify:** `excised` row ok.

**Symptom:** `[FAIL] startup: ... SyntaxError` or `No such JSWindowActor`
**Cause:** A shipped module does not parse or an actor registration names a removed module.
**Remedy:** Fix the module named in the line; the full output is kept in `<temp>/fieldkit-logs/gproof_*/stderr.txt` (the row's `log`).
**Verify:** `startup` row reports `0 bad line(s)`.

**Symptom:** Install unpacked but no `install` journal entry
**Cause:** `clear_startup_caches` raised `Refused` because some Firefox was running.
**Remedy:** Close every Firefox and rerun `install`.
**Verify:** Journal shows an `install` entry with `ok: true`.

## Technical Debt

🟠 **MEDIUM** — `clear_startup_caches` refusal after the unpack aborts `run()` before `verify` and the journal entry. → Check for any running Firefox before the backup, or catch the refusal and journal a partial install.
🟠 **MEDIUM** — `_zip_dir` silently skips files that raise `OSError`, so a backup can be incomplete without a warning. → Record skipped paths in `manifest.json` and print a count.
🟠 **MEDIUM** — `http_hosts` drops IP-literal hosts, so a vendor request by IP is not judged. → Keep IP literals as `unknown` hosts in the evidence.
🟡 **LOW** — `capture` docstring says sockets are sampled every second; `run_browser` sleeps 2 s, so short connections can be missed by the packets pass. → Align the docstring or sample faster; prefer ETW process attribution.
🟡 **LOW** — `packet_run` starts with `pktmon stop`, ending any capture already running on the machine. → Check `pktmon status` first and refuse if a capture is active.
🟡 **LOW** — Windows-only process control (`powershell`, `taskkill`, `firefox.exe`) beside Linux branches in `documents()`, `profiles_dir()`, `local_profiles()`. → Mark the module Windows-only or add Linux process handling; Linux behaviour is untested.
🟡 **LOW** — Duplicate startup checks (`install.startup_errors` and `proof.startup_row`). → Delete `install.startup_errors`.
🟡 **LOW** — Proofs depend on two live third-party pages; a page change or outage changes the verdict. → Record the page host count in the journal and keep the URLs configurable.
🟠 **MEDIUM** — `restore` neither clears startup caches nor journals. → Call `clear_startup_caches()` after a restore and append a `restore` journal entry with the hashes checked.
🟠 **MEDIUM** — The zip containment check runs after `rmtree(dest)`, so a refused member leaves a half-populated install dir. → Validate every member path before deleting the old install, or unpack into a sibling folder and swap.
🟠 **MEDIUM** — `--install-dir` bypasses the Gorilla check entirely and becomes an `rmtree` target. → Refuse a `--install-dir` whose `application.ini` is not a Gorilla build unless a `--force`-style flag is given.
🟡 **LOW** — `<temp>/fieldkit-logs/` and `.before-restore-` folders accumulate and are never pruned; the former lists visited URLs, the latter holds browsing data. → Print their paths at the end of each run and add a prune command.
🟡 **LOW** — `restore()` relies on `ZipFile.extractall` instead of the explicit containment check used by `install_from_zip`. → Share one contained-unpack helper between install and restore.
🟡 **LOW** — Throwaway profiles left by code before `throwaway.py` carry no marker and are never removed. → Document a one-off manual clean-up, or let `discard` accept legacy prefixed folders older than a cut-off date after confirmation.

## Impact If Removed

Without `install.py` an install falls back to the NSIS installer, which once exited 0 having installed nothing, with no backup, no hash check against `build-verify`, no Gorilla-only target selection, no marker proving this run installed the files, no way back for profiles, and no startup-cache clear, so a profile can keep running a broken build's cached scripts when two builds share a BuildID. Without `proof.py` and `leaks.py`, nothing checks the installed browser itself: a build can pass every build-time row and ship with a dead address bar (as on 2 October 2026), Mozilla egress would be judged by the DNS-cache method that passed 155.0.1 while it contacted eight Mozilla hosts, and ad blocking and WebRTC local-address exposure go unchecked. The `leakgate` action in `fieldkit/buildh/cli.py` reads `Gorilla.Firefox.Backups/*/manifest.json` to find the previous build's zip, so without the backups `leakgate` loses its previous-build comparison input. `capture.py` imports `VENDOR_HOST`, `LIST_HOSTS` and `AD_HOSTS` from `proof.py` and breaks if `proof.py` goes. Without `capture.py`, the only egress witness is the browser's own log, which cannot see traffic that bypasses necko. Without `throwaway.py`, `proof`, `leaks`, `capture` and `install.startup_errors` fail on import of their profile helper; restoring the old inline `mkdtemp` code would bring back undeleted temp profiles holding HTTP logs.

## Claim Sources

| Claim | Basis | Evidence |
|-------|-------|----------|
| keyboard-taking checks are gated behind --drive | 📄 stated in input | checks that take the keyboard / foreground: never run unless asked with --drive, and announced with a countdown first |
| the restore refuses a damaged backup | 📄 stated in input | not restoring from a damaged backup |
| profiles are backed up because an upgrade migrates them | 📄 stated in input | Profiles are never touched by install; they are backed up because an upgrade migrates them in place. |
| capture never touches the installed browser | 📄 stated in input | A throwaway copy of the build (from the hashed zip) |
| fingerprinting settings beyond the three judged rows are reported, not judged | 📄 stated in input | Rows judge the things a privacy build must not do |
| zip install replaces the NSIS installer because it once installed nothing | 📄 stated in input | went the elevated route, could not, and exited 0 having installed nothing |
| the browser's own nsHttp log replaces the DNS-cache egress check | 📄 stated in input | The browser's own nsHttp log names every URL it asks for; that is the only honest witness. |
| startup caches are cleared because builds shared one BuildID | 📄 stated in input | Four builds in a row carried one BuildID, so a profile kept running a broken build's cached scripts |
| build-time checks missed faults that shipped a dead address bar | 📄 stated in input | two modules that did not parse and one that read enums from the wrong module all built green and shipped with a dead address bar |
| build 11 passed egress, adblock and WebRTC checks | 📄 stated in input | 0 Mozilla or Firefox hosts in the browser's own HTTP log over 75 s on a real page; 0 of 16 ad and tracker domains reached |
| the browser's own log cannot see traffic that bypasses necko | 📄 stated in input | code can bypass necko, a component can open a raw socket, the OS can resolve on its behalf |
| a cache-clear refusal leaves the install unjournaled | 🤖 model inference | *(none — model judgment)* |
| install.startup_errors is dead code | 🤖 model inference | *(none — model judgment)* |
| IP-literal hosts are not judged by egress | 🤖 model inference | *(none — model judgment)* |
| the localhost leak endpoint accepts posts from any local process | 🤖 model inference | *(none — model judgment)* |
| Linux behaviour is untested | 🤖 model inference | *(none — model judgment)* |
| leakgate reads the backup manifests to find the previous build | 🤖 model inference | *(none — model judgment)* |
| an ordinary Mozilla Firefox is never selected as the install target | 📄 stated in input | An ordinary Mozilla Firefox is never picked; none or several -> refused with a NEXT hint. |
| the marker makes a stale uninstall entry unable to pass verification | 📄 stated in input | a stale uninstall entry of an earlier installer can never pass it |
| post_install fails closed on skipped checks | 📄 stated in input | SKIPPED is never OK |
| the current profiles folder is kept on restore | 📄 stated in input | the current profiles folder is first moved |
| discard refuses paths it did not create | 📄 stated in input | a path from anywhere else is refused, never deleted |
| kept logs exclude cookies, cache and prefs | 📄 stated in input | no cookies, no cache, no prefs |
| the zip containment check runs after the old install dir is removed | 🤖 model inference | *(none — model judgment)* |
| --install-dir is trusted without a Gorilla check | 🤖 model inference | *(none — model judgment)* |
| restore relies on extractall instead of the explicit containment check | 🤖 model inference | *(none — model judgment)* |
| legacy throwaway profiles without the marker are never removed | 🤖 model inference | *(none — model judgment)* |


---
**How to verify this document:**
`📄 stated in input` — the model's phrasing of something your source text said.
Find the matching line in the original to verify.
`🤖 model inference` — the model's own judgment or synthesis. Treat as opinion,
not measurement. Re-run on the same input and check whether specific numbers
stay consistent between runs.

*Auto-generated DITA-structured developer documentation.*