# Port engine (`fieldkit/buildh/`): carrying the Gorilla patch set onto a new stable Firefox release

> Generated 2026-10-02 | Source: `port-engine`

---

## Purpose

The port engine takes the Gorilla Firefox patch set (grouped `.patch` files, `NEW_FILES`, `REPLACE_FILES` and `DELETED_FILES.manifest.txt`, ordered by `config/patch_policy.json`) and carries it onto the latest stable Firefox. It exists because a small local model (Gemma, 4.6B per the source) cannot port a browser fork in one context: the engine cuts the work into a plan of `script`, `model` and `owner` steps (`task.py`), runs every deterministic tier itself first (`firefox.auto_port`, with format-aware tiers in `fluent.py`, `keyed.py`, `prefs.py` and `relocate.py`), sends only the residue to the model as one bounded packet whose answer is parsed text (`answer.py`), and judges every result by a check the model cannot influence. Untouched sources live in a read-only vault (`vault.py`, `upstream.py`). What a script cannot decide is parked with a measured brief (`deferred.py`, `decision.py`), and a person's own fixes are recorded with an explicit kind, `privacy` or `port` (`handedit.py`), and exported back into the matching patch-set group (`export.py`). `snapshot.py` derives a complete, proven patch set from a live built tree. Trust level: the engine trusts git, GNU `patch`, Mozilla's product-details and its GitHub release tags, and the maintainer's patch set and settings. It does not trust model output, the task record (the tree decides), the model's claims of success, or any decision not typed at a real terminal. The group measured 5,594 lines of Python in MEASUREMENTS.md; the security fixes that added the explicit patch kind and the restore refusal came after that count, so the current size is not measured.

## Known Alternatives Considered

The source documents these rejected alternatives. (1) Building from `main`: rejected, `main` is Nightly (`upstream.py`). (2) Measuring size with a partial git clone: rejected, it "quietly downloaded the whole source" (`vault.measure_firefox`). (3) Letting the model fetch its own job with a tool, or name a task: rejected after live run 1, Gemma "invented arguments for the tool" (`cli.py`). (4) Giving the model edit and submit tools: rejected after live run 3, it "wrote detailed reports of edits it never made"; tool mode survives only behind `--tools` and `FIELDKIT_ALLOW_MODEL_TOOLS=1`. (5) Asking the model to rewrite whole blocks: rejected after live runs 4 and 5, "a 4.6B model cannot copy 30 lines back faithfully" (`answer.py`); replaced by line operations, then by REMOVE/KEEP questions. (6) Fuzz 3 in GNU `patch`: rejected, it misplaced a `return NS_OK;` block before a function signature; the engine uses `--fuzz=0`. (7) A Tier 0 "answer-key" fallback copying the previous release's file: removed, "copying Firefox 156 files over 157 breaks upstream changes". (8) Parallel jobs: rejected after an overnight run where "git locks collided". (9) Line-based porting of Fluent and pref files: replaced by message-id and pref-name porting. (10) Last-writer-wins `task.save`: replaced by a merge of on-disk steps after a `record` was overwritten. (11) Classifying hand patches only by a keyword regex over the free-text reason (`export.PRIVACY`): replaced by a `kind` recorded with the hand step (`handedit.KINDS`); the regex survives only as a fallback for steps recorded without one, and every such patch is printed as a guess.

## Architecture

- **Pattern:** Persistent state machine over a git working copy: a JSON task (`state/build-harness/<task>/task.json`) holds an ordered step list; `task.advance` runs `script` steps in-process, stops at `model` steps (packet, apply, check, checkpoint-or-revert) and turns `owner` steps into `blocked`. Steps are dicts naming `module:function` callables (`run`, `packet`, `check`, `auto`), resolved with `importlib`. Script steps may return `add_steps` to grow the plan (`plan_groups`, `step_apply_group`, `relocate.heal`). Every event is appended to a hash-chained `journal.jsonl`. The `drive` loop in `cli.py` is a sequential worker (a one-thread `ThreadPoolExecutor`) that spawns one fresh `gorilla-opencode -p` process per job.
- **Trust boundary:** Untrusted: model stdout (parsed by `answer.parse` / `answer.parse_questions`, which refuse out-of-range or duplicate lines), the model's own claims (never read as success; `check_port` reads the file), the task record (`drive` calls `verify.reopen` before handing out jobs). Trusted: the vault after `vault.verify`, the patch set, `patch_policy.json`, `fieldkit.local.json`, Mozilla's product-details JSON and `git ls-remote` output (the tag commit is re-checked after clone, but product-details itself is not signature-checked), and kernel.org `sha256sums.asc` (compared by hash; the `.asc` signature is not verified in this group). Human decisions are gated by `task.owner_terminal()` (`sys.stdin.isatty() and sys.stdout.isatty()`), a typed confirmation sentence and `COOLING_OFF_SECONDS = 30`.
- **Attack surface:** The `fieldkit build-harness` CLI; three MCP tools (`build_harness_status`, `build_harness_next`, `build_harness_submit`) via `cli.mcp_call`, with no task argument and no approve or unblock; model output text passed into `answer.apply` against one allowed file; network inputs from `product-details.mozilla.org`, `github.com/mozilla-firefox/firefox`, `archive.mozilla.org` and `kernel.org`; patch files and `patch_policy.json` in the maintainer's folder; `tarfile.extractall(..., filter="data")` on kernel tarballs; the Gorilla OpenCode config the worker profile is derived from.
- **Dependencies:** `git (CLI)`, `GNU patch (`shutil.which('patch')` or Git for Windows `usr/bin/patch.exe`)`, `node (optional, `node --check`)`, `gorilla-opencode (drive only)`, `Python stdlib: json, re, subprocess, hashlib, urllib.request, tarfile, difflib, concurrent.futures, threading, importlib`, `fieldkit.core.settings`, `fieldkit.buildh.verify`, `fieldkit.buildh.preflight`, `fieldkit.buildh.mozbuild_rules`, `fieldkit.buildh.ownercheck`, `fieldkit.buildh.buildrun`, `fieldkit.build.kernel (expected_sha256)`, `fieldkit.leakgate (cli dispatch only)`, `cmd /c mklink /J (snapshot, Windows only)`

## Flags & Configuration

| Name | Type | Default | Effect | Notes |
|------|------|---------|--------|-------|
| `--pin` | `string` | `none (latest stable)` | `start`: port to this stable version instead of the latest. | Must match `^\d+\.\d+(\.\d+)?$`; the tag must exist on `--source`. |
| `--source` | `string` | `https://github.com/mozilla-firefox/firefox` | `start`: repository URL or local git path holding the release tag. | Used by `upstream.remote_commit` and the vault clone. |
| `--budget` | `int` | `100000` | The model's context window in tokens; packets get `budget * 0.4 * 4` characters. | `drive` additionally parks packets over `PACKET_LIMIT = 24_000` chars. |
| `--workdir` | `string` | `<vault root>/../Build.Work/firefox/<version>` | `start`: where the working copy is made. | `firefox.py` docstring names `<vault root>/../work/firefox/<version>`: docstring drift. |
| `--harness` | `string` | `${LOCAL:firefox.root}` | `start`, `snapshot`: the Gorilla.firefox folder holding the patch set. | Stored in `meta.harness_root`. |
| `--task` | `string` | `contents of `state/build-harness/CURRENT`` | Selects the task; most actions also take the task id as the first positional argument. | `start` writes `CURRENT`. |
| `--version` | `string` | `newest in the vault` | `vault verify|restore`; `snapshot`: the live tree's version. | `snapshot` refuses without it. |
| `--note` | `string` | `""` | `submit`, `record`: reason stored in the journal and, for hand ports, `hand_note`. | `keeps: <line>` entries declare removed lines a hand port keeps on purpose (`firefox.hand_keeps`). |
| `--hand` | `bool` | `false` | `submit`: a person ported the hunk; `check_port` uses `hand_port_check` (judged by meaning). | With no change, passes only if the tree already holds the result. |
| `--do` | `string` | `none` | `brief`, `deferred`: the exact confirmation sentence that carries out revert or drop. | Also needs a real terminal and a prior `shown` log entry at least 30 s old. |
| `--technical` | `bool` | `false` | `brief`, `deferred`: full technical brief instead of the plain one. | Showing either form starts the cooling-off clock. |
| `--tools` | `bool` | `false` | `drive`: model edits with tools instead of answering in text. | Refused unless the environment has `FIELDKIT_ALLOW_MODEL_TOOLS=1`. |
| `--line-ops` | `bool` | `false` | `drive`: force DELETE/CHANGE/INSERT AFTER instead of REMOVE/KEEP questions. | The packet text still decides; packets that only offer line operations are parked for a person in answer mode. |
| `--agent` | `string` | `gorilla-opencode` | `drive`: agent executable. | Run as `<agent> -p <prompt> -c <workdir> -q`. |
| `--max-jobs` | `int` | `200 (when unset)` | `drive`: stop after this many jobs. |  |
| `--job-timeout` | `string` | `45m` | `drive`: sets `GORILLA_OPENCODE_HEADLESS_TIMEOUT`. | The worker profile also sets first-byte `20m` and stream-stall `10m`. |
| `--prove` | `bool` | `false` | `snapshot`: rebuild from pristine plus the captured set and compare with the live tree. | Needs a fresh `proof-tree` folder. |
| `--out` | `string` | `Build.Work/snapshot-<version>` | `snapshot`: output folder. | `patchset/` inside it is deleted and rewritten. |
| `FIELDKIT_ALLOW_MODEL_TOOLS` | `env` | `unset` | Permits `drive --tools`. | Tool mode lets the model write anywhere the user account can. |
| `${LOCAL:vault.root}` | `setting` | `<Fieldkit>/vault` | Vault location. | Must expand to an absolute path. |
| `kind=privacy|port` | `positional token` | `none (export falls back to keywords)` | `record`: stored as `kind` in the `hand-edit` journal event; `export` uses it to choose `22.EGRESS.LOCKDOWN.<v>` or `21.PORT.FIXES.<v>`. | Parsed out of `args` in `buildh/cli.py` because `fieldkit/cli.py` owns the parser; more than one `kind=` raises `Refused`, a value outside `KINDS` raises `Refused` in `handedit.check_kind`. |

## API Surface

| Symbol | Description | Side Effects |
|--------|-------------|--------------|
| `upstream.latest_firefox()` | Latest stable version, tag, peeled commit, source and reason. | HTTP GET to product-details; `git ls-remote`. |
| `upstream.firefox_tag()` | `157.0` to `FIREFOX_157_0_RELEASE`; raises on non-stable versions. | none |
| `vault.fetch_firefox()` | Shallow clone of the tag into `<root>/firefox/<version>`, commit check, read-only files, `vault.json`. | Network clone; writes the vault. |
| `vault.verify()` | HEAD equals recorded commit and porcelain status is empty (git); sha256 matches (tarball). | May refresh git's index. |
| `vault.restore()` | Fresh working copy from a verified vault entry. | `git clone --no-hardlinks` or tar extract; makes files writable. |
| `vault.measure_firefox()` | Source archive size via HEAD request. | One HTTP HEAD. |
| `task.start()` | Creates `task.json` with a plan hash; refuses an existing id. | Writes state; journal `start`. |
| `task.advance()` | Runs script and auto tiers until a model step, BLOCKED, DEFERRED, WAITING or DONE. | Edits the working copy, commits checkpoints, writes the journal. |
| `task.packet()` | The one packet for the current model step, trimmed to `budget_tokens * 0.4 * 4` chars. | Calls `advance`; journal `packet`. |
| `task.submit()` | Runs the step's check; checkpoint on pass, `git reset --hard` plus `git clean -fd` on fail. | Commits or resets the whole working copy. |
| `task.unblock()` | Reopens, hands to a person, or (owner terminal only) skips a step. | May commit a checkpoint. |
| `task.rewind()` | Resets the working copy to before the step's checkpoint and reopens it and every later step. | `git reset --hard`; drops checkpoints. |
| `task.journal() / task.verify_journal()` | Append-only, hash-chained event log and its verifier. | Appends to `journal.jsonl`. |
| `firefox.plan()` | Initial steps: resolve, vault, workcopy, plan-groups. | none |
| `firefox.step_apply_group()` | GNU patch `-p1 --forward --fuzz=0` per patch; failed hunks become `port-*` model steps, missing files go through `relocate`. | Edits the working copy; deletes untracked `.rej`/`.orig`. |
| `firefox.auto_port()` | Deterministic tiers: fluent, keyed, prefs, already-upstream, obsolete, transplant, auto_substitute, block_removal, renamed_removal, auto_merge. | Writes the target file on success; may return `defer`/`obsolete`. |
| `firefox.check_port()` | Format-aware check of one hunk against `HEAD`, plus collateral and size guards. | none |
| `firefox.packet_port()` | Packet text: hunk, numbered window of at most `MAX_WINDOW = 160` lines, and REMOVE/KEEP questions or line-operation instructions. | none |
| `firefox.step_final_checks()` | Leftovers, conflict markers, unexplained deletions, syntax, regression re-check via `still_holds`. | Journal `regression` events. |
| `fluent.port() / fluent.transfer() / fluent.check()` | Message-id porting of `.ftl` hunks and strict wording transfer onto renamed ids. | none (pure) |
| `fluent.step_dedupe()` | Removes surplus message copies and restores lost ones against the maintainer's truth tree. | Writes the file. |
| `keyed.port() / keyed.check()` | Key-based porting for `.properties`, `.dtd`, `.ini`/`.inc`. | none (pure) |
| `prefs.port() / prefs.check()` | Pref-name porting for `firefox.js`, `all.js`, `mobile.js`, `firefox-branding.js`. | none (pure) |
| `relocate.steps_for_missing() / relocate.heal()` | Find a moved file (`git grep -nF`) or a generated CSS's Sass entry; replace owner steps. | `heal` mutates `t['steps']`. |
| `answer.apply() / answer.parse_questions()` | Parse and apply model text; raise `BadAnswer` on any malformed answer. | `apply` writes the file, keeping its line endings. |
| `worker.write_profile()` | Minimal Gorilla OpenCode profile under `Build.Work/worker-config`. | Writes `config.json`, `loadout.json`, `connection.json`. |
| `handedit.record()` | Validates `kind` with `check_kind`, then turns the working-tree diff of `files` into done hand steps (moves merged) and checkpoints them. | Commit; journal `hand-edit` with `files`, `why` and `kind`. |
| `handedit.record_from_commit()` | Recovery: re-creates hand steps from an existing checkpoint commit; validates `kind` first. | Writes `task.json`; journal `hand-edit` with `kind`; no commit. |
| `export.export()` | Writes each hand-edit checkpoint as `NNN-<slug>.patch` into `21.PORT.FIXES.<v>` or `22.EGRESS.LOCKDOWN.<v>` with a `README.md`. Returns `{'privacy': [...], 'port': [...], 'kinds': [(name, kind, how), ...]}`; prints one `[privacy cut]`/`[port fix]` line per patch with how its kind was decided, and a closing `REVIEW the kind of every patch` line that counts keyword guesses. | Writes into the maintainer's patch set (unless `dry`). |
| `deferred.build() / deferred.apply_drop()` | Read-only brief for a deferred step; guarded drop. | `apply_drop` marks the step done with `dropped_by_owner`; logs. |
| `decision.owner_file_edit() / decision.apply()` | Brief for an uncommitted edit in the maintainer's repo; guarded revert that saves the diff first. | `apply` runs `git checkout -- <path>`; appends `decisions.jsonl`. |
| `snapshot.capture() / snapshot.prove() / snapshot.compare_curated()` | Patch set from live tree versus pristine vault; byte proof; curated hunks judged by the live tree. | Writes `patchset/`, `harness/` (with an `mklink /J` junction), `MANIFEST.json`, `PROOF.json`. |
| `cli.mcp_call()` | The three MCP tools; always the current task. | Through `task.packet` / `task.submit`. |
| `cli.drive()` | Preflight, tree sync, then one fresh agent run per job until DONE (0), BLOCKED/DEFERRED (3), crash stop (4) or preflight failure (5). | Spawns processes; writes `drive.log`, journal, working copy. |
| `handedit.check_kind() / handedit.KINDS` | Accepts `None` or a member of `KINDS`; anything else raises `task.Refused`. | None. |
| `export.kind_of()` | Precedence: (1) a file already in `privacy_files` forces `privacy` (a recorded `port` is reported as KEPT privacy, because 21.PORT.FIXES applies before 22.EGRESS.LOCKDOWN); (2) a recorded kind in `KINDS`; (3) `PRIVACY.search(why)`, labelled `keyword guess, nothing recorded: review`. | None; `export` adds privacy files to `privacy_files` after each privacy patch. |
| `export.hand_commits()` | Pairs each `checkpoint: hand edit` commit (oldest first) with the first `hand-edit` journal event whose file set equals the commit's; falls back to the commit subject and no kind. | Runs `git log` and `git show`; reads `journal.jsonl`. |

## Kill Switches

### `task.advance / task.submit`
- **Condition:** `t['approved']` is false
- **Effect:** Raises `Refused`; no step runs and no submit is accepted.
- reversible
- Approval exists only on the CLI (`task.approve`), never over MCP.

### `cli.drive (preflight)`
- **Condition:** `preflight.run(tid, model=True)` reports any failed row
- **Effect:** Returns 5; no job starts.
- reversible
- Locks are not fixed automatically (`fix_locks=False`).

### `cli.drive (crash counter)`
- **Condition:** Two consecutive jobs return `CRASH`
- **Effect:** Returns 4 with `STOPPED: two jobs in a row crashed`.
- reversible
- A non-crash result resets the counter.

### `cli.drive (PACKET_LIMIT)`
- **Condition:** Packet over 24,000 characters
- **Effect:** Step set to `blocked` with a `too-large` journal event; no attempt counted.
- reversible
- `unblock ... retry` or a hand port.

### `cli.drive (tools guard)`
- **Condition:** `--tools` without `FIELDKIT_ALLOW_MODEL_TOOLS=1`
- **Effect:** Raises `Refused`.
- reversible
- 

### `task.unblock skip`
- **Condition:** Step is a script step or `final*`, or no real terminal
- **Effect:** Raises `Refused`.
- reversible
- Added after an overnight agent skipped many steps including failing final checks.

### `deferred.apply_drop / decision.apply`
- **Condition:** Wrong sentence, no real terminal, brief not shown, less than 30 s since shown, file changed since the brief, or (`apply_drop`) `drive.log` written in the last 180 s
- **Effect:** Raises `Refused`; nothing changes.
- reversible
- `hold` is the default and needs no input.

### `task.advance (BLOCKED / DEFERRED states)`
- **Condition:** Any step `blocked` or `deferred` at the end of the plan
- **Effect:** The run ends with that state, never `DONE`; the build gate (another group) stays closed.
- reversible
- 

### `vault.restore`
- **Condition:** Target not empty, target inside the vault, or the vault fails `verify`
- **Effect:** Raises; no copy is made.
- reversible
- 

## Dead Code

- **`cli.py `CURRENT` (module constant)`** — Defined but never read; `current_id` and `start_firefox` rebuild the same path. (risk: None if removed.)
- **`cli.drive: `if res == "DONE": return 0``** — `process_job_inner` returns only `BLOCKED`, `CONTINUE` or `CRASH`. (risk: None; DONE is detected through `task.packet` instead.)
- **`cli.drive: thread pool, `lock`, `in_flight_steps``** — Vestigial parallel design; `max_workers=1` and the loop submits only when no future is pending. (risk: Low; removing it simplifies `task.current` (its in-flight branches would also become dead).)
- **`answer.parse: inner `import re` and the `elif not current_op ...: pass` branch`** — Redundant import; the branch does nothing. (risk: None.)
- **`worker.py docstring: `fieldkit build-harness worker-profile``** — Not among the argparse choices; the command does not exist. (risk: Misleads readers; fix the docstring or add the action.)
- **`handedit.record_from_commit`** — No caller in `fieldkit/`; manual recovery helper only. (risk: Removing it loses the documented recovery path.)
- **`firefox.auto_port docstring`** — Lists an "answer-key fallback" tier that the code comment says was removed. (risk: Misleading documentation only.)

## Performance

- **CPU:** Not measured for this group. Per job the cost is dominated by the local model; the source cites prompt reading on a CPU as the reason for 20-minute first-byte and 10-minute stall limits.
- **MEMORY:** Not measured. Files are read whole into lists; `snapshot.prove` hashes only touched files and treats untouched files as identical by construction.
- **IO:** Not measured. Each checkpoint is a `git add -A` plus commit across the Firefox tree; each failure is `git reset --hard` plus `git clean -fd`. `relocate.moved_file` uses one `git grep` for up to 40 lines, scoped to the file's top directory first.
- **NOTES:** Strictly sequential by design. No timing figures for a full port are in MEASUREMENTS.md: not measured.

## Security

- **Remote execution:** No inbound network service. Outbound: product-details JSON, `git ls-remote`/`git clone` of the release tag, archive HEAD, kernel.org. Model output is never executed; it is parsed into line edits or REMOVE/KEEP verdicts against one allowed file. In `--tools` mode (opt-in) the model can write anywhere the user account can.
- **Data handling:** Firefox source and patch files only. Job text goes to the endpoints in the user's Gorilla OpenCode config (`localEndpoints`, `agents` copied into the worker profile). `export.public_note` strips dates, build narration and maintainer wording before writing public patch headers. Checkpoints use a fixed identity (`build-harness` / `build-harness@localhost`) through environment variables. The patch-set group of a hand patch (`export.kind_of`) comes from the kind the maintainer recorded, not from free-text keywords, so a repair can no longer be published silently as a privacy cut or the reverse; unrecorded kinds are printed as guesses for review.
- **Attack surface:** A malicious or compromised patch set, `patch_policy.json` or product-details response would be applied as trusted input. Kernel tarballs are extracted with `filter="data"`. `snapshot` runs `cmd /c mklink /J`. `__init__.py` strips inherited `GIT_*` variables so a git hook cannot redirect `git -C` calls.
- **Notes:** The `owner_terminal()` TTY test distinguishes a person from an agent's shell only as long as agents run without a TTY; it is not an authentication mechanism.

## Error Conditions

| Error | Cause | Remedy |
|-------|-------|--------|
| `Refused: no build job has been started` | No `CURRENT` file and no task id given. | `fieldkit build-harness start firefox`. |
| `Refused: the plan is not approved` | `approved` is false. | `fieldkit build-harness approve <task>`. |
| `Refused: task ... already exists` | `task.start` with an existing id. | Continue with `next`/`drive`, or pass another `--task`. |
| `ValueError: ... is not a stable Firefox version` | `--pin` or product-details gave a/b/esr. | Pin a stable version. |
| `ValueError: Mozilla lists ... but ... has no tag ... yet` | Tag not yet on the repository. | Retry later or pass `--source`. |
| `RuntimeError: fetched ..., but the tag ... should be ...` | Cloned HEAD differs from `ls-remote` commit. | Investigate the source; the partial clone is deleted. |
| `RuntimeError: the vault itself is damaged` | `restore` ran `verify`, which found changes. | Re-fetch the version into a clean vault. |
| `FileExistsError: ... is not empty; restore makes a FRESH copy` | Non-empty `--workdir`. | Move or remove it. |
| `BadAnswer: line N is named by two operations` | Model answer touches a line twice. | None needed; counted as a failed attempt via `fail_attempt`. |
| `BadAnswer: line(s) not answered: [...]` | Model skipped a question. | Counted as a failed attempt. |
| `changed files outside the step: [...]` | A file not in `allowed` changed. | Automatic revert; attempt counted. |
| `Refused: a check step cannot be skipped` | `unblock ... skip` on a script or `final*` step. | Fix what the check reports, then `retry`. |
| `Refused: other files changed too, record them or revert them first` | `record` saw changes outside the named files. | Name all changed files or revert the others. |
| `Refused: wait N more seconds` | Cooling-off period not over. | Wait, then repeat the same command. |
| `Refused: the live tree is at ... but pristine ... is ...` | `snapshot` base mismatch. | Fetch the matching version with `vault fetch` and check the live tree's HEAD. |
| `regression: <step> no longer holds` | A later step undid an earlier port. | `rewind` to the step or re-port it. |
| `Refused: kind must be one of privacy, port, not '...'` | `record` or `record_from_commit` got a `kind` outside `handedit.KINDS`. | Use `kind=privacy` or `kind=port`; nothing was recorded. |
| `Refused: one kind= per record` | More than one `kind=` token in the `record` arguments. | Split mixed edits into one `record` per kind. |
| `RESTORE REFUSED: ... / NEXT: pass --install-dir <the Gorilla install folder>` | `install --restore` without `--install-dir`, and `install.find_install()` found no single Gorilla install; `install.no_target()` names none found or the list of several. | Pass `--install-dir` explicitly. Exit code 3; nothing is restored. |

## Tasks

### Run the unit tests for the port engine

Before changing any module in this group, and before trusting a port.

**Prerequisites:**
- Python with pytest
- Run from the Fieldkit folder

**Step 1:** Run the group's tests:

```
python -m pytest tests -k buildh
```
  - Expected: Pass: no failures. The measured full suite (`python -m pytest tests`) on 2026-10-02 was 584 passed, 1 skipped, 2 xfailed in 194.89 s.
**Step 2:** `python -m pytest tests/test_buildh_fluent.py tests/test_buildh_prefs.py tests/test_buildh_keyed.py tests/test_buildh_relocate.py tests/test_buildh_answer.py tests/test_buildh_task.py tests/test_buildh_vault.py`
  - Expected: Pass: the format tiers, answer parsing, task state machine and vault pass on their own.

**After this task:** The group's behaviour matches its tests on this machine.

### Port the patch set to the latest stable Firefox

When Mozilla ships a new stable release.

**Prerequisites:**
- git and GNU patch on PATH (or Git for Windows' `patch.exe`)
- `firefox.root` set in `fieldkit.local.json`
- gorilla-opencode and a local model for `drive`

**Step 1:** `fieldkit build-harness latest firefox` then `fieldkit build-harness vault measure firefox`
  - Expected: Version, tag, commit; download size with no download.
**Step 2:** `fieldkit build-harness vault fetch firefox` then `fieldkit build-harness vault verify firefox`
  - Expected: `INTACT <vault path>`; exit 0. Fail: `DAMAGED` and exit 3.
**Step 3:** `fieldkit build-harness start firefox` then `fieldkit build-harness approve firefox-157.0`
  - Expected: Plan with four initial steps; `approved firefox-157.0`.
**Step 4:** Run the jobs:

```
fieldkit build-harness drive
```
  - Expected: Exit 0 at DONE; exit 3 with a BLOCKED or DEFERRED list; exit 4 after two crashes; exit 5 on preflight failure.
**Step 5:** `fieldkit build-harness deferred firefox-157.0` and, per step, `fieldkit build-harness deferred firefox-157.0 <step>`
  - Expected: Every deferred and obsolete step listed with its reason; briefs logged as `shown`.
**Step 6:** Hand ports: `fieldkit build-harness unblock firefox-157.0 <step> hand`, edit, then `fieldkit build-harness submit --hand --note "keeps: <line>"`
  - Expected: `ok: true` with a checkpoint, or reasons and a revert.
**Step 7:** Unpatched fixes: `fieldkit build-harness record firefox-157.0 kind=port <file> [<file>...] --note "<reason>"` (or `kind=privacy` for a cut)
  - Expected: `recorded: hand-hand-<name>-<stamp>-h1, ...`, one checkpoint, and a `hand-edit` journal event carrying `kind`. Fail: `REFUSED: kind must be one of privacy, port, ...` or `REFUSED: one kind= per record`.
**Step 8:** `fieldkit build-harness export-hand`
  - Expected: One `  [port fix|privacy cut] NNN-<slug>.patch (<how>)` line per patch, where `<how>` is `recorded`, `follows an earlier privacy cut of the same file`, `recorded port, KEPT privacy: ...` or `keyword guess, nothing recorded: review`; then `21.PORT.FIXES.<major>` and `22.EGRESS.LOCKDOWN.<major>` patch counts; then `REVIEW the kind of every patch above before publishing` (with a count of keyword guesses when there are any). Register new groups in `config/patch_policy.json` and prove them with the scratch-index replay in the maintainer's `RUNBOOK.md`.

**After this task:** Every step is done, obsolete or dropped on record; `final-checks` passed; hand work exists as patches for the next port. In the measured 157 port, 20 exported patches replayed in order gave an identical git tree hash.

### Take and prove a snapshot of a live build tree

When the curated patch set and the tree that was actually built may have drifted apart.

**Prerequisites:**
- The live tree at `<firefox.root>/src` is a git tree whose HEAD is the pristine commit of the version
- That version is in the vault

**Step 1:** `fieldkit build-harness vault fetch firefox` for the matching version if it is missing (start a task with `--pin` or fetch through a pinned plan)
  - Expected: `vault list` shows the version.
**Step 2:** `fieldkit build-harness snapshot --version 155.0.1 --prove`
  - Expected: `PROOF: IDENTICAL - the snapshot reproduces the build`, exit 0. Fail: `PROOF: FAILED - do not port from this snapshot`, exit 3.

**After this task:** `MANIFEST.json`, `PROOF.json`, `CURATED-DEAD.txt` and a derived harness root exist under the output folder.

### Undo a step that passed but is wrong

A check was too weak and a bad port got a checkpoint.

**Prerequisites:**
- Stop `drive`
- Know the step id from `status` or `log`

**Step 1:** `fieldkit build-harness rewind firefox-157.0 <step>`
  - Expected: The working copy is at the commit before that step's checkpoint; the step and every later step are pending.
**Step 2:** Fix the check in code, then `fieldkit build-harness drive`
  - Expected: The step is ported again and judged by the new check.

**After this task:** Later checkpoints are dropped from the task record; the git commits still exist.

## Troubleshooting

**Symptom:** A hand edit made during a run has vanished.
**Cause:** `revert_to_checkpoint` runs `git reset --hard` and `git clean -fd` on the whole working copy after any failed attempt.
**Remedy:** Never edit the working copy while `drive` runs; use `record` immediately after a manual edit.
**Verify:** `git -C <workdir> log --oneline -3` shows a `checkpoint: hand edit` commit.

**Symptom:** Steps marked done reopen at the start of `drive`.
**Cause:** `verify.reopen` found the tree does not back the record.
**Remedy:** Expected behaviour; let the jobs run again.
**Verify:** `drive.log` lists `SYNC:` lines naming the reopened steps.

**Symptom:** `final-checks` fails with `node is not installed: changed .js/.mjs files were NOT syntax-checked`.
**Cause:** No `node` on PATH.
**Remedy:** Install Node.js, then `fieldkit build-harness unblock <task> final-checks retry`.
**Verify:** `final-checks` summary reads `clean`.

**Symptom:** `final-checks` reports `N file(s) of the pristine source are gone and no patch deletes them`.
**Cause:** A step deleted tracked upstream files (the source cites an early draft deleting tracked `.orig` files).
**Remedy:** Call `firefox.restore_deleted(task_id)`, which restores them from the root commit as one checkpoint.
**Verify:** `unexplained_deletions` returns an empty list.

**Symptom:** Hand steps written by `record` disappeared from `task.json`.
**Cause:** Another process saved a stale copy (pre-merge `task.save`).
**Remedy:** `handedit.record_from_commit(task_id, commit, files, why)` from the checkpoint commit.
**Verify:** `status` counts include the hand steps; `merged_steps` notes any later merges.

**Symptom:** `journal.jsonl` audit reports `chain broken`.
**Cause:** A journal line was edited, removed or reordered.
**Remedy:** Treat the run's history as untrusted; investigate before building.
**Verify:** `task.verify_journal(task_id)` returns no problems.

**Symptom:** A Fluent step is deferred with `message '...' no longer exists (upstream may have renamed it: ...)`.
**Cause:** More than one rename candidate, or the candidate's text no longer contains the old text exactly once.
**Remedy:** Port by hand with `unblock ... hand` and `submit --hand`, or decide via `deferred`.
**Verify:** Step status `done` with `done_by: hand`.

**Symptom:** `export-hand` prints `keyword guess, nothing recorded: review` for some patches.
**Cause:** Their `hand-edit` journal events carry no `kind` (recorded before the field existed, or without `kind=`), so `kind_of` fell back to `export.PRIVACY`.
**Remedy:** Review each flagged patch's group by hand before publishing; record future edits with `kind=`. The source offers no command to amend `kind` on an existing event, and `record_from_commit` would not help: `hand_commits` takes the first matching event.
**Verify:** A re-run lists every patch as `(recorded)` or with a `privacy cut` reason you accept.

**Symptom:** A patch recorded with `kind=port` is exported as a privacy cut.
**Cause:** It touches a file an earlier privacy patch in the same export touched; `kind_of` keeps it in `22.EGRESS.LOCKDOWN` so it applies after the cut it was made on top of.
**Remedy:** Expected; the line reads `recorded port, KEPT privacy: an earlier privacy cut of the same file must apply first`.
**Verify:** `export(...)['kinds']` shows `('privacy', 'recorded port, KEPT privacy: ...')` for that patch.

## Technical Debt

🟡 **LOW** — `cli.py` help docstring lists a subset of actions and `unblock ... retry|skip` only; `hand` and many actions are undocumented there. → Generate the usage text from the argparse choices.
🟡 **LOW** — `firefox.py` docstring work-copy path (`../work/firefox`) differs from `cli.start_firefox` (`../Build.Work/firefox`). → Correct the docstring.
🟠 **MEDIUM** — `submit` without `step_id` acts on the first pending or failed step, not necessarily the one most recently unblocked. → Accept a step id on the CLI `submit`.
🟠 **MEDIUM** — `export.hand_commits` matches a checkpoint to the FIRST `hand-edit` journal event with an identical file set, so two hand edits of exactly the same files both take the first event's reason and `kind`; keyword fallback (`export.PRIVACY`) remains for events without `kind`. → Record the checkpoint commit id in the `hand-edit` event and match on it; add a command to set `kind` on an existing event (as a new, hash-chained journal entry).
🟠 **MEDIUM** — `cli.run` hard-codes `firefox-155.0.1` for `truthbound` and a backups folder under the user's Documents for `leakgate`. → Move both to settings.
🟡 **LOW** — Thread pool and in-flight bookkeeping retained in `drive` and `task.current` although runs are strictly sequential. → Remove the concurrency scaffolding or document why it stays.
🟠 **MEDIUM** — Kernel workflow: vault supports it, `start kernel` refuses. → Mark as not available in user-facing help until `kernel.py` supplies a plan.
🟠 **MEDIUM** — `owner_terminal()` relies on TTY detection as the human gate. → Document the assumption; consider an interactive challenge for destructive decisions.
🟡 **LOW** — Upstream product-details and kernel `.asc` signatures are not cryptographically verified in this group. → Verify the kernel signature; the Firefox tag commit is already cross-checked after clone.

## Impact If Removed

Without this group there is no way to move the Gorilla patch set to a new Firefox except by hand: no vault (no verified pristine base for builds, snapshots, `truthbound` or `leakgate` comparisons), no task state (the build gate, `verify`, `repair`, `install` and the recorder all read `state/build-harness/<task>`), no MCP build tools, and no export of hand work into the next release's patch set. The verify-and-build, install-and-proof and leakgate groups import `task`, `firefox` and `vault` directly and would fail to load.

## Claim Sources

| Claim | Basis | Evidence |
|-------|-------|----------|
| Model never chooses the source | 📄 stated in input | The model never chooses a source. |
| Patch applied with zero fuzz | 📄 stated in input | the group apply uses --fuzz=0 |
| Default answer mode gives the model no tools | 📄 stated in input | Answer mode (the default) gives the model no tools at all |
| Tool mode can write outside the working copy | 📄 stated in input | With tools it can write anywhere the user account can |
| Jobs are strictly sequential | 📄 stated in input | ONE job at a time, always |
| Journal is hash-chained | 📄 stated in input | Each carries the hash of the line before it |
| Task record is overridden by the tree | 📄 stated in input | the tree decides what is done, not the record |
| Skip refused for check steps and non-terminal callers | 📄 stated in input | a check step cannot be skipped |
| Answer-key tier removed | 📄 stated in input | copying Firefox 156 files over 157 breaks upstream changes |
| Export output reproduced the 157 tree | 📄 stated in input | identical git tree hash |
| Group size 5,594 lines at the MEASUREMENTS.md count; current size not measured | 📄 stated in input | port-engine 5,594 |
| `CURRENT` constant and DONE branch are dead | 🤖 model inference | *(none — model judgment)* |
| `submit` may target a different step than the one unblocked | 🤖 model inference | *(none — model judgment)* |
| Recorded kind takes precedence over keyword classification | 📄 stated in input | The recorded kind wins; keywords only when none was recorded. |
| TTY detection is not authentication | 🤖 model inference | *(none — model judgment)* |
| Other groups fail to load without this one | 🤖 model inference | *(none — model judgment)* |
| product-details response is not signature-checked | 🤖 model inference | *(none — model judgment)* |
| A file cut earlier keeps later patches in the privacy group | 📄 stated in input | 21.PORT.FIXES applies before 22.EGRESS.LOCKDOWN |
| Restore refuses when the install target is ambiguous | 📄 stated in input | not guessing which |
| Two hand edits of the same file set share the first journal event | 🤖 model inference | *(none — model judgment)* |


---
**How to verify this document:**
`📄 stated in input` — the model's phrasing of something your source text said.
Find the matching line in the original to verify.
`🤖 model inference` — the model's own judgment or synthesis. Treat as opinion,
not measurement. Re-run on the same input and check whether specific numbers
stay consistent between runs.

*Auto-generated DITA-structured developer documentation.*