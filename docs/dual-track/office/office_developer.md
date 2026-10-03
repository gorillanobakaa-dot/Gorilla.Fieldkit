# `fieldkit.office`: offline read, create, check, scrub and deliver for OOXML and PDF

> Generated 2026-10-02 | Source: `office`

---

## Purpose

`fieldkit.office` gives an agent or a person deterministic, offline operations on Word, Excel, PowerPoint and PDF files, so that the agent writes data and Fieldkit writes, reads and validates the file. It has five modules: `read.py` (document to Markdown-ish text), `create.py` (JSON/YAML spec to file, written to a temporary sibling, self-checked, then atomically moved into place), `check.py` (structural validation of OOXML packages and PDFs), `scrub.py` (removal of personal names from OOXML properties and review marks; inspection, not cleaning, of PDF Info and XMP metadata; backups outside the document's folder; a `restore()` helper; detection of stale backups and lock files beside a document) and `deliver.py` (pipeline stages behind `fieldkit office deliver` and `pipelines/office-deliver.yaml`). `__init__.py` is empty. The security fixes of 2026-10-02 changed `create.py`, `deliver.py`, `read.py` and `scrub.py`; the hole list they close is the docstring of `tests/test_office_security.py`. The 598-line size in MEASUREMENTS.md predates those fixes; the current size is not measured. Trust level: it handles untrusted input files and rewrites user documents in place (`scrub`), so treat it as data-modifying code; every in-place write now goes through write-to-temp, check, `os.replace`, and (for `scrub`) a post-swap re-check with byte-for-byte rollback. It makes no network calls in the built-in paths ("Nothing is uploaded anywhere.").

## Known Alternatives Considered

`check.py` documents one rejected alternative: XSD validators from another vendor's document skills, "under a licence that forbids copying, so nothing of theirs is used here". `read.py` names two optional engines that remain selectable but are not the default: Microsoft's markitdown and IBM's Docling (`engine="markitdown" / "docling"`); "the built-in engine needs neither and is what the tests pin down". For PDF text, `read.py` prefers Poppler's `pdftotext` when `find_tool` locates it and falls back to `pypdfium2`. `scrub.py` states its model: Word's own "Remove Personal Information". Two further rejections are documented in today's source. (1) Cleaning PDF metadata in place: `PDF_CANNOT_SCRUB` says that with pypdfium2 and reportlab "the only way to change them is an incremental update, which leaves the old values in the file", so `scrub()` raises `ValueError` for `.pdf` and `stage_scrub` fails with re-export instructions instead. (2) A `.bak` sibling: the module docstring rejects it because "a .bak beside the cleaned file still holds every removed name"; backups moved to `backup_root()`. No other alternatives are documented: Not available in the source material.

## Architecture

- **Pattern:** Stateless function library behind an `argparse` CLI (`fieldkit office <action>`), plus stage functions run by Fieldkit's YAML pipeline engine (`office-deliver`: `check` -> `scrub` verified by `recheck` -> `privacy`). Dispatch by file suffix (`MAIN_PART`, `WRITERS`, `SUPPORTED`, `OFFICE_SUFFIXES`; `.pdf` short-circuits to `inspect_pdf` in `inspect`). Writes follow one pattern in both `create` and `scrub`: `tempfile.mkstemp(prefix=".fieldkit-create-" | ".fieldkit-scrub-", dir=<target folder>)`, write, validate the temporary file, re-test the precondition, `os.replace`, and unlink the temporary in a `finally`. `scrub` additionally keeps the original bytes in memory and re-validates the file in place after the swap (`_left_over`), writing the original back on failure.
- **Trust boundary:** Trusts: the spec passed to `create` (block text is escaped for ReportLab with `xml.sax.saxutils.escape`), the private-terms list from `fieldkit.local.json`, `pdftotext` found on `PATH` or a known location, the `FIELDKIT_OFFICE_BACKUPS` environment variable (overrides `backup_root()`; documented for tests), and the `original.json` beside a backup, whose `original` path `restore(backup)` writes to without validation. Does not trust: input documents. `check.py` parses XML with `resolve_entities=False, no_network=True, huge_tree=False`, flags absolute or `..` part names, and treats any library exception as a finding. `scrub.py` never extracts members to disk; it rewrites the ZIP with the original member names, and `_left_over` treats a check or re-inspection that raises as a failed check. `inspect_pdf` reads the Info dictionary through pypdfium2 and searches the raw bytes plus every stream decoded by `privacy.pdf_streams` for `<x:xmpmeta>` blocks with regular expressions.
- **Attack surface:** Any document passed to `read`, `check`, `scrub` or `deliver`: ZIP container parsing, XML parsing (lxml directly in `check.py`; python-docx, openpyxl and python-pptx internally; regular expressions in `scrub`), PDF parsing (pypdfium2, pdftotext, and the stdlib stream decoders in `privacy.pdf_streams` that `inspect_pdf` and `stage_privacy` drive). Spec files passed to `create`. The document's parent directory: `stale_backups` lists it with `iterdir()`, and both writers create temporary siblings there. The backup directory and its `original.json` (a tampered `original.json` redirects `restore()`; inference, untested). The `--engine` choice loads markitdown or Docling if installed. No listening sockets.
- **Dependencies:** `lxml`, `python-docx (docx)`, `openpyxl`, `python-pptx (pptx)`, `reportlab`, `pypdfium2 (read, check, and now scrub.inspect_pdf)`, `zipfile`, `posixpath`, `subprocess`, `tempfile`, `shutil`, `os`, `re`, `json (backup original.json)`, `html (XMP value unescaping)`, `time (backup folder stamp)`, `fieldkit.core.host.find_tool`, `fieldkit.core.privacy (private_terms, scan_path, pdf_streams)`, `fieldkit.core.settings.ROOT (backup_root default)`, `fieldkit.core.pipeline.Pipeline (via cli.py)`, `optional: markitdown`, `optional: docling`, `optional external program: pdftotext (Poppler)`

## Flags & Configuration

| Name | Type | Default | Effect | Notes |
|------|------|---------|--------|-------|
| `action` | `string` | `required` | One of `read`, `create`, `check`, `scrub`, `deliver`. | Positional. `create` takes exactly `SPEC OUT` as the two `files`. |
| `files` | `string (1..n)` | `required` | Target files. | `read` uses only `files[0]`; extra files are ignored silently. |
| `--engine` | `string` | `builtin` | `read` engine: `builtin`, `markitdown` or `docling`. | Optional engines need the `readers` extra; they are not covered by tests. |
| `--out` | `string` | `None` | `read` writes the text to this path (UTF-8) and prints `{file, out, chars}`. | Overwrites without prompting (unlike `create`, `read --out` has no `--force` guard). |
| `--force` | `bool` | `false` | `create`: allow replacing an existing `OUT`. | Without it `create` raises `FileExistsError` before writing and again immediately before `os.replace` if `OUT` appeared meanwhile. Even with it, a failed self-check leaves the old file untouched. |
| `--check` | `bool` | `false` | `scrub` reports findings via `inspect()` and changes nothing; works on `.pdf` too (Info and XMP). | Exit code 3 when anything is found. |
| `--term` | `string (1..n, repeatable)` | `[]` | Extra words to hunt, added to `privacy.private_terms()`. | `action="extend", nargs="+"`: place it after the file names or it swallows them; repeat `--term A --term B` to accumulate. |
| `--no-backup` | `bool` | `false` | `scrub` and `deliver` keep no backup. | `deliver` passes it to the pipeline as `no_backup="1"`; `stage_scrub` reads it via `_no_backup(ctx)` (`1/true/yes/on`, case-insensitive). The in-memory rollback in `scrub` still applies. |
| `--json` | `bool` | `false` | Machine-readable output for every action. | `scrub` and `create` print JSON even without it. |
| `file (pipeline var)` | `string` | `""` | Target of `fieldkit pipeline run office-deliver --var file=PATH`. | Empty value raises `ValueError("no file given: --var file=PATH")`. |
| `no_backup (pipeline var)` | `string` | `""` | `--var no_backup=1` makes `stage_scrub` call `scrub(..., backup=False)`. | Documented in the `deliver.py` docstring usage line. |
| `FIELDKIT_OFFICE_BACKUPS (env)` | `path` | `unset` | Overrides `backup_root()`; default is `settings.ROOT / "state" / "office-backups"`. | Used by the tests to isolate backups. |
| `privacy.terms (`fieldkit.local.json`)` | `list[string]` | `[]` | Words that make `scrub` clear title/subject/keywords/description/category and custom properties, that make PDF Title/Subject/Keywords/Creator/Producer and the conditional XMP fields count as findings, and that the privacy stage flags. | `private_terms()` drops terms of two characters or fewer. |

## API Surface

| Symbol | Description | Side Effects |
|--------|-------------|--------------|
| `read.read()` | Document text; Markdown for Office files (tables via `_md_table`), `pdftotext -layout` or pypdfium2 text for PDF. | Runs `pdftotext` as a subprocess when found. |
| `read._md_table()` | One Markdown table as one `\n`-joined block; pads ragged rows to the widest. Callers escape `|` (`_docx` and, since today, `_xlsx`). | None. |
| `create.create()` | Validates type against suffix and existence, writes via `WRITERS[kind]` to a `.fieldkit-create-*` temporary, checks it, `os.replace`s it to `out`; returns the check report with `file` set to `out`. | Creates parent folders; replaces `out` only with `force`; raises on a failed self-check. |
| `check.check()` | Dispatches to `check_pdf` or `check_office` by suffix. | None (read-only). |
| `check.check_office()` | ZIP, part names, required parts, XML well-formedness, internal relationships, main part, library round trip, xlsx formula cache. | None. |
| `check.check_pdf()` | `%PDF-` header, `%%EOF` in last 2048 bytes, opens, page count, text in first five pages. | Reads the whole file into memory. |
| `scrub.inspect()` | Lists `(where, field, value)` for identifying properties and review-mark authors; delegates `.pdf` to `inspect_pdf`. | None. |
| `scrub.inspect_pdf()` | Info dictionary: `Author` always, `Title/Subject/Keywords/Creator/Producer` when named. XMP: `dc:creator`, `pdf:Author` always; `dc:title`, `dc:subject`, `dc:description`, `pdf:Keywords`, `pdf:Producer`, `xmp:CreatorTool`, `dc:rights`, `xmpRights:Owner`, `photoshop:AuthorsPosition` when named. Where values are `"pdf properties"` or `"pdf xmp"`. | Reads the whole file; decodes streams via `privacy.pdf_streams`. |
| `scrub.scrub()` | `_rewrite` to a `.fieldkit-scrub-*` temporary, `_left_over` on it, `_backup`, `os.replace`, `_left_over` on the file in place, rollback from memory on failure. | Backup under `backup_root()`; temporary sibling (always removed); replaces the original. |
| `scrub.backup_root()` | `$FIELDKIT_OFFICE_BACKUPS` or `settings.ROOT/state/office-backups`. | None. |
| `scrub._backup()` | Copies to `<Fieldkit>/state/office-backups/<YYYYmmdd-HHMMSS>[-n]/``<name>` (a `-n` suffix on same-second collisions, at most 1000 attempts) and writes `original.json` with the resolved source path. | Creates the folder; `shutil.copy2`; raises `RuntimeError` if no free folder name. |
| `scrub.restore()` | Copies a backup over `target`, or over the path in `original.json`, via a `.fieldkit-restore-*` temporary and `os.replace`. | Overwrites the target. No CLI entry point; only the tests call it. |
| `scrub.stale_backups()` | Siblings named `<name>.bak*`, `<stem>.bak*`, `<stem>.wbk`, `Backup of <stem>*`, `.fieldkit-scrub-*`, or Office lock files `~$` whose tail matches the name (case-insensitive). | Lists the parent folder; returns `[]` on `OSError`. |
| `deliver.stage_check()` | Pipeline wrapper over `check.check`; detail lists up to three problems. | None. |
| `deliver.stage_scrub()` | Office: `scrub` with `privacy.private_terms()` and `backup=not _no_backup(ctx)`; detail names the backup path or says "no backup kept (no_backup)". PDF: `inspect` only; fails on any finding. | Rewrites Office files; writes a backup unless `no_backup`. |
| `deliver.stage_recheck()` | Verify step for scrub: still sound and `inspect` finds nothing, now for PDFs as well. | None. |
| `deliver.stage_privacy()` | `privacy.scan_path` over every ZIP member (or the PDF's raw bytes, decoded streams and text layer) plus `scr.stale_backups(f)`. | Lists the document's folder. |

## Kill Switches

### `create.create() preconditions`
- **Condition:** spec `type` differs from the output suffix, or `OUT` exists without `force`
- **Effect:** `ValueError("<name>: spec type is ... but the file name ends ...")` or `FileExistsError("... refusing to overwrite it (use --force to replace it)")` before any file is created.
- reversible
- The existence test is repeated after the self-check, immediately before `os.replace` (a narrowed, not closed, race window).

### `create.create() self-check`
- **Condition:** `_check.check(tmp)` returns any problem
- **Effect:** Raises `RuntimeError("<name> failed its own check and was not written: ...")`; the temporary is unlinked in `finally`.
- reversible
- An existing `OUT` (with `force`) is untouched (`test_create_failed_check_with_force_keeps_the_old_file`).

### `scrub.scrub() pre-swap check`
- **Condition:** `_left_over(tmp, terms)` reports structural problems or remaining names
- **Effect:** `RuntimeError("... the cleaned copy failed its check, the file was not changed: ...")`; no backup is taken (the backup happens after this check).
- reversible
- Exceptions from `check`/`inspect` inside `_left_over` are converted into problems, so they also stop the swap.

### `scrub.scrub() post-swap check`
- **Condition:** `_left_over(path, terms)` fails on the file now in place
- **Effect:** `path.write_bytes(original)` restores the pre-scrub bytes, then `RuntimeError("... the original was put back ...")`.
- reversible
- Works with `backup=False` too, because `original` is held in memory.

### `scrub.scrub() on `.pdf``
- **Condition:** suffix `.pdf`
- **Effect:** `ValueError(PDF_CANNOT_SCRUB)`; nothing is written.
- reversible
- Checked before the `OFFICE_SUFFIXES` test, so the message explains why.

### `scrub.scrub() no findings`
- **Condition:** `inspect()` finds nothing
- **Effect:** Returns `{"removed": [], "backup": None}` without rewriting the file or creating a backup.
- reversible
- Keeps clean files byte-identical.

### `deliver.stage_scrub() for PDF`
- **Condition:** `scr.inspect(f, terms)` finds anything (Author is always a finding)
- **Effect:** `ok: False` with the field names and `PDF_CANNOT_SCRUB`; the pipeline stops at `scrub`.
- reversible
- A clean PDF passes with a detail naming the fields inspected.

### `deliver.stage_privacy()`
- **Condition:** any `privacy.scan_path` finding, including `not-scanned`, or any `scr.stale_backups(f)` hit
- **Effect:** `ok: False`; detail lists finding kinds, the unreadable parts and the stale neighbours.
- reversible
- Fail closed: `not-scanned` covers members over `TEXT_LIMIT`, unreadable files, corrupt members and PDF streams with filters `pdf_streams` cannot undo (from `fieldkit/core/privacy.py`).

### `office-deliver pipeline`
- **Condition:** A stage returns `ok: False` or its verify fails
- **Effect:** The pipeline stops; later stages do not run. A broken file is never scrubbed (`test_broken_file_is_not_touched`).
- reversible
- `cmd_office` runs with `force=True`, so every invocation re-runs all stages instead of trusting saved state.

## Dead Code

- **`office/__init__.py`** — Empty file; it exists only to make the folder a package. (risk: Removing it breaks `from fieldkit.office import ...` on tooling that needs a regular package.)
- **`scrub.restore()`** — Not dead (the tests call it), but unreachable from the CLI, the pipeline or the skill; users restore by copying files by hand. (risk: Removing it removes the only programmatic undo and breaks `test_scrub_backup_is_kept_outside_the_documents_folder_and_restores`.)

## Performance

- **CPU:** Not measured. Today's changes add work per file: `scrub` runs `check.check` and `inspect` twice (`_left_over` before and after the swap); `inspect_pdf` and `stage_privacy` decode PDF streams and extract the full text layer.
- **MEMORY:** Not measured. Observations from the source: `check_office` loads an xlsx twice with full (not `read_only`) openpyxl workbooks; `check_pdf`, `inspect_pdf` and the privacy scan read the whole PDF into memory; `scrub` now also holds the whole original (`path.read_bytes()`) for rollback; `check_office` and `_rewrite` read each XML part fully with `z.read`.
- **IO:** Not measured. `scrub` writes a full temporary rewrite and, unless disabled, a full backup copy plus `original.json` under `backup_root()`; nothing prunes that folder. `create` writes once to a temporary and renames. `deliver` writes pipeline state under `state/office-deliver/<sha256[:12] of the resolved path>` and lists the document's folder.
- **NOTES:** The full Fieldkit suite (all groups) ran 584 passed, 1 skipped, 2 xfailed in 194.89 s on 2026-10-02; whether that run included `tests/test_office_security.py` is not available in the source material, and per-group timing is not measured.

## Security

- **Remote execution:** No network access in built-in paths. `subprocess.run` calls `pdftotext` with an argument list (no shell). Optional engines import third-party packages whose network behaviour is not described in the source.
- **Data handling:** `scrub` clears `dc:creator`, `cp:lastModifiedBy`, `Company`, `Manager` always; `dc:title`, `dc:subject`, `cp:keywords`, `dc:description`, `cp:category` and custom properties only when they contain a term; replaces `w:author`, `w:initials`, Excel `<author>`, `displayName` and `p:cmAuthor name` values. Body text is never changed. Backups, which keep every removed name, live under `backup_root()` with an `original.json` holding the absolute source path; `state/` is git-ignored in the Fieldkit repository, but the folder is never pruned. PDF metadata is reported, never modified.
- **Attack surface:** XXE and external entity fetches are disabled in `check.py`'s own parser. No ZIP size or ratio limits exist in `check_office`, `_rewrite` or `_left_over` (zip-bomb exposure: inference, untested); the privacy stage alone bounds member size via `TEXT_LIMIT` and reports oversize members instead of skipping them. Part-name traversal is reported, not exploited, because nothing is extracted. Temporary files are created with `mkstemp` in the target folder, so a crash can leave a `.fieldkit-*` sibling; `stale_backups` flags a leftover `.fieldkit-scrub-*` on the next `deliver`.
- **Notes:** Closed on 2026-10-02 and pinned by `tests/test_office_security.py`: backups beside the document; silent skips of oversize, unreadable or corrupt parts (now `not-scanned`); invisible text in Flate/ASCIIHex/ASCII85 PDF streams; uninspected PDF Info/XMP; unverified `scrub` output; silent `create` overwrite and leftover bad files; `deliver` ignoring `no_backup`; broken Markdown tables in `read`. Still open: PDF metadata cannot be cleaned; a PDF or part over `TEXT_LIMIT` can never pass `deliver`; regex-based XML rewriting in `scrub`.

## Error Conditions

| Error | Cause | Remedy |
|-------|-------|--------|
| `not a ZIP package (corrupt, or not really an Office file)` | `zipfile.BadZipFile` in `check_office`. | Re-save the document from its Office application. |
| `malformed XML in <part>: <error>` | An `.xml` or `.rels` part fails lxml parsing. | Re-save the file; find the producer that wrote the broken part. |
| `<rels>: relationship <Id> points at missing part <path>` | An internal relationship target is absent from the package. | Re-save from Office, or restore the missing part. |
| `no %%EOF marker near the end (truncated?)` | `%%EOF` absent from the last 2048 bytes of a PDF. | Re-download or re-export the PDF. |
| `<name>: unsupported type <ext>; supported: ...` | `read` given a suffix outside `SUPPORTED` (includes `.docm`, `.pptm`). | Convert, or use `--engine markitdown`/`docling` if installed. |
| `type must be one of ['docx', 'pdf', 'pptx', 'xlsx'], got ...` | `create` spec `type` (or output suffix) not in `WRITERS`. | Set `type` to a supported value. |
| `<name>: spec type is '<kind>' but the file name ends .<ext>; name the output .<kind> or change the spec's type` | `create` spec `type` and output suffix disagree; nothing written. | Align them, or omit `type` so the suffix decides. |
| `FileExistsError: <out> already exists; refusing to overwrite it (use --force to replace it)` | `create` without `force` on an existing path. `cli.main` does not catch `FileExistsError`, so the CLI shows a traceback. | Choose another path or pass `--force`. |
| `<name> failed its own check and was not written: [...]` | Self-check failure of the temporary file. | Read the listed problems; fix the spec or writer. |
| `unknown docx block: ... / unknown pdf block: ...` | A block key the writer does not handle; PDF has no `table`, `numbered` or `page_break`. | Use only the documented block shapes. |
| `<name>: the cleaned copy failed its check, the file was not changed: ...` | `_left_over` on the temporary found problems or remaining names. | Inspect with `scrub --check`; a name in an unhandled location needs a code change. |
| `<name>: failed its check after cleaning (...); the original was put back` | Post-swap `_left_over` failed on the file in place. | Investigate the listed problem; the file is as before. |
| `PDF properties cannot be cleaned by Fieldkit: ...` | `scrub()` on a `.pdf` (`ValueError`), or the `deliver` scrub stage on a PDF with findings. | Re-export the PDF with author and title blank; rerun `deliver`. |
| `<name>: only .docx, .pptx, .xlsx, .docm, .pptm, .xlsm` | `scrub` called on a suffix that is neither OOXML nor PDF. | Convert the file first. |
| `stale backup or lock file next to it (...): <names>` | `stale_backups` matched a sibling. | Close Office, delete or move the sibling, rerun. |
| `<n> part(s) could not be read, so cannot be called clean: ...` | `not-scanned` findings from `scan_path`. | Shrink or re-save the file; a part over `TEXT_LIMIT` cannot pass. |
| `could not make a backup folder under <root>` | 1000 same-second folder names already taken. | Retry; inspect `backup_root()`. |
| `<dir>/original.json missing: say where to restore <name> to` | `restore(backup)` without `target` and no `original.json`. | Pass `target`. |
| `no file given: --var file=PATH` | `deliver` stage called with no `file` var. | Pass `--var file=PATH`. |
| `exit 3` | `check` found problems, `scrub --check` found names, or `deliver` was not safe. | Read the printed problems or stage detail. |

## Tasks

### Run the office tests

Before you change any module in `fieldkit/office/` or rely on it on a new machine.

**Prerequisites:**
- Fieldkit installed with `python -m pip install -e ".[test]"`
- A shell in the Fieldkit repository root

**Step 1:** Run the three office test files:

```bash
python -m pytest tests/test_office.py tests/test_deliver.py tests/test_office_security.py -q
```

  - Expected: All tests pass. `test_pdf_read_via_poppler` is skipped when `pdftotext` is not on `PATH`. The security file sets `FIELDKIT_OFFICE_BACKUPS` to a temporary folder, so no backups land in the real `state/`.
**Step 2:** Run the full suite before a release:

```bash
python -m pytest tests
```

  - Expected: The last recorded result is 584 passed, 1 skipped, 2 xfailed in 194.89 s (2026-10-02); a run after today's fixes has its own counts (not measured here).

**After this task:** Round trips for docx/xlsx/pptx/pdf, byte-identical PDF output, broken-file detection, scrub of properties and review authors, the deliver scenarios, and the eight security properties listed in the `test_office_security.py` docstring are confirmed on this machine.

### Create and verify a document from a spec

When an agent must produce a document: it writes the spec, Fieldkit writes and checks the file.

**Prerequisites:**
- A spec file, for example `report.json` with `{"type": "docx", "title": "Field report", "blocks": [{"heading": "Findings", "level": 1}, {"paragraph": "The pump failed at 14:00."}]}`
- `report.docx` does not exist yet (or you intend `--force`)

**Step 1:** Create the file:

```bash
fieldkit office create report.json report.docx --json
```

  - Expected: JSON with `"problems": []`, `info` counts and `file` naming the output. Exit 0. On a second run without `--force`: a `FileExistsError` traceback and the file unchanged.
**Step 2:** Read it back:

```bash
fieldkit office read report.docx
```

  - Expected: Contains `# Findings` and `The pump failed at 14:00.`
**Step 3:** Inspect its properties:

```bash
fieldkit office scrub report.docx --check
```

  - Expected: No `creator` or `lastModifiedBy` entry, because `create` writes them empty unless the spec sets `author`.
**Step 4:** Replace it deliberately:

```bash
fieldkit office create report.json report.docx --force
```

  - Expected: Same report as step 1; no `.fieldkit-create-*` file remains in the folder.

**After this task:** A checked document with empty author fields and no temporary siblings.

### Make a file safe to send

After the last save of a document that leaves the machine.

**Prerequisites:**
- `privacy.terms` set in `fieldkit.local.json` or terms known to pass with `--term`
- The file is closed in Office (an open file leaves a `~$` lock file that fails `privacy`)
- No `.bak`, `.wbk` or `Backup of` copies of it in the same folder

**Step 1:** Run the pipeline:

```bash
fieldkit office deliver report.docx
```

  - Expected: `SAFE TO SEND` and three stage lines; the `scrub` detail ends "backup kept outside the document's folder: <path>"; exit 0. `NOT SAFE` and exit 3 otherwise, with the failing stage's detail.
**Step 2:** Confirm nothing is left:

```bash
fieldkit office scrub report.docx --check
```

  - Expected: Empty `found` list.
**Step 3:** For a PDF, inspect first:

```bash
fieldkit office scrub report.pdf --check
```

then deliver it:

```bash
fieldkit office deliver report.pdf
```

  - Expected: With an Author set: `NOT SAFE` at `scrub` with `PDF properties hold names: Author` and the `PDF_CANNOT_SCRUB` text. Without: `SAFE TO SEND` if the privacy stage is clean.
**Step 4:** Prune old backups under `state/office-backups/` when they are no longer needed, or run with `--no-backup`:

```bash
fieldkit office deliver report.docx --no-backup
```

  - Expected: The `scrub` detail says "no backup kept (no_backup)" when items were removed.

**After this task:** The file opens, has no names in its properties or review marks, the privacy scan read every part and found nothing, and no stale copy sits beside it.

### Add a new block type to a writer

When a spec needs a block that `_docx` or `_pdf` rejects with `unknown ... block`.

**Prerequisites:**
- The office tests pass

**Step 1:** Add an `elif` branch in `_docx` or `_pdf` in `fieldkit/office/create.py` before the final `else: raise ValueError`. Writers receive the temporary path, not `out`, so never derive anything from the path's name.
  - Expected: The new key is handled.
**Step 2:** If `read.py` should show it, extend `_docx` (style-name mapping) or the PDF path in `fieldkit/office/read.py`; route tabular output through `_md_table`.
  - Expected: The block reads back.
**Step 3:** Add a case to `test_create_check_read_round_trip` in `tests/test_office.py`, then run

```bash
python -m pytest tests/test_office.py -q
```

  - Expected: The new parametrised case passes.

**After this task:** The block is created, self-checked and covered by a round-trip test.

## Troubleshooting

**Symptom:** `create` prints a traceback ending `FileExistsError: ... refusing to overwrite it (use --force to replace it)`.
**Cause:** `OUT` exists; `cli.main` catches `ValueError`/`RuntimeError`/`FileNotFoundError` but not `FileExistsError`.
**Remedy:** Pass `--force` or choose a new path; consider catching `FileExistsError` in `cli.main` for a one-line message.
**Verify:** `fieldkit office check OUT` prints `OK` after a forced create.

**Symptom:** `check` reports `formulas_without_cached_value` greater than zero on a file from `create`.
**Cause:** openpyxl writes formulas without computing them (`test_xlsx_flags_formulas_without_cached_values`).
**Remedy:** Expected for generated files; open and save in Excel, or recalculate with another tool, if non-Excel readers need values.
**Verify:** Re-run `check`; the count is zero after a recalculating save.

**Symptom:** `deliver` fails at `privacy` with `stale backup or lock file next to it` on a folder that looks clean.
**Cause:** Hidden `~$` lock file from an open Office session, or a `.bak` left by the pre-2026-10-02 scrub.
**Remedy:** Close Office; show hidden files; delete or move the named sibling.
**Verify:** `deliver` passes the `privacy` stage.

**Symptom:** `deliver` fails at `privacy` with `part(s) could not be read`.
**Cause:** A member or the PDF exceeds `TEXT_LIMIT`, is corrupt, or uses a PDF filter without a decoder (for example `LZWDecode`).
**Remedy:** Re-save to shrink or re-encode; there is no override by design.
**Verify:** `fieldkit privacy scan FILE` exits 0.

**Symptom:** `scrub` raises `the cleaned copy failed its check` with `<where> <field> still set`.
**Cause:** A name sits where `_rewrite` does not reach, for example a single-quoted attribute that the regular expressions do not match (inference).
**Remedy:** Extend `_rewrite`/`_anonymise` (preferably with lxml) and add a fixture.
**Verify:** `scrub --check` returns an empty list after scrubbing.

**Symptom:** `PermissionError` during `scrub` on Windows.
**Cause:** The document is open in Office, so `os.replace` cannot replace it (inference; not handled in source).
**Remedy:** Close the file and re-run.
**Verify:** `scrub --check` returns an empty list.

## Technical Debt

🟠 **MEDIUM** — PDF Info and XMP metadata are inspected but cannot be cleaned; any PDF with an Author is undeliverable. → Add a full (non-incremental) PDF rewrite with a library that can drop Info/XMP, re-verify with `inspect_pdf`, and cover it in `test_office_security.py`.
🟠 **MEDIUM** — Backups under `backup_root()` are never pruned and keep every removed name; `restore()` has no CLI. → Add a `restore` action for `fieldkit office` (it does not exist yet) and a retention option (age or count) with a test.
🟡 **LOW** — `FileExistsError` from `create` escapes `cli.main` as a traceback. → Add `FileExistsError` to the caught exceptions in `cli.main`.
🟡 **LOW** — `restore()` trusts `original.json` and overwrites whatever path it names. → Refuse a target outside the folder recorded at backup time, or require an explicit `target` when `original.json` and the backup's name disagree.
🟠 **MEDIUM** — `scrub` uses regular expressions over decoded XML instead of an XML parser. → Rewrite with lxml and the same `_PARSER` settings as `check.py`; the pre- and post-swap checks now catch misses but do not fix them.
🟡 **LOW** — Files or members over `TEXT_LIMIT` can never pass `deliver`. → Stream-scan large members in chunks instead of refusing them, keeping the fail-closed default.
🟡 **LOW** — No ZIP size or compression-ratio limits in `check_office`, `_rewrite` and `_left_over`. → Reject members above a configured uncompressed size before `z.read`.
🟡 **LOW** — `read` supports `.xlsm` but not `.docm` or `.pptm`, unlike `check` and `scrub`. → Add `.docm` and `.pptm` to `SUPPORTED` and the dispatch, with tests.
🟡 **LOW** — Determinism is claimed for all formats in the `create.py` docstring but tested only for PDF. → Add byte- or content-equality tests for docx, xlsx and pptx, or narrow the docstring.
🟡 **LOW** — The overwrite guard in `create` is check-then-replace, not exclusive creation; a file created between the second existence test and `os.replace` is overwritten. → Without `force`, link or rename with an exclusive primitive (for example `os.link` then unlink) so an existing target makes the call fail atomically.

## Impact If Removed

`fieldkit office` (all five actions) fails at import in `cmd_office`; the `office-deliver` pipeline cannot resolve `fieldkit.office.deliver:stage_*`; `tests/test_office.py`, `tests/test_deliver.py` and `tests/test_office_security.py` fail; the `fieldkit-office` skill documents commands that no longer exist. Agents lose the only offline path in Fieldkit to produce and validate Office or PDF files, strip author metadata, detect names in PDF metadata, and refuse delivery when backups or lock files sit beside a document. Existing backups under `state/office-backups/` stay on disk but nothing in Fieldkit can restore them.

## Claim Sources

| Claim | Basis | Evidence |
|-------|-------|----------|
| Built-in paths upload nothing | 📄 stated in input | Nothing is uploaded anywhere. |
| XSD validators rejected for licence reasons | 📄 stated in input | forbids copying, so nothing of theirs is used here |
| Tests cover the built-in reader only | 📄 stated in input | what the tests pin down |
| create never overwrites without force | 📄 stated in input | An existing `out` is never overwritten unless force=True. |
| create refuses a type/suffix mismatch | 📄 stated in input | name the output .{kind} or change the spec's type |
| create leaves nothing behind on a failed check | 📄 stated in input | failed its own check and was not written |
| Author fields empty unless spec sets author | 📄 stated in input | author/last-modified-by are written EMPTY unless the spec sets |
| Scrub mirrors Word's Remove Personal Information | 📄 stated in input | the same thing Word's own "Remove Personal Information" does |
| Scrub must be the last step | 📄 stated in input | Word writes the name back on every save, so scrub is the LAST step. |
| Backups moved out of the document's folder | 📄 stated in input | a .bak beside the cleaned file still holds every removed name. |
| scrub rolls back on a failed post-swap check | 📄 stated in input | on failure the original is put back. |
| PDF metadata cannot be cleaned with the available libraries | 📄 stated in input | which leaves the old values in the file |
| deliver fails on stale backups or lock files | 📄 stated in input | stale backup or lock file next to it |
| deliver fails closed on unread parts | 📄 stated in input | could not be read, so cannot be called clean |
| deliver honours no_backup | 📄 stated in input | no backup kept (no_backup) |
| read emits each table as one block | 📄 stated in input | a blank line between rows ends the table |
| XML parser hardened against entities and network | 📄 stated in input | resolve_entities=False, no_network=True, huge_tree=False |
| PDF output is byte-identical | 📄 stated in input | invariant=1: byte-identical output |
| Suite result | 📄 stated in input | 584 passed, 1 skipped, 2 xfailed in 194.89 s |
| The 598-line size predates today's fixes | 🤖 model inference | *(none — model judgment)* |
| TEXT_LIMIT and not-scanned semantics come from fieldkit/core/privacy.py (outside this group) | 🤖 model inference | *(none — model judgment)* |
| FileExistsError escapes cli.main as a traceback | 🤖 model inference | *(none — model judgment)* |
| restore() trusts original.json | 🤖 model inference | *(none — model judgment)* |
| restore() has no CLI entry point | 🤖 model inference | *(none — model judgment)* |
| Backups are never pruned | 🤖 model inference | *(none — model judgment)* |
| Regex scrub misses single-quoted attributes | 🤖 model inference | *(none — model judgment)* |
| Zip-bomb exposure in check and scrub | 🤖 model inference | *(none — model judgment)* |
| PermissionError when the file is open in Office | 🤖 model inference | *(none — model judgment)* |
| create's overwrite guard has a residual race | 🤖 model inference | *(none — model judgment)* |


---
**How to verify this document:**
`📄 stated in input` — the model's phrasing of something your source text said.
Find the matching line in the original to verify.
`🤖 model inference` — the model's own judgment or synthesis. Treat as opinion,
not measurement. Re-run on the same input and check whether specific numbers
stay consistent between runs.

*Auto-generated DITA-structured developer documentation.*