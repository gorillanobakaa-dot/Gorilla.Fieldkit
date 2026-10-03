# Fieldkit agent door: `fieldkit.agent`, `fieldkit.mcp`, `fieldkit.cli` and package metadata

> Generated 2026-10-02 | Source: `agent-door`

---

## Purpose

This group is the single entry point through which a person or a language model reaches every Fieldkit harness. `fieldkit/__init__.py` declares the package and `__version__ = "0.1.0"`. `fieldkit/cli.py` is the `fieldkit` command: an `argparse` dispatcher with top-level sub-commands `host`, `tools`, `office`, `pipeline`, `triage`, `privacy`, `gather`, `harvest`, `agent`, `release`, `mcp`, `cards`, `readiness`, `next`, `refcheck`, `snapshot`, `lifecycle`, `exam`, `docs` (registered from `fieldkit/gdocs/cli.py`), `kernel`, `thermal` and `build-harness`, lazily importing each harness and mapping results to exit codes 0 (fine), 1 (error), 2 (bad usage) and 3 (findings). `fieldkit/agent.py` is the enforcement layer for model-driven tool use: `discover`, `describe`, `run`, `undo`, driven by tool cards from `fieldkit.desk.cards`. `fieldkit/mcp.py` exposes that layer, plus pipeline `next`, `readiness` and three build-harness tools, as an MCP server over stdio (JSON-RPC 2.0, one message per line, protocol `2024-11-05`). Trust level: this is the highest-trust code in the collection, because it decides what a model may execute. Its central invariant is that a model can never supply `approve=True`; only a person typing `fieldkit agent run TOOL ... --approve` (or `fieldkit agent undo RUN_ID --approve`) can.

A security review on 2026-10-02 changed `agent.py` substantially: input values starting with `-` are refused unless the card opts in; `scope` entries may be folders and are copied whole; backup is fail-closed and hash-checked; the journal records the resolved scope, a SHA-256 state hash of each entry before and after the run, and the `changed` list; verify and undo commands inherit the run timeout; and `undo` validates the run id, the journal and every backup entry, and refuses to overwrite post-run edits without `approve`, all before it touches anything. `mcp.py` now reports `isError` for a failed verify and a failed undo, and `cli.py` passes `--approve` through to `undo`. The same day `cli.py` also gained `office create --force`, a repeatable `--term`, a `--no-backup` passed to `office deliver`, `snapshot` turning `ValueError` into a clean exit, and a `try/finally` so `Ctrl+C` during `fieldkit thermal` still restores the power-plan cap.

`MEASUREMENTS.md` records 1,258 lines for this group, measured before these fixes; the four files now count 1,448 lines (not measured: a `wc -l` taken while writing this document). Per-file line counts are not measured. The changes have not been exercised by a model over MCP in a live session; the evidence is the unit tests in `tests/test_agent.py`, `tests/test_mcp.py` and `tests/test_agent_security.py`.

## Known Alternatives Considered

Not available in the source material. The source states design rules ("Approval must come from a person, not from text a model produced."; "Every answer is short and ends with NEXT: or CHOOSE:, so a small model can follow it.") but documents no rejected alternative designs.

## Architecture

- **Pattern:** Command dispatcher (`argparse` sub-parsers with `set_defaults(fn=...)`) in front of a policy-enforcing facade (`agent.run`) over a card registry; a line-delimited JSON-RPC loop (`mcp.serve` -> `handle` -> `call_tool`) reuses the same facade.
- **Trust boundary:** Trusted: curated cards in `tools.yaml` (`reviewed: True`), their `entry`, `modes.preview`, `modes.verify`, `modes.undo` command templates, their declared `safety`, `effects` and `scope`, and any `allow_dash` marks on inputs; the local test-results file read by `cards.test_results()`; whoever types on the command line (treated as the maintainer). Not trusted: everything arriving through MCP `tools/call` arguments, which pass through `check_inputs` and always run with `approve=False`; draft cards (`trust` level `gathered`), which `run` refuses. Partly trusted: journal files under `state/agent-runs/`. `undo` no longer acts on them blindly: the file name must match `RUN_ID`, the record's `run_id` must match, every `path` must lie inside the record's `scope`, every `copy` must lie inside `BACKUPS/<run_id>` and exist with the recorded kind, and the recorded inputs pass `check_inputs` again before a card `undo` command runs. The `scope` and the post-run `after` hashes are themselves stored in the same journal, so a writer who controls both `state/agent-runs/` and `state/agent-backups/` can still choose what is restored, within a scope they also wrote. The layer does not sandbox the started process: a tool may do more than its card declares, and nothing detects that outside the scope entries.
- **Attack surface:** MCP stdio (`fieldkit mcp`): `tools/call` for `discover`, `describe`, `run`, `undo`, `next`, `readiness`, `build_harness_status`, `build_harness_next`, `build_harness_submit`. CLI arguments of every sub-command. Environment variables `FIELDKIT_MCP_TOOLS` and `FIELDKIT_RECORDER`. Writable state under `settings.ROOT / "state"` (journal files and backup folders that `undo` reads back, validates and acts on). `tools.yaml` card content, which supplies command templates. Target tools' own argument parsers, which receive model-supplied values as argv elements.
- **Dependencies:** `argparse`, `json`, `re`, `shutil`, `time`, `uuid`, `os`, `sys`, `pathlib`, `hashlib`, `fieldkit.core.settings`, `fieldkit.core.proc.Runner`, `fieldkit.core.next`, `fieldkit.core.pipeline.Pipeline`, `fieldkit.desk.cards`, `fieldkit.desk.readiness`, `fieldkit.buildh.cli`, `fieldkit.gdocs.cli (imported unconditionally in `build_parser` to register `docs`)`, `yaml (only in `cmd_kernel` with `--out`)`

## Flags & Configuration

| Name | Type | Default | Effect | Notes |
|------|------|---------|--------|-------|
| `fieldkit agent --mode` | `string` | `apply` | `preview` runs the card's preview command; `apply` backs up `scope` files and folders, runs, verifies, restores on failure. | Default is `apply`: a `reversible` tool without system effects changes files at once unless you pass `--mode preview`. |
| `fieldkit agent --approve` | `bool` | `false` | `run`: allows apply for `irreversible`/unknown-safety cards and cards whose `effects` intersect `SYSTEM_EFFECTS`. `undo`: allows overwriting scope entries whose state hash differs from the recorded post-run hash. | No MCP equivalent exists; `mcp.call_tool` hard-codes `approve=False` for `run` and passes no approve to `undo`. The argparse help text still describes only the `run` use. |
| `fieldkit agent --input` | `string (repeatable name=value)` | `none` | Builds the inputs dict; each value is parsed with `json.loads`, falling back to the raw string. | `--input n=3` gives int 3, `--input v=true` gives bool, `--input tags='["a","b"]'` gives a list. A value without `=` raises `SystemExit`. A value starting with `-` is refused by `check_inputs` unless the input has `allow_dash: true` or the value is one of its `choices`. |
| `fieldkit agent --include-drafts` | `bool` | `false` | `discover` also returns `gathered` (draft) cards. | `run` still refuses drafts. |
| `--json` | `bool` | `false` | Machine-readable output on every sub-command that takes the shared parent parser. | `mcp` does not take it (`json=False` is set as a default). |
| `card input field allow_dash` | `bool (tools.yaml)` | `false` | Lets values of that input start with `-`. | Not set on any card in `fieldkit/desk/tools.yaml` at the time of writing. `flag`-type inputs are exempt from the dash check because they carry a bool. |
| `FIELDKIT_MCP_TOOLS` | `string (comma list)` | `unset (all nine tools)` | Restricts `tools/list` and `tools/call` to the named tools. | Used to give a harness worker only what a job needs. |
| `FIELDKIT_RECORDER` | `string (path)` | ``state/recorder/mcp-<YYYYMMDD>.jsonl`` | Redirects the MCP flight recorder. | The test suite sets it so tests never write into real evidence. |
| `agent.run / agent.undo timeout` | `int` | `600` | Seconds before a started process is killed with its child processes and exit 124: the tool (run or preview), every verify `command`, and the card `undo` command. | Not exposed on the CLI or over MCP. |

## API Surface

| Symbol | Description | Side Effects |
|--------|-------------|--------------|
| `agent.discover()` | Keyword score (title 3, id 2, inputs 1, plus trust order) over non-retired cards; words of three or more characters. | Reads cards and test results. |
| `agent.describe()` | The card plus `trust` and `trust_blockers`. | None. |
| `agent.check_inputs()` | Rejects unknown, missing required, wrongly typed (`int`, `float`, `flag`, `list`, `str`), out-of-`choices` and leading-`-` inputs (unless `allow_dash` or a listed choice). | None; raises `Refused`. |
| `agent.build_argv()` | Entry, then positionals in declared order, then flags; `python`/`python3` become `sys.executable`; flag values are stringified element by element. | None. |
| `agent.verify()` | Runs `command`, `output_contains`, `output_lacks`, `files_exist` checks; unknown kinds fail. `run` passes its own `timeout`. | Starts verify processes. |
| `agent._state_hash()` | `file:<sha256>`, `dir:<sha256>` over sorted relative names and file hashes (links recorded by target, not followed), `link:<target>`, `other`, or `None` if absent. | Reads every file under a folder. |
| `agent._backup()` | Copies each scope entry to `BACKUPS/<run_id>/<i>/<name>` (`copy2` or `copytree(symlinks=True)`) and checks the copy's hash; entries carry `path`, `kind`, `copy`, `existed`, `before`. | Writes the backup folder; removes it and raises `Refused` on any failure. |
| `agent._check_restore()` | Validates a journal's backup list against its scope and the run's backup folder; reports up to five problems. | None; raises `Refused`. |
| `agent.run()` | The enforced flow: trust gate, input check, preview or approval gate, scope record, backup, run, verify, post-run hashes and `changed` list, restore, journal. | Starts processes; writes `state/agent-runs/<id>.json`, `state/agent-backups/<id>/`, `state/agent/logs/`, `state/agent/started-pids.json`; may restore or delete files and folders. |
| `agent.undo()` | Validates, then restores backups and/or runs the card's `modes.undo`; marks the journal `undone`. Returns `ok: False` when the undo command fails. | Overwrites or deletes entries in the validated backup list; runs the undo command. |
| `agent.Refused` | A refusal whose message ends with `NEXT:` or `CHOOSE:`. | None. |
| `mcp.handle()` | `initialize`, `tools/list`, `tools/call`, `ping`; notifications get no reply; others get error -32601. | `tools/call` appends to the recorder. |
| `mcp.call_tool()` | Dispatches the nine tools; `Refused` and `SystemExit` become `REFUSED: ...` text with `is_error` false; `run` is an error when `ok` is false or `verified is False`; `undo` is an error when its result has `ok: False`. | As the dispatched tool. |
| `mcp.serve()` | Line loop; invalid JSON gets error -32700. | Sets module global `RECORDER`; writes the recorder file. |
| `mcp.record()` | One JSON line per call; argument values cut at 2,000 characters, answer at 4,000; never raises. | Appends to the recorder file; swallows `OSError`. |
| `cli.main()` | Reconfigures stdout/stderr to UTF-8, parses, dispatches; `SettingsError`, `FileNotFoundError`, `ValueError`, `RuntimeError` become exit 1. | As the sub-command. |
| `cli.build_parser()` | Defines every sub-command and flag; registers `docs` via `gdocs_cli.register(sub, common)`. | Imports `fieldkit.gdocs.cli`. |

## Kill Switches

### ``mcp.call_tool`, branches `run` and `undo``
- **Condition:** Every MCP `run` or `undo` call.
- **Effect:** `run` passes `approve=False`; `undo` is called without `approve`, so post-run edits stay refused. No tool input schema has an `approve` property (asserted by `test_run_has_no_approve_argument_and_refusals_are_answers` and `test_mcp_offers_no_approval_anywhere`).
- **not reversible**
- Code-level invariant; changing it removes the main protection against model-initiated irreversible changes.

### ``agent.run`, approval gate`
- **Condition:** `safety != "reversible"` or `effects` intersect `SYSTEM_EFFECTS` (`registry`, `services`, `admin`, `power`, `hardware`), and `approve` is false.
- **Effect:** Raises `Refused` telling the caller to run `mode=preview` and show the maintainer.
- reversible
- Applies to `irreversible` and `unknown`/`None` safety alike.

### ``agent.run`, trust gate`
- **Condition:** Card trust level below `carded`.
- **Effect:** Raises `Refused` with the first trust blocker.
- reversible
- Drafts are refused even with `--approve`.

### ``agent.check_inputs`, dash gate`
- **Condition:** A non-`flag` value (or any list element) starts with `-`, the input lacks `allow_dash`, and the value is not one of its `choices`.
- **Effect:** Raises `Refused` before any process starts.
- reversible
- Also applied to the recorded inputs before a card `undo` command runs.

### ``agent._backup`, fail-closed backup`
- **Condition:** A scope entry is neither file nor folder (`_state_hash` returns `other`), is a drive root, contains or lies inside `BACKUPS`/`RUNS`, raises `OSError`, or its copy hashes differently from the original.
- **Effect:** Removes `BACKUPS/<run_id>` and raises `Refused`; the tool never runs.
- reversible
- No journal is written for a refused apply.

### ``agent.run`, verify failure`
- **Condition:** Tool exit non-zero or a verify check fails.
- **Effect:** `_restore` copies files back, rebuilds folders (copy to a sibling `.fieldkit-restore-<6 hex>`, remove, rename) and removes entries that did not exist before.
- reversible
- Covers only the entries named by `scope` inputs.

### ``agent.undo`, pre-flight checks`
- **Condition:** Run id fails `RUN_ID.fullmatch`; journal path outside `RUNS`; journal unreadable JSON or naming another run; already `undone`; `_check_restore` fails; any entry's current hash differs from `after` (or `after` missing) without `approve`; no backup and no card `undo`.
- **Effect:** Raises `Refused`; nothing is restored and no command runs.
- reversible
- All checks complete before the first write.

### ``mcp.offered``
- **Condition:** `FIELDKIT_MCP_TOOLS` set.
- **Effect:** Hides and refuses every tool not in the list.
- reversible
- Unknown names answer `no tool ... on this server` with `isError: true`.

## Dead Code

- **``agent.undo`, local `ok``** — Initialised to `True` and never reassigned; the failing branch returns a literal `"ok": False`, so the variable is a constant. (risk: None; it can be replaced by the literal.)
- **``agent.ORDER` use in `discover` score`** — Not dead, but `ORDER[level]` adds 1 to 3 points, which can rank a trusted weak match above a draft strong match by design. (risk: N/A - kept for completeness; not removable code.)

## Performance

- **CPU:** Not measured.
- **MEMORY:** Not measured. Output is held in memory in full, then cut to 4,000 characters for the journal.
- **IO:** Not measured. Each apply copies every `scope` file with `shutil.copy2` and every scope folder with `shutil.copytree`, then reads each entry in full to hash it: once before, once for the copy, once after the run, and once more at undo. Each run writes one journal file and one log per started process.
- **NOTES:** `discover` calls `cards.all_cards()`, which caches in `state/cards-cache.json`. The full test suite (all groups) ran 584 passed, 1 skipped, 2 xfailed in 194.89 s, measured before the 2026-10-02 fixes; no per-group timing is measured.

## Security

- **Remote execution:** No network listener. MCP runs over stdio of a process the client starts. Commands come only from card templates; inputs fill argv slots and run without a shell (`subprocess.Popen` with a list), so shell metacharacters are not interpreted. Argument injection through a leading `-` is refused in `check_inputs` unless the card opts in with `allow_dash`. Values starting with `/`, which some Windows programs parse as switches, are not refused.
- **Data handling:** Journals store all inputs, the resolved scope paths, per-entry state hashes, the `changed` list and the last 4,000 characters of output; the recorder stores arguments (values cut at 2,000 characters) and answers (4,000). Backups are full copies of `scope` files and folders. None of these files is rotated or deleted by this group. A secret passed as an input lands in plain text in `state/`.
- **Attack surface:** `undo` is reachable over MCP with any run id. It now refuses ids that do not match `RUN_ID`, journals that do not name their own run, restore targets outside the recorded `scope`, backup copies outside `BACKUPS/<run_id>`, and entries whose current state differs from the post-run hash, all before any write. What remains: `scope` and `after` live in the same journal as the entries they validate, so an actor with write access to both `state/agent-runs/` and `state/agent-backups/` can still steer a restore within a scope it wrote; and a model can undo a run the maintainer started, provided the files are unchanged since.
- **Notes:** Reversible tools without system effects apply over MCP with no human step; protection there is backup plus verify plus restore. Folder scope entries are now copied whole and restored by copy-then-swap; a drive root, a folder holding `BACKUPS` or `RUNS`, or a folder inside `BACKUPS` is refused. Between `_remove(p)` and `stage.rename(p)` in a folder restore the target briefly does not exist. The journal no longer promises a before/after diff; it records hashes and the list of changed entries.

## Error Conditions

| Error | Cause | Remedy |
|-------|-------|--------|
| `REFUSED: no tool '<id>'. NEXT: call discover with what you want to do.` | Unknown tool id. | Use an id from `fieldkit agent discover` or `fieldkit cards list`. |
| `'<id>' is not ready for an agent (gathered)` | Draft or unreviewed card, unknown inputs, unknown safety or no entry. | Review the card in `tools.yaml`. |
| `unknown input(s) [...]` | Input name not on the card. | Use only the listed names. |
| `missing required input(s) [...]` | Required input absent or empty. | Supply it. |
| `input '<n>' must be <type>` | Type conversion failed, or a `flag` input was not a bool. | Correct the value. |
| `input '<n>' starts with '-', so the tool could read it as an option` | Leading `-` on an input without `allow_dash`. | Prefix a path with `./` (or `.\` on Windows), or mark the input `allow_dash` after review. |
| `changes things and its card has no preview` | `mode=preview` on a card without `modes.preview`. | Apply with the maintainer's approval, or add a preview to the card. |
| `it needs approve=true, which only the owner may give` | Irreversible, unknown safety or system effects without approval. | Preview, then the maintainer runs the CLI with `--approve`. |
| `scope entry <p> is neither a plain file nor a folder / scope folder <p> is a drive root or holds fieldkit's own backups / the backup of <p> does not match the original / could not back up <p>` | `_backup` could not make a verified copy. | Narrow the scope, stop concurrent writers, fix permissions. Nothing ran. |
| `'<x>' is not a run id (they look like 20261002-142530-a1b2c3)` | Run id fails `RUN_ID.fullmatch`. | Use the id from the run's answer. |
| `run <id>'s journal is not valid JSON / names a different run` | Corrupt or substituted journal. | Investigate `state/agent-runs/`; restore by hand. |
| `run <id>'s journal has no usable scope record` | Journal written before the fix, or edited. | Restore by hand from the backup folder. |
| `run <id>'s backup record fails its checks (...)` | A `path` outside the recorded scope, a `copy` outside `BACKUPS/<run_id>` or missing, or a malformed entry. | Treat the journal as tampered; restore by hand. |
| `<n> file(s) changed after run <id>: ... Undoing would overwrite those later edits` | Current state hash differs from the recorded `after` (including a deleted file recreated). | The maintainer decides; `fieldkit agent undo RUN_ID --approve` forces it. |
| `run <id> was already undone at <time>` | Second undo of one run. | None needed. |
| `made no backup (or was already put back) and its card has no undo` | No `scope` entries, or a failed run already restored, and no `modes.undo`. | Restore manually. |
| `FAILED VERIFY` | Read-only tool exited 0 but a verify check failed. | Read the verify lines. |
| `JSON-RPC -32700 parse error` | A non-JSON line on MCP stdin. | Send one JSON object per line. |
| `JSON-RPC -32601 method not found` | Unsupported method with an id. | Use `initialize`, `tools/list`, `tools/call` or `ping`. |
| `[fieldkit] timed out after 600 s; it and its child processes were stopped (exit 124)` | Tool, verify command or undo command exceeded the timeout. | Investigate the tool; the timeout is not configurable from the CLI. |

## Tasks

### Run the unit tests for this group

Before changing `agent.py`, `mcp.py` or the agent parts of `cli.py`, and after any change to `tools.yaml` cards used by the tests.

**Prerequisites:**
- Python with `pytest` installed.
- Working directory is the Fieldkit folder.

**Step 1:** `python -m pytest -q tests/test_agent.py tests/test_mcp.py tests/test_agent_security.py`
  - Expected: Pass: all tests pass. They cover draft refusal, input checks, argv order, approval, missing preview, restore on failed verify, removal of files created by a failed run, the real `office-scrub` preview/apply/verify/undo cycle, discover ordering, system effects, card undo commands, the stdio handshake, no `approve` on any MCP tool, recorder output, and from `test_agent_security.py`: malformed run ids, recorded scope, backup copies outside the run's folder, paths outside scope, journals without scope or naming another run, folder backup and restore, fail-closed backup, the backups folder refused as scope, leading-dash inputs, verify and undo timeouts, MCP `isError` on a failed read-only verify, and refusal to overwrite later edits or a recreated file without approval. Fail: any `FAILED` line.
**Step 2:** `python -m pytest -q tests/test_registry_cli.py`
  - Expected: Pass: the CLI registry tests pass. Fail: any `FAILED` line.

**After this task:** The enforcement invariants hold on this checkout. Tests isolate `RUNS` and `BACKUPS` to a temporary folder and set `FIELDKIT_RECORDER`, so no real evidence is touched.

### Drive a reversible tool through the full cycle from the CLI

To confirm preview, apply, verify and undo on a real card.

**Prerequisites:**
- A copy of a `.docx` file, here `report.docx`, in the working directory.

**Step 1:** `fieldkit agent run office-scrub --input file=report.docx --mode preview`
  - Expected: Pass: status `PREVIEW (nothing changed)`, exit 0, file unchanged.
**Step 2:** `fieldkit agent run office-scrub --input file=report.docx`
  - Expected: Pass: `DONE and verified`, a `changed: <absolute path>` line, a `NEXT: ... undo run_id=<id>` line, exit 0. Fail: `FAILED - files restored from backup`, exit 3.
**Step 3:** `fieldkit agent undo <id>` with the id from step 2
  - Expected: Pass: `UNDONE: 1 file(s) put back` and `NEXT: tell the owner it is undone.` A second call is refused with `already undone` and exits 3. If `report.docx` was edited between steps 2 and 3, the call is refused with `file(s) changed after run` and exits 3; `fieldkit agent undo <id> --approve` then forces it.
**Step 4:** `fieldkit agent run office-scrub --input file=-x.docx --mode preview`
  - Expected: Pass: `REFUSED: input 'file' starts with '-', so the tool could read it as an option.` on stderr, exit 3, no process started.

**After this task:** `state/agent-runs/<id>.json` carries `scope`, per-entry `before`/`after` hashes, `changed` and an `undone` timestamp; the file matches its pre-run state.

### Smoke-test the MCP server by hand

After changing `mcp.py` or the `TOOLS` list.

**Prerequisites:**
- Working directory is the Fieldkit folder.
- Set `FIELDKIT_RECORDER` to a scratch path so the real recorder is not written.

**Step 1:** Pipe an `initialize` and a `tools/list` request, one per line, into the server (Git Bash, from the Fieldkit folder):

```bash
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' | FIELDKIT_RECORDER=./mcp-smoke.jsonl python -m fieldkit mcp
```

  - Expected: Pass: two reply lines; the first has `serverInfo.name` `fieldkit` and version `0.1.0`; the second lists nine tools, none with an `approve` property.
**Step 2:** Send an `undo` call with a malformed run id:

```bash
printf '%s\n' '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"undo","arguments":{"run_id":"../x"}}}' | FIELDKIT_RECORDER=./mcp-smoke.jsonl python -m fieldkit mcp
```

  - Expected: Pass: text starts `REFUSED:` and says it is not a run id; `isError` is false; nothing under `state/` changes.

**After this task:** One recorder line per `tools/call` in the scratch file.

### Add a card the agent door will run

To expose a new tool to models.

**Prerequisites:**
- The tool has a test command.

**Step 1:** Add an entry to `fieldkit/desk/tools.yaml` with `id`, `title`, `entry`, `inputs`, `effects`, `safety`, `scope`, `modes.preview`, `modes.verify` and `test`. Mark an input `allow_dash: true` only if its values must start with `-` and the tool cannot misread them.
  - Expected: `fieldkit cards show <id>` prints the card.
**Step 2:** `fieldkit tools check --run-tests --id <id>`
  - Expected: Pass: test PASS, so trust can reach `tested` or `verified`.
**Step 3:** `fieldkit cards list --level verified`
  - Expected: Pass: the new id is listed. If it shows `tested`, read the blocker column (for example `changes things but has no preview/undo/verify`).

**After this task:** `discover` can return the tool and `run` accepts it.

## Troubleshooting

**Symptom:** `fieldkit agent run` exits 0 with `DONE (not verified)`.
**Cause:** The card has no `modes.verify`; `verified` is `None`, which the CLI exit check (`verified is not False`) treats as success.
**Remedy:** Add verify checks to the card.
**Verify:** Rerun; status reads `DONE and verified`.

**Symptom:** `undo` refuses with `file(s) changed after run`.
**Cause:** The entry's current `_state_hash` differs from the recorded `after`: edited, deleted and recreated, or touched by another run.
**Remedy:** Confirm with the maintainer; force with `--approve` on the CLI only.
**Verify:** Compare the journal's `after` with the current state.

**Symptom:** `undo` refuses an old run with `has no usable scope record`.
**Cause:** The journal predates the 2026-10-02 change and has no `scope` list.
**Remedy:** Restore by hand from `state/agent-backups/<run_id>/`.
**Verify:** The journal JSON has no `scope` key.

**Symptom:** Apply refuses with `scope folder ... is a drive root or holds fieldkit's own backups`.
**Cause:** The scope input names a drive root, the Fieldkit folder or `state/`, or a folder inside `state/agent-backups/`.
**Remedy:** Pass a narrower folder.
**Verify:** Apply proceeds to `DONE`.

**Symptom:** `fieldkit agent undo` exits 0 but prints `UNDO COMMAND FAILED`.
**Cause:** `cmd_agent` returns 0 for every `undo` that does not raise; `ok: False` is ignored on the CLI (MCP reports it as `isError`).
**Remedy:** Read the answer, or use `--json` and check `ok`.
**Verify:** `--json` output shows `"ok": false`.

**Symptom:** MCP client receives nothing for a request.
**Cause:** The message had no `id` (a notification), so `handle` returns `None` by design.
**Remedy:** Send an `id`.
**Verify:** A reply with the same `id` arrives.

**Symptom:** Test calls appear in the live recorder file.
**Cause:** `FIELDKIT_RECORDER` unset during tests.
**Remedy:** Set it, as the test suite does.
**Verify:** The dated file under `state/recorder/` has no new lines.

## Technical Debt

🟠 **MEDIUM** — `scope` and `after` hashes are stored in the same journal they validate, so write access to `state/agent-runs/` and `state/agent-backups/` together still lets an actor steer a restore. → Sign or MAC the journal with a key outside `state/`, or keep the scope and hashes in a separate append-only record.
🟠 **MEDIUM** — Values starting with `/` (Windows switch syntax) pass `check_inputs`. → Add a per-card or per-platform switch-prefix rule, or insert `--` before positionals where the target tool supports it.
🟡 **LOW** — CLI `fieldkit agent undo` exits 0 when the card's undo command fails. → Return 3 when `res["ok"]` is false, as `run` already does for failures.
🟡 **LOW** — `mcp.call_tool` has no final return after its branches; a tool added to `TOOLS` without a branch returns `None`, and the tuple unpack in `handle` raises `TypeError`, ending `serve`. → Restore a final `return f"no tool {name!r}", True` inside the `try`.
🟡 **LOW** — Folder scope entries have no size limit; every apply copies and hashes the whole tree. → Refuse or warn above a configured size, and report the backup size.
🟡 **LOW** — Journals, backups, logs and recorder files grow without limit. → Add a retention command or document manual cleanup.
🟡 **LOW** — `mcp.py` module docstring lists six tools; nine are offered. → List the three build-harness tools in the docstring.
🟡 **LOW** — The `--approve` help text mentions only `run`; it now also forces `undo`. → Update the argparse help string.
🟡 **LOW** — The run timeout is not exposed on the CLI or over MCP. → Add a card-level `timeout` field or a `--timeout` option.

## Impact If Removed

Removing `cli.py` removes the `fieldkit` command and every sub-command; the harnesses stay importable but nothing starts them. Removing `agent.py` removes the only enforcement of card trust, input checking (including the dash rule), approval, fail-closed backup, verify, restore and validated undo; `fieldkit agent` and the MCP `discover`/`describe`/`run`/`undo` tools fail on import. Removing `mcp.py` cuts every MCP client off from Fieldkit, including the build-harness tools. Removing `__init__.py` breaks the package and `--version`.

## Claim Sources

| Claim | Basis | Evidence |
|-------|-------|----------|
| MCP run never approves | 📄 stated in input | approve=False)                      # never from a model |
| MCP undo passes no approve | 📄 stated in input | # no approve here: edited files stay refused |
| System effects need approval even when reversible | 📄 stated in input | Changes to these need the owner's approval even when the tool can undo them |
| Failed verify restores the backup | 📄 stated in input | if verification fails, restores the backup automatically |
| Leading-dash inputs are refused | 📄 stated in input | starts with '-', so the tool could read it as an option |
| Backup is fail-closed | 📄 stated in input | Fails closed: anything that cannot be backed |
| Undo checks everything before touching anything | 📄 stated in input | Every check happens before anything is touched. |
| Undo will not overwrite post-run edits without approval | 📄 stated in input | a file edited after the run is not overwritten |
| MCP run is an error on failed verify | 📄 stated in input | # an error if the tool failed OR its verify checks failed (read-only tools included) |
| CLI agent run defaults to apply | 📄 stated in input | default="apply" |
| Recorder cuts arguments and answers | 📄 stated in input | str(v)[:2000] + "..." |
| Group was 1,258 lines before the fixes | 📄 stated in input | agent-door 1,258 |
| Journal scope and hashes share the journal's trust, so joint write access still steers a restore | 🤖 model inference | *(none — model judgment)* |
| Slash-prefixed values are not refused | 🤖 model inference | *(none — model judgment)* |
| CLI undo exits 0 on a failed undo command | 🤖 model inference | *(none — model judgment)* |
| call_tool falls through to None for an unhandled offered tool | 🤖 model inference | *(none — model judgment)* |
| Local ok in undo is never reassigned | 🤖 model inference | *(none — model judgment)* |
| A model can undo a run the maintainer started if files are unchanged | 🤖 model inference | *(none — model judgment)* |
| Folder restore leaves a brief window where the target does not exist | 🤖 model inference | *(none — model judgment)* |


---
**How to verify this document:**
`📄 stated in input` — the model's phrasing of something your source text said.
Find the matching line in the original to verify.
`🤖 model inference` — the model's own judgment or synthesis. Treat as opinion,
not measurement. Re-run on the same input and check whether specific numbers
stay consistent between runs.

*Auto-generated DITA-structured developer documentation.*