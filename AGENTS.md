# Fieldkit - instructions for coding agents

Fieldkit is a set of deterministic tools. The model decides what to do; Fieldkit does
it the same way every time and reports the result in JSON. Prefer a Fieldkit command
to writing a one-off script for the same job.

## Commands

Every command accepts `--json`.

- Exit codes: 0 fine, 1 error, 2 bad usage, 3 findings (problems, secrets or a
  failed stage).
- Machine-specific paths live in `fieldkit.local.json`, which is git-ignored and
  never published. Your own tools, sources and their tests live in `local/`
  (`local/tools.yaml`, `local/imports.yaml`, `local/tests/`), also git-ignored.

```
fieldkit host
fieldkit doctor [--for PIPELINE]                  what this machine still needs, and the line that installs it
fieldkit where                                   the Fieldkit folder: in a document, cd (fieldkit where), never a <blank>
fieldkit icons cache [--apply] | frames | check-ico | build | install-fix-button   sharp desktop icons (any computer)
fieldkit audio                                   Linux sound chain: each stage, what is done twice, the fix (read-only)
fieldkit backup [--to FOLDER]                    a dated, verified zip of the harness (the folder is never guessed)
fieldkit cv init|import|check|render             a candidate's CV: fixed rules, the questions to ask, UK CVs in EN/RO
fieldkit jobs run|ingest-advert|pack|check-letter|apply|status ...   the job hunt: official APIs only, never submits
fieldkit tools list | check [--run-tests]
fieldkit office read|create|check|scrub|deliver ...
fieldkit pipeline list|plan|run|status|reset NAME
fieldkit triage LOG --set auto
fieldkit privacy scan PATH --git
fieldkit kernel localversion|fragment ...
fieldkit kernel migrate-check|migrate-apply|migrate-verify ...   carry a kernel patch set to a newer kernel: what fits, the port, the proof
fieldkit gather [--check|--test] [--only NAME]   GitHub repos and local folders -> toolbox/
fieldkit tools list --all                        every tool, including every gathered script
fieldkit harvest [--find WORDS]                  what every script does; search it in plain words
fieldkit next PIPELINE                           the one next thing to do (DO / BLOCKED / CANNOT HERE / DONE)
fieldkit snapshot take NAME ... / diff A B       record state, then prove exactly what changed
fieldkit refcheck manifest|markdown|python|regex every named file, link or module must exist
fieldkit exam run --model ID / report            measure a model with and without the kit
fieldkit agent discover|describe|run|undo ...     the agent interface: preview, approve, back up, verify, undo
fieldkit mcp                                     the same interface over MCP (stdio)
fieldkit cards list|show / readiness             each tool's contract and trust level
fieldkit release check|prove SPEC                published == tested; every claim proven, per platform
fieldkit docs plan|prep|fill|render|check        dual-track documentation: a layman track and a developer track
fieldkit docs guide                              how to write for a reader who has never opened a terminal
fieldkit docs philosophy                         why: the Gorilla Open Source Philosophy. Read it before writing for a reader
fieldkit docs release --manifest release-docs.yaml   a release's documents: every number and web address in its sources
fieldkit release-page compose|check              build or check a release page: both tracks in full ON the page, plain language first
fieldkit lifecycle SPEC --approve                install, verify, uninstall; list what was left behind
```

## Working with a small model

- **Let the harness plan.** Ask `fieldkit next`, do the one `DO:` line or pick one
  `CHOOSE:` number, then ask again.
- **Let the tools answer.** Each tool's output ends with `NEXT:`, the step to take.
- **Snapshot around changes.** Take a snapshot before and after any change, and report
  the diff, not the model's own account of what it did.
- **Measure.** `fieldkit exam` shows whether the kit really helps a given model.

## Rules this code enforces, and that you should keep to

0. **The explanation is the product.** Every release page carries the plain-language
   document in full, on the page, before the developer one, and opens by saying what the
   program is, whether to download it and why it matters. Never a summary with a link.
   Build the page with `fieldkit release-page compose`; it refuses a page that breaks this.

1. **Verify the artefact, not the exit code.** A stage is done only when its verify
   checks pass.
2. **Stop only what you started.** The runner records the PIDs it starts and refuses
   to stop any other process. The owner runs the same programs.
3. **Do not probe a script that runs on load.** Check `fieldkit tools check` first.
4. **Nothing personal is published.** Run `fieldkit privacy scan REPO --git` before
   any commit, push or release. Nothing is pushed without the owner's yes.
5. **The same failure is never diagnosed twice.** When `triage` says unrecognised,
   add the signature once the real cause is known.
6. **Report the platform honestly.** A Debian-only stage on Windows reports
   `not-this-platform`, and it is not faked.

## Decisions: a model never asks a bare question

A model never asks a bare question; it runs `briefs` and shows the brief.

```
fieldkit build-harness briefs TASK [--json] [--technical]   every open decision, each a full brief
fieldkit build-harness brief show ID [--task TASK]          one brief in full (MCP: build_harness_briefs)
fieldkit build-harness decide ID OPTION --words "..."       the maintainer only, at a real terminal
```

- A brief shows what is affected, word for word (counts, excerpts, file:line, where it is
  linked from), every option with what it changes, its cost to users and to the project's
  credibility, a recommendation with its reason, and what will be recorded. The code
  (`fieldkit/briefs/schema.py`) refuses a brief that lacks any of these.
- Show the brief as it is. Do not shorten it into "should I do X?". Do not summarise the
  excerpts: they are there so the person reads the content itself.
- An unproven privacy or security claim is made true and proven, never deleted: a brief
  that recommends deleting one is refused.
- You cannot record an answer. `decide` needs a real terminal, the brief shown first, and
  the person's own words; the MCP door has no decide tool.

## Layout

```
fieldkit/audio    the Linux sound chain (read-only) + signatures.yaml
fieldkit/icons    IconKit: icon cache (Windows/GTK/KDE), .ico frames and checks, crisp recoloured icons
fieldkit/career   CV and job hunt: rules/trades/scoring/advert as YAML; personal data only in local/career
fieldkit/core     host, settings, proc (runner), privacy, pipeline engine, checks, next, snapshot
fieldkit/office   read, create, check, scrub, deliver
fieldkit/build    triage + signatures/*.yaml, kernel, refcheck, pipelines/*.yaml
fieldkit/desk     registry + tools.yaml, discover (every gathered script)
fieldkit/exam     fixture, tasks + graders, raw/kit toolsets, runner (model measurement)
fieldkit/agent.py, mcp.py         the agent interface (CLI and MCP)
fieldkit/briefs   decision briefs: schema (validation, rendering), producers, record (owner only)
fieldkit/release.py              the release gate
fieldkit/gather.py, harvest.py   bring tools in; index what they do
skills/           SKILL.md pointers, installed by install_skills.py
tests/            pytest; python -m pytest
imports.yaml      what gather brings in, from where, and how each source is tested
toolbox/          generated by gather (git-ignored); PROVENANCE.json per source
local/            your own cards, sources and tests (git-ignored, merged at load time)
```

## Changing Fieldkit

- Run `python -m pytest` before and after every change. A new check needs a test that
  proves it can fail.
- Pipelines, signatures and the registry are data (YAML). Change the data before
  changing the code.
