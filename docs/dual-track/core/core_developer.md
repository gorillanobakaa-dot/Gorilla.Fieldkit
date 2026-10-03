# `fieldkit.core`: host facts, settings expansion, process runner, staged pipeline engine, next-step decider, privacy scanner and snapshot differ

> Generated 2026-10-02 | Source: `core`

---

## Purpose

`fieldkit/core/` is the shared base layer of Fieldkit. It holds eight modules: `host.py`, `checks.py`, `settings.py`, `proc.py`, `pipeline.py`, `next.py`, `privacy.py` and `snapshot.py`, plus an empty `__init__.py`. MEASUREMENTS.md records 1,017 lines of Python source for the group; that count predates the 2026-10-02 security changes to `privacy.py`, `proc.py` and `snapshot.py`, and the current count is 1,465 lines (not measured: a `wc -l` count taken while writing this document, not in MEASUREMENTS.md). The modules exist so that platform branching, process launching, settings expansion and publication checks live in one place: the `host.py` docstring says every Windows/Debian difference "lives here or in a tool's own small adapter, never scattered through the harness code". Trust level: high. `pipeline.py` executes arbitrary commands and arbitrary `module:function` Python targets named in a pipeline file, with the caller's privileges. `privacy.py` is a publication gate other parts of Fieldkit call (for example the `office-deliver` pipeline's `privacy` stage and `office/scrub.py`, which reuses `privacy.pdf_streams`); since the security changes it fails closed, reporting anything it could not read as a `not-scanned` finding. `proc.py` identifies the processes it starts by PID plus creation time and kills whole process trees. `snapshot.py` validates snapshot names and records failed system queries as not captured. The engine's design rules come from the `pipeline.py` docstring: verify the artefact, never the exit code; skip a stage only when its status is `done`, its fingerprint is unchanged and its verify checks still pass; report another platform's stage as `not-this-platform`, which stops the run unless `optional: true`; run `always: true` stages after a failure; write a JSON report every run. Linux branches are not measured on Linux.

## Known Alternatives Considered

The source documents these rejected approaches, each in a docstring or comment. (1) Stopping processes by name or by bare PID: `proc.py` rejects it because "the owner runs the same programs, so stopping by name or by a stale number kills their work too"; `Runner.stop()` acts only on recorded PIDs whose creation time still matches. (2) Trusting exit codes: `pipeline.py` requires verify checks because a stage is "only 'done' when its verify checks pass". (3) Scanning archive bytes: `privacy.py` scans each member because "scanning their bytes proves nothing". (4) Silently skipping unreadable input: `privacy.py` now treats it as a finding, "never a silent skip". (5) Storing a failed system query as an empty part: `snapshot.diff` refuses to compare such a part "so a failed query can never look like everything was removed". (6) Decoding PDF image data: `pdf_streams` skips image streams because "pixels hold no text". `snapshot.py` records process names with counts, not PIDs, because PIDs "change every run". Any other alternative: Not available in the source material.

## Architecture

- **Pattern:** Data-driven staged pipeline (YAML/JSON spec interpreted by `Pipeline.run`) with a persisted state file and a stateless decider (`next.decide`) layered on top; the other modules are plain function libraries with no shared mutable state except the `lru_cache` on `host()`.
- **Trust boundary:** Trusted: the pipeline spec file (it names commands and Python targets), `fieldkit.local.json`/`.yaml` (it supplies `${LOCAL:...}` values and the private term list), and environment variables read through `${ENV:NAME}`. Untrusted: the content of files scanned by `privacy.scan_path` and hashed by `snapshot.files`; these are only read, regex-matched, decompressed or hashed, never executed. Decompression is bounded: each FlateDecode stream is inflated with `max_length=TEXT_LIMIT + 1` and rejected beyond `TEXT_LIMIT`, zip members over `TEXT_LIMIT` (by their declared `file_size`) are not read, and nested archives stop at `ZIP_DEPTH` (3). PDF bytes up to `TEXT_LIMIT` are also handed to pypdfium2, a native PDF parser, in-process. Output of `powershell`, `systemctl`, `dpkg-query`, `tasklist` and `ps` is parsed as data through `snapshot._run`: a non-zero exit, a timeout, a start failure, unparseable or wrongly shaped JSON, or an empty process list raises `NotCaptured`, which `capture()` stores as `{"_not_captured": reason}`. Process identity is taken from the operating system (psutil, `GetProcessTimes`, or `/proc/<pid>/stat`), never from the PID file alone.
- **Attack surface:** The CLI entry points `fieldkit pipeline run|plan|status|reset`, `fieldkit next`, `fieldkit privacy scan`, `fieldkit snapshot take|diff|list` and `fieldkit host` (defined in `fieldkit/cli.py`). A pipeline file is code: `run.cmd` launches any program and `run.python` / `verify.python` call `importlib.import_module(mod)` then `getattr(..., fn)` on any importable target. `snapshot.take(name)` calls `check_name`, which accepts only `^[A-Za-z0-9._-]+$` and rejects names made only of dots, so the write path stays inside `SNAP_DIR`. `snapshot.load(name)` reads `SNAP_DIR/<name>.json` for a valid name, otherwise only an existing file whose suffix is `.json` at any path (read only); anything else is refused by `check_name`. Scanned PDFs reach pypdfium2's native parser. `file_contains` and `output_contains` patterns are user-supplied regexes compiled by `re` without a time limit; the PDF regexes in `privacy.py` are fixed. `started-pids.json` is trusted only together with a live creation-time match, so editing it cannot make `stop()` kill an unrelated process.
- **Dependencies:** `base64`, `binascii`, `bisect`, `glob`, `hashlib`, `importlib`, `io`, `json`, `os`, `platform`, `re`, `shutil`, `signal`, `subprocess`, `sys`, `time`, `zipfile`, `zlib`, `ctypes (Windows creation-time fallback in proc.py)`, `functools.lru_cache`, `dataclasses`, `pathlib`, `yaml (PyYAML, imported lazily for .yaml/.yml)`, `tomllib (imported lazily for .toml)`, `pypdfium2 (declared in pyproject.toml; imported lazily by privacy._pdf_text_layer for every PDF)`, `psutil (optional, not declared; proc.py prefers it for creation time and tree kills when importable)`, `fieldkit.build.triage (imported lazily by Pipeline.run on a failed stage with a triage set)`, `external programs: git (privacy --git), powershell, tasklist, taskkill (Windows, taskkill only without psutil); systemctl list-units, list-unit-files, list-timers and is-enabled, dpkg-query, ps (Linux); /proc/<pid>/stat (Linux creation time without psutil)`

## Flags & Configuration

| Name | Type | Default | Effect | Notes |
|------|------|---------|--------|-------|
| ``fieldkit pipeline <list|plan|run|status|reset> [name]`` | `string` | `required action` | Selects the pipeline action. `name` is a file path ending `.yaml`/`.yml`/`.json` or a stem looked up in `fieldkit/build/pipelines/`. | `list` reads only `*.y*ml` files, so a `.json` pipeline in that folder runs by name but does not appear in `list`. |
| ``--var k=v` (pipeline, next)` | `string list` | `none` | Overrides pipeline `vars` before `_fill` substitution. | A value without `=` exits with `--var needs k=v`. Changing a var changes the stage fingerprint, so affected stages re-run. |
| ``--only ID [ID ...]`` | `string list` | `all stages` | Runs only the named stages. | Unknown IDs raise `PipelineError: no stage ...`. |
| ``--from ID`` | `string` | `first stage` | Skips stages before `ID`. | Combines with `--only` as an intersection. |
| ``--dry-run`` | `bool` | `false` | Reports `would-run` for stages that are not up to date; runs nothing; `settings.expand` runs non-strict. | Up-to-date checks still run verify checks, including `python` verify targets, so a dry run is not side-effect free if a verify target has side effects. |
| ``--force`` | `bool` | `false` | Ignores saved `done` status and re-runs every selected stage. | Does not bypass platform checks. |
| ``--stage ID` (pipeline reset)` | `string` | `all` | Removes one stage's record from `state.json`; without it, clears the whole state. | Deletes no artefacts. |
| ``--json`` | `bool` | `false` | Prints machine-readable JSON instead of the human format. | Shared by every subcommand through the `common` parent parser. |
| ``fieldkit privacy scan PATH [PATH ...]`` | `string list` | `required` | Scans files or folder trees. | Exit 0 = clean, 3 = findings, including `not-scanned` findings for anything that could not be read. Member keys look like `a.zip!b.docx!word/document.xml`; PDF keys like `file.pdf!stream@OFFSET` and `file.pdf!text`. |
| ``--allow-paths` / `--allow-emails`` | `bool` | `false` | Drops the home-path rules or the email rule from `scan_text`. | Secret rules and private terms always apply. |
| ``--git`` | `bool` | `false` | Scans only `git ls-files -co --exclude-standard` output for a folder. | Raises `ValueError` ("is not a git repository") on a non-repository folder instead of guessing. |
| ``fieldkit snapshot take NAME --path [DIR ...] --services --tasks --programs --processes`` | `mixed` | `nothing recorded` | Records the selected parts to `state/snapshots/NAME.json`. | At least one of `--path` or a part flag is required. NAME must match `^[A-Za-z0-9._-]+$`; otherwise the CLI exits with `fieldkit: snapshot name '...' refused: use only letters, digits, '.', '_' and '-'`. A part whose query fails is stored as `{"_not_captured": reason}`. |
| ``fieldkit snapshot diff A B` / `list`` | `string` | `required` | Diffs two snapshots by name or path; `list` prints snapshot stems. | `diff` exits 3 when there are changes or when a part was not captured on either side (reported as `NOT COMPARED - not captured (...)`). A bare name must pass `check_name`; an explicit path must end in `.json` and exist. |
| `Pipeline spec keys` | `YAML/JSON` | `n/a` | `name`, `description`, `vars`, `stages[]` with `id`, `run.cmd` or `run.python` (+ `args`, `cwd`, `env`), `verify[]`, `platforms`, `optional`, `always`, `triage`, `timeout`. | Verify kinds: `files_exist`, `file_contains`, `output_contains`, `output_lacks`, `python`. Any other kind raises `PipelineError: unknown verify kind`. |
| `Settings variables` | `string` | `n/a` | `${HOME}`, `${DOCUMENTS}`, `${FIELDKIT}`, `${ENV:NAME}`, `${LOCAL:dotted.key}`; pipeline-local `{name}` vars after that. | `${LOCAL:...}` values may contain variables; nested expansion stops at depth five. Strict mode raises `SettingsError`; non-strict leaves the token in place. |

## API Surface

| Symbol | Description | Side Effects |
|--------|-------------|--------------|
| ``host.host`` | JSON-safe facts: `system`, `is_windows`, `is_linux`, `release`, `machine`, `python`, `distro`, `distro_like`, `cpus`. | Reads `/etc/os-release` on Linux; cached by `lru_cache(maxsize=1)` for the process lifetime. |
| ``host.is_debian_family`` | Linux with `ID` debian/ubuntu or `debian` in `ID_LIKE`. | none |
| ``host.find_tool`` | `shutil.which`, then `extra`, then `WINDOWS_EXTRA[name]` on Windows. | none |
| ``host.platform_ok`` | True for None/empty, or when the host matches `windows`, `linux` or `debian`. | none |
| ``checks.disk_free`` | Free space at the nearest existing ancestor of `path` is at least `gb` (decimal, 1e9 bytes). | none |
| ``checks.tools_present`` | Every name resolves through `find_tool`. | none |
| ``checks.always_ok`` | No-op stage for placeholders and tests. | none |
| ``settings.read_file`` | Parses `.json`, `.yaml`/`.yml`, `.toml`; other suffixes raise `SettingsError`. | File read. |
| ``settings.expand`` | Recursively expands `${...}` tokens in strings inside dicts/lists. | Reads the local settings file when `local` is None. |
| ``settings.load`` | `read_file` then `expand`. | File reads. |
| ``settings.local_settings`` | First of `fieldkit.local.json`, `fieldkit.local.yaml` under `ROOT`. | File read. |
| ``proc.clean_env`` | Copy of `os.environ` minus `AGENT_ENV`, plus `extra`. | none |
| ``proc.Runner`` | Creates `state_dir` and `log_dir` (default `state_dir/logs`). | Creates directories. |
| ``proc.Runner.run`` | Runs to completion; never raises on non-zero exit; 127 when the program is missing, 124 on timeout (whole tree killed). On POSIX the child gets its own session (`start_new_session=True`). | Starts a process; records `{cmd, started, create_time, how}` in `started-pids.json` after pruning dead entries; removes the entry when the call returns or raises; writes a timestamped log. |
| ``proc.Runner.stop`` | Stops a recorded PID and its descendants only while its creation time matches; `False` when it had already exited; `PermissionError` when unrecorded or reused. | Kills a process tree (SIGKILL / `TerminateProcess` via psutil or taskkill); rewrites the PID file. |
| ``proc.Result`` | `ok` is `returncode == 0`; `as_dict(tail=40)` keeps the last lines of stdout/stderr. | none |
| ``pipeline.Pipeline`` | Validates and resolves a spec; state under `ROOT/state/<name>/` by default. | Creates the state directory and its `logs/` directory. |
| ``pipeline.Pipeline.load`` | `settings.read_file` then constructor. | As constructor. |
| ``pipeline.Pipeline.plan`` | Resolved stages with `runs_here`, `last_status`, `changed_since_last`. | none |
| ``pipeline.Pipeline.run`` | Executes stages per the engine rules and returns the report. | Runs commands/Python targets; writes `state.json` after each executed stage and `last-report.json` at the end. |
| ``pipeline.Pipeline.verify`` | `(None, [])` when no checks; a crashing `python` check counts as failed. | Calls `python` verify targets. |
| ``pipeline.Pipeline.fingerprint`` | First 16 hex characters of SHA-256 over `json.dumps(stage, sort_keys=True)`. | none |
| ``pipeline.Pipeline.reset`` | Forgets one stage or all. | Rewrites `state.json`. |
| ``next.decide`` | One of `DO`, `BLOCKED`, `CANNOT_HERE`, `DONE` from saved state and `last-report.json`. | Constructing a `Pipeline` from a path creates its state directory; no stage runs. |
| ``next.lines`` | Human lines ending with a `NEXT:` instruction, or `DONE: ...`. | none |
| ``privacy.scan_text`` | Applies `SECRETS`, `PERSONAL` and private terms line by line; masks excerpts. | none |
| ``privacy.scan_path`` | Walks a file or tree with `os.walk` (unlistable folders become findings); recurses into ZIP-like files up to `ZIP_DEPTH`; scans PDFs' decoded streams and text layer; files over `TEXT_LIMIT` or unreadable become `not-scanned`; raises `FileNotFoundError` on a missing path. | Runs `git ls-files` when `git_only`; loads pypdfium2 for each PDF. |
| ``privacy.git_publishable`` | Tracked plus untracked-not-ignored files. | Runs `git`. |
| ``privacy.private_terms`` | `privacy.terms` entries longer than two characters. | Reads local settings. |
| ``snapshot.take`` | Validates `name` with `check_name`, then records files and selected parts to `SNAP_DIR/<name>.json`; a failed part query is stored as `{"_not_captured": reason}`. | Hashes files; runs PowerShell/`tasklist` or `systemctl`/`dpkg-query`/`ps`; writes the snapshot. |
| ``snapshot.diff`` | Compares parts present in both; files by SHA-256 when both have one, else size and mtime; a part not captured on either side is returned with empty lists and `not_compared`. | Reads snapshot files when given names (via `load`). |
| ``snapshot.summary_lines`` | One summary line per part plus up to `limit` entries per list, or `<part>: NOT COMPARED - not captured (...)`; `['no changes']` when empty. | none |
| ``proc.create_time`` | Creation time of a running PID by `psutil`, `windows` (`GetProcessTimes`, seconds since 1601, only while `GetExitCodeProcess` is `STILL_ACTIVE`) or `linux` (`/proc/<pid>/stat` field 22, clock ticks since boot); `None` when not running, a zombie, or not queryable. | none |
| ``proc.Runner.prune`` | Drops entries whose process has exited or whose PID now has another creation time. | Rewrites `started-pids.json` when anything was dropped. |
| ``privacy.not_scanned`` | The fail-closed finding `{kind: 'not-scanned', line: 0, excerpt: 'not scanned: ...'}`. | none |
| ``privacy.pdf_streams`` | Every stream in a PDF, decoded through FlateDecode/Fl, ASCIIHexDecode/AHx and ASCII85Decode/A85 chains; image streams and unfiltered streams are skipped (already in the raw scan); undecodable ones come back as `None` with the reason. Stdlib only. Also used by `office/scrub.py`. | none |
| ``snapshot.check_name`` | Returns `name` when it matches `NAME_RX` and is not only dots; raises `ValueError` otherwise. | none |
| ``snapshot.capture`` | Runs `QUERIES[part]()`; returns `{NOT_CAPTURED: reason}` on `NotCaptured`. `PARTS` maps each part name to a wrapper around it. | Runs the part's system query. |
| ``snapshot.not_captured`` | The stored failure reason of a part, a fixed reason for a non-dict section, or `None` for real data. | none |

## Kill Switches

### ``Runner.stop(pid)` in `proc.py``
- **Condition:** Caller asks to stop a PID.
- **Effect:** Raises `PermissionError` unless the PID is in `started-pids.json`. If the entry's recorded `create_time` no longer matches the live process (within `CT_TOLERANCE`, 0.05 s, using the method recorded in `how`), the entry is forgotten and the call returns `False` when the PID is not running, or raises `PermissionError: PID N is now a different process (creation time differs)` when it is. Otherwise `_kill_tree` kills the process and all descendants and returns `True`.
- **not reversible**
- `_kill_tree`: with psutil, `children(recursive=True)` then `kill()` each, children first; without psutil on Windows, `taskkill /PID <pid> /T /F`; on POSIX additionally `killpg(SIGKILL)` when the PID leads its own process group (the runner uses `start_new_session`), else `kill(SIGKILL)`. SIGTERM is no longer sent: there is no graceful stop.

### ``Runner.run(..., timeout=)` in `proc.py``
- **Condition:** `subprocess.TimeoutExpired`, or any `BaseException` (for example `KeyboardInterrupt`) while waiting.
- **Effect:** On timeout: `_kill_tree(p.pid)` then `p.kill()`; waits up to 30 s for the pipes, then closes them and returns empty output; sets return code 124 and appends `[fieldkit] timed out after N s; it and its child processes were stopped` to stderr. On any other `BaseException`: kills the tree and re-raises. The PID entry is removed in a `finally` block either way.
- **not reversible**
- Safe because the child is not reaped yet, so `p.pid` still names it. Covered by `test_timeout_stops_the_grandchild_too`.

### ``Pipeline.run` platform gate`
- **Condition:** `platform_ok(stage['platforms'])` is false.
- **Effect:** Marks `not-this-platform`, sets `ok=False` and stops later non-`always` stages, unless `optional: true`.
- reversible
- Run on the right platform with `--from <id>`.

### ``Pipeline.run` failure stop`
- **Condition:** Action fails or verify returns `False`.
- **Effect:** Sets `failed=True`; later stages run only if `always: true`.
- reversible
- Resume with `--only <id>` after fixing.

### ``clean_env(strip_agent=True)` in `proc.py``
- **Condition:** Every `Runner.run` call by default.
- **Effect:** Removes the eight names in `AGENT_ENV` from the child environment.
- reversible
- Pass `strip_agent=False` to keep them. Reason in source: mozbuild hid its own output when it saw them.

### ``ALLOW_MARK` in `privacy.py``
- **Condition:** A line contains `privacy-scan: allow`.
- **Effect:** Skips every rule for that line.
- reversible
- This is a suppression switch, not a safety switch: it also hides a real secret on the same line.

### ``privacy.not_scanned` (fail-closed gate) in `privacy.py``
- **Condition:** A file or member over `TEXT_LIMIT`, an `OSError` on stat/read, an unlistable folder (`os.walk` `onerror`), an unreadable or encrypted archive member, an archive nested `ZIP_DEPTH` deep, a PDF stream with an unknown filter or a corrupt, truncated or oversized FlateDecode stream, a stream without `endstream`, or a failing pypdfium2 text layer.
- **Effect:** Adds `{kind: "not-scanned", line: 0, excerpt: "not scanned: <reason>"}`; the CLI then exits 3.
- reversible
- Turns every silent skip of the previous version into a finding. Folders in `SKIP_DIRS` are still skipped without a finding.

### ``snapshot.diff` not-compared guard`
- **Condition:** Either side of a part is `{"_not_captured": reason}` or is not a dict.
- **Effect:** Returns `{added: [], removed: [], changed: {}, not_compared: "before: ...; after: ..."}` for that part; `summary_lines` prints `NOT COMPARED`.
- reversible
- Retake the snapshot whose part failed.

## Dead Code

- **``proc.Result.extra``** — No code in `fieldkit/` or `tests/` reads or sets it (grep of `.extra`). (risk: Low; removal changes the dataclass signature for any external caller.)
- **``pipeline.Context.dry_run``** — Set but never read: `Pipeline.run` returns `would-run` before any Python stage receives the context, so a stage never sees `dry_run=True`. (risk: Low; a future Python stage might rely on it.)
- **``checks.always_ok``** — Used only by `tests/test_pipeline.py` as a placeholder stage; not dead, but test-only. (risk: Removing it breaks that test.)

## Performance

- **CPU:** Not measured. `snapshot.files` SHA-256 hashes every file up to `HASH_LIMIT` (64 MB) in 1 MiB chunks; `privacy` inflates PDF streams and runs pypdfium2 text extraction on every PDF; `Pipeline.run` re-runs verify checks for each up-to-date stage on every run; `Runner.prune` queries the creation time of every recorded PID on each process start.
- **MEMORY:** Not measured. `Runner.run` holds the full stdout and stderr in memory via `communicate()` before writing the log. `privacy.scan_path` reads whole files and archive members up to `TEXT_LIMIT` (5,000,000 bytes) into memory; nested archives are held as in-memory `BytesIO` copies; each decoded FlateDecode stream is capped at `TEXT_LIMIT + 1` bytes.
- **IO:** Not measured. `state.json` is rewritten after each executed stage; `started-pids.json` is rewritten on every process start and end, and is pruned of dead entries on each start; each `Runner.run` writes one log file.
- **NOTES:** The only timing figure for this repository is the whole test suite before the security changes: 584 passed, 1 skipped, 2 xfailed in 194.89 s (MEASUREMENTS.md). Per-module timings are not measured. Snapshot queries use `QUERY_TIMEOUT` = 180 s; Linux `systemctl is-enabled` fallbacks 30 s each; a timed-out `Runner.run` waits up to 30 s for its pipes after the kill.

## Security

- **Remote execution:** None from the network: no module opens a socket. Local code execution is by design: pipeline specs run arbitrary `cmd` programs and import arbitrary `module:function` targets. Treat a pipeline file as executable code.
- **Data handling:** Privacy findings carry a masked excerpt (`_mask`: first four characters, `…`, last two) so reports are safe to paste; `not-scanned` findings carry only the reason. Snapshots store absolute file paths and installed program names; logs store full program output; `started-pids.json` stores command lines and creation times; all live under `state/`, which `.gitignore` lists. Private terms live in `fieldkit.local.json`, also ignored. Child processes inherit the full environment minus `AGENT_ENV`, including any secrets in environment variables.
- **Attack surface:** Pipeline spec (code execution); user regexes in verify checks (no time limit); scanned file content (regex input, bounded zlib and base-16/base-85 decoding, and pypdfium2's native parser for PDFs up to `TEXT_LIMIT`); explicit `.json` paths given to `snapshot diff` (read only). Snapshot names no longer reach the file system unvalidated.
- **Notes:** The privacy scan is pattern-based and fails closed on unreadable input, but it has documented gaps: eight secret patterns only; no phone numbers, postal addresses or names outside the term list; image streams are not decoded and there is no OCR, so scanned pages are unread; `ZIP_LIKE` lists `.zip`, `.docx`, `.xlsx`, `.pptx`, `.docm`, `.xlsm`, `.pptm`, `.jar`, `.whl` and `.xpi` only, so OpenDocument or EPUB containers are scanned as raw compressed bytes without a `not-scanned` finding (inference); a ZIP-suffixed file that fails `zipfile.is_zipfile` is scanned as plain bytes; `SKIP_DIRS` are skipped without a finding; the `privacy-scan: allow` marker suppresses a whole line; `PLACEHOLDER_USERS` and `EMAIL_OK` whitelist placeholder names and addresses. The scanner lists AI-provider key prefixes (`sk-`, `sk-ant-`) that can also match unrelated `sk-` strings (inference). Process identity: without psutil on Windows, `OpenProcess` with `PROCESS_QUERY_LIMITED_INFORMATION` can fail for a process the caller may not query, which `stop()` treats as exited and returns `False` (inference).

## Error Conditions

| Error | Cause | Remedy |
|-------|-------|--------|
| `PipelineError: <source>: a pipeline needs 'name' and 'stages'` | Spec is not a dict or lacks a key. | Add `name` and `stages`. |
| `PipelineError: every stage needs a unique 'id'` | Missing or duplicate `id`. | Give each stage a distinct `id`. |
| `PipelineError: stage X has no 'run' / run needs 'cmd' or 'python'` | Stage lacks an action. | Add `run.cmd` or `run.python`. |
| `PipelineError: python target must be 'module:function'` | Dotted target without a colon. | Use `package.module:function`. |
| `PipelineError: unknown verify kind` | Verify entry not one of the five kinds. | Use `files_exist`, `file_contains`, `output_contains`, `output_lacks` or `python`. |
| `PipelineError: no stage 'X'; stages are [...]` | `--only`/`--from` names a missing stage. | Use an ID from `fieldkit pipeline plan <name>`. |
| `SettingsError: cannot expand ${KIND:arg}` | Strict expansion of an unknown env var, missing local key, or unknown kind. | Add the key to `fieldkit.local.json` or set the variable; `plan` and `--dry-run` expand non-strict. |
| `SettingsError: unsupported settings format` | Suffix not `.json`/`.yaml`/`.yml`/`.toml`. | Convert the file. |
| `PermissionError: PID N was not started by fieldkit; refusing to stop it` | `Runner.stop` on an unrecorded PID. | Stop it yourself if it is yours; the refusal is intended. |
| `Result.returncode 127 / 124 (`[fieldkit] timed out after N s; it and its child processes were stopped`)` | Program not found / timeout expired. | Check the program path with `find_tool`; raise the stage `timeout`. |
| `FileNotFoundError: ... nothing scanned is not the same as nothing found` | `privacy.scan_path` on a missing path. | Fix the path. |
| `ValueError: ... is not a git repository; drop --git to scan every file` | `--git` on a non-repository folder. | Drop `--git` or scan the repository root. |
| `SystemExit: snapshot take needs one NAME / say what to record: ... / snapshot diff needs two names: BEFORE AFTER` | CLI argument checks in `cmd_snapshot`. | Supply the missing names or parts. |
| `SystemExit: no pipeline 'X'; have: ...` | `_pipeline_path` cannot resolve the name. | Use a listed name or a path to a `.yaml`/`.yml`/`.json` file. |
| `PermissionError: PID N is now a different process (creation time differs); refusing to stop it` | The recorded process exited and the PID was reused by another process. | None needed; the original process is gone and the new owner of the PID is left alone. |
| `fieldkit: snapshot name '...' refused: use only letters, digits, '.', '_' and '-'` | `check_name` rejected a name with a separator, space, `..`-style path or other character. | Use a plain name such as `before-install`. |
| `not-scanned: too large / unreadable / unreadable archive / archive nested more than 3 deep / <Filter> stream: no decoder for this filter / FlateDecode stream ... / text layer could not be read` | The fail-closed paths in `privacy.py`. | Inspect the named file or member by hand, or remove it from what is published; the CLI exit code stays 3 until then. |
| `_not_captured: <cmd> timed out after 180 s / could not start / exit N / PowerShell answer is not readable JSON / has an unexpected shape / the process list came back empty` | `snapshot._run`, `_ps_json` or `processes` raised `NotCaptured`. | Retake the snapshot; `diff` reports the part as `NOT COMPARED` until both sides are captured. |

## Tasks

### Run the unit tests for this group

Run after any change under `fieldkit/core/`. The test files cover host, settings, runner, privacy, pipeline, next and snapshot, and the security fixes (reused PIDs, tree kill, snapshot names, not-captured parts, not-scanned and PDF streams).

**Prerequisites:**
- Python with `pytest`, `pyyaml` and `pypdfium2` installed (the Office tests also need the office group's libraries)
- Working directory is the Fieldkit folder
- `git` on PATH (the privacy `--git` test runs `git init`)

**Step 1:** Run:

```powershell
python -m pytest tests/test_core.py tests/test_pipeline.py tests/test_next.py tests/test_snapshot.py tests/test_proc_security.py tests/test_snapshot_security.py tests/test_office_security.py
```
  - Expected: A summary line with `passed` and no `failed`. The per-file count is not measured; the measured full suite, from before the security changes, is 584 passed, 1 skipped, 2 xfailed.
**Step 2:** Run:

```powershell
python -m pytest tests
```
  - Expected: The full suite passes; this also exercises callers of `core` in other groups.

**After this task:** The behaviour asserted by the tests holds on this platform. Linux-only branches (`systemctl`, `dpkg-query`, `ps`, `/etc/os-release`, `/proc/<pid>/stat`, `killpg`) run only on Linux; the Linux `systemctl` parser is tested with simulated output, and real Linux behaviour is not measured.

### Add a stage to a pipeline and prove it

Use when you extend `fieldkit/build/pipelines/*.yaml` or write a new pipeline.

**Prerequisites:**
- A pipeline file with `name` and `stages`
- A concrete artefact the stage produces

**Step 1:** Add a stage with a unique `id`, a `run` (`cmd` list or `python: "pkg.mod:fn"` plus `args`) and at least one `verify` entry that checks the artefact, for example `{files_exist: ["{out}/result.txt"]}`.
  - Expected: The spec loads without `PipelineError`.
**Step 2:** Run:

```powershell
fieldkit pipeline plan <name>
```
  - Expected: The new stage appears with `here` or `NOT HERE`, `last=None` and its resolved command.
**Step 3:** Run:

```powershell
fieldkit pipeline run <name> --dry-run
```
  - Expected: The new stage shows `would-run`; nothing executes.
**Step 4:** Run:

```powershell
fieldkit pipeline run <name> --only <stage-id>
```
  - Expected: Status `done` with `ok` verify lines; exit code 0. `ran-unverified` means the stage has no verify checks.
**Step 5:** Run:

```powershell
fieldkit next <name>
```
  - Expected: The decider moves past the new stage (`DO` for a later stage, or `DONE: all N stages verified.`).

**After this task:** `state/<name>/state.json` records the stage as `done` with its fingerprint; a later run reports it `up-to-date` while the artefact exists and the definition is unchanged.

### Gate a folder before publishing

Run before any commit, push or release that includes files from the folder.

**Prerequisites:**
- Private terms, if any, in `fieldkit.local.json` under `privacy.terms`

**Step 1:** Run:

```powershell
fieldkit privacy scan . --git
```
  - Expected: `0 finding(s) in 0 file(s)` and exit 0, or a list of `line N  kind  excerpt` rows and exit 3, where `not-scanned` rows (line 0) name input that could not be read.
**Step 2:** Open each reported file at the reported line and remove or replace the value; use `privacy-scan: allow` only for deliberate test fixtures. For each `not-scanned` row, inspect the file or member by hand or exclude it.
  - Expected: The rerun of step 1 exits 0.
**Step 3:** Check manually for data types the scanner does not cover: phone numbers, addresses, unlisted names, text in images or scanned PDFs, and the contents of ZIP containers outside `ZIP_LIKE` such as `.odt` or `.epub`.
  - Expected: Nothing found outside the scanner's coverage.

**After this task:** No known pattern matches in the files git would publish. This is not proof that no personal data remains.

### Prove what an action changed

Use around an install, uninstall or agent action to get a record independent of any self-report.

**Prerequisites:**
- Write access to the Fieldkit `state/` folder

**Step 1:** Run:

```powershell
fieldkit snapshot take before --path <DIR> --services --tasks --programs
```
  - Expected: JSON naming `state/snapshots/before.json`.
**Step 2:** Perform the action, then repeat step 1 with the name `after` and the same flags.
  - Expected: `state/snapshots/after.json` exists.
**Step 3:** Run:

```powershell
fieldkit snapshot diff before after
```
  - Expected: `no changes` (exit 0) or per-part `+N added, -N removed, ~N changed` lines (exit 3); a part that failed in either snapshot prints `NOT COMPARED - not captured (...)` (exit 3).

**After this task:** Two JSON records and a diff that names every added, removed and changed key in the parts both snapshots captured, and names every part it refused to compare. Files `snapshot.files` could not stat or hash are absent from a record without a marker.

### Confirm the fail-closed behaviour of the privacy scan

Use after changing `privacy.py`, or to show a reviewer that unread input is not reported as clean.

**Prerequisites:**
- PowerShell in the Fieldkit folder

**Step 1:** Run:

```powershell
Set-Content "$env:TEMP\fk-big.txt" ("x" * 6000000)
fieldkit privacy scan "$env:TEMP\fk-big.txt"
```
  - Expected: `line 0     not-scanned                not scanned: too large (6,000,002 bytes > 5,000,000)` (not measured: the byte count includes the CRLF Set-Content adds and was observed once while writing this document), then `1 finding(s) in 1 file(s)`; exit 3.
**Step 2:** Run:

```powershell
Remove-Item "$env:TEMP\fk-big.txt"
```
  - Expected: The test file is gone.

**After this task:** A file over `TEXT_LIMIT` is a finding, not a silent skip.

## Troubleshooting

**Symptom:** A stage re-runs on every invocation.
**Cause:** No `verify` checks (`ran-unverified`), or verify relies only on `output_contains`/`output_lacks`, which fail when `output` is None during the up-to-date check.
**Remedy:** Add a `files_exist`, `file_contains` or `python` check that proves the artefact.
**Verify:** Second `fieldkit pipeline run <name>` shows `up-to-date`.

**Symptom:** A stage re-runs after an unrelated edit.
**Cause:** The fingerprint covers the whole resolved stage dict, including filled vars; any `--var` change or edit to the stage changes it.
**Remedy:** Expected behaviour. Keep `--var` values stable between runs.
**Verify:** `fieldkit pipeline plan <name>` shows `changed_since_last` false in `--json` output.

**Symptom:** A child build tool prints less than expected, or behaves differently from your own shell.
**Cause:** `clean_env` strips `AGENT_ENV` names; conversely, the child sees every other variable in your environment.
**Remedy:** Pass `env` in `run.env`, or call `Runner.run(..., strip_agent=False)` from Python.
**Verify:** The stage log under `state/<name>/logs/` shows the expected output.

**Symptom:** `snapshot diff` prints `services: NOT COMPARED - not captured (before: ...)`.
**Cause:** The part's query failed when that snapshot was taken (timeout, start failure, non-zero exit, unreadable JSON or empty process list) and was stored as `_not_captured`.
**Remedy:** Retake the snapshot named by `before`/`after`; snapshots taken before the security changes may still hold a silently empty part and should be retaken.
**Verify:** The diff shows `+N added, -N removed, ~N changed` for that part.

**Symptom:** `fieldkit privacy scan` exits 3 with only `not-scanned` rows.
**Cause:** Input over `TEXT_LIMIT`, unreadable files or folders, encrypted or corrupt members, unknown PDF filters (for example LZWDecode), or pypdfium2 missing or failing.
**Remedy:** Read the reason in each excerpt; install pypdfium2 if every PDF reports `text layer could not be read`; otherwise inspect or exclude the input.
**Verify:** The rerun exits 0 or lists only real findings.

**Symptom:** `Runner.stop` raises `PID N is now a different process (creation time differs)`.
**Cause:** The recorded child exited and the operating system reused its PID.
**Remedy:** None; the refusal is intended. If it happens for a live child, check that the same creation-time method (`how`) is still available, for example that psutil was not removed between start and stop.
**Verify:** `started-pids.json` no longer lists the PID.

**Symptom:** `fieldkit next` reports `BLOCKED` with only `failed.`
**Cause:** The last report has no triage matches, no error lines and no result `detail`.
**Remedy:** Add a `triage` set to the stage, or read the log named in `last-report.json`.
**Verify:** `next` names a cause and fix after the next failure.

## Technical Debt

🟡 **LOW** — `Runner.run` buffers all output in memory before logging. → Stream stdout/stderr to the log file while the process runs.
🟡 **LOW** — `pipeline list` globs `*.y*ml` only, while `_pipeline_path` also accepts `.json`. → Include `*.json` in `cmd_pipeline`'s list.
🟠 **MEDIUM** — `snapshot.files` still drops a file it cannot `stat` or hash (`except OSError: continue`) without a marker, unlike the fail-closed `privacy` and part queries; a file readable in one snapshot and not the other shows as added or removed. → Record `{"error": reason}` for such files and make `diff` report them as not compared.
🟠 **MEDIUM** — `ZIP_LIKE` omits other ZIP containers (OpenDocument `.odt`/`.ods`/`.odp`, `.epub`); they are scanned as compressed bytes and can hide text without a `not-scanned` finding (inference). → Detect ZIPs by the `PK\x03\x04` signature regardless of suffix, as `_scan_zip` already does for members.
🟠 **MEDIUM** — pypdfium2 parses untrusted PDFs in the Fieldkit process; a parser crash or hang in native code would take down or stall the scan (inference; not measured). → Run `_pdf_text_layer` in a subprocess through `Runner.run` with a timeout and report a failure as `not-scanned`.
🟡 **LOW** — `_systemd_services` falls back to `systemctl is-enabled` per unit and maps any failure to `unknown` instead of `NotCaptured`; the Linux parsers are tested only with simulated output. → Run the snapshot tests on a real Debian host and record the result in MEASUREMENTS.md.
🟡 **LOW** — psutil is optional and undeclared, so process identity and tree kills take one of three code paths depending on the machine; the path is recorded per PID (`how`) but only the psutil path enumerates descendants on POSIX before the kill. → Declare psutil as a dependency, or document the fallback behaviour in `INSTALL.md`.

## Impact If Removed

Removing `fieldkit/core/` breaks Fieldkit as a whole. `cli.py` imports `core.settings` at module load, so every `fieldkit` subcommand fails. `fieldkit pipeline`, `next`, `privacy`, `snapshot` and `host` lose their implementation. The three shipped pipelines (`debian-kernel`, `firefox-windows`, `office-deliver`) cannot run, `office-deliver`'s privacy stage loses its scanner, and `office/scrub.py` loses `privacy.pdf_streams`. `fieldkit/release.py` and `fieldkit/build/lifecycle.py` call `settings.load` and would fail on import or first use.

## Claim Sources

| Claim | Basis | Evidence |
|-------|-------|----------|
| Platform differences are centralised in host.py | 📄 stated in input | Every difference between Windows and Debian lives here |
| A stage is done only when verify passes | 📄 stated in input | a stage is only "done" when its verify checks pass |
| Stages without verify always re-run | 📄 stated in input | A stage with no verify is re-run every time. |
| Output-only stages always re-run | 📄 stated in input | stages proven only by their output always re-run |
| Stopping by name or by a stale PID is rejected | 📄 stated in input | stopping by name or by a stale number |
| Agent environment variables are stripped because mozbuild hid output | 📄 stated in input | mozbuild (and possibly other tools) hides its own output when it sees them |
| Archive members are scanned individually | 📄 stated in input | scanning their bytes proves nothing, so scan every member instead |
| Privacy reports are safe to paste | 📄 stated in input | the scan report is itself safe to paste |
| next never runs a stage | 📄 stated in input | it never runs a stage |
| Snapshots are read-only on the system | 📄 stated in input | Read-only: taking one changes nothing. |
| Group size is 1,017 lines | 📄 stated in input | core 1,017 |
| Pipeline specs are executable code | 🤖 model inference | *(none — model judgment)* |
| Context.dry_run is never observed by a stage | 🤖 model inference | *(none — model judgment)* |
| Result.extra is unused | 🤖 model inference | *(none — model judgment)* |
| Linux branches not exercised in the measured run | 🤖 model inference | *(none — model judgment)* |
| Removing core breaks every subcommand | 🤖 model inference | *(none — model judgment)* |
| Measured suite result | 📄 stated in input | 584 passed, 1 skipped, 2 xfailed in 194.89 s |
| Unreadable input is a finding, not a skip | 📄 stated in input | never a silent skip |
| PID identity includes creation time | 📄 stated in input | Every process started is recorded with its PID AND its creation time. |
| Timeout kills the runner's tree only | 📄 stated in input | descendants), never anything outside that tree. |
| Failed snapshot parts are not compared | 📄 stated in input | so a failed query can never look like everything was removed |
| Snapshot names are restricted | 📄 stated in input | (no folders, no '..') |
| Image streams are not decoded | 📄 stated in input | pixels hold no text |
| Nested archives deeper than the limit are reported | 📄 stated in input | deeper is reported, not skipped |
| Current line count 1,465 | 🤖 model inference | *(none — model judgment)* |
| OpenDocument/EPUB containers bypass member scanning | 🤖 model inference | *(none — model judgment)* |
| snapshot.files drops unreadable files silently | 🤖 model inference | *(none — model judgment)* |
| pypdfium2 native parsing is an in-process risk | 🤖 model inference | *(none — model judgment)* |
| Unqueryable processes are treated as exited by stop() | 🤖 model inference | *(none — model judgment)* |


---
**How to verify this document:**
`📄 stated in input` — the model's phrasing of something your source text said.
Find the matching line in the original to verify.
`🤖 model inference` — the model's own judgment or synthesis. Treat as opinion,
not measurement. Re-run on the same input and check whether specific numbers
stay consistent between runs.

*Auto-generated DITA-structured developer documentation.*