# `fieldkit.exam`: deterministic tool-use benchmark for local models, `raw` against `kit` toolsets

> Generated 2026-10-02 | Source: `exam`

---

## Purpose

`fieldkit.exam` measures whether a small local model completes six fixed tasks (the sixth, `kernel-port`, added 2026-10-10 with the kit tool `kernel_migrate_check`, has not been run with a model yet) with a basic agent toolset (`raw`: `list_dir`, `read_file`, `search_text`) and with Fieldkit's toolset (`kit`: `raw` plus `find`, `triage`, `refcheck`). `fixture.py` writes a deterministic 29-file project (seeded `random.Random(20260929)`), `tasks.py` defines the five tasks and their regex graders, `tools.py` implements both toolsets behind a path-confined `Toolbox`, and `runner.py` drives an OpenAI-compatible chat-completions loop and aggregates records. Grading is mechanical: "no model judges another". The group is 578 lines of Python. Trust level: it trusts the model server's JSON and the local `pfind` script; it does not trust model-supplied paths, which `Toolbox._path` confines to the fixture root. The verified result is Gemma at 1 of 5 with `raw` and 5 of 5 with `kit`; no other run figure is verified, so treat timing and token numbers as not measured.

## Known Alternatives Considered

The source names one rejected approach: model-as-judge grading. The package docstring states "Every task is graded by a script from the final answer and the tool trace - no model judges another (the rule from model-eval)." `runner.py` states the chat loop "follows model-eval's run_scenario". `tools._find_name` documents why near-miss names are not returned as answers: "on 2026-09-29 that pushed Gemma into naming a Markdown file as the definition." `tools._check_paths` documents why a path list is checked per path: answering "NOT FOUND" to the whole string "made Gemma report all 12 files of a manifest as missing." No other alternatives are documented. Not available in the source material.

## Architecture

- **Pattern:** Fixture-generate, agent tool-call loop (send, dispatch tool calls, append results, repeat until no tool call or round limit), then deterministic grading and per-(run, model, toolset) aggregation.
- **Trust boundary:** Untrusted: tool names and arguments from the model. `Toolbox.dispatch` maps names through a fixed dict, refuses kit tools when `kit=False`, catches `TypeError` for bad arguments and `ValueError`/`OSError` for path errors. `Toolbox._path` resolves the path and rejects anything not equal to or under `self.root` with `path is outside the project`. Trusted: the server response shape (only `choices[0].message` and `usage` are read; malformed tool-call JSON is counted, not raised), the `pfind` script at `settings.ROOT/toolbox/pfind/pfind.py`, and the `fieldkit.build` triage and refcheck modules. Model text is never executed; it is only matched by graders.
- **Attack surface:** Model-controlled tool arguments (paths, search text, `find` query) reach the filesystem only inside the fixture root, and reach `pfind` via stdin or as a regex built from an identifier that `find` has already matched against `[A-Za-z_][A-Za-z0-9_]*`. The `--base` URL is operator-controlled and passed unvalidated to `urllib.request.urlopen`; it sends no authentication header. `search_text` walks `self.root.rglob("*")` and reads every file without re-checking each resolved path; the generated fixture contains no symlinks, so this is not reachable with the shipped fixture (inference).
- **Dependencies:** `json`, `re`, `os`, `random`, `shutil`, `subprocess`, `sys`, `tempfile`, `time`, `urllib.request`, `urllib.error`, `pathlib.Path`, `dataclasses.dataclass`, `typing.Callable`, `fieldkit.core.settings (ROOT, used to locate toolbox/pfind/pfind.py)`, `fieldkit.build.triage (triage_file, imported lazily in Toolbox.triage)`, `fieldkit.build.refcheck (check_manifest, lines; imported lazily in Toolbox.refcheck)`, `toolbox/pfind/pfind.py (external script run as a subprocess; gathered with `fieldkit gather --only pfind`)`, `An OpenAI-compatible server at `--base` (default `http://localhost:1234/v1`, LM Studio)`

## Flags & Configuration

| Name | Type | Default | Effect | Notes |
|------|------|---------|--------|-------|
| `action` | `string (`run` | `report`)` | `required` | `run` executes tasks against models and writes records; `report` reads every `*.json` under the results folder and prints the summary without contacting a server. | `report` sets `run` to the parent folder name for records written before runs were named. |
| `--model` | `string list` | `[] (empty)` | Model ids as the server lists them; each runs all selected tasks and toolsets. | With no `--model`, `run` executes nothing and prints an empty table with exit code 0. |
| `--toolset` | ``raw` | `kit` list` | ``raw kit`` | Which toolsets to run per task. | Order is per task: `raw` then `kit` by default. |
| `--task` | `string list` | `all five` | Restricts to task ids `locate`, `trap`, `build-log`, `snippet`, `manifest`. | Unknown ids are skipped silently; no task runs. |
| `--base` | `string (URL)` | ``http://localhost:1234/v1`` | Chat-completions endpoint root; the runner posts to `{base}/chat/completions`. | No auth header; a hosted API that needs a key fails with an HTTP error recorded as `error`. |
| `--out` | `path` | ``settings.ROOT / "exam-results"`` | Results folder; `run` writes `{model}__{task}__{toolset}.json` there, `report` reads it recursively. | `cmd_exam` passes `out` as `out_dir`, so records go directly in `--out`, and the record's `run` field is the folder name. Reruns into the same folder overwrite files of the same name. |
| `--max-rounds` | `int` | `10` | Maximum chat rounds per task before the record is marked `truncated`. | A truncated record is always `passed: false`, `why: "no final answer"`. |
| `--json` | `bool` | `false` | Prints the summary rows as JSON instead of the table. | From the shared `common` parser. |

## API Surface

| Symbol | Description | Side Effects |
|--------|-------------|--------------|
| `fixture.build()` | Writes the deterministic exam tree under `root` (must be empty or absent). | Creates directories and 29 files; `src/win/profile_loader.py` is written with CRLF line endings. |
| `fixture.definition_line()` | 1-based line of `def apply_fragment` in the generated `kernel_tools.py` (40). | none |
| `tasks.TASKS / tasks.BY_ID` | The six tasks; `Task(id, prompt, grade)` where `grade(answer) -> (bool, str)`. | none |
| `tasks.final_answer()` | Text after the last `ANSWER:` and whether the marker was present; falls back to the whole stripped reply. | none |
| `tools.Toolbox` | Path-confined toolset; records each call as `{name, args, chars, seconds}`. | Reads files under `root`; `find` spawns `pfind` subprocesses (timeout 120 s each). |
| `runner.chat()` | One chat-completions call at `temperature: 0`, `tool_choice: auto`; returns content, parsed tool calls, malformed count and token usage. | HTTP POST to `{base}/chat/completions`. |
| `runner.run_task()` | Builds a fresh fixture, runs the loop, grades, returns one record. | Creates a `fieldkit-exam-*` temp dir and removes it when `workdir` is None; network calls to `base`. |
| `runner.run()` | All selected tasks times toolsets for one model. | Writes one JSON per record to `out_dir` when given; prints progress via `log`. |
| `runner.summary()` | Aggregates per `(run, model, toolset)`: tasks, passed, prompt_tokens, seconds, tool_calls, format_misses, chose_kit_tool. | none |
| `runner.used_kit_tool()` | True if the record's trace contains the tool `KIT_TOOL_FOR[task]`. | none |

## Kill Switches

### ``runner.run_task`, `for _ in range(max_rounds)` with `else: rec["truncated"] = True``
- **Condition:** The model is still calling tools after `max_rounds` rounds.
- **Effect:** Stops the loop; the task fails with `no final answer`.
- reversible
- Raise with `--max-rounds`; results are then not comparable with the default.

### ``runner.chat`, `urlopen(req, timeout=timeout)` with `timeout=900``
- **Condition:** A single request takes longer than 900 s, or the server is unreachable.
- **Effect:** `URLError`, `TimeoutError` or `OSError` is caught, stored in `rec["error"]`, and the loop breaks; the task fails.
- reversible
- `timeout` is not exposed on the CLI.

### ``Toolbox._path``
- **Condition:** A resolved path is neither the root nor under it.
- **Effect:** Raises `ValueError("path is outside the project")`, returned to the model as `ERROR: ...`.
- **not reversible**
- Covered by `test_raw_tools` (`../../etc/passwd`).

### ``Toolbox.dispatch``
- **Condition:** A kit tool is called on a `raw` toolbox, or the name is unknown.
- **Effect:** Returns `ERROR: no tool named ...` with the allowed tool list.
- **not reversible**
- Covered by `test_raw_toolbox_refuses_kit_tools`.

### ``runner.run_task`, `box.dispatch(...)[:8000]``
- **Condition:** A tool result exceeds 8,000 characters.
- **Effect:** The result is truncated before it reaches the model.
- reversible
- The recorded `chars` is the untruncated length.

## Dead Code

- **``Toolbox.calls[*]["seconds"]``** — Recorded per call but dropped when `run_task` copies `tool_calls` (only `name`, `args`, `chars` are kept). (risk: Low; removing it changes nothing downstream.)
- **``rec["completion_tokens"]``** — Accumulated and saved, but never aggregated by `summary()` or shown by `cmd_exam`. (risk: Low; external readers of the JSON may use it.)

## Performance

- **CPU:** Not measured. The exam's own work is file writes, line scans and `pfind` subprocesses; model inference dominates and runs in the server.
- **MEMORY:** Not measured. `search_text` and `read_file` read whole files into memory; the largest fixture file is the 1,488-line build log.
- **IO:** Each task run writes the 29-file fixture to a new temp dir and deletes it; `run` writes one JSON record per task and toolset. Totals not measured.
- **NOTES:** The only verified figures: Gemma 1 of 5 (`raw`) and 5 of 5 (`kit`). The package docstring cites reading speeds for Gemma and Qwen; those are not in the verified measurements, so treat them as not measured. Wall time per run is not measured here.

## Security

- **Remote execution:** None from model output. Model text is graded by regex only; tool calls go through a fixed name-to-method map. `find` runs `pfind` via `subprocess.run` with an argument list (no shell) and the query on stdin.
- **Data handling:** Sends the fixture-derived conversation to `--base`. Records store up to 400 characters of the answer plus every tool call's name and arguments. The fixture is synthetic; no host file outside the temp root is readable through the tools.
- **Attack surface:** Model-controlled paths (confined), search strings (substring match), `find` queries (identifier regex or stdin to `pfind`), and the operator-controlled `--base` URL.
- **Notes:** `pfind` inherits the full environment (`env=dict(os.environ)`). `--base` accepts any URL scheme `urlopen` supports; no allow-list exists (inference: low risk because the operator sets it).

## Error Conditions

| Error | Cause | Remedy |
|-------|-------|--------|
| ``ERROR: path is outside the project`` | A model-supplied path resolves outside the fixture root. | None needed; this is the confinement working. |
| ``ERROR: no tool named '...'. Tools: ...`` | Unknown tool name, or a kit tool called on the `raw` toolset. | None needed; recorded in the trace. |
| ``ERROR: bad arguments for <tool>: ...`` | The model passed argument names the tool does not accept (`TypeError`). | None; counts against the model. |
| ``ERROR: find needs pfind, which is not gathered here - run: fieldkit gather --only pfind`` | `toolbox/pfind/pfind.py` is absent. | Run `fieldkit gather --only pfind`. |
| ``ERROR: find failed: ...`` | `pfind` stdout is not JSON (crash or usage error). | Run `pfind` by hand with the same arguments and read its stderr. |
| `record `error`: `URLError: ...` / `TimeoutError: ...`; `why: no final answer`` | Server unreachable, refused, HTTP error, or a request over 900 s. | Start the server, check `--base`, load the model. |
| `record `truncated: true`; `why: no final answer`` | The model still called tools after `max_rounds`. | Model failure; optionally raise `--max-rounds` and note it in the comparison. |
| `record `malformed_calls > 0`` | Tool-call `arguments` were not valid JSON; the call is dispatched with `{}`. | Model failure; usually followed by `bad arguments`. |

## Tasks

### Run the exam module's unit tests

Before changing graders, tools or the fixture. The tests pin the fixture's determinism, grader verdicts both ways, tool outputs and loop accounting, with a scripted fake model (no server needed).

**Prerequisites:**
- Python with `pytest` installed
- Working directory: the Fieldkit folder
- `toolbox/pfind/pfind.py` gathered, or the `needs_pfind` tests are skipped

**Step 1:** `python -m pytest tests/test_exam.py -q`
  - Expected: Pass: all tests pass. Fail: a grader or fixture assertion names the task and answer that broke.
**Step 2:** If tests are reported as skipped, run `fieldkit gather --only pfind` and repeat step 1.
  - Expected: The `find` tests run instead of skipping.

**After this task:** Graders, fixture and tools behave as the tests describe.

### Run the exam against a local model

To measure a model with and without the kit.

**Prerequisites:**
- An OpenAI-compatible server (LM Studio) at `http://localhost:1234/v1` with a tool-calling model loaded
- `pfind` gathered for the `find` tool

**Step 1:** `fieldkit exam run --model google/gemma-4-e2b` (use the id your server lists)
  - Expected: Pass: one progress line and one `PASS`/`FAIL` line per task and toolset, then the summary table. Fail: every task `no final answer` after one round means the server was not reached.
**Step 2:** `fieldkit exam run --model google/gemma-4-e2b --toolset kit --task trap`
  - Expected: One task, one toolset, one record in `exam-results/`.
**Step 3:** `fieldkit exam report --json`
  - Expected: Summary rows as JSON, built from saved records without contacting any server.

**After this task:** One `{model}__{task}__{toolset}.json` per run in the results folder.

### Add a sixth task

To extend the benchmark without breaking comparability of old results.

**Prerequisites:**
- The fixture change must keep `fixture.build` deterministic

**Step 1:** Add any needed files in `fixture.build` (fixed content or the seeded `rng`).
  - Expected: `test_fixture_is_deterministic` still passes.
**Step 2:** Write a `_grade_<id>(ans) -> (bool, str)` in `tasks.py` and append a `Task` to `TASKS`.
  - Expected: `tasks.BY_ID` contains the new id.
**Step 3:** Add pass and fail cases to the `test_graders` parametrize list in `tests/test_exam.py`.
  - Expected: The grader is pinned both ways.
**Step 4:** Add the id to `runner.KIT_TOOL_FOR` with the kit tool meant to solve it.
  - Expected: `chose kit tool` counts the new task.

**After this task:** Old records stay readable, but summaries mixing five-task and six-task runs are no longer like for like.

## Troubleshooting

**Symptom:** All records fail with `why: no final answer`, `rounds: 1`.
**Cause:** `chat()` raised `URLError`/`OSError`; the server is down or `--base` is wrong.
**Remedy:** Start the server; check `--base`.
**Verify:** The record's `error` field holds the exception text.

**Symptom:** `kit` passes but `chose kit tool` is low.
**Cause:** The model solved tasks with `raw` tools, or solved `manifest` with `find` (path-list branch) instead of `refcheck`, which `used_kit_tool` does not count.
**Remedy:** Read `tool_calls` in the records before claiming the kit helped.
**Verify:** `tool_calls[*].name` in each record.

**Symptom:** `find` tests skipped in `pytest`.
**Cause:** `PFIND` path is not a file.
**Remedy:** `fieldkit gather --only pfind`.
**Verify:** `python -m pytest tests/test_exam.py -q` reports no skips.

**Symptom:** A correct-looking answer fails `locate`.
**Cause:** The grader needs both the path (or `build/kernel_tools.py`) and the exact line number 40, not adjacent digits.
**Remedy:** Check the answer text after the last `ANSWER:`.
**Verify:** `tasks.BY_ID["locate"].grade(answer)` returns the reason.

**Symptom:** `report` shows rows from old runs mixed in.
**Cause:** `report` reads every `*.json` under `--out` recursively.
**Remedy:** Use a separate `--out` folder per comparison.
**Verify:** The `run` column shows the folder names.

## Technical Debt

🟡 **LOW** — Fixture docstring says "About 40 files" and "a 1,500-line failed Debian build log"; `build` writes 29 files and a 1,488-line log. → Correct the docstring to the generated counts.
🟡 **LOW** — Package docstring lists the kit as `raw + find + triage`; `KIT_SCHEMA` also has `refcheck`. → Add `refcheck` to the `__init__.py` docstring.
🟡 **LOW** — Package docstring states model reading speeds that are not in the verified measurements. → Remove them or move them into the measurements file with their source run.
🟡 **LOW** — `used_kit_tool` counts only `refcheck` for `manifest`, though `find` has a path-list branch built for the same task. → Decide whether `find` path-list use counts, and make `KIT_TOOL_FOR` accept a set.
🟠 **MEDIUM** — The same author wrote tasks, graders and kit tools; kit tools were adjusted after observed Gemma failures (2026-09-29) on these tasks. → Add held-out tasks not used while tuning the kit before citing the kit result as general.
🟡 **LOW** — `timeout=900` and `max_tokens=1024` are not configurable from the CLI. → Expose them as flags and record them in each result.
🟡 **LOW** — Per-call `seconds` and `completion_tokens` are recorded but unused. → Either report them in `summary()` or stop recording them.

## Impact If Removed

`fieldkit exam run` and `fieldkit exam report` fail at `from .exam import runner`, and `tests/test_exam.py` and `tests/test_manifest_on_the_exam_fixture` in `tests/test_refcheck.py` fail at import, because `test_refcheck.py` uses the exam fixture. The README's "Measure it on your own model" section and its result table lose their reproduction path: the 1 of 5 against 5 of 5 claim could no longer be rerun. No other module under `fieldkit/` imports `fieldkit.exam` except `cli.py` (a text search of `fieldkit/` and `tests/`).

## Claim Sources

| Claim | Basis | Evidence |
|-------|-------|----------|
| Gemma 1 of 5 raw, 5 of 5 kit | 📄 stated in input | got 1 of 5 tasks right with basic tools and 5 of 5 with Fieldkit |
| Group is 578 lines | 📄 stated in input | exam 578 |
| Grading uses no model | 📄 stated in input | no model judges another |
| Paths confined to fixture root | 📄 stated in input | All paths are confined to the exam folder |
| Truncation never scored as pass | 📄 stated in input | Truncation is recorded, never scored as a pass. |
| Near-miss names not returned as answers, after a Gemma failure | 📄 stated in input | Near-misses (rebalance_caches for rebalance_cache) are never presented as the answer |
| Path lists checked per path, after a Gemma failure | 📄 stated in input | made Gemma report all 12 files of a manifest as missing |
| Fixture docstring counts disagree with generated output | 🤖 model inference | *(none — model judgment)* |
| `seconds` per call and `completion_tokens` are unused downstream | 🤖 model inference | *(none — model judgment)* |
| `used_kit_tool` undercounts manifest solved via find | 🤖 model inference | *(none — model judgment)* |
| Same-author tuning limits generality of the kit result | 🤖 model inference | *(none — model judgment)* |
| search_text symlink gap unreachable with shipped fixture | 🤖 model inference | *(none — model judgment)* |
| Only cli.py and two test files import fieldkit.exam | 🤖 model inference | *(none — model judgment)* |


---
**How to verify this document:**
`📄 stated in input` — the model's phrasing of something your source text said.
Find the matching line in the original to verify.
`🤖 model inference` — the model's own judgment or synthesis. Treat as opinion,
not measurement. Re-run on the same input and check whether specific numbers
stay consistent between runs.

*Auto-generated DITA-structured developer documentation.*