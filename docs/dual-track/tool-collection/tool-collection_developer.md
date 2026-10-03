# Tool collection: `fieldkit.gather`, `fieldkit.harvest` and the `fieldkit.desk` registry, discovery, cards and readiness

> Generated 2026-10-02 | Source: `tool-collection`

---

## Purpose

This group answers one question for an agent: which of the collected tools may you run, and how. `gather.py` copies sources listed in `imports.yaml` into `toolbox/<name>/` with a per-file SHA-256 provenance record, detects drift, and can run each source's declared tests. `harvest.py` statically indexes every script under a root (no execution) into `harvest.json` and `HARVEST.md`, and ranks scripts for a plain-words query. `desk/registry.py` loads the curated registry `fieldkit/desk/tools.yaml` (merged with `local/tools.yaml`) and decides probe safety from the AST with a strict module-level work rule. `desk/discover.py` derives per-file facts for every toolbox script. `desk/cards.py` turns curated and gathered entries into tool cards (drafts get AST-detected write, delete and process effects) and computes a four-level trust ladder from facts. `desk/readiness.py` aggregates trust levels into a report and a generated block in `local/READINESS.md`. `desk/__init__.py` is empty. The whole group was 974 lines of Python when the measurements file was written; today's security fixes made it longer (1,291 lines in the staged copies, not measured: counted by the offline pre-check for this document). Trust level: the group itself never executes a gathered script; it executes only `git`, `gh`, and the test commands declared in `imports.yaml` and `tools.yaml`, and only on request.

## Known Alternatives Considered

The source documents one rejected approach: probing a tool by running it with `--help`. The `registry.py` docstring records that `organize.py --help` "has no argument handling and ran its whole job instead", so probe safety is decided statically. The `readiness.py` docstring rejects hand-typed counts because an earlier count mismatch "came from typing counts by hand". No other alternatives are documented: Not available in the source material.

## Architecture

- **Pattern:** Batch pipeline of pure-ish functions over the file system: manifest -> copy + provenance (`gather`) -> static index (`harvest`) -> cards (`desk.cards`) -> computed trust (`cards.trust`) -> aggregate (`desk.readiness`). Cards are memoised in `state/cards-cache.json` keyed by an mtime signature.
- **Trust boundary:** Trusted: `imports.yaml`, `local/imports.yaml`, `fieldkit/desk/tools.yaml`, `local/tools.yaml` (their `tests` and `python` entries are executed verbatim). Untrusted: every file under `toolbox/` and every gathered repository; these are read as text or parsed with `ast.parse`, never imported or run by this group. `state/test-results.json` is trusted as the record of test passes, bound to a file SHA-256.
- **Attack surface:** A hostile manifest entry: `tests` and `python` run via `subprocess.run` with the caller's rights in `gather.test_one` and `registry.check(run_tests=True)`. A hostile gathered repository: its content cannot run through this group, but it can shape a draft card (title, inputs, effects). Module-level work in the probed file itself is now rejected by `registry._work`; work placed in a sibling module that the probed file imports is not seen, because `ast.Import`/`ast.ImportFrom` are always harmless and analysis is per file. Network input to `gather`: a failed `git pull` or `gh repo clone` raises `RuntimeError` and becomes an error row, so a stale clone is no longer presented as current. Pathological source files: `ast.parse` on very large or deeply nested input (resource exhaustion; not measured); `RecursionError` and `MemoryError` are caught inside `_const_expr`'s `literal_eval` attempt.
- **Dependencies:** `ast`, `fnmatch`, `hashlib`, `json`, `py_compile`, `re`, `shutil`, `subprocess`, `collections`, `pathlib`, `fieldkit.core.settings`, `fieldkit.core.privacy`, `fieldkit.core.host.platform_ok`, `external: git`, `external: gh (GitHub CLI)`

## Flags & Configuration

| Name | Type | Default | Effect | Notes |
|------|------|---------|--------|-------|
| ``gather --only NAME...`` | `list` | `all sources` | Restricts gather, check or test to the named sources. | Unknown names are silently skipped. |
| ``gather --offline`` | `bool` | `false` | `source_root` delegates to `existing_root`: no `git pull` or `gh repo clone`, no folder created. | Fails with `no clone at ... yet (run gather, online, to fetch it)` for a source never cloned. |
| ``gather --check`` | `bool` | `false` | Calls `check_one`, which uses `existing_root` only: reports `in-sync`, `drift`, `not-gathered` or `source-unavailable`, each row tagged `mode: offline check`. No network, no write, no folder created. | Always offline: the `offline` argument of `check_one` is accepted for old callers and ignored. `--offline` alongside it is redundant. |
| ``gather --test`` | `bool` | `false` | Runs each source's `tests` commands with a 900 s timeout per command. | Executes manifest-declared commands. `test_in: source` runs them in the original folder, otherwise in `toolbox/<name>`. |
| ``harvest --root DIR`` | `string` | ``toolbox/`` | Folder to index. | Skips `__pycache__`, `.git`, `target` and `node_modules`. |
| ``harvest --out DIR`` | `string` | ``harvest/`` | Where `harvest.json` and `HARVEST.md` go. | `--find` reads the index from here, or rebuilds in memory if absent. |
| ``harvest --find WORDS...`` | `list` | `none` | Ranks scripts with `harvest.find`. | Words of two characters or fewer are dropped; exit `3` on no match. |
| ``harvest --limit N`` | `int` | `10` | Maximum number of `--find` results. |  |
| ``tools list --all`` | `bool` | `false` | Adds every `discover.discover()` entry not already registered. | Discovered rows are shown with `changes=True`. |
| ``tools check --run-tests`` | `bool` | `false` | Runs each registered tool's `test` command (300 s timeout) and records the result with `record_test`. | Skipped when `platform_ok` rejects the host. |
| ``tools check --id ID...`` | `list` | `all` | Restricts the check to these tool ids. |  |
| ``cards list --level L`` | `string` | `all` | Filters by computed level: `gathered`, `carded`, `tested`, `verified`. |  |
| ``readiness --write-docs`` | `bool` | `false` | Writes the generated block into `local/READINESS.md`. | Idempotent: replaces the block between the `READINESS:BEGIN` and `READINESS:END` markers. |

## API Surface

| Symbol | Description | Side Effects |
|--------|-------------|--------------|
| `gather.load_manifest()` | Reads and validates `imports.yaml` plus `local/imports.yaml`; merges default excludes. | None |
| `gather.select()` | Sorted relative POSIX paths matching include and not exclude; `./pat` matches top level only. | None |
| `gather.existing_root()` | The folder already on disk (`local` path or `_sources/repos/<name>`) and its commit; raises `FileNotFoundError` if absent. | Read-only `git rev-parse --short HEAD` via `_commit`; `OSError` returns `None` |
| `gather.source_root()` | Resolves a source to a folder and short commit. Local or offline: delegates to `existing_root`. Otherwise pulls or clones, then returns `existing_root(src)`. | Runs `git pull -q --ff-only` or `gh repo clone ... --depth 1`; raises `RuntimeError` on a non-zero exit of either (pull message ends `the old copy was NOT used`, stderr truncated to 300 characters) |
| `gather.gather_one()` | Copies selected files, writes `PROVENANCE.json`, compiles `.py`, privacy-scans. | Network as `source_root`; on success `shutil.rmtree(toolbox/<name>)` then recreates it. A failed pull raises before the tray is touched. |
| `gather.check_one()` | Drift report against `existing_root(src)`: added, removed, changed at source; edited in toolbox; every row carries `mode: offline check`. | None (read-only `git rev-parse` only) |
| `gather.test_one()` | Runs a source's declared test commands. | Executes arbitrary manifest commands |
| `gather.gather()` | Dispatcher; converts `FileNotFoundError`, `RuntimeError`, `ValueError` per source into `{'name', 'error'}`. | As the called function |
| `harvest.index_file()` | Static facts for one script: title, summary, functions, options, needs, touches, lines, portable, is_test, probe. | None |
| `harvest.build()` | `index_file` for every supported extension under root. | None |
| `harvest.find()` | Weighted token ranking (title 4, id 3, functions 2, summary 2, options 1, touches 1) with prefix matching; skips tests. | None |
| `harvest.to_markdown()` | One line per script, grouped by source. | None |
| `registry.load()` | Curated registry merged with `local/tools.yaml`; rejects duplicate ids. | None |
| `registry.probe_safety()` | AST rule: an exact `__main__` guard (`_is_main_guard`, either operand order; its `else` branch is checked as module-level code), `_uses_argparse` (an `argparse` import AND a `parse_args`/`parse_known_args`/`parse_intermixed_args` call), and no statement for which `_work` returns a description. Honours `from __future__ import annotations` by not evaluating annotations. | None; nothing is run |
| `registry._work()` | None when a module-level or class-body statement does no work on load, else a reason such as `assignment from a call or computed value`, `decorator that runs code`, `default value computed by a call`, `if-test that calls something`. Built on `_const_expr`, `_pure_call` (`_PURE_CALLS`, `_PURE_METHODS`), `_safe_decorator` (`_SAFE_DECORATORS`) and the exact-name `_SETUP_CALLS` with constant arguments. | None |
| `registry.check()` | Per-tool existence, probe safety, optional tests. | With `run_tests`: subprocess and write to `state/test-results.json` |
| `discover.discover()` | Per-file title, platforms, portability, probe safety for `toolbox/`. | None |
| `cards.argparse_inputs()` | Every `add_argument` call read from the AST as an input spec. | None |
| `cards.code_effects()` | AST scan of every call, with import aliases resolved by `_aliases`: `writes-files` (`_WRITE_CALLS`, `_WRITE_METHODS`, open-like calls whose mode contains `w`, `a`, `x` or `+`, or whose mode is not a string constant), `deletes-files` (`_DELETE_CALLS`, `_DELETE_METHODS`), `processes` (`_PROCESS_CALLS`, `subprocess.`, `os.exec`, `os.spawn`, `os.posix_spawn`, `multiprocessing.` prefixes). `None` when the file cannot be parsed. | None; nothing is executed |
| `cards.draft_card() / curated_card()` | Card builders. `draft_card` unions harvest touches with `code_effects(path)` for `.py` files and sets `safety` to `unknown` when the file cannot be parsed. `curated_card` fills missing inputs and effects from the code (effects from harvest touches only). | None |
| `cards.trust()` | Computes the level and the reasons it is not higher. | Reads `state/test-results.json` when `results` is None |
| `cards.record_test()` | Stores a pass or fail bound to the file SHA-256. | Writes `state/test-results.json` |
| `cards.all_cards()` | Curated plus draft cards, cached by `_signature()`. | Writes `state/cards-cache.json` |
| `readiness.report() / write_docs()` | Counts per level and source, top 10 blockers; writes the generated block. | `write_docs` writes `local/READINESS.md` |

## Kill Switches

### ``registry.check(run_tests=False)``
- **Condition:** Default
- **Effect:** No tool and no test executes.
- reversible
- Only `--run-tests` changes this.

### ``cards.trust()` draft branch`
- **Condition:** `draft` is true or `reviewed` is false
- **Effect:** Level is capped at `gathered` with the reason `card is a draft: a person has not reviewed its inputs and safety`.
- reversible
- Callers in the agent door decide what `gathered` permits; that code is outside this group.

### ``cards.trust()` SHA binding`
- **Condition:** `file_sha(path)` differs from the recorded `sha256`
- **Effect:** Level drops to `carded` with `file changed since its tests passed`.
- reversible
- Re-run the tests to restore.

### ``gather.check_one()` / `existing_root()``
- **Condition:** `--check`
- **Effect:** No subprocess other than the read-only `git rev-parse`; no write; no `CLONES.mkdir`.
- reversible
- Covered by `test_check_never_pulls_even_when_online` and `test_check_without_a_clone_creates_nothing`.

### ``gather --offline``
- **Condition:** Flag set
- **Effect:** No network access from `source_root`.
- reversible
- 

### ``load_manifest()` validation`
- **Condition:** A source has both or neither of `github`/`local`, or names repeat
- **Effect:** Raises `ValueError` before any copy.
- reversible
- 

### ``cards.draft_card()` parse failure`
- **Condition:** `code_effects(path)` returns `None` (OSError, SyntaxError, ValueError)
- **Effect:** `safety` is forced to `unknown`; the trust ladder then reports `safety class unknown`.
- reversible
- Never falls back to the `read-only` guess.

### ``registry._PURE_CALLS``
- **Condition:** Set emptied
- **Effect:** Every module-level call becomes work, so only literal constants pass.
- reversible
- Documented in the source as the way to make every module-level call unsafe.

## Dead Code

- **``cards._effects()``** — Returns `sorted(set(touches))`; a one-line wrapper with no added logic. (risk: Low; two call sites (`draft_card`, `curated_card`) to inline.)
- **``harvest.TOUCHES` entry `office``** — Harvest records it, but `cards.CHANGING` ignores it, so it never affects safety. (risk: None; informational only.)

## Performance

- **CPU:** Not measured for this group. Every `index_file` call runs regexes over the full text and `ast.parse` for Python; `curated_card` calls `index_file` again for each curated tool; `draft_card` now parses each Python draft a second time in `code_effects`, and `_const_expr` calls `ast.literal_eval` before structural checks.
- **MEMORY:** Not measured. `build` holds the whole index in memory; `read_text` loads each file whole.
- **IO:** Not measured. `gather_one` copies every selected file and hashes it; `check_one` re-hashes source and toolbox files. `_signature` stats every curated tool file and every `PROVENANCE.json` on each `all_cards` call.
- **NOTES:** The only timing available is the whole Fieldkit suite in MEASUREMENTS.md: 584 passed, 1 skipped, 2 xfailed in 194.89 s on an Intel i7-1255U; whether that run includes today's security tests is not stated. This group's seven test files (89 tests, not measured: a run made while writing this document) passed in 7.34 s (not measured, same run).

## Security

- **Remote execution:** No network listener. Outbound only: `git pull` and `gh repo clone` to GitHub in `source_root`, never on `--check` or `--offline`. Local execution of manifest-declared commands in `test_one` and `registry.check(run_tests=True)`.
- **Data handling:** `gather_one` runs `privacy.scan_path` on each tray and reports counts per file; it removes the `PROVENANCE.json` finding by design because that file records the local source path. All outputs (`toolbox/`, `_sources/`, `harvest/`, `state/`, `local/`) are in `.gitignore`.
- **Attack surface:** Manifests (command execution). Gathered files (parsed only). `state/test-results.json` (a forged pass with a matching SHA would raise trust; the file is local and git-ignored).
- **Notes:** Closed today, with tests: (1) `_harmless` no longer accepts every `Assign`/`AnnAssign`; values must be constant expressions or pure calls, targets must be names or subscripts of names, and decorators, defaults, annotations, class bases, class bodies, `if` tests and `except` types are checked (`tests/test_probe_security.py`). (2) `_SETUP_CALLS` is an exact-name set with constant arguments; the old suffix match let `atexit.register(job)` and `evil.reconfigure()` pass. (3) argparse is detected from the AST, not by substring. (4) The main guard must be exactly `__name__ == "__main__"`. (5) Python drafts get AST-detected `writes-files`, `deletes-files` and `processes` effects, `writes-files` joined `CHANGING`, and an unparsable file is `unknown` (`tests/test_cards_security.py`). (6) `gather --check` is offline and write-free; a failed pull or clone is an error (`tests/test_gather_security.py`). Remaining: imports are always harmless, so a sibling module's import-time work is invisible; harvest has no `writes-files` pattern, so non-Python drafts can still be guessed `read-only`; curated cards' effects come from harvest touches, not `code_effects`; `code_effects` cannot see dynamic dispatch (`getattr`, `eval`, `exec`, `__import__`). Drafts stay at `gathered`, which limits the impact.

## Error Conditions

| Error | Cause | Remedy |
|-------|-------|--------|
| ``source <name>: needs exactly one of github/local`` | Manifest entry with both or neither key. | Fix the entry in `imports.yaml` or `local/imports.yaml`. |
| ``duplicate source names in imports.yaml / local/imports.yaml`` | Repeated `name`. | Rename one source. |
| ``duplicate tool ids in tools.yaml / local/tools.yaml: [...]`` | Repeated `id` across the two registries. | Rename one entry. |
| ``<name>: git pull failed (exit N): <stderr>; the old copy was NOT used`` | `git pull -q --ff-only` returned non-zero (offline, diverged history, auth). | Fix connectivity or the clone, or use `--offline` deliberately; the tray is unchanged. |
| ``<name>: clone failed: <stderr>`` | `gh repo clone` returned non-zero. | Check `gh` is installed and can reach the repository, or use `--offline`. |
| ``<name>: no clone at <path> yet (run gather, online, to fetch it)`` | Offline gather or `--check` for a never-cloned GitHub source (`--check` reports it as `source-unavailable`). | Run once online. |
| ``<name>: <path> not found`` | A `local` source folder is absent. | Correct or remove the source. |
| ``does not parse: SyntaxError` (probe reason)` | File fails decoding or `ast.parse` (`OSError`, `SyntaxError`, `UnicodeDecodeError`, `ValueError`). | Fix the file; it is reported as not safe. |
| ``RUNS ON LOAD - line N: ...`` | A module-level statement (or the main guard's `else`) for which `_work` returns a reason; up to three are listed. | Move the work under `if __name__ == "__main__":`. |
| ``no card '<id>'; see: fieldkit cards list`` | `cards show` with an unknown id. | Use an id from `fieldkit cards list`. |
| `Exit code `3` from `gather`, `tools check`, `harvest --find`` | `gather`: an error row (including a failed pull or clone), drift, compile errors or failed tests; `tools check`: missing tool or failed test; `harvest --find`: no match. | Read the per-row output. |
| ``no argparse: --help is not understood`` | No `argparse` import, or no `parse_args`-style call, in the syntax tree (text mentions no longer count). | Add real argument parsing, or do not probe the file. |

## Tasks

### Run this group's unit tests

After any change to `gather.py`, `harvest.py` or `desk/`.

**Prerequisites:**
- Fieldkit installed with `python -m pip install -e ".[test]"`
- A shell in the Fieldkit folder

**Step 1:** Run:

```bash
python -m pytest tests/test_gather.py tests/test_gather_security.py tests/test_harvest.py tests/test_cards.py tests/test_cards_security.py tests/test_probe_security.py tests/test_registry_cli.py -q
```
  - Expected: Pass: all tests pass. Fail: pytest names the failing assertion.


**After this task:** Selection, provenance, drift, offline check, failed-pull errors, static indexing, `find` ranking, argparse reading, the strict probe-safety rule, AST effect detection, trust ladder, SHA binding, cache invalidation and readiness counts are confirmed on your machine.

### Audit sources without changing anything

Before a gather, or to see if the toolbox is stale.

**Prerequisites:**
- Sources gathered at least once

**Step 1:** Run:

```bash
fieldkit gather --check
```
  - Expected: Pass: one row per source, `in-sync` or `drift` with counts such as `changed_at_source=1`. Exit `3` if any drift.

**Step 2:** Run:

```bash
fieldkit gather --check --json
```
  - Expected: Pass: full lists for `added_at_source`, `removed_at_source`, `changed_at_source`, `edited_in_toolbox`.


**After this task:** No file under `toolbox/` or `_sources/` changes and no network request is made.

### Promote a gathered script to a verified tool

When an agent needs to run a gathered script.

**Prerequisites:**
- The script has a test
- You reviewed its inputs and effects

**Step 1:** Run:

```bash
fieldkit cards show <id>
```
  - Expected: Pass: `trust` is `gathered` with the draft reason; note `inputs`, `effects`, `probe`.

**Step 2:** Add an entry to `fieldkit/desk/tools.yaml` (or `local/tools.yaml` for private tools) with `id`, `path`, `entry`, `safety`, `test`, and `modes` (`preview`, `undo`, `verify`) if it changes anything.
  - Expected: Pass: `fieldkit tools check --id <id>` shows `present`.

**Step 3:** Run:

```bash
fieldkit tools check --run-tests --id <id>
```
  - Expected: Pass: `test PASS`; a row appears in `state/test-results.json` with the file SHA-256.

**Step 4:** Run:

```bash
fieldkit cards show <id>
```
  - Expected: Pass: `trust` is `verified`, or `tested` with explicit `trust_blockers`.


**After this task:** The trust level holds until the file changes.

### Refresh the harvest index and readiness block

After a gather.

**Prerequisites:**
- `toolbox/` populated

**Step 1:** Run:

```bash
fieldkit harvest
```
  - Expected: Pass: JSON summary with `scripts`, `tests`, `undocumented`, `not_portable`, `out`.

**Step 2:** Run:

```bash
fieldkit readiness --write-docs
```
  - Expected: Pass: counts per level printed; `local/READINESS.md` contains exactly one generated block.


**After this task:** `harvest/HARVEST.md` and `local/READINESS.md` match the current files.

## Troubleshooting

**Symptom:** A tool you tested shows `carded` with `file changed since its tests passed`
**Cause:** The file SHA-256 differs from the recorded pass.
**Remedy:** `fieldkit tools check --run-tests --id <id>`.
**Verify:** `fieldkit cards show <id>` shows `tested` or `verified`.

**Symptom:** `cards list` does not reflect a hand edit to a script inside `toolbox/`
**Cause:** `_signature` covers `tools.yaml`, `local/tools.yaml`, curated tool files, `PROVENANCE.json` mtimes and the card-building modules, not individual gathered scripts; only a regather updates `PROVENANCE.json`.
**Remedy:** Regather the source, or delete `state/cards-cache.json`.
**Verify:** The cache file is rewritten on the next `fieldkit cards list`.

**Symptom:** `gather` returns exit `3` with `git pull failed ... the old copy was NOT used`
**Cause:** `git pull --ff-only` failed: no network, a diverged clone, or lost access.
**Remedy:** Run `git -C _sources/repos/<name> pull --ff-only` by hand to see the full error; re-clone if the history diverged, or use `fieldkit gather --offline` deliberately.
**Verify:** `fieldkit gather --only <name>` shows the file count and the new short commit.

**Symptom:** A previously `probe-safe` tool is now `DO NOT PROBE` with `RUNS ON LOAD`
**Cause:** The stricter `_work` rule found a module-level call outside `_PURE_CALLS`/`_SETUP_CALLS`, a non-allow-listed decorator, or a computed default or class attribute.
**Remedy:** Move the work under the `__main__` guard, or, if the call is genuinely pure, add its exact dotted name to `_PURE_CALLS` with a test in `tests/test_probe_security.py`.
**Verify:** `fieldkit tools check --id <id>` shows `probe-safe`.

**Symptom:** A non-Python draft card shows `safety: read-only` for a script that writes files
**Cause:** `code_effects` covers `.py` only and `harvest.TOUCHES` has no `writes-files` pattern.
**Remedy:** Review the script and curate it in `tools.yaml` with the correct `safety`.
**Verify:** `fieldkit cards show <id>` shows `draft: false` and the reviewed safety.

## Technical Debt

🟠 **MEDIUM** — Probe safety is per file: `import` statements are always harmless, so import-time work in a sibling module of the probed script is not seen. → Resolve relative and same-folder imports and apply `_work` to those files too; add a test with a sibling module that calls `shutil.rmtree` at load.
🟠 **MEDIUM** — No `writes-files` pattern in `harvest.TOUCHES`; non-Python drafts can still be guessed `read-only`. → Add patterns for `Set-Content`, `Out-File`, `Add-Content`, `Copy-Item`, `Move-Item`, `>` redirection and `New-Item`; or default non-Python drafts to `unknown`.
🟡 **LOW** — `curated_card` fills missing effects from harvest touches only, not from `code_effects`. → Union `code_effects(p)` into curated effects when `effects` is unset, as `draft_card` does.
🟡 **LOW** — `_PURE_CALLS` and `_PURE_METHODS` are hand-maintained allow-lists; a wrong entry silently re-opens the hole. → Keep each entry covered by a parametrised case in `tests/test_probe_security.py`.
🟡 **LOW** — Keyword-based `TOUCHES` regexes match comments (for example `Administrator`). → Strip comments and docstrings before matching, or label the field as a hint in the card.
🟡 **LOW** — `record_test` read-modify-write on `state/test-results.json` without a lock. → Write to a temporary file and rename atomically.
🟡 **LOW** — `cards._effects` is a trivial wrapper. → Inline it at its two call sites.

## Impact If Removed

`fieldkit gather`, `fieldkit harvest`, `fieldkit tools`, `fieldkit cards` and `fieldkit readiness` stop working. Agent-facing discovery loses its catalogue and its trust computation: no card means no computed level, so nothing distinguishes a reviewed, tested tool from an unreviewed draft. `harvest.py` imports `desk.discover.HOME_PATH` and `desk.registry.probe_safety`, and `desk.cards` imports `harvest.index_file`, so the modules cannot be removed separately.

## Claim Sources

| Claim | Basis | Evidence |
|-------|-------|----------|
| probe safety is decided statically because of a past incident | 📄 stated in input | it has no argument handling and ran its whole job instead |
| readiness counts are generated to avoid hand-typed drift | 📄 stated in input | came from typing counts by hand |
| trust is computed from facts | 📄 stated in input | Computed from facts, never declared. |
| toolbox trays are fully regenerated | 📄 stated in input | toolbox/<name> is fully generated; no hand edits live here |
| PROVENANCE finding is dropped on purpose | 📄 stated in input | it records the local source path by design |
| nothing is pushed | 📄 stated in input | nothing is ever pushed anywhere |
| registry check runs only declared tests on request | 📄 stated in input | Runs nothing except the listed tests, and only if asked. |
| the modules cannot be removed separately | 🤖 model inference | *(none — model judgment)* |
| gather --check is an offline check that writes nothing | 📄 stated in input | a check is always offline |
| a failed pull or clone is an error, not a silent fall-back | 📄 stated in input | the old clone is never passed off as current |
| argparse detection reads the syntax tree | 📄 stated in input | From the syntax tree: argparse is imported AND some parse_args-style call exists. |
| setup calls are exact names with constant arguments | 📄 stated in input | Exact dotted names, and only when every |
| unparsable drafts are unknown | 📄 stated in input | unreadable code: never guess read-only |
| imports remain a blind spot of probe safety | 🤖 model inference | *(none — model judgment)* |
| non-Python drafts can still be guessed read-only | 🤖 model inference | *(none — model judgment)* |
| curated effects do not use code_effects | 🤖 model inference | *(none — model judgment)* |
| code_effects cannot see dynamic dispatch | 🤖 model inference | *(none — model judgment)* |


---
**How to verify this document:**
`📄 stated in input` — the model's phrasing of something your source text said.
Find the matching line in the original to verify.
`🤖 model inference` — the model's own judgment or synthesis. Treat as opinion,
not measurement. Re-run on the same input and check whether specific numbers
stay consistent between runs.

*Auto-generated DITA-structured developer documentation.*