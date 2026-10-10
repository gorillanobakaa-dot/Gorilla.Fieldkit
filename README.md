# Fieldkit

<!-- WHO-THIS-IS-FOR: managed block, do not edit by hand -->

**Tested tools that let a small AI model on your own laptop do real work: find code, check and clean documents, run builds, and refuse to publish anything it cannot prove.**

Built for the people every other tool prices out: kids with no credit
card, 15-year-old laptops, data sold by the megabyte. Free forever, by
design, not as a trial.
Why, with the numbers: [PHILOSOPHY.md](https://github.com/gorillanobakaa-dot/Gorilla.Opencode/blob/main/PHILOSOPHY.md)

<!-- /WHO-THIS-IS-FOR -->

[![tests](https://github.com/gorillanobakaa-dot/Gorilla.Fieldkit/actions/workflows/tests.yml/badge.svg)](https://github.com/gorillanobakaa-dot/Gorilla.Fieldkit/actions/workflows/tests.yml)

**The model is a small part of the job. The tools around it decide whether the job gets done.**
Fieldkit is those tools: deterministic Python that does the same thing every time, checks
its own result, and answers in plain JSON. The model only has to choose the right tool.

> Measured on one ordinary laptop (Intel i7-1255U, LM Studio): the same small model, Gemma,
> got **one of five** tasks right with basic tools and **five of five** with Fieldkit, in
> about half the time and with fewer tokens. Five tasks is a small test; run it on your own
> model with `fieldkit exam`.

---

## What it does (Layman Track)

Start here if you have never opened a terminal. This section explains every part of Fieldkit in
plain words: what it does for you, how it works step by step, what it touches on your computer,
and what it cannot do. Each part ends with two links: a full plain-language guide, and the
technical guide for people who want to check the work.

### The idea in one paragraph

You run an AI helper on your own computer, and it is usually a small model. Left alone, a small
model guesses: it opens file after file, loses track, and sometimes makes things up. Fieldkit is a
box of tools that do the hard part, check their own result and hand back a plain answer, so the
model only has to pick the right tool. Everything runs on your own machine. There is no account,
nothing is sent anywhere, and it costs nothing.

### How you use it

There are two ways, and they reach the same tools.

1. **You type a command.** On Windows, press the Windows key, type `PowerShell` and press Enter.
   Then type a command such as `fieldkit host` and press Enter. It answers in plain words.
2. **Your AI helper uses it for you,** through the "agent door" described in part 1. You stay in
   charge: anything risky waits for you.

Every command ends with a fixed number that programs can read: `0` all fine, `1` something broke,
`2` the command was typed wrongly, `3` it found problems you should look at. Add `--json` to any
command for an answer a program can read.

### 1. The agent door: how an AI is allowed to touch your computer

Think of a reception desk. The AI never walks into the building; it asks at the desk.

1. The AI describes the job in plain words, for example `fieldkit agent discover word file safe to send`,
   and gets back the tools that fit.
2. Every tool has a **card**: what it needs, what it changes, and how risky it is: *read-only*,
   *reversible* or *irreversible*.
3. A tool nobody has reviewed is refused, with the reason.
4. What the AI asks for is checked against the card before anything runs.
5. A tool that changes files shows a **preview** first. When it runs for real, Fieldkit backs up
   the files, runs the tool, checks the result, and puts the backup back by itself if the check
   fails. It keeps a record so `fieldkit agent undo RUN` can reverse it later.
6. Anything that changes your system (registry, services, power, hardware) or cannot be undone
   waits for **you** to type `--approve`. AI programs reach Fieldkit through MCP, the standard link
   for AI tools, and that link has no way to approve.

**What it touches:** only the files and folders a card names, plus its backups and records in the
`state` folder. `undo` checks its own record before trusting it, like a cloakroom attendant who
checks the ticket number and the coat: it puts back only files inside that run's own backup and
scope, and refuses if you edited a file after the run (you can overrule that at the keyboard with
`--approve`; an AI cannot). **What it cannot do:** it is not a sandbox: a tool still does what its
own code does. `fieldkit agent run` applies the change unless you add `--mode preview`.

Guides: [plain language](docs/dual-track/agent-door/agent-door_layman.md) ·
[technical](docs/dual-track/agent-door/agent-door_developer.md)

### 2. The engine: long jobs, privacy checks and before/after pictures

- **Long jobs in stages.** A job such as a build is a list of numbered stages. A stage counts as
  finished only when its checks pass (a file exists, the output says what it should), never just
  because the program said it finished. If the job stops, it resumes where it stopped.
  `fieldkit next NAME` tells you the one next thing to do: `DO`, `BLOCKED`, `CANNOT HERE` or `DONE`.
- **Privacy scan.** `fieldkit privacy scan FOLDER` looks for passwords and keys, home-folder paths,
  email addresses and your own private words before you publish anything. You list your private
  words in `fieldkit.local.json`, so they are never written into the code.
- **Before and after.** `fieldkit snapshot take before`, do something, `fieldkit snapshot take after`,
  then `fieldkit snapshot diff before after` shows exactly what changed: files, services, scheduled
  tasks and installed programs.
- **Stopping programs safely.** Fieldkit remembers the programs it started and stops only those,
  never a program of the same name that you are running.

**What it cannot do:** the privacy scan only finds patterns it knows and words you listed. A file
it cannot read, or one over 5,000,000 bytes, is reported as "not scanned", which counts as a
problem, never as clean. A pipeline file can run any program, so run only pipelines you trust. The
Linux side of snapshots and of stopping programs has only been tested on Windows so far.

Guides: [plain language](docs/dual-track/core/core_layman.md) ·
[technical](docs/dual-track/core/core_developer.md)

### 3. The tool collection: knowing which scripts can be trusted

1. `fieldkit gather` copies scripts from the GitHub projects and folders listed in `imports.yaml`
   into one `toolbox` folder, and records where every file came from with a fingerprint, so it can
   tell you later if a copy has changed.
2. `fieldkit harvest` reads every script **without running it** and writes a one-line index of
   what each does and what it touches. `fieldkit harvest --find WORDS` searches it in plain words.
3. `fieldkit cards list` and `fieldkit readiness` give each tool a trust level: *gathered* (only
   copied in), *carded* (reviewed), *tested* (its tests passed on this exact file) and *verified*.
   An AI may only start tools that have been reviewed.

**What it cannot do:** the "safe to start with `--help`" check reads the script's structure
without running it and treats any real work at the top of the file as unsafe; it can still miss
work hidden behind an unusual trick. `fieldkit gather --check` never touches the network, and a
failed update is reported as an error instead of quietly keeping the old copy. Draft cards stay at
*gathered*, so an AI must not run them unreviewed.

Guides: [plain language](docs/dual-track/tool-collection/tool-collection_layman.md) ·
[technical](docs/dual-track/tool-collection/tool-collection_developer.md)

### 4. Office documents: read, make, check, clean, send

| You want to | Type |
|---|---|
| Read a Word, Excel, PowerPoint or PDF file as plain text | `fieldkit office read FILE` |
| Make a new one from a short description | `fieldkit office create SPEC OUT` |
| Check the file is not broken | `fieldkit office check FILE` |
| Remove people's names from its hidden properties, comments and tracked changes | `fieldkit office scrub FILE` |
| All of that, then a privacy search: is it safe to send? | `fieldkit office deliver FILE` |
| Check a CV, hear what is missing, and get a UK-style CV in English and Romanian | `fieldkit cv check FILE`, `fieldkit cv render` |
| Find UK jobs on official job sites, score them, and prepare each application (you press Submit) | `fieldkit jobs run` |
| A student: when is it due, is every source cited and listed, is it all in the right language, what do I do next | `fieldkit academic next FOLDER` |
| Back up the whole kit into your backup folder, and prove the copy is good | `fieldkit backup` |
| Blurry desktop icons: find out why and fix them with one click (Windows, and GTK/KDE on Linux) | `fieldkit icons cache --apply`, `fieldkit icons install-fix-button` |

`deliver` answers `SAFE TO SEND` or `NOT SAFE`, with the reason. Nothing is uploaded.

Backups of cleaned files go into Fieldkit's own `state` folder, never next to your document, so
sharing the folder cannot leak the names that were removed. `deliver` says NOT SAFE if an old
backup or a Word lock file sits beside the document.

**What it cannot do:** it cannot clean the Author and similar properties inside a PDF; it finds
them and says NOT SAFE, so you re-export the PDF with those fields empty. Parts it cannot read or
decode are reported as "not scanned", which also means NOT SAFE. `create` refuses to replace an
existing file unless you add `--force`.

Guides: [plain language](docs/dual-track/office/office_layman.md) ·
[technical](docs/dual-track/office/office_developer.md)

### 5. Builds, releases and the laptop's temperature

- **Why did the build fail?** `fieldkit triage LOG` reads a failed build log and names the known
  cause and fix, or says plainly that this failure is new.
- **Does everything it names exist?** `fieldkit refcheck` checks every file, link and module a
  project names really exists.
- **Is this release true?** `fieldkit release check` refuses to call a release safe to publish
  unless the published files match the tested ones and every claim in the release notes has a
  passing proof. There is no way to force it.
- **Install, test, remove.** `fieldkit lifecycle SPEC --approve` installs a program, checks it,
  uninstalls it, and lists anything left behind.
- **Debian kernels.** `fieldkit kernel` helps build a custom Debian kernel step by step. Its full
  build has not run yet; only a dry run has.
- **Temperature.** On 2026-10-02 the build laptop switched itself off in the middle of a compile.
  The build had been trusting a temperature sensor that was stuck at 41.85 C for 386 readings
  while the processor heated up. Now Fieldkit trusts a sensor only after watching it rise under
  full load (`fieldkit thermal prove`). During a long build it lowers the processor's top speed
  to keep the laptop under the limit (`fieldkit thermal watch`).

**What it cannot do:** `thermal watch` changes your power plan's maximum processor setting while it
runs and puts it back when it stops, also when you press Ctrl+C; if the laptop loses power mid-run,
check the setting in Power Options. It only understands English `powercfg` output. The kernel
download's signature is not checked yet.

Guides: [plain language](docs/dual-track/builds-releases-thermal/builds-releases-thermal_layman.md) ·
[technical](docs/dual-track/builds-releases-thermal/builds-releases-thermal_developer.md)

### 6. The exam: measure your own model

`fieldkit exam run --model ID` builds the same small invented project every time and gives your
model five jobs: find a function, spot a name that does not exist, explain a failed build log,
find a block of code, and list missing files. It runs each job once with basic tools and once with
Fieldkit. A fixed script marks each answer; no AI judges another. `fieldkit exam report` shows the
table. The same small model, Gemma, got 1 of 5 right with basic tools and 5 of 5 with Fieldkit.

**What it cannot do:** five jobs is a small test, and the same author wrote the jobs and the tools.
The tools were improved after watching Gemma fail these jobs, so 5 of 5 shows the effect; it does
not prove your model will do every real job.

Guides: [plain language](docs/dual-track/exam/exam_layman.md) ·
[technical](docs/dual-track/exam/exam_developer.md)

### 7 to 10. The Gorilla Firefox build harness

The last four parts carry the Gorilla Firefox privacy changes onto each new Firefox release, build
it, install it, and prove it does not talk to anyone it should not. They were built for that
browser, but the lesson applies to any big build: **a build that finished is not a browser that
works, and a browser that works is not a browser that keeps your secrets.** Each step must be proven.

What this whole machine is, why it exists, who else does similar work and how much it does, with the
numbers: **[The build harness, explained](docs/THE-BUILD-HARNESS-EXPLAINED.md)**.

### 7. Porting a new Firefox release

1. Fieldkit fetches the untouched source of the latest stable Firefox from Mozilla's own release
   tag and keeps a read-only copy (the *vault*).
2. It makes a working copy and applies every Gorilla change that still fits, with no guessing.
3. Every change that no longer fits becomes one small, checked job. Fieldkit tries it itself
   first, carrying wording files, key files and settings across by name.
4. Only the leftovers go to the small local model. The model answers in text; Fieldkit makes the
   edit and checks the file.
5. Anything that needs human judgement is parked for the maintainer with a written explanation.
6. The maintainer's own fixes are recorded (`fieldkit build-harness record`) and exported as
   patches (`fieldkit build-harness export-hand`), so the next Firefox release starts from them.
   For Firefox 157 that was 20 patches: 3 port fixes and 17 privacy cuts. Applied in order, they
   rebuild the exact same source tree.

**What it cannot do:** a failed model attempt resets the working copy, so an edit that was not
recorded is lost. Kernel porting is not available. Each recorded edit says whether it is a privacy
cut or a port fix; an older edit without that label is sorted by keywords, and the export prints
each patch's kind and how it was decided, so read the list before publishing it.

Guides: [plain language](docs/dual-track/port-engine/port-engine_layman.md) ·
[technical](docs/dual-track/port-engine/port-engine_developer.md)

### 8. Checking the source and building it

In Firefox 157 a half-removed piece of code left one file that could not be read. The build still
finished, and the browser's address bar did nothing. So before compiling, Fieldkit reads the files
themselves, not the work record:

1. Every changed JavaScript file must parse.
2. Every name a file imports must still exist in the file it comes from.
3. No build list may be left empty.
4. Every change must be explained by the previous release or by a recorded step (`truthbound`).
5. Three known kinds of breakage are fixed by `repair` itself. Each fix is proven, and the file is
   put back if the proof fails.

Then `build-run` waits for an idle processor, proves a temperature sensor, and runs the build under
the temperature guard. It recognises known stops (a broken object file, a missing tool, a full
disk, an interrupted console, an empty build list and others), fixes them and retries. A stop it
does not know is written down with its first errors, so a fixer can be added and the next one fixes
itself. When an AI does the work, its result is judged from the files on disk, not from its report.

A repair is proven with Node.js; if Node.js is missing, the repair counts as failed and the file is
put back. A fix that names a file outside the working copy is refused.

**What it cannot do:** several paths are set for the maintainer's own machine. The session watcher
has no tests yet.

Guides: [plain language](docs/dual-track/verify-and-build/verify-and-build_layman.md) ·
[technical](docs/dual-track/verify-and-build/verify-and-build_developer.md)

### 9. Installing the browser and proving it works

1. **Backup.** The installed browser and your profiles are zipped into
   `Documents\Gorilla.Firefox.Backups`, with a fingerprint list.
2. **Install from the fingerprinted zip,** not the installer: the installer once reported success
   after installing nothing.
3. **Clear old cached scripts.** Four builds once shared one build number, and a profile kept
   running a broken build's cached scripts. Now the build number changes with the source, and every
   install clears the caches.
4. **Prove it on the browser you actually run** (`fieldkit build-harness post-install TASK`): the
   settings are in force, the removed AI parts are really gone, it starts cleanly, its own web log
   shows no Mozilla addresses, ads are blocked on a real news page, and a web page cannot see your
   local network address. Build 11 asked 0 Mozilla or Firefox addresses over 75 seconds on a real
   page and reached 0 of 16 ad and tracker domains.
5. **The address-bar test takes your keyboard.** It runs only with `--drive`, after a 20-second
   countdown. Save your work and stop typing when you see the warning.
6. **The way back:** `fieldkit build-harness install TASK --restore BACKUP_FOLDER`.

It only installs over a program that is clearly Gorilla: an entry that only says "Firefox" is never
picked, and if it finds none or several it stops and asks for `--install-dir`. `--restore` puts
back the browser and your profiles; your current profiles are moved aside, never deleted. A check
that was skipped is shown as SKIPPED, and the verdict is OK only when every check ran and passed.
Throwaway test profiles are deleted after each check; the logs are kept as evidence.

**What it cannot do:** it cannot prove a website works the way you expect; it proves the settings,
the removed parts, a clean start, the web addresses the browser asks for on its own, ad blocking
and local-address hiding.

Guides: [plain language](docs/dual-track/install-and-proof/install-and-proof_layman.md) ·
[technical](docs/dual-track/install-and-proof/install-and-proof_developer.md)

### 10. The leak gate: no release until every connection is proven

This is the last checkpoint before a build is published. It answers `PASS` or `FAIL`.

- **It fails closed.** Anything the browser does that is not on the approved list is a failure.
  An entry nobody approved is a failure. A check that collected no evidence is a failure, never a
  pass. `PASS` needs all 18 rules to pass.
- **It uses many witnesses, not one.** Up to seven watch at once: the browser's own logs, a
  decrypting proxy, the table of open connections, the list of running programs, the files on disk,
  a packet capture, and a test name server that logs every name looked up. One witness can be
  fooled; seven that must agree are much harder to fool.
- **It plays 13 scenes:** starting and idling, the new tab, the home page, the add-ons and settings
  pages, secret "canary" words in normal and private windows, background workers, WebRTC, bad
  certificates, a crash, deliberately broken server answers, a real web page, and a normal close.
- **Only the maintainer approves.** A model may propose an entry for the list; approving it needs
  the maintainer at a real keyboard.
- **It reads the code too.** 103 source files that can reach the network, in 25 parts of Firefox,
  each have a written decision; one (OCSP, certificate checking) is still the maintainer's call. It
  also checks the packed files, the programs' network libraries and the Rust libraries against the
  previous release.
- **Quick and release runs.** A quick run helps while fixing and can never pass. Only a release run
  in an administrator window, three times over with packet capture, counts.

**The first baseline.** The regression rule compares a new build with the last approved one. For
the very first release there is nothing to compare with, so the maintainer, at the keyboard, may
save the first baseline from a release run whose only failure is that missing baseline; every
other rule must have passed. The next release run must then pass in full before anything is
published.

**What it cannot do:** the Linux runner, meant to be the final referee, has never been run on Linux.
On Windows the packet capture records every program's traffic, not only the browser's.

Guides: [plain language](docs/dual-track/leakgate/leakgate_layman.md) ·
[technical](docs/dual-track/leakgate/leakgate_developer.md)

### What Fieldkit will never do

- Approve something on your behalf, or let an AI approve it.
- Count a check that did not run as a pass.
- Send your files anywhere. It uses the network only when you ask: to fetch tools (`gather`), to
  talk to the model server on your own computer (`exam`), or to open the leak gate's test pages.

## Technical Definition (Developer Track)

Python 3.11+, one codebase for Windows 11 and Debian, AGPL-3.0-or-later. Every command
takes `--json`; exit codes are fixed: `0` fine, `1` error, `2` bad usage, `3` findings.

- **Agent interface.** `discover`, `describe`, `run`, `undo`, served on the command line
  and over MCP (`fieldkit mcp`, JSON-RPC 2.0 on stdio). `run` enforces one sequence: refuse
  draft cards, validate inputs, preview, require approval where the card says so, back up
  the scope, apply, verify, restore on failure. The MCP surface has no approval argument.
- **Tool cards.** Each tool declares inputs (read from argparse by AST, never by running
  it), effects, safety (`read-only`, `reversible`, `irreversible`), modes (preview, apply,
  undo, verify) and tests. Trust ladder: gathered, carded, tested (a pass recorded on the
  file's current sha256), verified.
- **Pipelines.** YAML stages with fingerprinted, resumable state and verify checks
  (`files_exist`, `file_contains`, `output_contains`, `output_lacks`, `python`). A stage
  is done only when its checks pass, never on exit code alone.
- **Release gate.** `release check` exports the tag with `git archive`, runs its tests
  there, compares published files by sha256, privacy-scans, and demands a passing proof
  for every claim in the notes. `release prove` records evidence from another machine
  (platform, git tree id, result). No `--force`.
- **Tests.** `python -m pytest`. On the author's Windows 11 laptop (2026-10-02): 809 passed,
  1 skipped, 1 known fault kept as a strict xfail. GitHub Actions runs them on Windows and
  Ubuntu on every push (see the badge). A skipped test names the tool it needs.
- **Documentation.** `fieldkit docs` (Gorilla.Documentation.IBM.Style): every module group has a
  layman and a developer track built with DualTrackAgent and the writer brief
  (`fieldkit/gdocs/WRITER_BRIEF.md`), and `fieldkit docs check --strict` fails when a track is
  stale, thin, unsafe or quotes a number nobody measured. Index: [docs/dual-track](docs/dual-track/README.md).

Release notes: [GitHub releases](https://github.com/gorillanobakaa-dot/Gorilla.Fieldkit/releases).

---

## Table of contents

1. [Install](#1-install)
2. [Connect it to your AI helper](#2-connect-it-to-your-ai-helper)
3. [What is in the box](#3-what-is-in-the-box)
4. [How it keeps changes safe](#4-how-it-keeps-changes-safe)
5. [Your own tools stay private](#5-your-own-tools-stay-private)
6. [Measure it on your own model](#6-measure-it-on-your-own-model)
7. [The release gate](#7-the-release-gate)
8. [Honest limits](#8-honest-limits)
9. [Layout](#9-layout)

---

## 1. Install

**Step-by-step guide, for people and for developers: [INSTALL.md](INSTALL.md).** It shows how to
ask your AI to install Fieldkit or do it yourself, where to put it, how to connect it to
Gorilla OpenCode and LM Studio, and how to make a small model aware of it.

The short version (Python 3.11 or newer and Git):

```
git clone https://github.com/gorillanobakaa-dot/Gorilla.Fieldkit
cd Gorilla.Fieldkit
python -m pip install -e ".[test]"
python -m pytest -q
fieldkit host
```

- **Pass:** the tests end with zero failed. Some say `skipped` and name a tool: those need
  `fieldkit gather` first (section 3).
- **Pass:** `fieldkit host` prints your system and Python version.
- **If `fieldkit` is not recognised:** use `python -m fieldkit` instead. It does the same.

To remove it: `python -m pip uninstall fieldkit`, then delete the folder. Fieldkit changes
nothing else on your computer.

## 2. Connect it to your AI helper

Full steps, backups and checks: [INSTALL.md](INSTALL.md), steps 4 to 6.

Fieldkit speaks MCP, the standard way AI helpers use outside tools. Your helper then sees
nine tools: `discover`, `describe`, `run`, `undo`, `next` and `readiness`, plus the three the build
harness gives a model (`build_harness_status`, `build_harness_next`, `build_harness_submit`: one
small job at a time, and the harness checks the result; approving and skipping are not among them).

**Gorilla OpenCode** - add to your `config.json` (on Windows,
`%USERPROFILE%\.config\gorilla-opencode\config.json`), then restart it:

```json
"mcpServers": {
  "fieldkit": { "type": "stdio", "command": "fieldkit", "args": ["mcp"], "trust": "local" }
}
```

`"trust": "local"` (Gorilla OpenCode with local-server trust) lets Fieldkit say, per answer, whether it
carries text someone else wrote and whether it went online. Your own checks then run without the "untrusted
content" question after every step; reading a downloaded document still asks. Tools that go online run only
when the call says `network=true`. Leave `trust` out to have every call treated as a stranger's.

**Claude Code:**

```
claude mcp add fieldkit -- fieldkit mcp
```

**LM Studio (versions with MCP support)** - add the same `fieldkit` entry to its
`mcp.json`.

If your helper cannot find the `fieldkit` command, give the full path to it instead, for
example the `fieldkit.exe` in your Python `Scripts` folder.

- **Pass:** the first time the AI uses Fieldkit, your helper asks you to allow it.

## 3. What is in the box

| Job | Command |
|-----|---------|
| Read a Word, Excel, PowerPoint or PDF file as text | `fieldkit office read FILE` |
| Create one from a JSON or YAML description | `fieldkit office create SPEC OUT` |
| Check it is not broken | `fieldkit office check FILE` |
| Remove people's names from its hidden properties | `fieldkit office scrub FILE` |
| All of the above, safe to send? | `fieldkit office deliver FILE` |
| A CV against fixed rules (general + trade), the questions to ask; UK CVs from a profile, EN/RO, .docx + .pdf | `fieldkit cv check FILE` / `fieldkit cv render --variant V --lang ro` |
| UK job hunt: Reed and Adzuna APIs only, scoring, packs, letters of approved sentences only, tracking; never submits | `fieldkit jobs run --top 8` (skill: `fieldkit-career`) |
| Academic work in 7 countries (uk, us, es, pt, it, de, ro): the deadline and upload date asked, citations <-> references, APA 7 / Harvard / ISO 690 shape, work-language and spelling check, words, plagiarism pre-check, the one next step; finished Word documents, slides and posters, and templates for 32 document types, in the country's layout and the work language | `fieldkit academic` (skill: `fieldkit-academic`) |
| A dated, verified zip of the harness into the backup folder (never guessed) | `fieldkit backup --to FOLDER` |
| IconKit: why icons are soft (icon cache vs. the file), cache rebuild with backup, .ico frame reader and crisp gates, recoloured icons from one master | `fieldkit icons --help` (skill: `fieldkit-icons`) |
| Find secrets, home paths, emails, your private words | `fieldkit privacy scan PATH [--git]` |
| Name the cause of a failed build | `fieldkit triage LOG` |
| Run a staged, resumable build | `fieldkit pipeline run NAME` |
| The one next thing to do in a pipeline | `fieldkit next NAME` |
| Every named file, link or module must exist | `fieldkit refcheck ...` |
| Record state, then prove what changed | `fieldkit snapshot take / diff` |
| Install, check, uninstall; list leftovers | `fieldkit lifecycle SPEC --approve` |
| Prove a release before publishing it | `fieldkit release check / prove` |
| Find a tool by describing the job | `fieldkit agent discover WORDS` |
| Index what every script in a folder does | `fieldkit harvest --find WORDS` |
| Bring in tools from GitHub, with their tests | `fieldkit gather [--test]` |
| Measure a model with and without the kit | `fieldkit exam run --model ID` |
| Is the temperature sensor real? Keep a build cool | `fieldkit thermal prove / watch` |
| Port, build, install and prove Gorilla Firefox | `fieldkit build-harness ...` (part 7 to 10 above) |
| Release gate for leaks and telemetry | `fieldkit build-harness leakgate TASK --release` |
| Open every about: page of the installed build and judge it (text, artwork, errors, missing strings, requests); `walk=1` clicks every about:about link in a visible window, live | `fieldkit build-harness about-pages TASK [walk=1]` |
| Every about: page the source can register (tree and upstream), what the last build registered, and the owner's verdict from decisions/ABOUT-PAGES.yaml; the register is enforced before the build (D-157-40) and after it (about-pages) | `fieldkit build-harness about-registry TASK` |
| Every hidden about: page explained, with its picture: English (both tracks, published) and Romanian, rendered from one source and checked against the research (also in release-check) | `fieldkit build-harness hidden-pages-doc TASK [write=1]` |
| The release page tells everything since the last release: every decision named, under GitHub's 125,000-character limit, the hidden pages on the page itself | `fieldkit build-harness release-cover TASK page=FILE` (and `hidden-pages-doc TASK release=FILE` for the hidden pages' release edition) |
| What a change costs or saves in RAM and CPU, page by page | `fieldkit build-harness weigh TASK` |
| Run a long build-harness command like `screen`: its own visible window, started by Windows, survives the session, logged, and the machine kept from idle-sleeping until it ends | `fieldkit build-harness window build-run TASK` |
| Keep the machine from idle-sleeping until a run already going ends | `fieldkit build-harness awake PID` |
| Satellite mode judged: identity and scripts per level and per kind of site, call sites included (also after every build and install) | `fieldkit build-harness satellite TASK` |
| Help > About carries the build stamp; build-verify and post-install hold it to the build that was made | `buildstamp.py` (rows in build-verify and post-install `stamp`) |
| Write or check the dual-track documentation | `fieldkit docs plan / prep / render / check` |
| Learn how to write for a reader who has never opened a terminal | `fieldkit docs guide` |
| Read why: the Gorilla Open Source Philosophy | `fieldkit docs philosophy` |
| Build a release page with both documents on it, plain language first | `fieldkit release-page compose` |
| Check a release page before publishing it | `fieldkit release-page check PAGE` |

Pipelines that ship: `office-deliver`, `firefox-windows` (drives the Gorilla Firefox build
harness) and `debian-kernel`. Triage knows Firefox on Windows, the Debian kernel and Debian
packaging failures. New failures are added as data, in `fieldkit/build/signatures/`.

`fieldkit gather` copies these repositories into `toolbox/` and records the commit and
sha256 of every file: pfind, searchfox-tools, model-eval, code-review-toolkit,
dual-track-doc-generator, debian-kernel, sensors-gorilla, speaker-loudness-fix,
black-gorilla-theme, apple-superdrive-enabler, respect-your-llms and the HDMI harness.
`fieldkit gather --test` runs each one's own tests.

## 4. How it keeps changes safe

Every tool has a card that says what it changes. When the AI asks to run one:

1. **Read-only tools** run straight away.
2. **Tools that change files** show a preview first. When applied, Fieldkit backs up the
   files, runs the tool, checks the result, and puts the backup back if the check fails.
   `undo` restores them later.
3. **Tools that change the system** (registry, services, power, hardware) **or cannot be
   undone** wait for your approval. Over MCP there is no way for the AI to give it.

The process runner records the programs it starts and stops only those, never a program
of the same name that you are running yourself.

## 5. Your own tools stay private

Put your own tools in a `local/` folder next to the code. Git ignores it, and Fieldkit
merges it in when it loads:

| File | What goes in it |
|------|-----------------|
| `local/tools.yaml` | Cards for your own scripts (same format as `fieldkit/desk/tools.yaml`) |
| `local/imports.yaml` | Your own folders for `fieldkit gather` |
| `local/tests/` | Tests for your own scripts; `pytest` runs them with the rest |
| `fieldkit.local.json` | Machine paths and your private words for the privacy scan |

Copy `fieldkit.local.example.json` to `fieldkit.local.json` to start. Your name and email go
in its `privacy.terms` list, so the scan finds them without them ever being written in code.

## 6. Measure it on your own model

`fieldkit exam` builds the same small test project every time and gives your model five
tasks, once with basic tools and once with Fieldkit. It talks to an OpenAI-compatible
server on your own computer (LM Studio's address by default).

```
fieldkit exam run --model <model id as your server lists it>
fieldkit exam report
```

The result on the author's laptop:

| Model | Basic tools | With Fieldkit |
|-------|-------------|---------------|
| Gemma | 1 of 5, 762 s, 8,349 tokens | 5 of 5, 410 s, 6,630 tokens |
| Qwen3 30B-A3B | 3 of 5, 1,415 s, 60,379 tokens | 5 of 5, 1,030 s, 34,455 tokens |

Five tasks on one test project, written by the same author as the tools. It shows the
effect; it does not prove it for every job.

## 7. The release gate

A release spec in `releases/` lists the tag, its tests, the files that must match what was
tested, and the claims the release notes may make, each with a proof.

```
fieldkit release check releases/<name>.yaml    CLEAR, or DO NOT PUBLISH with every reason
fieldkit release prove releases/<name>.yaml    run the tag's tests on THIS machine; record evidence
```

A claim such as "runs on Windows and Linux" passes only with a passing `prove` from each
platform for the same code. There is no `--force`: a failing gate means you fix the
release or fix the check.

## 8. Honest limits

- The Debian kernel pipeline has run as a dry run only; its full build has not run yet.
- The `firefox-windows` pipeline needs the Gorilla Firefox build harness.
- The privacy scan finds patterns and the words you list. It cannot know a secret it has
  no pattern for.
- Fieldkit reads commands and files. It is **not a sandbox**.
- Office text extraction handles ordinary documents; for complex layouts, install the
  optional `[readers]` extra (markitdown, docling).
- Known faults are kept as tests marked "expected to fail", so a fix is noticed.

## 9. Layout

```
fieldkit/core     host, settings, process runner, privacy, pipeline engine, next, snapshot
fieldkit/office   read, create, check, scrub, deliver
fieldkit/build    triage + signatures, kernel, refcheck, lifecycle, pipelines
fieldkit/desk     registry (tools.yaml), cards, discover, readiness
fieldkit/exam     fixture, tasks and graders, toolsets, runner
fieldkit/thermal  proven temperature sensors, the build governor
fieldkit/buildh   Gorilla Firefox build harness: port, verify, repair, build-run, install, proof
fieldkit/leakgate the fail-closed leak and telemetry release gate
fieldkit/gdocs    Gorilla.Documentation.IBM.Style: fieldkit docs plan/prep/fill/render/check/index/guide
docs/             groups.yaml, dual-track/<group>/ layman and developer tracks, MEASUREMENTS.md
fieldkit/agent.py, mcp.py, release.py, gather.py, harvest.py
skills/           short SKILL.md pointers; install_skills.py puts them where agents look
tests/            python -m pytest
AGENTS.md         instructions for AI agents working on this code
```

Licence: [AGPL-3.0-or-later](LICENSE). If you run a changed version as a service, its
source must stay open too.
