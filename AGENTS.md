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
fieldkit academic init|setup|next|accessed|refs|style|language|words|dashes|plagiarism|search|build|template|finish ...   students, 7 countries
fieldkit evidence fetch|import|check|show|sources|search|render ...   research evidence: sources, searches, pages kept by hash, every claim checked, the report
fieldkit hdmi status|doctor|diag|profile-list|tv-info|tv-wifi-report|fix|test-quick   the HDMI Harness (fix: elevated)
fieldkit win11 modules|fit|oem|run MODULE check|preview|apply|undo   the Windows 11 tweaks framework, safely
fieldkit peers [list] | send NAME "text" | wait [--seconds N] [--notify-when-idle NAME]   messages between sessions on this computer (Gorilla OpenCode's protocol)
fieldkit jobs run|ingest-advert|pack|check-letter|apply|status ...   the job hunt: official APIs only, never submits
fieldkit tools list | check [--run-tests]
fieldkit office read|create|check|scrub|deliver ...
fieldkit pipeline list|plan|run|status|reset NAME
fieldkit pipeline run gorilla-opencode --var src=PATH [--var version=vX]   build Gorilla OpenCode (any Go app: go-app)
fieldkit wait|mutate|regress|drift|mcp-probe|survey|i18n|ship|handover ...   methods as tools (docs/METHODS.md)
fieldkit cards check | draft FILE.py             every card against its command; a card started from argparse code
fieldkit office render FILE --sheet              look at the rendered pages, not the code that wrote them
fieldkit mcp                                     the agent door; with Gorilla OpenCode "trust": "local", answers are labelled
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
fieldkit sweep [--run-tests]                     the weekly walk: what under Documents/Scripts (and sweep.roots) has no card, no test, or changed since
fieldkit adopt NAME [--into G] [--apply]         move a Scripts tool into local/kits; repoints cards, shortcuts, Startup, skills, memory
fieldkit guard -- CMD ...                        EVERY compile job on the Windows laptop: refuses unless the fan control is cooling
                                                 (Compile/Gorilla mode), then keeps the CPU near 70 C (thermal.build_target 67)
fieldkit release check|prove SPEC                published == tested; every claim proven, per platform
fieldkit docs plan|prep|fill|render|check        dual-track documentation: a layman track and a developer track
fieldkit docs guide                              how to write for a reader who has never opened a terminal
fieldkit docs philosophy                         why: the Gorilla Open Source Philosophy. Read it before writing for a reader
fieldkit docs release --manifest release-docs.yaml   a release's documents: every number and web address in its sources
fieldkit release-page compose|check              build or check a release page: both tracks in full ON the page, plain language first
fieldkit lifecycle SPEC --approve                install, verify, uninstall; list what was left behind
```

## The scratch folder and the weekly sweep

`Documents/Scripts` is where a model saves the scripts it writes during a session, one
folder per tool with a README, so they outlive the session. It is scratch, not the
harness. Once a week, and at the end of any session that left scripts there, run
`fieldkit sweep`. Every item it lists is either adopted or retired (`sweep.ignore` in
`fieldkit.local.json`, with the reason). Adopted means it now lives inside Fieldkit with a
card, a test, a doctor and a skill: `fieldkit adopt NAME` shows the plan, `--apply` moves it
to `local/kits/` (the owner's own tools; `kits/` for public ones, by a reviewed commit) and
repoints everything that used the old place. A card that still points into Scripts is not
adoption: the sweep reports it as `adopt` and exits 3. Extra scratch roots (a repository's
own test scripts, say) go in `sweep.roots`. The sweep is done when the command exits 0.

## Unattended work (autopilot)

Safety comes from the harness, not from approval prompts (owner, 2026-10-18, after 40 hours of pressing Yes).
- `fieldkit autopilot claude PROMPT` runs Claude Code headless in bypass mode; a Claude Code desktop session opened
  in `Documents\Autopilot` (`fieldkit autopilot workspace`) works the same way. `fieldkit autopilot antigravity`
  starts Antigravity for unattended work (close it first). No prompts; only the hard lines hold
  (`fieldkit/autopilot/hardlines.py`, enforced by the hook of each tool): no push or publish, no deletes outside
  scratch, no changes to the build harness, guards, rules files, leak-gate allowlists or the decision register,
  no owner approvals, no credential files.
- An autopilot run has no GitHub or git credential: publishing is impossible, not just forbidden.
- Anything that needs the owner goes to `fieldkit inbox add TITLE WHY [COMMAND]`, and the run carries on with what
  does not depend on it. The owner clears the inbox in one sitting and publishes with `fieldkit publish`.
- `fieldkit autopilot stop` (the owner's STOP) leaves only reads; `resume` lifts it. A stop is never argued with.
- Before a measuring run: `fieldkit autopilot quiet --apply --hold PID` stops the apps that call home, by PID,
  for the length of the run, never touching the agent's own app.
- Report from the logs, never from memory: a PASS needs the line that printed it.

## Two machines, one harness (Windows and Linux)

The owner runs Fieldkit on a Windows laptop and a Linux machine, each with its own agent session.

- **At session start and before ending, run `fieldkit sync`.** It fetches and reports every repository in
  `sync.repos` (`fieldkit.local.json`) against GitHub. `fieldkit sync --apply` takes upstream commits only as a
  fast-forward, only with a clean tree, and only when CI ran green on both windows-latest and ubuntu-latest for
  that commit. Diverged history and a default branch ahead of a feature branch are reported for a person to merge.
  Pushing still needs the owner's yes. Private files (`local/`, memory) travel by `catchup`, not git.
- **A tool is written for both systems.** CI runs every test on both. A card naming one system needs
  `platforms_why` (what the other system lacks). `python -m fieldkit.desk.platformcheck` fails on a new
  single-system card without one, and on a card that promises a system its test file is skipped on. The cards
  that were single-system on 2026-10-16 are the frozen backlog (`fieldkit/desk/single_platform_baseline.txt`):
  it only shrinks.
- **A tool for the other system never starts (owner, 2026-10-20).** A card's `platforms` is enforced at every door,
  through one rule (`core/host.py: platform_refusal`): the command line refuses it before any tool code runs (exit 2,
  the card's `platforms_why` and what to use instead; `--help` always passes), `agent.run` refuses it, and
  `agent.discover` never offers it. The command line reads `fieldkit/desk/platform_index.json`, generated from the
  cards: after changing a card's `platforms` or entry, run `python -m fieldkit.core.platform_gate --write`
  (`tests/test_platform_gate.py` fails until you do). Inside a tool, a reader or lever that exists on one system
  only checks `sys.platform` itself and returns nothing elsewhere (the thermal sensors and cap are the example).
- **A Firefox patch is applied only where it builds.** `build-harness set-proof` (run by the patch set's pre-push
  hook for both systems) fails on a patch whose files only the other system builds, unless the policy declares it.
  Declare it with `platforms` on the group or `exclude_on.<system>`. rebase-set writes these declarations for its
  own group.

## Notes between the two computers (`fieldkit peers link send`)

The tool keeps the convention, not the model (owner, 2026-10-22: "we both know the models wont remember it").
- The sending link stamps every note `[FROM WINDOWS|LINUX|OSX #N]`, N this computer's own counter
  (`~/.fieldkit/link/sent-seq.json`), sent as a signed field and advanced only after the peer answered ok. Tags you
  type are stripped and replaced: do not number notes yourself.
- `[NEEDS YOU]` and `[DONE]` come from flags (`--needs-you`, `--done`). `[DONE]` is refused without `--evidence`
  (a commit, a VERDICT line, a test count); the evidence travels signed and is shown as `EVIDENCE: ...`.
- The receiving link prints `!! LINK CHECK: ...` above a note when signed numbers have a gap ("#a-#b never arrived"),
  a number repeats or arrives out of order, a tag in the text disagrees with the signed number, or `[DONE]` has no
  evidence. Treat a LINK CHECK as a fact to resolve with the other side, never as noise.

## Working with a small model

- **Let the harness plan.** Ask `fieldkit next`, do the one `DO:` line or pick one
  `CHOOSE:` number, then ask again.
- **Let the tools answer.** Each tool's output ends with `NEXT:`, the step to take.
- **Snapshot around changes.** Take a snapshot before and after any change, and report
  the diff, not the model's own account of what it did.
- **Measure.** `fieldkit exam` shows whether the kit really helps a given model.

## Telemetry and the tool router (owner, 2026-10-18)

- Telemetry is off and cut out in every project, without asking: `fieldkit telemetry scan <project>` exits 0 (SDKs,
  endpoints and collectors excised, not disabled; a deliberate line says `telemetry-ok: <why>`). The machine:
  `fieldkit telemetry machine`, every row closed; the build gate fails while one is open.
- The tool router (`fieldkit/autopilot/toolrouter.py`, a Claude Code PreToolUse hook and Antigravity's guard) refuses
  hand-rolled recursive searches and size scans and names the tool to use (`fieldkit size find`, `fieldkit size`).
  A new tool that replaces a hand-rolled habit gets a row in its ROUTES table, with a test.

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
fieldkit/academic  students in uk/us/es/pt/it/de/ro: locales/*.yaml, styles.yaml, languages.yaml; data in local/academic
fieldkit/evidence  research evidence: safe, polite fetch into a content-addressed store; import and check a run's claims
fieldkit/win11    the Windows 11 tweaks framework: modules.yaml (the trio per module), fit.yaml (what each profile entry assumes)
fieldkit/peers    messages between sessions on this computer: the register, the socket or named pipe, the fence
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

## Methods

How the work gets done, and the tool each method becomes: [docs/METHODS.md](docs/METHODS.md). Before inventing a
procedure, look there; when you invent one, add it there, and turn it into a tool with a test.

## Commands for the owner

OWNER COMMANDS ARE PASTEABLE AS THEY ARE (owner, 2026-10-18). Every command the owner is to paste is a Windows PowerShell block that starts with cd to a full path (cd $HOME; ...), calls every program by its full real path (an MSIX app such as Claude Desktop keeps its files under $env:LOCALAPPDATA\Packages\<package>\LocalCache, not where the app itself sees them), uses PowerShell syntax only (no &&, export, $VAR, ~/), and says in words where anything typed AFTER it goes (for example: type /login inside Claude's prompt, not in PowerShell). Checked by fieldkit.autopilot.ownercmd; in Claude Code a Stop hook sends a failing answer back.
