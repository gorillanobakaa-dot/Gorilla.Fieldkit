# Fieldkit's tool desk: collect your scripts, see what each one does, and know which ones are safe to run — Plain Language Guide

> Generated 2026-10-02 from `tool-collection`

---

## Should You Run This?

Yes, for the read-only commands: run `fieldkit harvest`, `fieldkit tools list`, `fieldkit tools check`, `fieldkit cards`, `fieldkit readiness` and `fieldkit gather --check` freely. They read files, never go on the internet, and write only Fieldkit's own git-ignored folders. Only if you have read `imports.yaml` and agree with every source in it: run `fieldkit gather`. Do not run it while you have hand edits inside `toolbox/` that you want to keep. Only if you trust every test command listed: run `fieldkit gather --test` or `fieldkit tools check --run-tests`, because those run with your full user rights. When not to run any of it: when you expect it to tell you a script is harmless. It reads code; it does not prove behaviour, and it does not check non-Python scripts for file writes.

## Worst Case, Honestly

Two harms are plausible. First, `fieldkit gather` deletes the whole tray `toolbox/<name>/` and copies it fresh every time. If you edited a script inside `toolbox/` by hand, that edit is gone with no backup. For example, you fix a typo in `toolbox/mytools/clean.py`, run `fieldkit gather` the next day, and the typo is back. Second, `fieldkit gather --test` and `fieldkit tools check --run-tests` run the test commands written in `imports.yaml` and `tools.yaml`. Those commands run with your own rights. If a gathered repository contains a broken or hostile test, it can do anything you can do: delete files, change settings, or send data.

A smaller, remaining risk: the probe-safety check reads the script's own file only. Since today's fix it refuses any script that does real work at its top level, but it still treats `import` lines as harmless. If a script imports another file of its own that does work the moment it is loaded, starting the script with `--help` would do that work. This is the one way left for "safe to probe" to be wrong, and it needs the other file to be written that way.

## What Data This Touches

These modules read the script files you point them at and the two lists `imports.yaml` and `fieldkit/desk/tools.yaml` (plus private lists in `local/imports.yaml` and `local/tools.yaml` if you have them). They write only inside the Fieldkit folder: `toolbox/`, `_sources/repos/` (downloaded copies of GitHub repositories), `harvest/`, `state/test-results.json`, `state/cards-cache.json` and `local/READINESS.md`. Fieldkit's `.gitignore` lists all of these folders, so git does not publish them. Only `fieldkit gather` without `--check` contacts GitHub, to download or update the repositories listed in `imports.yaml`, and `--offline` stops even that. `fieldkit gather --check` never uses the network and writes nothing: no download, no update, no new folder. The source says plainly that nothing is ever pushed anywhere. After copying, `fieldkit gather` scans the new files for personal data and reports what it finds; it does not delete or hide findings. Each `PROVENANCE.json` records the folder a local source came from, which can include your user name; the scan skips that file on purpose. `fieldkit harvest`, `fieldkit tools list`, `fieldkit cards` and `fieldkit readiness` make no network connections. Reading a script to judge it (probe safety, draft cards) only parses its text; the script is never started.

## Before You Trust It

These commands copy files, delete and recreate folders inside Fieldkit, and can run test commands written by other people. Check the lists and the safe commands first.

**Step 1:** Open `imports.yaml` in Notepad (it is in the Fieldkit folder). Read each `github:` and `local:` line.
  - Look for: Pass: every source is one you know and want. Fail: a repository or folder you do not recognise; remove it before gathering.

**Step 2:** In the same file, read every `tests:` list.
  - Look for: Pass: each test is a command such as `[python, tests/test_parsers.py]` from a source you trust. Fail: a command you do not understand; do not use `--test` until you know what it does.

**Step 3:** Type this and press Enter:

```powershell
fieldkit gather --check
```
  - Look for: Pass: one line per source showing `in-sync`, `drift`, `not-gathered` or `source-unavailable`; nothing is downloaded and no files change. Fail: an `ERROR` line names the source that is wrong.

**Step 4:** Type this and press Enter:

```powershell
fieldkit tools check
```
  - Look for: Pass: each tool shows `present` and either `probe-safe` or `DO NOT PROBE` with a reason. No test runs because you did not add `--run-tests`.

**Step 5:** Type this and press Enter:

```powershell
fieldkit cards list --level gathered
```
  - Look for: Pass: the list shows the unreviewed tools and why each one is held back. Never ask an agent to run one of these without reviewing it first.


## The Big Picture

Over time you collect many small scripts in many places: some on GitHub, some in folders on your laptop. After a while nobody remembers what each one does, whether it still works, or whether starting it to look at its help text will make it do its whole job by surprise. This group of Fieldkit modules answers those questions for you, and for an AI agent working on your behalf.

It does three jobs. First, `fieldkit gather` copies your scripts from the places listed in a file called `imports.yaml` into one folder, `toolbox/`, and writes down where every file came from. Second, `fieldkit harvest` reads every script in that folder without running it and writes a short index: one line per script saying what it does and what it touches. Third, `fieldkit tools`, `fieldkit cards` and `fieldkit readiness` give every tool a contract card and a trust level, from "gathered" (only a copy exists) to "verified" (tested, portable, safe to start, with preview, undo and verify steps).

Think of it as the front desk of a workshop. The desk takes in tools, labels each one, keeps a register, and tells you which tools passed inspection and which are still in the box unchecked. The whole group was 974 lines of Python when the measurements file was written; today's security fixes made it longer (1,291 lines in the staged copies, not measured: counted by the offline pre-check for this document).

## Key Concepts

| Name | What It Means | Real-World Comparison |
|------|--------------|------------------------|
| `Toolbox` | The `toolbox/` folder inside Fieldkit where `fieldkit gather` puts a copy of each source, one sub-folder per source. | A shared shelf where you put a copy of every tool, each in its own labelled tray. |
| `Provenance` | The `PROVENANCE.json` file written into each tray. It records where the files came from, the commit if the source is a git repository, the time, and a fingerprint (SHA-256) of every file. | The delivery note in a parcel that lists every item and where it was sent from. |
| `Drift` | A difference between the copy in `toolbox/` and the original: a file added, removed or changed at the source, or a file someone edited inside `toolbox/`. | A photocopy of a recipe that no longer matches the cook's current version. |
| `Harvest index` | The files `harvest/harvest.json` and `harvest/HARVEST.md`: one short entry per script with its title, summary, options, the extra packages it needs, and what it touches. | A library catalogue card for every book, so you do not have to read each book to know what it is about. |
| `Probe safety` | Whether you can start a Python script with `--help` to see its options without it doing real work. Fieldkit decides this by reading the code, never by running it. | Checking a power tool has a safety catch before you plug it in to read its label. |
| `Tool card` | A record for each tool: what it is for, the inputs it takes, what it touches, how dangerous it is, how to preview, apply, undo and verify it, and how it is tested. | The instruction and safety sheet that comes with a hired machine. |
| `Trust ladder` | Four levels Fieldkit computes for every tool from facts: gathered, carded, tested, verified. Nobody can type a level in by hand. | A driving licence with stages: provisional, passed theory, passed practical, full licence. |
| `Draft card` | A card Fieldkit writes by itself from a gathered script's own code. It stays a draft until a person reviews it in `tools.yaml`, and a draft never rises above "gathered". | An unsigned form: filled in, but not yet checked by anyone responsible. |
| `Syntax tree` | The structure of a Python script that Fieldkit builds by reading its text, without running it: which lines are imports, which are definitions, which are calls, and what each call is called on. Fieldkit uses it to decide probe safety and, since today's fixes, to see whether a script writes, deletes or starts other programs. | Reading the floor plan of a house instead of walking through it: you can see every door and every tap without turning anything on. |
| `Offline check` | What `fieldkit gather --check` now is: it compares the toolbox with the copies already on your disk, never goes on the internet, and writes nothing. | Stock-taking with the shop door locked: you count what is on the shelves, and no delivery comes in while you count. |

## How It Works — Step by Step

### Step 1: Read the list of sources

`fieldkit gather` opens `imports.yaml` (and `local/imports.yaml` if present). Each entry names one source: either a GitHub repository or a folder on this machine, never both. Each entry also says which files to include and which to leave out. A default exclude list drops pictures, archives, programs, Office files, PDFs, logs and spreadsheets, so personal data and build output do not come in. If two sources share a name, or one names both GitHub and a folder, Fieldkit stops with an error. It is like a shopping list that says which shop to visit and which items to skip.

### Step 2: Fetch the source

For a GitHub source, Fieldkit makes a shallow copy (latest version only) into `_sources/repos/<name>` using the GitHub command-line tool `gh`, or updates an existing copy with `git pull`. If the update or the download fails, for example because you have no internet, Fieldkit now stops for that source and says so: the line shows `ERROR` with `git pull failed` and the words `the old copy was NOT used`, the tray in `toolbox/` is left exactly as it was, and the whole command ends with exit code `3`. With `--offline` it uses the copy already there, on purpose. For a local folder, it reads the folder in place and asks git for the current commit if the folder is a repository. It is like a courier who, finding the shop shut, comes back empty-handed and tells you, instead of handing you last week's parcel as if it were today's.

### Step 3: Copy, fingerprint and check

Fieldkit empties `toolbox/<name>/`, copies the selected files in, and records a SHA-256 fingerprint of each one in `PROVENANCE.json`. It test-compiles every `.py` file and lists any that do not compile. It then runs Fieldkit's privacy scan over the new files. Example from the tests: a source with `tool.py` and `data/private.json`, with `data/**` excluded, gives a tray holding only `tool.py`, and its fingerprint is 64 characters long.

### Step 4: Compare without copying or downloading

`fieldkit gather --check` compares each tray with its source as it already is on your disk, and reports `in-sync` or `drift`. It never goes on the internet, never updates a downloaded copy, and creates no folder, even if you leave out `--offline`. Drift lists files added, removed or changed at the source, and files edited inside `toolbox/`. A GitHub source that was never downloaded shows `source-unavailable`. Example from the tests: after the original `tool.py` changes and a `new.py` appears, the check reports `drift` with `changed_at_source` = `tool.py` and `added_at_source` = `new.py`. It is stock-taking with the shop door locked.

### Step 5: Read every script without running it

`fieldkit harvest` walks through `toolbox/` (or the folder you give with `--root`). It reads Python, PowerShell, shell, Rust, C, C++, C#, JavaScript and batch files as text. For each it takes the title and summary from the script's own documentation, lists its functions, its command-line options, the extra Python packages it needs, and what it touches. "Touches" comes from matching words in the text: for example `winreg` means the Windows registry, `Remove-Item` means it deletes files. Example from the tests: a Python file whose documentation starts "Render crisp icon frames from one master." gets that as its title, `--size` as its option, `PIL` as a needed package, and `registry` in its touches.

### Step 6: Write the index and answer questions

Harvest writes `harvest/harvest.json` and `harvest/HARVEST.md`, one line per script; for a test file `a/x.py` documented "Does X." the line ends `[python, 2 lines]`. `fieldkit harvest --find crisp icons` scores every script by how many of your words appear in its title, path, function names, summary, options and touches, with title words counting most. It skips test files and shows the 10 best matches by default. The source says the index is small on purpose so that a small local model can read it.

### Step 7: Decide if a script is safe to start

For Python files, Fieldkit reads the syntax tree of the code and answers "safe to probe" only if three things hold. One: the script has exactly the guard `if __name__ == "__main__":` (it only acts when started directly); a look-alike test does not count, and anything in that guard's `else` part counts as work. Two: it imports `argparse` and really calls `parse_args` (so it understands `--help`); a comment or example text that only mentions argparse no longer counts. Three: nothing at the top level does work. Since today's fix, "does work" is strict. Allowed at the top level: imports, definitions of functions and classes, explanatory text, and values written out in full or built from a short list of calculations that touch nothing, such as making a search pattern or a folder path. Refused: any other call, including one hidden on the right of `=` (for example `x = do_work()`), a decorator or default value that runs code, a class body that calls something, a loop, or opening a file. Otherwise the answer says why, for example `RUNS ON LOAD - line 1: call at module level`. The source explains why this exists: once, a script asked for `--help` had no option handling and ran its whole job instead. Think of a fire inspector who used to wave through any room labelled "storage" and now opens every cupboard.

### Step 8: Make a card and a trust level for each tool

Reviewed tools listed in `tools.yaml` get a curated card. Every other gathered script gets a draft card built from its own code. For a Python draft, Fieldkit now also reads the syntax tree for calls that write files, delete files or start other programs, and adds `writes-files`, `deletes-files` or `processes` to the card's effects. A draft with any of those is never guessed `read-only`, and a Python file that cannot be read at all is marked `unknown`. Fieldkit then computes the trust level. A draft, or a card with unknown inputs, unknown safety or no way to start it, stays "gathered". A reviewed card with no passing test is "carded". A card whose declared tests passed on the current file is "tested". It becomes "verified" only if it is also portable (no fixed path into one person's home folder), safe to start, and either read-only or has preview, undo and verify steps. If the file changes after its tests passed, it drops back to "carded" with the reason `file changed since its tests passed`. It is like a hire shop that will not label a machine "no blades" until it has looked inside the casing.

### Step 9: Count how much you can rely on

`fieldkit readiness` counts tools at each trust level, per source, and lists the 10 most common blockers. `fieldkit readiness --write-docs` writes those counts into `local/READINESS.md` between two markers, so the numbers always come from a fresh scan, never from someone typing them. The file describes your own machine, so it stays in the git-ignored `local/` folder.

## Quirky Things Worth Knowing

### A failed update now stops, loudly

Before today, a failed `git pull` was ignored and the older copy was used. Now the line for that source shows `ERROR`, says `git pull failed` and `the old copy was NOT used`, and `fieldkit gather` ends with exit code `3`. Your tray in `toolbox/` stays as it was. If you are offline on purpose, add `--offline`.

### `--check` and `--offline` together are no longer needed

`fieldkit gather --check` is always offline now. Adding `--offline` does no harm and changes nothing; older notes that tell you to add it are still correct.

### Hand edits in the toolbox do not survive

The source calls `toolbox/<name>/` "fully generated; no hand edits live here". Edit the original source, then gather again.

### "Touches" is still a word match for the index

Harvest flags a script as touching the network, the registry or processes by matching words in its text. A comment that mentions "Administrator" marks a script as `admin`. Harvest still has no word pattern for writing files. For Python drafts this gap is now covered by the syntax-tree reading described in How It Works; for PowerShell, shell and other scripts it is not.

### A draft card can still say "read-only" for a non-Python script that writes

The new code reading works on Python files only. A PowerShell or batch script that writes files with, say, `Set-Content` has no matching word in harvest, so its draft can still be guessed `read-only`. The card is still marked `safety_basis: inferred from code` and stays at the "gathered" level, so an agent must not run it unreviewed.

### Placeholders are not home folders

The portability check ignores documentation placeholders such as `/home/you`, using the same list as Fieldkit's privacy scan, so example text in a README does not count as a hard-coded home path.

### What this cannot do

It cannot tell you that a script is correct or harmless; it only reads what the code says. It does not run or sandbox anything, so it cannot catch work hidden inside another file that a script imports, or work done by a tool the script starts. It does not check non-Python scripts for file writes. It does not protect you from the test commands in `imports.yaml` and `tools.yaml`: those run with your full rights when you ask for `--test` or `--run-tests`. It never backs up `toolbox/` before rebuilding it. Its judgement of a curated tool's effects comes from the reviewed entry and harvest's word list, not from the new syntax-tree reading. Speed, memory and battery use are not measured.

## What This Means For You

### Battery, Processor & Memory

Not measured. Harvest, cards and readiness read files and parse them, and cards keep a cache in `state/cards-cache.json` so they rebuild only when a source changes.

### Speed

Not measured for this group alone. The whole Fieldkit test suite passed (584 passed, 1 skipped, 2 expected failures) in 194.89 seconds on the author's Intel i7-1255U laptop, as recorded in the measurements file; whether that run includes today's new security tests is not stated there.

### Your Privacy

Your scripts stay on your machine. The output folders are git-ignored. `fieldkit gather` scans copied files for personal data and lists any findings. `PROVENANCE.json` records local folder paths by design, so do not publish the `toolbox/` folder.

### Your Internet

Only `fieldkit gather` without `--check` uses the internet, to download or update GitHub repositories. Data use per download is not measured. `--offline` stops all downloads. `fieldkit gather --check`, `fieldkit harvest`, `fieldkit tools`, `fieldkit cards` and `fieldkit readiness` use no network.

## The Off Switch

**What it is:** There is no single off switch. Four things limit what happens: `--offline` (no downloads), `--check` (compare only: no download, nothing written anywhere), leaving out `--test` and `--run-tests` (no test commands run), and the trust ladder, which keeps every unreviewed draft at "gathered" so an agent must not run it.

**Without it:** Without `--offline`, every full gather contacts GitHub. Without the trust ladder, an agent would treat every gathered script as usable and could start one that deletes files or changes the registry.

**Think of it like:** A workshop where new tools sit in a locked cage until an inspector signs them off, and you can audit the cage without opening it.

## How to use this

**Before you start:**
- Fieldkit installed as the README describes (`python -m pip install -e ".[test]"` inside the Fieldkit folder). If `fieldkit` is not recognised, type `python -m fieldkit` instead.
- For GitHub sources: `git` and the GitHub command-line tool `gh` installed.
- An `imports.yaml` that lists only sources you trust.

**Step 1:** Open PowerShell: press the Windows key, type `PowerShell`, and press Enter. A window with a blinking cursor opens.
  - You should see: Pass: a window with a line ending in `>` and a blinking cursor. Fail: nothing opens; press the Windows key again and check the spelling.

**Step 2:** Go to the Fieldkit folder. Type this, with your own folder in place of the part in angle brackets, and press Enter:

```powershell
cd "<your Fieldkit folder>"
```
  - You should see: Pass: the line before the cursor now ends with the Fieldkit folder's name. Fail: `Cannot find path`; check the folder name in File Explorer and try again.

**Step 3:** If all your sources are folders on this computer, type this and press Enter:

```powershell
fieldkit gather --offline
```powershell

To also download the GitHub sources, type this instead and press Enter:

```powershell
fieldkit gather
```
  - You should see: Pass: one line per source with its name, the number of files, and `@` followed by a commit or `local`. Fail: a line with `ERROR` and the reason, such as a folder that does not exist on your machine, `git pull failed` or `clone failed` when the internet is down (the old copy is not used), or a warning that some `.py` files do not compile. The command ends with exit code `3` if anything failed.

**Step 4:** Type this and press Enter.

```powershell
fieldkit harvest
```
  - You should see: Pass: a short summary with `scripts`, `tests`, `undocumented`, `not_portable` and `out`. You can now open `harvest/HARVEST.md` in Notepad to read one line per script.

**Step 5:** Type this and press Enter. Replace `crisp icons` with your own words.

```powershell
fieldkit harvest --find crisp icons
```
  - You should see: Pass: up to 10 results, each a score and a script path with its title underneath. Fail: `no match` (exit code `3`); try other words.

**Step 6:** Type this and press Enter.

```powershell
fieldkit tools list --all
```
  - You should see: Pass: one line per tool showing its platforms, `changes` or `read-only`, `tested` or `NO TEST`, and `hard-coded-home:` with a count for scripts that will not run on another machine.

**Step 7:** Type this and press Enter.

```powershell
fieldkit cards list
```
  - You should see: Pass: one line per tool with its trust level, safety class, name and first blocker, ending with the total number of cards.

**Step 8:** Type this and press Enter. Replace `NAME` with a name from the list in step 5.

```powershell
fieldkit cards show NAME
```
  - You should see: Pass: the full card, including `trust` and `trust_blockers`. Fail: a `no card` message that tells you to see `fieldkit cards list`, if the name is mistyped.

**Step 9:** Type this and press Enter.

```powershell
fieldkit readiness
```
  - You should see: Pass: a first line that counts all tools and how many are verified, tested, carded and gathered only, followed by the top blockers. Add `--write-docs` to save it to `local/READINESS.md`.


## If Something Goes Wrong

**`fieldkit gather` shows `ERROR` and `clone failed`**
The download from GitHub failed: no internet, `gh` not installed, or no access to that repository.
What to do: Check your connection and that `gh` is installed, or run `fieldkit gather --offline` to use copies you already have.

**`fieldkit gather` shows `ERROR`, `git pull failed` and `the old copy was NOT used`**
Fieldkit could not update a repository it had downloaded before, usually because the internet is down. Since today it refuses to pass the old copy off as current.
What to do: Reconnect and run `fieldkit gather` again, or run `fieldkit gather --offline` to use the copy you already have, knowing it may be old.

**`ERROR` with `no clone at` and `run gather, online, to fetch it`**
You asked for offline mode, or for `--check`, but that repository was never downloaded.
What to do: Run `fieldkit gather --only NAME` once while online, replacing `NAME` with the source name.

**`ERROR` with `not found` after a source name**
A `local:` source points to a folder that does not exist on this machine. The supplied `imports.yaml` lists the author's own folders.
What to do: Remove that source from your copy of `imports.yaml` or correct the folder.

**`needs exactly one of github/local` or `duplicate source names`**
An entry in `imports.yaml` or `local/imports.yaml` is malformed or repeated.
What to do: Give each source a unique `name` and exactly one of `github:` or `local:`.

**My change to a file in `toolbox/` disappeared**
Each gather empties and rebuilds the tray.
What to do: Make the change in the original source, then gather again. `fieldkit gather --check` lists hand edits as `edited_in_toolbox` before you lose them.

**A tool I tested is back at "carded"**
The file changed after its tests passed, so the old pass no longer counts.
What to do: Run `fieldkit tools check --run-tests --id NAME` only if you trust its test command.

**`fieldkit tools check` now says `DO NOT PROBE` for a script it used to call `probe-safe`**
Today's stricter rule found work at the top level of the script, such as `x = do_work()`, or found that it only mentions argparse without using it.
What to do: Do not start that script with `--help`. Read the reason shown; the script's author can move the work under `if __name__ == "__main__":`.

## Why a Developer Would Do This

An agent that has to guess what a script does may start it to find out, and some scripts do their whole job the moment they start. The source records one such case. This group replaces guessing with facts read from the code: what the script says it does, what it touches, whether it is safe to start, and whether it passed tests on this exact file. The trust level is computed, never typed in, so it cannot drift from reality the way hand-typed counts once did.

## Why It Matters That You Can Read This

This group decides which of your scripts an agent may run. If you could not read it, you would be trusting blindly that "safe to probe" really means the script does nothing when asked for help, and that "verified" really means it passed tests on this exact file. Because the code is readable, you can see the actual rules: the three conditions for probe safety, the short lists of calls it accepts as harmless, the calls it treats as writing, deleting or starting programs, the four trust levels, and the exact word patterns behind "touches". Being readable is also how the old holes were found and closed today: assignments that hid work, the word argparse in a comment, a draft that writes files labelled read-only, and a check that went online. You can see the gaps that remain, described here, as plainly.

## Glossary

**Commit** — A saved version of a git repository, named by a short code.

**SHA-256** — A 64-character fingerprint of a file that changes if even one byte changes.

**Shallow clone** — A download of only the latest version of a repository, without its history.

**Probe** — Starting a script with `--help` to read its options.

**Portable** — Able to run on another computer because it has no fixed path into one person's home folder.

**Exit code** — A number a command leaves behind; `0` means fine and `3` means Fieldkit found a problem.

**Git-ignored** — Listed in `.gitignore`, so git never publishes it.

**Registry (Windows)** — The Windows settings database that harvest flags when a script mentions it.

**Syntax tree** — The structure of a script's code, read from its text without running it.

## Claim Sources

| Claim | Basis | Evidence |
|-------|-------|----------|
| gather never pushes anything | 📄 stated in input | nothing is ever pushed anywhere |
| toolbox trays are rebuilt and hand edits are lost | 📄 stated in input | toolbox/<name> is fully generated; no hand edits live here |
| registry check runs nothing unless asked | 📄 stated in input | Runs nothing except the listed tests, and only if asked. |
| harvest reads without running | 📄 stated in input | Reads every script under toolbox/ (or any folder) WITHOUT running it |
| probe-safety rule origin | 📄 stated in input | it has no argument handling and ran its whole job instead |
| trust levels are computed | 📄 stated in input | Trust ladder (computed, never declared) |
| drafts must not be run unreviewed | 📄 stated in input | draft card only - an agent must not run it unreviewed |
| readiness numbers come from the scan | 📄 stated in input | the numbers can never drift from the scan |
| index sized for a small model | 📄 stated in input | a 4B model can read HARVEST.md or ask --find |
| test commands run with full user rights and are the main risk | 🤖 model inference | *(none — model judgment)* |
| read-only commands are safe to run freely | 🤖 model inference | *(none — model judgment)* |
| gather --check is offline and writes nothing | 📄 stated in input | no network, nothing written |
| a failed pull or clone is an error and the old copy is not used | 📄 stated in input | the old copy was NOT used |
| argparse is detected from the syntax tree | 📄 stated in input | read from the syntax tree, not by |
| any other module-level call makes a file unsafe | 📄 stated in input | Any other call makes the file unsafe |
| unreadable Python drafts are never guessed read-only | 📄 stated in input | unreadable code: never guess read-only |
| imports of the script's own files remain a way for probe safety to be wrong | 🤖 model inference | *(none — model judgment)* |
| non-Python drafts can still be guessed read-only when they write files | 🤖 model inference | *(none — model judgment)* |
| curated card effects do not use the new syntax-tree reading | 🤖 model inference | *(none — model judgment)* |


---
**How to verify this document:**
`📄 stated in input` — the model's phrasing of something your source text said.
Find the matching line in the original to verify.
`🤖 model inference` — the model's own judgment or synthesis. Treat as opinion,
not measurement. Re-run on the same input and check whether specific numbers
stay consistent between runs.

*Human Track. Its Developer Track twin covers the same changes in technical detail. Neither is a simplified copy of the other — they are the same truth in two languages.*