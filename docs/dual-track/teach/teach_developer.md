# fieldkit.teach: a teaching log extracted from a coding-agent session transcript (calls, render, write)

> Generated 2026-10-14 | Source: `teach`

---

## Purpose

fieldkit/teach/__init__.py reads one coding-agent session transcript (a JSON-lines file under the agent's sessions folder, resolved by the watcher module's resolve()) and writes one markdown log of every Bash or PowerShell tool call in order, with the matched tool result and its is_error flag. Trust level: reads one user-owned file, writes one file the caller names, no network, no subprocess. It is a formatter over a record the session cannot edit; the record is the authority. New on 2026-10-14; not yet used to teach any pipeline. The module exists for one reason the maintainer stated on 2026-10-14: a job a model does once on a machine (today: checking an old working folder against the latest published patch set, command by command) should leave a complete operational record from which the harness can later be given a pipeline stage, a signature or a card. The transcript is that record already; this module only reshapes it. It deliberately keeps the two things a reader of such a record needs most and a model's own report tends to blur: the exact command text, including multi-line here-documents, and whether the result block carried is_error. Everything else about the session (its reasoning, its claims of success, its file edits through other tools) is out of scope by default and can be widened with --tools. The log is written once, is plain markdown, and can be checked against the transcript line by line, which is the property that makes it usable as evidence instead of as a story.

## Known Alternatives Considered

The docstring records the rejected alternative: a live wrapper that appends each command as it runs (~/.agents/teach.sh, which still exists for logs that must live in a project folder as they happen). Rejected as the primary mechanism because the transcript already holds every call: "It is less than zero effort: the Claude Code transcript on disk already holds every tool call and every result in order." Also rejected: including the session's narration, because "a model's narration beside its commands is the thing the recorder (buildh/recorder.py) exists to distrust."

## Architecture

- **Pattern:** Three pure functions in a line: calls(path, tools, since) -> list of dicts; render(recs, title, source, max_chars) -> str; write(session, out, ...) resolves the path, calls both and writes the file. Resolution of session names is delegated to the watcher module in fieldkit.buildh, which already knew the transcript folder. calls() is a single pass over the file: every line is parsed as JSON (lines that fail to parse are skipped silently, because transcripts can hold partial last lines while a session is live); for each content block of type tool_use whose name is in the kept set, a record is created and remembered by its id; for each block of type tool_result, the pending record with the same tool_use_id receives the result text and the error flag. Result text is taken from a string content or from the text parts of a list content. render() never reads the file again: it works from the records only, so a unit test can hand it synthetic records. write() is the only function with a side effect and the only one that touches the file system beyond reading the transcript.
- **Trust boundary:** The transcript is trusted as the record of what ran; its text is copied verbatim into the log. The log is NOT trusted as safe to publish: it carries whatever the commands printed. The caller's --out path is written without confirmation; an existing file is overwritten.
- **Attack surface:** One file path read as text (lines that are not JSON are skipped); one output path written. A hostile transcript can only make a large or odd log. Nothing is executed; here-document bodies are copied as text inside a fenced block.
- **Dependencies:** `json`, `os`, `re`, `datetime`, `fieldkit.buildh (the watcher module's resolve)`

## Flags & Configuration

| Name | Type | Default | Effect | Notes |
|------|------|---------|--------|-------|
| `session` | `string` | `newest` | a session id prefix, a .jsonl path, or newest (most recently modified transcript) | resolved by the watcher module; a miss is REFUSED, exit 3 |
| `--out` | `string` | `none (required)` | the markdown file written | overwritten if it exists |
| `--since` | `string` | `empty` | tool calls whose transcript timestamp sorts before this ISO string are dropped | plain string comparison against the transcript's own timestamp format |
| `--tools` | `string` | `Bash,PowerShell` | tool names kept | comma-separated; other tool calls are ignored entirely |
| `--max-chars` | `int` | `4000` | output kept per command in the log | the cut line says how many characters were left out |
| `--title` | `string` | `Teaching log` | the H1 of the log |  |
| `--json` | `bool` | `false` | the write() result as JSON | {ok, source, out, commands, failed} or {ok: false, why, out} |

## API Surface

| Symbol | Description | Side Effects |
|--------|-------------|--------------|
| `calls()` | tool_use blocks of the named tools, each matched to its tool_result by tool_use_id: {time, tool, command, description, result, error} | none |
| `render()` | the markdown log plus a summary (tools, distinct first words, failures) | none |
| `write()` | resolve, calls, render, write the file | writes `out` |
| `TOOLS` | the default tool names kept | none |

## Kill Switches

### `write(): `if not path or not os.path.isfile(path): return {"ok": False, ...}``
- **Condition:** no transcript resolves
- **Effect:** nothing written, exit 3
- reversible
- an empty log would be a false record

### `calls(): `if since and t and t < since: continue``
- **Condition:** --since given
- **Effect:** earlier calls dropped; a call with no timestamp is kept
- reversible
- 

## Dead Code

- **`none found on 2026-10-14`** — the module has three functions and one constant, all reached by the CLI and the tests (risk: none)

## Performance

- **CPU:** not measured
- **MEMORY:** not measured: the whole transcript is read line by line, but all kept records stay in memory until render
- **IO:** one read of the transcript, one write of the log; not measured
- **NOTES:** no numbers in MEASUREMENTS.md for this group

## Security

- **Remote execution:** none
- **Data handling:** verbatim copy of command text and output from a private file into a file the caller names; the log inherits the transcript's sensitivity
- **Attack surface:** one input file parsed as JSON lines; one output file
- **Notes:** run `fieldkit privacy scan` on the log before it leaves the machine; the docstring and the layman track say so The one realistic misuse is publishing the log unread. The rendered page reproduces, verbatim, the output of commands such as `git remote -v`, `ls -la` in a home folder, or an environment dump, so it carries user names, folder layouts and whatever a careless command printed. The tool does not try to detect this, by design: detection belongs to the privacy scanner, which is the single gate the whole project uses before anything is pushed, and duplicating a weaker copy of it here would give a false sense of safety. The technical debt list proposes wiring the scanner in as a refusal, which would keep one implementation.

## Error Conditions

| Error | Cause | Remedy |
|-------|-------|--------|
| `REFUSED: no transcript found for '<session>' under the agent's sessions folder` | resolve() returned nothing or a non-file | use newest, a longer id prefix, or the .jsonl path |
| `a call shows (no output)` | no tool_result with that tool_use_id followed in the transcript (session ended, or the result block had no text) | read the transcript at that time; the call itself is still recorded |
| `'... more characters not shown'` | result longer than max_chars | raise --max-chars |
| `a transcript line is not JSON` | a partial last line while the session is still being written, or a corrupted record | none needed: the line is skipped and the rest of the file is read; re-run after the session ends if the tail matters |
| `the log has entries whose command box is empty` | --tools included a tool whose input has no `command` field (an editor or reader tool) | expected for non-shell tools; the description line still names what was done |

## Tasks

### Run the tests for this module

Before and after changing which blocks are kept or how results are matched.

**Prerequisites:**
- Fieldkit installed with the test extra

**Step 1:** Run:

```powershell
cd (fieldkit where)
```
  - Expected: the Fieldkit folder
**Step 2:** Run:

```powershell
python -m pytest -q tests/test_teach.py
```
  - Expected: 5 passed

**After this task:** calls(), since, write(), the cut and the refusal are each covered by one test.

### Make a teaching log of one job inside a long session

A session that ran all day holds several jobs; the log should cover one.

**Prerequisites:**
- the time the job started, in the transcript's own format (ISO, UTC)

**Step 1:** Run:

```powershell
fieldkit teach newest --out LOG.md --since 2026-10-14T14:40
```
  - Expected: wrote LOG.md: N command(s), M failed, from <transcript>
**Step 2:** Run:

```powershell
fieldkit privacy scan LOG.md
```
  - Expected: 0 finding(s), or the lines to reword before the log is shared

**After this task:** LOG.md holds the job's commands in order; the transcript is unchanged.

### Keep other tools in the log

A job done mostly through file edits leaves a short Bash-only log.

**Step 1:** Run:

```powershell
fieldkit teach newest --out LOG.md --tools Bash,PowerShell,Edit,Write
```
  - Expected: entries for those tools too; for non-shell tools the command field is empty and the description carries the detail

**After this task:** The rendered command box is empty for tools without a command input; this is a known limit (see technical debt).

### Turn one job's log into harness material, by hand

The log is raw material. Until a consumer exists, a person or a later session reads it and decides what becomes a pipeline stage, a failure signature or a card.

**Prerequisites:**
- a log written for one job (see the --since task)
- the harness checkout

**Step 1:** Read the Summary at the end of the log: the distinct first words say which programs the job used (git, diff, gh, grep, dpkg and so on).
  - Expected: a short list of operations
**Step 2:** For each entry marked FAILED, decide whether the failure is a recognisable error line. If it is, add a stub to the matching file under `fieldkit/build/signatures/` so `fieldkit triage` names it next time.
  - Expected: one signature per distinct failure
**Step 3:** For a sequence of commands that will be run again (fetch, compare, verify), write a pipeline stage in `fieldkit/build/pipelines/<name>.yaml` with the command and a verify row, then check it:

```powershell
fieldkit pipeline plan <name>
```
  - Expected: the resolved commands printed; nothing run
**Step 4:** A command that stands alone and will be reused gets a card in `local/tools.yaml` or `fieldkit/desk/tools.yaml` with a test, then:

```powershell
fieldkit cards check
```
  - Expected: 0 failing

**After this task:** The job's operations exist as checked stages, signatures or cards; the log stays as the record they came from.

## Troubleshooting

**Symptom:** REFUSED although the session exists
**Cause:** the id prefix matches no file name under the agent's sessions folder; the watcher module globs `<prefix>*.jsonl`
**Remedy:** give the .jsonl path
**Verify:** the result's source field names the file

**Symptom:** --since drops everything
**Cause:** the string sorts after every transcript timestamp (wrong year or a local time written as UTC)
**Remedy:** use the transcript's own format, e.g. 2026-10-14T14:40
**Verify:** commands count rises

**Symptom:** a result is attached to the wrong command
**Cause:** cannot happen by id: results are matched by tool_use_id; if the transcript reuses ids the later result wins
**Remedy:** inspect the transcript
**Verify:** the ids in the transcript are unique

**Symptom:** the log for a job started at a known wall-clock time misses its first commands
**Cause:** transcript timestamps are UTC with a Z; a local time given to --since sorts later than the first commands
**Remedy:** subtract the local offset, or give the time an hour earlier and cut by hand
**Verify:** the first entry's time is at or before the job's start

## Technical Debt

🟡 **LOW** — non-shell tools render with an empty command box; only `input.command` is read → render file_path or a JSON summary of the input for non-shell tools, as the watcher module's events() does
🟡 **LOW** — --since compares strings, not times → parse both to datetime when the transcript timestamp format is confirmed stable
🟠 **MEDIUM** — no privacy scan is run by the tool itself → call fieldkit.core.privacy.scan_text on the rendered log and append a REVIEW section naming the lines, or refuse to write when findings exist and --allow-private is not given
🟠 **MEDIUM** — the log is not yet consumed by anything in the harness → a first consumer that reads a log and proposes pipeline stages from the distinct commands, for a person to edit before anything is saved; until then the by-hand task above is the route

## Impact If Removed

The `fieldkit teach` command, its card and tests/test_teach.py stop working. Nothing else imports the module. The live wrapper ~/.agents/teach.sh still works for logs written as a job runs; the transcript-based route, which misses nothing and costs nothing during the job, is gone.

## Claim Sources

| Claim | Basis | Evidence |
|-------|-------|----------|
| The transcript already holds every tool call and result in order | 📄 stated in input | already holds every tool call and every result in order |
| Narration is excluded because the recorder exists to distrust it | 📄 stated in input | the thing the recorder (buildh/recorder.py) exists to distrust |
| Results are matched by tool_use_id | 📄 stated in input | pending.pop(b.get("tool_use_id"), None) |
| No subprocess or network is used | 🤖 model inference | *(none — model judgment)* |
| An existing --out file is overwritten | 🤖 model inference | *(none — model judgment)* |
| Nothing in the harness consumes the log yet | 🤖 model inference | *(none — model judgment)* |
| Performance is not measured | 🤖 model inference | *(none — model judgment)* |


---
**How to verify this document:**
`📄 stated in input` — the model's phrasing of something your source text said.
Find the matching line in the original to verify.
`🤖 model inference` — the model's own judgment or synthesis. Treat as opinion,
not measurement. Re-run on the same input and check whether specific numbers
stay consistent between runs.

*Auto-generated DITA-structured developer documentation.*