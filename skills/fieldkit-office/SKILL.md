---
name: fieldkit-office
description: Read, create, check or remove personal metadata from Word (.docx), Excel (.xlsx), PowerPoint (.pptx) and PDF files, offline, with the fieldkit command. Use when a task involves reading an Office/PDF file's content, producing one from data, checking a file is not corrupt before sending it, or removing author names from file properties.
---

# Office documents with fieldkit

Everything runs locally; no file is uploaded. Add `--json` to any command for
machine-readable output.

| Task | Command |
|---|---|
| Read a file as Markdown text | `fieldkit office read FILE [--out text.md]` |
| Create a file from data | `fieldkit office create spec.yaml OUT.docx` |
| Check a file is sound | `fieldkit office check FILE...` (exit 3 = problems) |
| See names in file properties | `fieldkit office scrub FILE --check --term "Name"` |
| Remove them (keeps a .bak) | `fieldkit office scrub FILE --term "Name"` |

## Writing a spec

Write data, not code. One `type` per file:

```yaml
type: docx            # docx | xlsx | pptx | pdf
title: Field report
blocks:
  - {heading: Findings, level: 1}
  - {paragraph: The pump failed at 14:00.}
  - {bullets: [check the seal, order parts]}
  - {table: [[Part, Qty], [seal, 2]]}
```

- xlsx: `sheets: [{name, rows: [[...]], freeze: A2, widths: {A: 20}}]`; a cell
  starting with `=` is a formula.
- pptx: `slides: [{title, bullets: [...], notes}]`.
- The author fields are left empty unless the spec sets `author`.

`create` re-opens and checks every file it writes, and fails if the file is broken.

## What check reports

- A missing or broken part: malformed XML, a relationship pointing at nothing, not a ZIP.
- Excel formulas with no cached value: other programs show these as blank until the
  file is recalculated in Excel or LibreOffice.
- PDF: page count and whether the text can be extracted. A scanned PDF has no text.

## Scrub

Scrub is the last step before sending: Word writes the user's name back on every save.
The document's own text is never changed. Private words can also be kept in
`fieldkit.local.json` → `privacy.terms`, so they need not be typed each time.
