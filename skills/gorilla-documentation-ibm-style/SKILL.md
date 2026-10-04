---
name: gorilla-documentation-ibm-style
description: Write or refresh Fieldkit's dual-track documentation (a layman track for someone who has never opened a terminal and a developer track for auditors) with the fieldkit docs workflow, DualTrackAgent's dual_track.py and the Gorilla writer brief. Use when asked to document a Fieldkit module group, when fieldkit docs check or the gorilla-documentation-ibm-style pipeline fails or reports a stale group, or when a .filled.json must be written.
---

# Gorilla.Documentation.IBM.Style

Every group of `fieldkit/` modules (listed in `docs/groups.yaml`) has two
rendered documents in `docs/dual-track/<group>/`: `<group>_layman.md` and
`<group>_developer.md`, plus `STATE.json` (the source hashes at the last
render). The `.md` files are generated. **Never edit a rendered `.md` by hand**:
change the `.filled.json` and render again, or the next render silently undoes
your edit and the checks never saw it.

## Ask what to do next

```
fieldkit next gorilla-documentation-ibm-style        # DO / BLOCKED / DONE, one thing at a time
fieldkit docs plan                                   # every group: fresh / stale / never, files present
```

A group is **stale** when a source file changed after its last render. Stale
groups are what prep, fill and render work on by default; name groups to choose
(`fieldkit docs prep exam office`).

## The steps

```
fieldkit docs prep [GROUP...]      # stage sources (docs/_staging/), dual_track prep, writer brief added
fieldkit docs fill [GROUP...]      # which .filled.json files are missing or invalid (exit 3 while any are)
fieldkit docs render [GROUP...]    # dual_track render, then the Gorilla checks (exit 3 with every reason)
fieldkit docs check [--strict]     # coverage + checks on the committed docs; --strict: stale fails too
fieldkit docs index                # docs/dual-track/README.md: links, scores, last render, stale flag
fieldkit docs guide                # the long guide to the layman track, for any project: why each rule exists, with examples
```

Or as a pipeline: `fieldkit pipeline run gorilla-documentation-ibm-style --only <stage>`
(stages plan, prep, fill, render, check, index; `--var groups="exam office"`).

## Filling a JSON (the step a model or a person does)

1. Open `docs/dual-track/<group>/<group>_<track>.prep.json`. It holds the
   system prompt, the user prompt with every source file, the JSON schema, the
   field hints, and, at the end of `instructions`, the **Gorilla writer brief**
   (`fieldkit/gdocs/WRITER_BRIEF.md`). Read the whole brief first.
2. Write ONE JSON object matching `json_schema` to the path in
   `write_completion_to` (relative to the Fieldkit folder). No fence, no text
   around it, UTF-8, LF line endings.
3. `fieldkit docs fill <group>` until it says OK for both tracks.
4. `fieldkit docs render <group>`. On FAIL, fix the JSON for each listed reason
   and render again. Do not lower a floor to make a document pass.

The rules that fail most often, all in the brief: British English; layman step
one says how to open PowerShell (press the Windows key, type `PowerShell`,
press Enter); every command in its own code block after a blank line; a
real-world comparison for every key concept; every number from
`docs/dual-track/MEASUREMENTS.md` or the source, otherwise "not measured" next
to it; every `fieldkit` command must exist; "the maintainer", never "the
owner"; no AI assistant names; no home paths, emails or user names; quotes from
the source are verbatim.

## What gets committed

Commit the rendered `.md` files, `PRECHECK.md`, `STATE.json`,
`docs/dual-track/README.md`, `docs/dual-track/MEASUREMENTS.md` and
`docs/groups.yaml`. The `.prep.json`, `.filled.json`, the renderer's
`<group>_<track>.json`, `PRECHECK.json` and `docs/_staging/` stay local (the
repository `.gitignore` says so).

## When checks fail

- `coverage FAILS: orphans [...]`: a new `.py` file under `fieldkit/` belongs
  to no group. Add it to the right group's `sources` in `docs/groups.yaml`; the
  group becomes stale and needs prep, fill and render.
- `number N is in neither MEASUREMENTS.md nor the group's source`: cite the
  measurement, or write "not measured" beside the number.
- `command does not parse`: the command is wrong; check `fieldkit --help` and
  `fieldkit <subcommand> --help`.
- `dual_track.py not found`: set `FIELDKIT_DUAL_TRACK` or `"dual_track"` in
  `fieldkit.local.json`, or `fieldkit gather --only dual-track-doc-generator`.

dual_track.py lives in DualTrackAgent and is never edited from Fieldkit; report
problems with it instead.
