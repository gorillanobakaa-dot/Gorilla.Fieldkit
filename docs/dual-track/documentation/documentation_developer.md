# `fieldkit.gdocs`: the dual-track documentation workflow (Gorilla.Documentation.IBM.Style) around DualTrackAgent's `dual_track.py`

> Generated 2026-10-04 | Source: `documentation`

---

## Purpose

`fieldkit.gdocs` turns the hand-run documentation routine into the `fieldkit docs` command family and the `gorilla-documentation-ibm-style` pipeline. It owns the group map (`docs/groups.yaml`), the coverage rule (every `.py` under `fieldkit/` except `__init__.py` and `__main__.py` in exactly one group), staleness tracking through a committed `STATE.json` per group (SHA-256 of each source with CRLF read as LF), staging into `docs/_staging/<group>/`, driving `dual_track.py` prep and render as a subprocess, rewriting each `.prep.json` (correct `then_run`, relative `write_completion_to`, the Gorilla writer brief appended to `instructions`), a stdlib JSON Schema subset for the fill check, and the deterministic Gorilla checks applied to the rendered Markdown. It never edits `dual_track.py` and never calls a model. Trust level: it trusts `dual_track.py`, `docs/groups.yaml` and the rendered Markdown it reads as data; it trusts nothing a writer claims and treats every check failure as exit 3. Untested on Linux in this form.

## Known Alternatives Considered

The source documents four choices against alternatives. (1) Flat staging instead of mirrored folders: `staged_name` states "dual_track.py skips any folder named build (its IGNORE_DIRS), so a nested build/kernel.py would silently drop out of the documentation". (2) Fixing `then_run` instead of passing `--validate`: `rewrite_prep` states that dual_track writes `--validate`, which render does not accept. (3) Running `dual_track.py` with the Fieldkit folder as working directory and relative paths, so "every path it writes into a prep file is relative (no home folder ends up in a working file)". (4) Stale groups warn instead of failing in `check` unless `--strict`, because refreshing a group needs a model to rewrite its JSON. Alternatives for the checks themselves (an LLM judge, a Markdown AST parser) are not discussed in the source. Not available in the source material beyond these.

## Architecture

- **Pattern:** Thin CLI (`gdocs/cli.py`) and pipeline adapters (`gdocs/stages.py`) over a data-returning core (`workflow.py`), with `groups.py` for the map, hashes and staging, `dualtrack.py` for locating and running the external tool, and `checks.py` as pure functions over Markdown text.
- **Trust boundary:** Untrusted: the content of `.filled.json` files and of the rendered `.md` (written by a model or a person). They are parsed as JSON or scanned as text; nothing in them is executed. Trusted: `docs/groups.yaml` (group names are validated against `^[a-z0-9][a-z0-9-]*$` because the name becomes a folder that `stage()` deletes and recreates), `dual_track.py` (located by `FIELDKIT_DUAL_TRACK`, `"dual_track"` in `fieldkit.local.json`, `Documents/Scripts/DualTrackAgent/dual_track.py`, then `toolbox/dual-track-doc-generator/dual_track.py`, and imported, so its module-level code runs in-process), and `fieldkit.local.json`.
- **Attack surface:** Whoever controls `FIELDKIT_DUAL_TRACK` or `fieldkit.local.json` controls which Python file is executed and imported. `docs/groups.yaml` globs are matched only against the real `.py` list under `fieldkit/`, so they cannot reach files outside it. Group names cannot contain path separators. `shlex.split` on commands found in documents only tokenises; nothing is executed.
- **Dependencies:** `argparse`, `fnmatch`, `hashlib`, `importlib.util`, `json`, `re`, `shlex`, `shutil`, `subprocess`, `pathlib.Path`, `PyYAML (through fieldkit.core.settings.read_file)`, `fieldkit.core.settings`, `fieldkit.core.privacy`, `fieldkit.cli.build_parser (command checks)`, `dual_track.py (subprocess for prep and render; imported by path for validate_json, score_document, _extract_json and _CODE_VALIDATION)`

## Flags & Configuration

| Name | Type | Default | Effect | Notes |
|------|------|---------|--------|-------|
| `action` | `string (plan | prep | fill | render | check | index)` | `required` | Selects the step. | `fill` is a read-only status of the `.filled.json` files. |
| `groups` | `string list` | `[] (stale groups; all groups for plan and check)` | Restricts the step to named groups. | Unknown names raise `GroupError`: exit 2, never a silent skip. |
| `--force` | `bool` | `false` | prep: re-prep even when the prep files already carry the current source hashes and brief hash. | Re-prepping makes every existing `.filled.json` older than its prep file. |
| `--strict` | `bool` | `false` | check: a stale or never-rendered group is a finding instead of a warning. | The release-gate form. |
| `--json` | `bool` | `false` | Machine output. | From the shared `common` parser. |
| `FIELDKIT_DUAL_TRACK` | `env path` | `unset` | First place `dualtrack.path()` looks. | Then `"dual_track"` in `fieldkit.local.json`. |
| `--var groups=...` | `pipeline var` | `""` | Names groups for the pipeline stages; space or comma separated. | Empty means the stale groups. |
| `docs guide` | `action` | `n/a` | Prints `fieldkit/gdocs/LAYMAN_GUIDE.md`; with `--json`, an object with `path`, `lines` and `text`. | Handled before `groups` and `workflow` are imported, so it needs no `docs/groups.yaml` and reads one file. |
| `release-page compose` | `command` | `n/a` | Builds a release page from `--opening`, `--layman`, `--developer` and optional `--extra`, in a fixed order, and writes it to `--out`. | Runs `releasepage.check` on the result first; on any finding prints `REFUSED:` lines, writes nothing and exits 3. |
| `release-page check PAGE` | `command` | `n/a` | Lists every reason a page must not be published; `--layman` and `--developer` add the full-text checks. | Exit 3 when there are findings, 0 otherwise, 1 if a file cannot be read. |
| `docs philosophy` | `action` | `n/a` | Prints `fieldkit/gdocs/PHILOSOPHY.md`; with `--json`, an object with `path` and `text`. | Reads one file that ships with the package. |

## API Surface

| Symbol | Description | Side Effects |
|--------|-------------|--------------|
| `groups.load(path=None)` | Parse and validate `docs/groups.yaml`. | none |
| `groups.coverage(groups, pkg=None)` | Every required `.py` in exactly one group. | none |
| `groups.status(group)` | fresh, stale or never, from `STATE.json`. | none |
| `groups.stage(group)` | Flat copy into `docs/_staging/<group>/`. | Deletes and recreates that folder. |
| `workflow.prep(names, force)` | Stage, dual_track prep, `rewrite_prep` for both tracks. | Writes prep files, PRECHECK files and the output `.gitignore` (dual_track). |
| `workflow.fill_status(names)` | Per file: exists, newer than prep, JSON, `schema_errors`, `validate_json`. | none |
| `workflow.render(names)` | Refuses when the prep files do not match the current sources or brief, or a `.filled.json` is older than its prep file; otherwise stage, dual_track render, scores, `check_group`, `STATE.json`. | Writes `.md`, renderer `.json`, `STATE.json`. |
| `workflow.check(names, strict)` | Coverage and Gorilla checks on committed docs; no rendering. | none |
| `workflow.index()` | Write `docs/dual-track/README.md`. | Writes one file. |
| `checks.check_layman / check_developer` | The deterministic checks per track. | none |
| `checks.check_command(cmd, parser)` | Subcommand, action and option names against `fieldkit.cli.build_parser()`. | none |
| `gdocs.cli._guide(as_json)` | Reads `cli.GUIDE` (`LAYMAN_GUIDE.md` beside the module) and prints it, followed by a `NEXT:` line. Returns 0, or 1 if the file cannot be read. | none |
| `releasepage.compose(opening, layman_md, developer_md, extra='')` | Opening, then the plain-language track in full under a marker comment, then `extra`, then the developer track in full. Each track loses its own title block and has its headings demoted one level outside code fences. | none |
| `releasepage.check(page, layman_md='', developer_md='')` | Findings for: an opening heading missing or (for what-it-is) below line 40; a track whose whitespace-normalised text is not on the page; the developer marker before the plain-language marker; a link to a `release-notes.layman.md` or `.developer.md` file; a relative link or image; a `Full notes` heading. | none |

## Kill Switches

### ``workflow.render``
- **Condition:** dual_track render exits non-zero, or any Gorilla check finding
- **Effect:** No new source hashes are written to `STATE.json` (only `last_failed_render`); exit 3.
- reversible
- dual_track has already overwritten the `.md`; `git diff` shows it.

### ``workflow.render` freshness gate`
- **Condition:** `_prep_current` is false for the current hashes, or `_filled_current` is false for a track
- **Effect:** Finding before anything is staged or rendered; answers written for older sources are never stamped fresh.
- reversible
- mtime ordering: copying files can reset it.

### ``groups.load``
- **Condition:** A group name outside `^[a-z0-9][a-z0-9-]*$`
- **Effect:** `GroupError` before anything is staged.
- reversible
- Guards `shutil.rmtree` in `stage()`.

### ``stages.fill``
- **Condition:** Any `.filled.json` missing, older than its prep file, invalid JSON, off-schema or failing `validate_json`
- **Effect:** The stage fails with the file list, so `fieldkit next` reports BLOCKED.
- reversible
- The detail lists six files and counts the rest.

### ``dualtrack.run``
- **Condition:** A `dual_track.py` call runs longer than `timeout=600` seconds
- **Effect:** `subprocess.TimeoutExpired` propagates; the command fails.
- reversible
- Not configurable from the CLI.

## Dead Code

- **``workflow.score` for groups without the renderer `.json``** — Returns None when `<group>_<track>.json` is absent (it is git-ignored), so on a fresh clone scores come only from `STATE.json`. (risk: None; documented behaviour.)

## Performance

- **CPU:** Not measured.
- **MEMORY:** Not measured. `workflow.corpus` holds the text of every `.py` under `fieldkit/` and `tests/` plus `MEASUREMENTS.md` in memory, cached per group name in `_CORPUS` for the life of the process.
- **IO:** Not measured. Each check reads every `.py` under `fieldkit/` and `tests/` once per group per process.
- **NOTES:** No figure for this group is in `MEASUREMENTS.md`.

## Security

- **Remote execution:** None from documents or filled JSON. `dual_track.py` is executed as a subprocess and imported in-process; its location is configuration, so configuration is trusted.
- **Data handling:** Reads `fieldkit.local.json` privacy terms and searches rendered docs for them; findings print masked excerpts from `fieldkit.core.privacy`. Prep files contain the group's source, `MEASUREMENTS.md` and dual_track's git context (remote URL, recent commit subjects) and are git-ignored.
- **Attack surface:** `FIELDKIT_DUAL_TRACK`, `fieldkit.local.json`, `docs/groups.yaml`.
- **Notes:** `stage()` uses `shutil.rmtree` on `docs/_staging/<name>`; the name validation is the guard.

## Error Conditions

| Error | Cause | Remedy |
|-------|-------|--------|
| `no group X; groups are ...` | Unknown group name. | Use a name from `fieldkit docs plan`. Exit 2. |
| `group name ... must be lower-case letters, digits and hyphens` | Invalid name in `docs/groups.yaml`. | Rename the group. Exit 2. |
| `dual_track.py not found; set FIELDKIT_DUAL_TRACK ...` | No candidate path exists (`DualTrackMissing`, a `RuntimeError`). | `fieldkit gather --only dual-track-doc-generator` or set the path. `fieldkit.cli.main` prints it and exits 1. |
| `not filled yet: ...` | render called before both `.filled.json` exist. | Fill them. Exit 3. |
| `dual_track render exited N: ...` | dual_track's own validation or quality score failed (it exits 2). | Fix the JSON per the quoted lines. |
| `older than its prep file (the sources or the brief changed): rewrite it` | mtime of `.filled.json` is before its `.prep.json`. | Re-check against the new prep and save. |
| `command does not parse: ...` | A `fieldkit` command in the document names a subcommand, action or option that does not exist. | Correct it in the JSON. |
| `number N is in neither MEASUREMENTS.md nor the group's source ...` | Unsourced digit group outside code blocks and command spans, above 10, not within 80 characters of "not measured". | Cite or mark "not measured". |

## Tasks

### Run this group's tests

After any change to `fieldkit/gdocs/`.

**Prerequisites:**
- Working directory: the Fieldkit folder
- `pytest` installed
- `dual_track.py` present, or the two prep/render tests are skipped

**Step 1:** Run:

```powershell
python -m pytest tests/test_gdocs.py -q
```
  - Expected: Pass: all pass. The committed-docs tests are parametrised per group and fail with every finding listed.

**After this task:** Checks, coverage, prep rewriting and the fail-closed render are pinned.

### Refresh a stale group

`fieldkit docs plan` shows a group as stale.

**Prerequisites:**
- A model or a person to write the JSON

**Step 1:** Run:

```powershell
fieldkit docs prep office
```
  - Expected: `office prepared` and two `fill:` paths.
**Step 2:** Have both `.filled.json` files written from the prep files, then run:

```powershell
fieldkit docs fill office
```
  - Expected: Two `OK` lines.
**Step 3:** Run:

```powershell
fieldkit docs render office
```
  - Expected: `PASS office` with both scores; `STATE.json` holds the new hashes. On `FAIL`, fix the JSON and repeat.
**Step 4:** Run:

```powershell
fieldkit docs index
```
  - Expected: `wrote docs/dual-track/README.md`.

**After this task:** `fieldkit docs check --strict` passes for that group.

### Add a new module to the map

`fieldkit docs check` reports `coverage: FAIL orphans=[...]`.

**Prerequisites:**
- The new file's purpose

**Step 1:** Add its path relative to `fieldkit/` to the right group's `sources` in `docs/groups.yaml` (globs match one folder level: `core/*.py` never matches `core/sub/x.py`).
  - Expected: `fieldkit docs plan` shows `coverage: every .py under fieldkit/ is in exactly one group`.
**Step 2:** Refresh that group as above.
  - Expected: The group is fresh again.

**After this task:** The coverage test passes.

### Drive the workflow as a pipeline

An agent that should only ever do the one next thing.

**Step 1:** Run:

```powershell
fieldkit next gorilla-documentation-ibm-style
```
  - Expected: `DO: fieldkit pipeline run gorilla-documentation-ibm-style --only plan` on a fresh state, later `BLOCKED` at `fill` with the files to write, finally `DONE`.
**Step 2:** Run the printed command, then ask `fieldkit next gorilla-documentation-ibm-style` again.
  - Expected: The next stage.

**After this task:** State in `state/gorilla-documentation-ibm-style/`.

## Troubleshooting

**Symptom:** Every number in a document is flagged.
**Cause:** `MEASUREMENTS.md` missing or the process cached an old corpus (`_CORPUS`).
**Remedy:** Restore `docs/dual-track/MEASUREMENTS.md`; restart the process.
**Verify:** `fieldkit docs check` on that group.

**Symptom:** A developer document reports `section missing: Troubleshooting` although the heading is there.
**Cause:** A code fence opened on the same line as a step label, so everything after it parses as code; the fence finding is listed too.
**Remedy:** Put `Run:` and a blank line before the fence in the step's `action`.
**Verify:** No `code fence must start its own line` finding.

**Symptom:** `fill` reports `older than its prep file` right after prep.
**Cause:** prep re-ran (sources or `WRITER_BRIEF.md` changed); mtimes order the files.
**Remedy:** Review the JSON against the new prep file, then save it.
**Verify:** `fieldkit docs fill <group>` shows `OK`.

**Symptom:** A group is stale on another machine but fresh here.
**Cause:** The files differ in content (hashes ignore CRLF versus LF only).
**Remedy:** Compare the files; `git status` on both machines.
**Verify:** `fieldkit docs plan` lists the changed files.

**Symptom:** `then_run` in a prep file still says `--validate`.
**Cause:** The prep file was written by `dual_track.py` directly, not through `fieldkit docs prep`.
**Remedy:** Run `fieldkit docs prep <group> --force`.
**Verify:** `then_run` reads `fieldkit docs render <group>`.

## Technical Debt

🟠 **MEDIUM** — The number rule accepts any digit group found anywhere in `fieldkit/` or `tests/`, not only in the group's own files. → Restrict to the group's sources plus modules they import, and measure the false-positive cost on the existing docs first.
🟠 **MEDIUM** — `WRITER_BRIEF.md` is not listed in `pyproject.toml` package-data, so a non-editable install lacks it and `prep` fails reading it. → Add `gdocs/*.md` to `[tool.setuptools.package-data]`.
🟡 **LOW** — `install_skills.py` copies only `fieldkit-*` skill folders, so `skills/gorilla-documentation-ibm-style` is not installed by it. → Rename the skill to a `fieldkit-` prefix or widen the filter.
🟡 **LOW** — Section parsing depends on the exact headings `dual_track.py` renders. → Pin a test against a rendered sample when dual_track changes; a heading change fails closed (section missing).
🟡 **LOW** — `check_command` checks names only, not argument counts or values. → Optionally attempt a full `parse_args` for code-block commands without placeholders.
🟡 **LOW** — The workflow imports private names from `dual_track.py` (`_CODE_VALIDATION`, `_extract_json`). → Ask upstream for a public API.
🟡 **LOW** — Untested on Linux in this form. → Run `tests/test_gdocs.py` on Linux.

## Impact If Removed

`fieldkit docs` disappears from the command line (the `register` call in `fieldkit/cli.py` fails at import, which breaks every `fieldkit` command until the two lines are removed), the `gorilla-documentation-ibm-style` pipeline fails at its first stage, `tests/test_gdocs.py` fails at import, and documentation returns to hand-copied staging folders with no coverage, staleness or Gorilla checks.

## Claim Sources

| Claim | Basis | Evidence |
|-------|-------|----------|
| Flat staging exists because dual_track skips build folders | 📄 stated in input | dual_track.py skips any folder named build (its IGNORE_DIRS) |
| then_run is rewritten because render rejects --validate | 📄 stated in input | dual_track writes `--validate`, which render does not accept |
| Prep files carry no home folder | 📄 stated in input | no home folder ends up in a working file |
| A failed render keeps the group stale | 📄 stated in input | a failed render never records fresh hashes |
| Group names are restricted because stage() deletes the folder | 📄 stated in input | the name becomes a folder that stage() deletes and recreates: never a path |
| Hashes read CRLF as LF | 📄 stated in input | Of the content with CRLF read as LF |
| dual_track calls time out after 600 seconds | 📄 stated in input | timeout=600 |
| Stale groups warn unless --strict | 📄 stated in input | check: a stale group is a failure (release gate) |
| Checks are structural and cannot judge truth | 🤖 model inference | *(none — model judgment)* |
| Removing the package breaks every fieldkit command until the register lines go | 🤖 model inference | *(none — model judgment)* |
| Number rule may accept invented numbers present elsewhere | 🤖 model inference | *(none — model judgment)* |
| Non-editable installs lack WRITER_BRIEF.md | 🤖 model inference | *(none — model judgment)* |
| The guide is a separate file so that editing it does not make every group stale | 📄 stated in input | the brief's hash is part |
| `docs guide` works outside a Fieldkit checkout that has no groups file | 🤖 model inference | *(none — model judgment)* |
| compose refuses a failing page instead of writing it | 📄 stated in input | Nothing is written: a page that fails is not left lying about |
| The checks are textual and do not assess the quality of the opening | 📄 stated in input | Judge whether the opening is well written |
| Matching the track by normalised text means any edit to the track on the page is reported as missing | 🤖 model inference | *(none — model judgment)* |


---
**How to verify this document:**
`📄 stated in input` — the model's phrasing of something your source text said.
Find the matching line in the original to verify.
`🤖 model inference` — the model's own judgment or synthesis. Treat as opinion,
not measurement. Re-run on the same input and check whether specific numbers
stay consistent between runs.

*Auto-generated DITA-structured developer documentation.*