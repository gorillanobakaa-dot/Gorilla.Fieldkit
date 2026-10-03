# `fieldkit.buildh` verify-and-build: tree verification, deterministic repair, build gate, build loop and supervisor audit

> Generated 2026-10-02 | Source: `verify-and-build`

---

## Purpose

This group (15 modules under `fieldkit/buildh/`, 2,881 lines when last measured; the total after the 2026-10-02 fixes is not measured) sits between a finished port of the Gorilla Firefox patch set and an installed browser. It exists because the task journal is not evidence: the 2026-10-01 audit recorded in `verify.py` found 208 steps marked done that the tree did not back, 44 files replaced by the old tree, four unrequested edits, 124 new files never copied and 560 upstream deletions, none visible in the journal. `verify.py` reads the tree and the patch set and scores every in-scope hunk; `symbols.py` and `mozbuild_rules.py` add semantic checks that a compile does not perform; `repair.py` fixes three failure classes and proves each fix; `ownercheck.py` and `preflight.py` turn the maintainer's checks and machine readiness into rows or steps; `compile.py` gates the build and verifies its output; `buildrun.py` runs the maintainer's build stage with a classifier of known stops (`STOPS`) and fixers (`creepfix.py`, `icons.py`, `mozbuild_rules.py`). `audit.py`, `recorder.py` and the session watcher audit the agent that did the work from disk records, never from its report. `truthbound.py` and `compare.py` are on-demand comparisons against reference trees. Trust level: verify, symbols, truthbound, compare, audit, recorder and the session watcher are read-only on the source tree; repair, buildrun (via fixers), creepfix and the mozbuild fixer write to the tree and record each write as a hand step through `handedit.record`, which also checkpoints. Since 2026-10-02 every fixer that derives a target path from build-log text resolves it and refuses anything outside the working copy (`creepfix.inside_tree` for the header fix, `relative_to(workdir.resolve())` in the `jar.mn` and empty-assignment fixers); `repair.prove` treats an absent `node` as a failed proof; `buildrun.stop_guidance` gives every give-up branch a reason plus the next action; and the repair and empty-assignment hand steps carry `kind="port"` so the patch-set export does not classify them by keyword. None of these four changes has a dedicated test in `tests/` yet.

## Known Alternatives Considered

Documented in source comments: (1) `creep` is not computed inside `verify` because "two tree-wide greps over hundreds of symbols held the build gate for three hours (2026-10-02)"; it is a separate command. (2) `truthbound` is a command, not a gate row, because it "Reads 1,500+ files through `git show`". (3) `truthbound` diffs against the root commit of the 155 repo, "never its working tree", because the working tree "carries port checkpoints" and "made 173 good files look unexplained". (4) `symbols.py` is narrow on purpose (UPPER_CASE members only, changed files only) because "a check that cries wolf gets deleted". (5) `repair.renamed_member` diffs the ported file against its pristine file, not two pristine versions because the maintainer's "lines may predate even the previous release". (6) `compile.py` originally left the compile to the maintainer; `buildrun.py` records the later instruction that the harness starts, monitors and fixes the build itself. (7) `buildrun.thermal_verdict` duplicates the stage's own thermal guard "in case the stage's thread is the thing that died".

## Architecture

- **Pattern:** Rule tables and row-producing check functions (`[(name, ok, evidence)]`) over a git working copy, plus a retry loop with a first-match-wins regex classifier (`STOPS`) dispatching to fixer functions; every state change is appended to a hash-chained JSONL journal (`task.journal`).
- **Trust boundary:** Trusted: the maintainer's patch policy (`config/patch_policy.json`), the vault's pristine copies, the root commit of the working copy as pristine upstream, the maintainer's `harness/gorilla_build.py` and `working scripts/` tools (executed with `sys.executable`), and the MozillaBuild toolchain. Not trusted: the task journal's claims of completion (re-derived from the tree), the supervised model's text (re-derived from tool records), and the build stage's exit status alone (re-checked by `build-verify`).
- **Attack surface:** Local only. Inputs that drive writes: build-log text parsed by `STOPS`, `creepfix.NOT_FOUND`, `mozbuild_rules.FILE_LOG` and `JARMN_FILE`. Every path taken from log text is resolved and confined to the working copy before any write: `fix_creep_include` maps it through `creepfix.rel_to_tree` and then `creepfix.inside_tree(rel, workdir)`, which resolves symlinks and `..` against `Path(workdir).resolve()` and returns `None` on `ValueError` from `relative_to`; the caller appends "outside the working copy ... refused, nothing written" and skips the file. `fix_jar_manifest` and `fix_mozbuild_empty` refuse through `resolve().relative_to(w.resolve())`. Executed code: the maintainer's scripts and `mach` from the working copy; these run with the user's rights and are not confined. No network listener.
- **Dependencies:** `hashlib`, `json`, `re`, `subprocess`, `pathlib`, `difflib`, `shutil`, `ast`, `sqlite3`, `ctypes`, `struct`, `urllib.request`, `argparse`, `fieldkit.buildh.firefox`, `fieldkit.buildh.task`, `fieldkit.buildh.vault`, `fieldkit.buildh.fluent`, `fieldkit.buildh.handedit`, `fieldkit.buildh.relocate`, `fieldkit.buildh.proof`, `fieldkit.core.settings`, `fieldkit.thermal.sensors`, `fieldkit.thermal.governor`, `git`, `node (node --check)`, `patch`, `taskkill`, `powercfg`, `MozillaBuild bash`, `mach`

## Flags & Configuration

| Name | Type | Default | Effect | Notes |
|------|------|---------|--------|-------|
| `--reopen` | `bool` | `false` | `verify`: calls `verify.reopen`, setting each false completion to `pending` and journaling `reopened`. | Changes the task record only, never a tree file. |
| `--fix-locks` | `bool` | `false` | `preflight`: deletes `.git/*.lock` when no git process runs. | Process check is by image name (`tasklist` / `pgrep`); any git process anywhere blocks the removal. |
| `--build` | `bool` | `false` | `preflight`: raises the disk floor from `MIN_FREE_GB_JOB` (20) to `MIN_FREE_GB_BUILD` (120) and makes the fan-control row required. | The fan row is Windows-only. |
| `--model` | `bool` | `false` | `preflight`: queries `LM_STUDIO` (`http://127.0.0.1:1234/v1/models`) and requires a model id containing `gemma`. | 5 s timeout. |
| `--force` | `bool` | `false` | `build-run`: passes `--force` to the maintainer's `build` stage when every maintainer blocker is in `ownercheck.BUILD_DEPENDENT`. | Never bypasses `compile.gate`; forced blocker names go into the `build-start` journal event. |
| `--reference` | `string` | `none` | `compare`: folder holding the person-made result. | Required; the command exits with an error without it. |
| `--session` | `string` | `newest / all driver sessions since task creation` | `watch` / `report`: one Gorilla OpenCode session id. | Without it, sessions are filtered by `title like '%ONE small job%'`. |
| `--harness` | `string` | ``${LOCAL:firefox.root}`` | `build-gate`: harness root for `mozconfig_path`. | `compile.verify` ignores it and always uses the default (see technical debt). |
| `RETRIES` | `int` | `4` | `buildrun`: retries per stage; the loop runs `range(1, RETRIES + 2)`, so five attempts. | Module constant. |
| `SURFACE_CAP` | `int` | `80` | `buildrun`: processor cap (%) when only a surface sensor is proven. | Also disables `thermal_verdict` watching and the governor's dead check. |
| `HEARTBEAT_S` | `int` | `300` | `buildrun._stream`: seconds between progress lines. |  |
| `DEAD_SAMPLES / DEAD_PERF / HARD_CEILING_C` | `int/float` | `36 / 50.0 / 95.0` | `thermal_verdict`: kill on 95 C, or on one constant temperature value across the whole CSV with the last 36 samples above 50% performance. | Checked every 30 s once the stage prints its telemetry path. |
| `MIN_ARTIFACT_MB` | `int` | `50` | `compile.verify`: minimum installer and zip size. |  |
| `SHORT / SPECIFIC` | `int` | `12 / 25` | `verify.score_hunk`: minimum stripped length for an added line / a removed line to count as evidence. | Shorter lines yield `NO-SIGNAL`. |

## API Surface

| Symbol | Description | Side Effects |
|--------|-------------|--------------|
| `verify.score_hunk()` | Scores one hunk APPLIED / PARTIAL / NOT-APPLIED / TARGET-GONE / NO-SIGNAL; Fluent files are judged by message semantics. | none |
| `verify.verify()` | Whole-tree report: hunk scores per group, false completions, old-tree copies, stray edits, missing new files, unexplained deletions, syntax, symbols, Fluent duplicates. | none (runs git) |
| `verify.reopen()` | Sets false completions back to pending. | task.save, journal `reopened` |
| `verify.problems()` | Gate rows from a report. | none |
| `verify.creep()` | Excision creep: upstream references to excised symbols absent from the older pristine copy. | none; slow |
| `symbols.problems()` | UPPER_CASE members read through an import that the resolved module does not define. | none |
| `repair.run()` | Repairs empty mozbuild assignments, dangling excisions and moved/renamed members. | writes tree files; `handedit.record` (steps + checkpoint); journal `repair` |
| `mozbuild_rules.fix_empty_assignments()` | Removes empty UPPER_CASE list statements, keeping their comments. | writes the file |
| `truthbound.run()` | Changes not explained by the maintainer's 155 truth delta or by a step. | journal `truthbound` |
| `compare.compare()` | same / differs / missing per file against a reference tree. | writes `compare.diff`, `compare.json` when `out_dir` |
| `audit.run()` | Baseline comparison plus journal rules and hash-chain anchors. | runs pytest; `git ls-remote`; appends to `_private/journal-anchors.json` |
| `recorder.detect()` | Rule-based findings in 22 categories, level incident or review. | none |
| `session watcher main()` | Polls a transcript every `--every` seconds (default 30) and prints WARN/FAIL findings; `--once` returns 1 on any FAIL. | appends to `_private/` watcher log |
| `ownercheck.step_owner_preflight()` | Runs the maintainer's preflight; BLOCKER lines become maintainer steps (kind `owner`); build-dependent ones deferred. | mutates `t['steps']` statuses; runs an external script |
| `preflight.run()` | Machine and working-copy readiness rows. | deletes stale locks only with `fix_locks` |
| `compile.gate()` | Pre-build rows. | writes `build-record.json` on pass |
| `compile.verify()` | Post-build rows: tree/mozconfig unchanged, fresh and sized artifacts, version, icons, maintainer preflight. | runs `firefox.exe --version`; marks post-build steps done; writes `build-result.json` on pass |
| `buildrun.run()` | Gate, maintainer preflight, thermal proof, staged build with stop classification and fixes, build-verify. | runs the maintainer's stage; may write tree files, delete objdir objects and dist/bin paths, rename a toolchain folder, change the power scheme back, kill processes |
| `buildrun.classify()` | First `STOPS` regex that matches the stage output. | none |
| `creepfix.excise()` | Comments out the include and self-contained uses; returns leftovers it will not touch. | none (caller writes) |
| `icons.embedded()` | Byte-matches a 512-byte slice of each .ico image payload against a PE file. | none |
| `creepfix.inside_tree()` | Resolves a compiler-named path (backslashes normalised, relative paths joined to the workdir, symlinks and `..` followed) and returns it only when it lies under the resolved workdir. | none (filesystem resolve only) |
| `repair.prove()` | `node --check` as the proof of a repair: `None` only when node is installed and the file parses; an error string otherwise, including when node is missing. | runs `node --check` |
| `buildrun.stop_guidance()` | The give-up line for `_stages`: reason for the stop (no fixer, repeat, retries exhausted) plus the fixed next-action text. | none |

## Kill Switches

### ``compile.gate()``
- **Condition:** Any row fails.
- **Effect:** `build-run` returns `{"ok": False, "why": "build gate"}`; `build-record.json` is not written.
- reversible
- Auto-remediation runs first only when every failing row is a stale final check, or every failing row is a parse/member row (then `repair.run` once).

### ``buildrun.run()` thermal proof`
- **Condition:** `sensors.best(prove=True)` returns no sensor after one 60 s retry.
- **Effect:** Journals `build-refused`; no stage runs.
- reversible
- Preceded by `wait_for_idle` (up to 600 s for <= 35% CPU).

### ``buildrun._stream()` / `thermal_verdict()``
- **Condition:** Telemetry at or above 95 C, or a static sensor under load.
- **Effect:** `taskkill /PID <pid> /T /F` on the stage's process tree.
- **not reversible**
- The governor object can also kill on its own verdict, journaled as `thermal-kill`.

### ``buildrun._stages()` loop`
- **Condition:** Unknown signature, fixer is `None`, attempt > `RETRIES`, or the same signature twice in a row in one stage.
- **Effect:** Returns `ok: False` with all stops; each stop is journaled as `build-stop`.
- reversible
- Every give-up branch prints `stop_guidance(fix, attempt, repeat)`: the reason ("no known fix", "the same stop again after its fix: no progress", or "still stopping after N fix attempt(s)") followed by "; the signature and the first errors are in the journal; write the tool, add it to STOPS, run again". KeyboardInterrupt is journaled as `interrupted`.

### ``repair.dangling_excision()` / `moved_member()` / `renamed_member()``
- **Condition:** `repair.prove` returns an error (node absent, or `node --check` fails), the member check still lists the member, or more than 400 lines would be removed.
- **Effect:** Original bytes written back; item listed as refused.
- reversible
- `repair.prove` checks `shutil.which("node")` first and returns "node is not installed: the repair cannot be proven" when it is `None`; `firefox.node_check` alone returns `None` for a missing node, which the repair functions would have read as a pass. The verify syntax row reports the same gap as FAIL.

### ``buildrun.fix_creep_include()` via `creepfix.inside_tree()``
- **Condition:** The source path parsed from the compiler line resolves outside the task's working copy.
- **Effect:** That file is skipped with "outside the working copy <workdir>: refused, nothing written"; the fixer returns not-ok unless every missing header was handled.
- reversible
- Nothing is written, so there is nothing to undo.

## Dead Code

- **``verify.verify()` key `rep["creep"]``** — Always set to `[]`; creep moved to its own command and nothing reads the key from the report. (risk: None if removed, unless an external consumer reads the JSON key.)
- **`Unused imports (pyflakes): `json` in `verify.py` (also re-imported inside `_truth_root`), `truthbound.py` and `buildrun.py`; `settings` in `recorder.py`; `sys` in `preflight.py`; `BUILD_DEPENDENT` in `buildrun.py` (marked `noqa`)`** — Imported names never referenced. (risk: None; `BUILD_DEPENDENT` may be kept as a re-export for callers of `buildrun`.)
- **``repair.renamed_member()`: `text.replace(f"lazy.lazy.", "lazy.")``** — f-string without placeholders (pyflakes); behaves as a plain string. (risk: None.)

## Performance

- **CPU:** The build stage dominates; its duration is not measured here. `audit.snapshot` runs the full pytest suite (measured 194.89 s for 584 passed, 1 skipped, 2 xfailed). `sweep_objects` is described in source as about 1 s over 4,000 objects. `verify` cost is not measured.
- **MEMORY:** Not measured. `verify` caches every changed file's lines in a dict for the run; `recorder.session_events` loads all session messages and file versions into memory.
- **IO:** `verify` runs `git show` per old-tree candidate and reads every changed file; `truthbound` reads 1,500+ files through `git show` (source comment); `creep` runs two tree-wide greps (held the gate three hours when it was inside it).
- **NOTES:** The heartbeat (300 s) and thermal check (30 s) run inside the stdout read loop, so they only fire when the stage prints a line.

## Security

- **Remote execution:** None from outside the machine. The group executes local code: the maintainer's `gorilla_build.py`, `working scripts/*.py`, `state/clobber.sh` via MozillaBuild bash, `mach artifact toolchain` from the working copy, `node --check`, and the built `firefox.exe --version`. A compromised working copy or maintainer folder therefore runs with the user's rights.
- **Data handling:** `recorder.py` opens the Gorilla OpenCode SQLite database read-only (`mode=ro`) and reads full message parts and file contents; the session watcher reads coding-assistant transcripts under the user's home folder and appends reports to a `_private` log. `audit` stores hashes, not file contents, in its baseline. `buildrun._env` strips agent-environment markers before running the stage.
- **Attack surface:** Log-derived paths drive writes in `fix_creep_include`, `fix_jar_manifest` and `fix_mozbuild_empty`; all three now resolve the path and refuse it when it lies outside the working copy (`creepfix.inside_tree` for the first, `resolve().relative_to(w.resolve())` for the other two), so a crafted or mis-parsed log line cannot redirect a write elsewhere on the machine. Repairs cannot be kept unproven: `repair.prove` fails when node is absent. `sweep_excised_dist` deletes `dist/bin` paths listed in `proof.EXCISED_PACKAGED` with `shutil.rmtree(ignore_errors=True)`. Code the loop executes on purpose (the maintainer's stage, `mach`, `clobber.sh`) is not confined to the working copy.
- **Notes:** Network: `git ls-remote origin` in `audit.snapshot`; `mach artifact toolchain` downloads in `fix_toolchain`; `urllib` to `127.0.0.1:1234` in `preflight --model`.

## Error Conditions

| Error | Cause | Remedy |
|-------|-------|--------|
| `no harness_root / patch policy: nothing can be verified` | Task meta lacks `harness_root` or `config/patch_policy.json` is absent. | Start the task with `--harness` pointing at the Gorilla.firefox folder. |
| `UNDETERMINED (no older pristine copy in the vault)` | A file equals the maintainer's old tree and the vault has no older Firefox to decide whether that loses upstream changes. | Fetch the older version into the vault; the row fails until then. |
| `node is not installed: changed .js/.mjs files were NOT syntax-checked` | `node` not on PATH. | Install Node.js. |
| `no GORILLA excised marker within 80 lines above line N` | Parse error not caused by a dangling excision. | Manual fix, then `fieldkit build-harness record`. |
| `N module(s) in <dir> define MEMBER` | Zero or several candidate modules define the moved member. | Manual decision; record the edit. |
| `no audit baseline` | `_private/audit-baseline.json` missing. | `fieldkit build-harness audit baseline` while state is known-good. |
| `no CPU temperature source responds to load` | No provider in `fieldkit.thermal.sensors` rose under the proof load. | `fieldkit thermal prove`; start fan control; idle the machine. |
| `only build-dependent owner blockers remain; run again with --force` | Maintainer preflight blockers are all in `BUILD_DEPENDENT`. | `fieldkit build-harness build-run --force`. |
| `unknown (stop signature)` | No `STOPS` regex matched. | Read the journal's `build-stop` errors; add a regex and fixer to `STOPS`. |
| `<rel>: outside the working copy <workdir>: refused, nothing written` | A `NOT_FOUND` compiler line named a source file that resolves outside the task's workdir. | Inspect the log; if the file belongs to the tree, check that the task's `workdir` is the folder actually being built. |
| `node is not installed: the repair cannot be proven` | `shutil.which("node")` returned `None` inside `repair.prove`; the edit is restored and the item refused. | Install Node.js so `node` is on PATH, then rerun `fieldkit build-harness repair`. |
| `still stopping after N fix attempt(s); the signature and the first errors are in the journal; ...` | `attempt > RETRIES` with a stop that differs from the previous one. | Read the `build-stop` and `build-fix` events with `fieldkit build-harness log`; the fixers ran but the stage keeps stopping. |

## Tasks

### Verify a ported tree and reopen false completions

Run before handing out jobs and before any build. `verify` is read-only; `--reopen` edits only the task record.

```bash
fieldkit build-harness verify
fieldkit build-harness verify --reopen
```


**Prerequisites:**
- A started task with `harness_root` set.
- `git` and `node` on PATH.

**Step 1:** `fieldkit build-harness verify`
  - Expected: Pass: per-group verdict table, all rows PASS, `VERIFY: CLEAN`, exit 0. Fail: `VERIFY: N problem(s)`, exit 3.
**Step 2:** `fieldkit build-harness verify --reopen`
  - Expected: Pass: `reopened: [...]` lists step ids now pending, and the journal has a `reopened` event.

**After this task:** No tree file changed; false completions are pending.

### Repair parse and member failures

Use when verify reports a JS/mjs parse failure, an UPPER_CASE member missing from its module, or an empty mozbuild assignment.

```bash
fieldkit build-harness repair
fieldkit build-harness verify
```


**Prerequisites:**
- Working copy has no other uncommitted changes (`handedit.record` refuses otherwise).
- `node` installed; without it `repair.prove` refuses every JS/mjs repair and restores the file.

**Step 1:** `fieldkit build-harness repair`
  - Expected: Pass: `[repaired]` lines and `REPAIR OK: N repaired, 0 refused`, exit 0. Fail: `[refused]` lines with reasons, exit 3.
**Step 2:** `fieldkit build-harness verify`
  - Expected: Pass: the parse and member rows now PASS; each repair appears as a hand step judged by the verifier.

**After this task:** Each repair is a recorded hand step with a checkpoint commit; the journal has a `repair` event.

### Gate, build and verify

The full loop. Keep the terminal open; output is also written to `build-<timestamp>.log` in the task state folder.

```bash
fieldkit build-harness preflight --build
fieldkit build-harness build-gate
fieldkit build-harness build-run
fieldkit build-harness build-run --force
fieldkit build-harness build-verify
```


**Prerequisites:**
- Maintainer folder with `harness/gorilla_build.py`, `config/mozconfig.win64` naming `MOZ_OBJDIR` and `--with-branding`.
- MozillaBuild at `C:\mozilla-build`.
- A temperature sensor that passes `fieldkit thermal prove`.

**Step 1:** `fieldkit build-harness preflight --build`
  - Expected: Pass: `PREFLIGHT: READY`.
**Step 2:** `fieldkit build-harness build-gate`
  - Expected: Pass: `BUILD-GATE: PASSED` and `build-record.json` written. Fail: `BUILD-GATE: NOT PASSED - N check(s) failed`.
**Step 3:** `fieldkit build-harness build-run`
  - Expected: Pass: `build OK`, `package OK`, build-verify rows, `BUILD OK`. Fail: `BUILD NOT OK: [signatures]` with `build-stop` events in the journal.
**Step 4:** If only build-dependent maintainer blockers remain: `fieldkit build-harness build-run --force`
  - Expected: Pass: `build-start` journal event lists the forced blocker names.
**Step 5:** `fieldkit build-harness build-verify` (standalone re-check)
  - Expected: Pass: `BUILD-VERIFY: PASSED`; `build-result.json` holds SHA-256 of installer and zip.

**After this task:** Artifacts are tied to the gated tree hash and mozconfig hash; post-build maintainer steps are marked done by `build-verify`.

### Add a new known stop to the build loop

When `build-run` ends with signature `unknown` or a stop with no fixer, the source asks for a deterministic tool per stop.

**Prerequisites:**
- The `build-stop` journal event with its first error lines (`fieldkit build-harness log`).

**Step 1:** Write a regex over the stage output and insert `(regex, name, fixer)` into `buildrun.STOPS`, before any broader pattern (first match wins).
  - Expected: `buildrun.classify(lines)` returns the new name for the logged lines.
**Step 2:** Write `fix_<name>(t, root, say, lines)` returning `(ok, what)`. If it takes a file path from the log, confine it first with `creepfix.inside_tree(path, workdir)` (or `resolve().relative_to(workdir.resolve())`) and refuse on `None`. Record tree edits with `handedit.record(..., kind="port")` so the export does not fall back to keywords. Add the function to the tuple in `_stages` that receives `lines`.
  - Expected: The fixer is called with the stage output; without the tuple entry it is called with three arguments and raises `TypeError`.
**Step 3:** Add a test to `tests/test_buildh_buildrun.py` and run `python -m pytest tests/test_buildh_buildrun.py`.
  - Expected: Pass: the new test passes with the rest of the file.

**After this task:** The same stop is fixed and retried automatically on the next run.

### Audit a supervised run

Take a baseline while state is known-good; audit after the supervisor reports. Run the recorder for the model session and the session watcher in its own terminal for a coding-assistant session.

```bash
fieldkit build-harness audit baseline
fieldkit build-harness audit
fieldkit build-harness report
fieldkit build-harness watch
```


**Prerequisites:**
- `origin` remote reachable for the push check.
- Time for a full pytest run (194.89 s measured).

**Step 1:** `fieldkit build-harness audit baseline`
  - Expected: JSON with the baseline path and the pytest summary line.
**Step 2:** `fieldkit build-harness audit`
  - Expected: Pass: all PASS, recounted tally, `VERDICT: CLEAN`, exit 0. Fail: `VERDICT: NOT CLEAN`, exit 3.
**Step 3:** `fieldkit build-harness report`
  - Expected: Category table with incident/review counts; `recorder-report.json` saved; exit 3 on any incident.
**Step 4:** Run the session watcher module from `fieldkit/buildh/` with `python -m` and `--once`, as its module docstring shows.
  - Expected: A report ending in a verdict line; exit 1 on any FAIL finding, 2 when no transcript is found.

**After this task:** A sound journal's anchor is appended to `_private/journal-anchors.json`, so a later full re-chain of the journal fails the next audit.

## Troubleshooting

**Symptom:** `verify` marks a correct CSS or pref merge PARTIAL / NOT-APPLIED.
**Cause:** Removed-line detection matched copies outside the hunk frame, or the hunk was misplaced by `firefox.misplaced`.
**Remedy:** Inspect the hunk span; for a legitimate hand port, submit with `--hand` so `hand_port_holds` judges it by meaning.
**Verify:** `fieldkit build-harness verify` no longer lists the step under false completions.

**Symptom:** `old_tree_copies` row FAIL with UNDETERMINED.
**Cause:** `older_pristine` found no older Firefox in the vault (or the vault raised).
**Remedy:** `fieldkit build-harness vault fetch firefox` for the older version.
**Verify:** Row reports `none lossy; N identical because upstream did not touch them`.

**Symptom:** `build-run` repeats `missing-toolchain` without progress.
**Cause:** An older bootstrap folder exists; `mach artifact toolchain` reports done and changes nothing.
**Remedy:** `fix_toolchain` renames the folder to `<alias>.stale-<timestamp>`; if the stop persists, check the toolchain job alias in `taskcluster/kinds/toolchain/*.yml`.
**Verify:** The journal's `build-fix` says the named file is `present`.

**Symptom:** Power plan stays on the build scheme after a crash.
**Cause:** The maintainer's stage failed in its `finally` block.
**Remedy:** `restore_power_scheme` puts the scheme found before the attempt back; check the `power-scheme-restored` event.
**Verify:** `powercfg /getactivescheme` shows the earlier scheme.

**Symptom:** Session watcher reports "no session transcript found".
**Cause:** No transcript file in the projects folder it scans, or `--session` prefix does not match.
**Remedy:** Pass `--session` with a full path to the `.jsonl` transcript.
**Verify:** The report header shows event and tool-call counts.

## Technical Debt

🟠 **MEDIUM** — The 2026-10-02 protections have no dedicated tests: `creepfix.inside_tree` (absolute, relative, `..` and symlink escapes), `repair.prove` with node absent, and `buildrun.stop_guidance` for its three branches. → Add cases to `tests/test_buildh_creepfix.py`, `tests/test_buildh_repair.py` (monkeypatch `shutil.which` to return `None`) and `tests/test_buildh_buildrun.py`.
🟠 **MEDIUM** — `compile.verify` calls `mozconfig_path()` without the harness root used by `gate(harness_root=...)`. → Store the mozconfig path in `build-record.json` at gate time and read it back in `verify`.
🟠 **MEDIUM** — Hard-coded machine paths: `audit.PATCHSET` / `SRC` / `OWNER_CFG` under the home folder, `C:\mozilla-build` in `fix_clobber` and `MOZBUILD_BASH`, `truthbound` CLI hard-codes task `firefox-155.0.1`, `audit.run` default task `firefox-155.0.1`. → Move to `fieldkit.core.settings` keys and pass task ids explicitly.
🟡 **LOW** — `fix_creep_include` calls `creepfix.missing_headers(lines)` twice: once to iterate and once in the return expression to compare counts. → Compute the list once and reuse it for the loop and the `len(done) == len(...)` comparison.
🟠 **MEDIUM** — The session watcher module has no test file; every other module in the group has one under `tests/test_buildh_*.py`. → Add tests for `events()` and `detect()` with a fixture transcript.
🟡 **LOW** — Recorder and watcher detectors are regex heuristics (`CLAIM`, `GIT_VANDAL`, `ROGUE`); `ROGUE` matches any `curl ` or `Stop-Process`, and false positives and misses are not measured. → Record per-rule hit counts from real runs and review rules that never fire or always fire.
🟡 **LOW** — PRECHECK P2-001 flags a TODO marker in `recorder.py`. It is the literal `TODO` inside `_weakened()`'s regex, which counts TODO markers added to test files; it is not an unfinished-work note. → Leave the code; add a pre-check exemption or build the pattern as `"TO" + "DO"` so the rule stops matching.
🟡 **LOW** — `preflight` fan row evidence says the fan control keeps the laptop at 70 C while the build governor targets 75 C. → Align the text with the governor target or read it from one constant.
🟠 **MEDIUM** — Thermal and heartbeat checks only run when the stage emits output (inside the stdout loop). → Run the watchdog on a timer thread independent of output.
🟡 **LOW** — `fix_creep_include` and `fix_jar_manifest` call `handedit.record` without `kind`, so their hand steps are classified by keyword at export time, unlike the repair and empty-assignment steps, which carry `kind="port"`. → Pass `kind="port"` (or `privacy` where the excision is a privacy cut) explicitly in both fixers.

## Impact If Removed

Without this group, the port would go from a journal that claims completion straight to a compile. The failure modes recorded in source would return undetected until runtime: false completions (208 on 2026-10-01), JavaScript modules that build but do not parse, constants read from modules that moved them (dead address bar), empty mozbuild lists, lossy copies of the old tree, and doubled Fluent messages. Each build stop would need a person to diagnose it again, there would be no evidence that the installer belongs to the gated tree, no temperature refusal (the 2026-10-02 reset happened with a blind governor), and no independent audit of a supervising agent.

## Claim Sources

| Claim | Basis | Evidence |
|-------|-------|----------|
| Journal claims are not evidence | 📄 stated in input | None of that showed in the journal. |
| verify is read-only except for the task record | 📄 stated in input | it only changes the task record, never a file in the tree |
| creep left the gate for performance | 📄 stated in input | held the build gate for three hours (2026-10-02) |
| symbols check is narrow to avoid false alarms | 📄 stated in input | a check that cries wolf gets deleted |
| Every repair is recorded as a hand step | 📄 stated in input | Every repair is recorded as a hand step (`handedit.record`) |
| Same stop twice ends the loop | 📄 stated in input | The same stop never costs a second investigation. |
| --force passes only build-dependent blockers | 📄 stated in input | Those four are the only reasons `--force` is ever passed on |
| Build refused without a proven sensor | 📄 stated in input | no build without a CPU sensor proven to move under load |
| Group size | 📄 stated in input | verify-and-build 2,881 |
| Full test run time | 📄 stated in input | 584 passed, 1 skipped, 2 xfailed in 194.89 s |
| compile.verify may read a different mozconfig than the gate | 🤖 model inference | *(none — model judgment)* |
| Thermal watchdog depends on stage output cadence | 🤖 model inference | *(none — model judgment)* |
| PRECHECK TODO finding is a false positive | 🤖 model inference | *(none — model judgment)* |
| Log-derived write paths are confined to the working copy | 📄 stated in input | only files under the task's workdir are ever edited |
| A missing node is a failed repair proof | 📄 stated in input | A missing node is a failed proof |
| Every give-up branch prints guidance | 📄 stated in input | always followed by what to do next |
| Repairs are recorded with kind port | 📄 stated in input | kind="port" |
| The new protections have no dedicated tests | 🤖 model inference | *(none — model judgment)* |
| Creep and jar.mn fixers rely on keyword classification at export | 🤖 model inference | *(none — model judgment)* |
| Executed build code is not confined to the working copy | 🤖 model inference | *(none — model judgment)* |


---
**How to verify this document:**
`📄 stated in input` — the model's phrasing of something your source text said.
Find the matching line in the original to verify.
`🤖 model inference` — the model's own judgment or synthesis. Treat as opinion,
not measurement. Re-run on the same input and check whether specific numbers
stay consistent between runs.

*Auto-generated DITA-structured developer documentation.*