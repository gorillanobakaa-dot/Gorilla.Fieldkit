# Checking a ported Firefox source tree before it is built, repairing known breakages, and recording every build stop — Plain Language Guide

> Generated 2026-10-02 from `verify-and-build`

---

## Should You Run This?

Run the read-only checks (`preflight`, `verify` without `--reopen`, `build-gate`, `audit`, `report`) whenever you port or build Gorilla Firefox: they change no source file. Run `repair` and `build-run` only on the porting working copy (their automatic fixes refuse to write anywhere else), on a machine with the maintainer's Gorilla.firefox folder, MozillaBuild, Node.js and a working fan-control program, and only after `build-gate` has passed or failed for reasons you understand. Do not run `build-run` on a laptop whose temperature sensor you have not proven. Do not treat a clean report as proof that the browser works: install it and test the address bar, extensions and the pages you use. Be aware that the three protections added on 2026-10-02 (writes confined to the working copy, no unproven repair, a reason every time the loop gives up) are in the code but have no automated test yet.

## Worst Case, Honestly

The realistic worst case is a browser that builds, installs and looks fine but has a quietly broken feature, because a check said PASS when it should not have. Every check here reads text patterns. A hunk made only of short or punctuation lines gets the verdict `NO-SIGNAL`, which means "cannot be judged", not "fine". The member check only follows UPPER_CASE constants, so a moved method or field is not caught.

The second risk is file changes you did not expect. The repair tool and the build loop edit source files, delete half-written object files from the build folder, delete excised paths from `dist/bin` before packaging, rename an old toolchain folder aside, and kill the whole build process tree when the temperature guard fires. Each of these is recorded, but a fix that is wrong for your tree costs you a rebuild. The source edits made by these fixes stay inside the porting working copy: a build message cannot steer the header fix into a file elsewhere on your computer, because a file name pointing outside the working copy is refused. A JavaScript repair is never kept unproven: without Node.js it is refused and the original file is put back.

The third risk is heat. On 2026-10-02 at 05:28 the laptop reset during a compile because the build governor read a sensor stuck at 41.85 C for 386 samples (measured). The build loop now refuses to start without a sensor proven to rise under load, but on a machine with no such sensor it runs with the processor capped at 80%, and an untested machine can still behave differently.

## What Data This Touches

Most of this group reads and writes files on your own computer only. Every file an automatic fix writes lies inside the porting working copy: when a build error message names a file outside that folder, the fix refuses and writes nothing. It reads the ported source tree, the maintainer's patch set, the task journal and Git history. The recorder reads the Gorilla OpenCode session database (every message, reasoning text, tool call and tool result of a model session) in read-only mode. The session watcher reads a coding-assistant session transcript from disk and appends its reports to a log file in Fieldkit's `_private` folder. Those transcripts contain everything typed in the session, so treat the reports and the log as private.

Three actions reach the network. `fieldkit build-harness audit` runs `git ls-remote origin` to check that nothing was pushed to GitHub, which contacts GitHub. The missing-toolchain fix in the build loop runs Mozilla's `mach artifact toolchain`, which fetches a compiler toolchain. `fieldkit build-harness preflight --model` asks a model server on your own machine (`127.0.0.1:1234`) which models it has. The source contains no code that uploads your files, transcripts or reports anywhere.

## Before You Trust It

This group edits source files, deletes build files and runs the maintainer's build scripts. Check that Fieldkit's own tests pass and that the read-only checks behave as described before you let the build loop act. To open a terminal on Windows: press the Windows key, type `PowerShell`, and press Enter. A window with a blinking cursor opens. Type each command exactly as shown and press Enter.

```
python -m pytest tests
```


**Step 1:** In the terminal, change to the folder where Fieldkit is installed (type `cd`, a space, the folder location, and press Enter), then run `python -m pytest tests`.
  - Look for: Pass: the last line shows passed tests and no failed ones; the measured full run on 2026-10-02 was 584 passed, 1 skipped, 2 xfailed in 194.89 s. Fail: any number followed by "failed".
**Step 2:** Run `fieldkit build-harness preflight`.
  - Look for: Pass: every row starts with PASS and the last line reads `PREFLIGHT: READY`. Fail: `PREFLIGHT: NOT READY` with the failing rows listed; nothing was changed.
**Step 3:** Run `fieldkit build-harness verify` (without `--reopen`).
  - Look for: Pass: a table of verdicts per patch group, PASS rows, and `VERIFY: CLEAN`. Fail: FAIL rows with examples and `VERIFY: N problem(s)`. Either way, run `git status` in the working copy afterwards: it must show no change, because verify is read-only.
**Step 4:** Run `fieldkit build-harness build-gate`.
  - Look for: Pass: `BUILD-GATE: PASSED` followed by the build commands. Fail: `BUILD-GATE: NOT PASSED` with the count of failed checks. Do not start a build on a failed gate.
**Step 5:** If a model or another person supervised the job, run `fieldkit build-harness audit` (a baseline must exist first).
  - Look for: Pass: every row PASS and `VERDICT: CLEAN`. Fail: `VERDICT: NOT CLEAN`; each FAIL row needs an explanation before you trust the job.

## The Big Picture

You use this part of Fieldkit when you move the Gorilla Firefox changes (a set of privacy patches) from one Firefox version to a newer one, and then build the browser. A program that only asks "does it compile?" misses the failures that hurt you most. A browser can build without a single error and still open with an address bar where pressing Enter does nothing. The source describes that exact case: the new Firefox moved a table of constants called `RESULT_SOURCE` from `UrlbarUtils` to `UrlbarShared`, the old code kept asking `UrlbarUtils` for it, the build was green, and every keystroke in the address bar threw an error.

This group of 15 Python files (2,881 lines when last measured, a little more after the fixes of 2026-10-02; the new total is not measured) checks the ported source tree itself before anything is compiled. It looks for a JavaScript file that does not parse, a module that reads a constant another module no longer has, a build list left empty, a change no patch explains, and a work record that claims steps the files do not contain. It repairs three known breakages by itself and proves each repair; when the proof cannot be run, the repair is refused and the file is put back. It then runs the maintainer's own build stage, recognises known stops, fixes them, retries, and writes every stop into a tamper-evident journal.

A second job of this group is supervision. When a language model (for example the small local model Gemma) does the porting work, three tools check that work from the evidence on disk instead of from the model's own report: `audit`, the recorder (`watch` and `report`), and the session watcher.

Three protections were added on 2026-10-02, and this document describes the tools with them in place. First, an automatic fix that reads a file name out of a build error message only ever writes to a file inside the porting working copy; a name that points anywhere else is refused and nothing is written. Second, a repair that cannot be proven because Node.js is missing counts as a failed repair, not a successful one. Third, when the build loop gives up, it always tells you why and what to do next. No automated test in Fieldkit's test folder covers these three protections yet.

## Key Concepts

| Name | What It Means | Real-World Comparison |
|------|--------------|------------------------|
| `source tree` | The folder of Firefox source files that the compiler turns into a browser. | The full set of drawings and parts for a house before anyone builds it. |
| `port` | Moving the Gorilla changes from the old Firefox version (155.0.1) to the new one (157). | Moving your alterations from last year's suit pattern to this year's pattern, where some seams have moved. |
| `hunk` | One small block of a patch: some lines to remove and some lines to add, with a few lines of surrounding context. | One instruction on a renovation list, such as "take out this wall, put a door here". |
| `journal` | The task's record of every event, each line chained to the previous one by a fingerprint so a rewrite shows. | A bound logbook with numbered pages: tearing one out leaves a visible gap. |
| `false completion` | A step the record calls done that the files on disk do not contain. | A builder's diary saying "door fitted" when the wall is still solid. |
| `excision` | Code the Gorilla patches cut out on purpose, marked with a `GORILLA excised` comment. | A room the plans say to remove, with a note on the wall saying why. |
| `build gate` | The list of checks that must all pass before the tree may be compiled. | A pre-flight checklist that keeps the aircraft on the ground until every line is ticked. |
| `stop` | A build attempt that ended with an error, recorded with a short name called a signature. | A breakdown recorded in a vehicle log with the fault code, so the next mechanic knows it at once. |
| `baseline` | A snapshot of known-good state that the audit compares against later. | A photo of a room before guests arrive, so you can see what moved. |
| `working copy` | The one porting folder that this group is allowed to change. Every automatic fix writes only to files inside it. | A workshop bench: the mechanic may take apart anything on the bench, but not the car parked outside. |
| `proof of a repair` | After a repair changes a JavaScript file, Node.js reads the file to confirm its grammar. No proof means no repair: the old file goes back. | A gas engineer who must run the leak test before leaving; if the test kit is missing, the old fitting goes back on and the job is written up as not done. |
| `port fix and privacy cut` | The two groups the maintainer's patch set is sorted into: changes that make the patches fit the new Firefox, and changes that remove a way the browser contacts others. | Two trays on a tailor's table: one for alterations that make last year's suit fit, one for pockets sewn shut on purpose. |

## How It Works — Step by Step

### Step 1: Why a tree that compiles can still give a broken browser

A compiler checks C++ and Rust code. Most of Firefox's interface is JavaScript, which nobody checks until the browser runs it. The source records two real cases. In the first, a half-removed block in `DesktopActorRegistry.sys.mjs` built without complaint, then killed every window actor in the installed browser: no address bar and no extensions. In the second, the address-bar code read a constant from a module that no longer had it, so Enter did nothing. There is a third kind of failure that has nothing to do with code: the work record can be wrong. An independent audit on 2026-10-01 found 208 steps marked done that the tree did not contain, 44 files silently replaced by the old tree, four edits no patch asked for, 124 new files never copied and 560 upstream files deleted. None of that showed in the journal. Think of a builder's diary that says every job is finished while the walls tell another story: this group reads the walls.

### Step 2: Is the machine fit to start? (preflight)

Before any job starts, `fieldkit build-harness preflight` checks the machine and the working copy. Each row prints PASS or FAIL with evidence. It wants 20 GB free for a porting job or 120 GB for a build (`--build`), 16 GB of memory, `git` and `patch` on the system path, an intact vault copy of upstream Firefox, a working copy that exists and has no uncommitted files, and no leftover Git lock files. On Windows it also checks that the fan-control program runs. It exists because during the overnight run of 2026-10-01 a stale Git lock crashed eight jobs in a row and nobody had checked the machine. It changes nothing, with one exception: with `--fix-locks` it deletes lock files, and only when no Git process is running at all.

### Step 3: Reading the tree, not the record (verify)

`fieldkit build-harness verify` takes every hunk of every enabled patch group and looks for it in the actual files. Each hunk gets one of five verdicts. `APPLIED` means the added lines are there and the removed lines are gone. `PARTIAL` means some are there. `NOT-APPLIED` means the change is missing, or the added lines sit in the wrong place, or upstream renamed the old lines and they still exist under new names. `TARGET-GONE` means neither the old nor the new lines exist, so upstream removed that code. `NO-SIGNAL` means the hunk holds only short or punctuation lines and cannot be judged by text. It then compares those verdicts with the task record. A step the record calls done but the tree scores `NOT-APPLIED` or `PARTIAL` is a false completion. Verify changes no file. With `--reopen` it puts each false completion back to pending in the task record, so a wrong record cannot carry a step past the build gate.

### Step 4: What each tree check looks for

Verify prints one PASS or FAIL row per check. A file that does not parse: every changed `moz.build` and `.py` file goes through Python's parser, every `.json` file through a JSON reader, and every `.js` or `.mjs` file through `node --check`. A module reading something another module no longer has: for each changed JavaScript file it follows the import to the module file and checks that every UPPER_CASE member read from it, such as `X.RESULT_SOURCE`, is still defined there. An empty build list: a `moz.build` line such as `DIRS = []` is valid Python but Mozilla's build system refuses it with "Variable DIRS assigned an empty value"; this stopped build 11 on 2026-10-02 after a cut left an empty list. An unexplained change: any changed file that no patch, new-file list, deliberate deletion or recorded step names is a stray edit. The other rows catch files byte-identical to the maintainer's old tree where upstream had changed them, new files of the patch set that are missing, upstream files deleted without a patch, and translation (Fluent) messages defined more or fewer times than in the maintainer's own tree.

### Step 5: What the repair tool fixes by itself, and how it proves the fix

`fieldkit build-harness repair` fixes three breakages and refuses everything else. First, a dangling excision: the `GORILLA excised` marker is in the file, but the tail of the block it meant to remove is still below it, so the file does not parse. The tool finds the marker within 80 lines above the parse error, removes lines until the curly brackets close below where they started, and runs `node --check`. If Node.js is not installed at all, the tool cannot prove anything, so it treats that as a failed proof: it puts the original bytes back and reports "node is not installed: the repair cannot be proven". If Node still refuses the file, the tool puts the original bytes back and reports "restored". It refuses to remove more than 400 lines. Second, a moved member such as `UrlbarUtils.RESULT_SOURCE`. It first reads upstream's own change to that file and applies the rename upstream made. If that gives no answer, it looks for exactly one module in the same component folder that defines the member, adds a loader line for it, and rewrites the reference. Two candidates or none: refused. Each rewrite must pass `node --check` and the member check, or the file is restored. Third, an empty `moz.build` list: the statement goes and a comment records why. Every repair is saved as a recorded hand step with a Git checkpoint, so the verifier judges it like any other change. Each repair is also labelled a port fix, so when the maintainer later exports the hand steps into the patch set, the repair lands with the port fixes and never among the privacy cuts. This is like a mechanic who may only fit parts from an approved list and must run the engine test before handing the car back.

### Step 6: The maintainer's own preflight becomes steps (ownercheck)

The maintainer wrote about 40 checks of their own after real build failures. On 2026-10-01 their first run on the ported 157 tree found four blockers the port could not see. This step runs that script at the end of every port and turns each BLOCKER line into a step for the maintainer, carrying the maintainer's own fix text. Four checks can only pass once a build folder exists (package manifest, localisation resources, bundled extensions, build folder against the CLOBBER file). Those are parked as "deferred" and checked again after the build. A blocker that later disappears is closed as obsolete with that reason.

### Step 7: The build gate

`fieldkit build-harness build-gate` decides whether the tree may be compiled. Every step must be done, the final checks must have passed with nothing changed since, nothing may be skipped or copied from the old tree, the journal's fingerprint chain must be intact, the working copy must have no uncommitted edits or `.rej` / `.orig` leftovers, and every verify row must pass. When all rows pass, it records the exact Git tree and a fingerprint of the build configuration file (`mozconfig`). After the build, `build-verify` checks against that record. This is the sealed envelope: the build afterwards must match what was sealed.

### Step 8: The build loop: recognise a known stop, fix it, retry

`fieldkit build-harness build-run` runs the whole loop. It runs the build gate. If the only failures are stale final checks, it re-runs them. If the only failures are a JavaScript file that does not parse or a moved member, it runs `repair` once and judges the gate again. It runs the maintainer's preflight and refuses on any blocker that is not one of the four build-dependent ones; for those four it needs `--force`. It proves a temperature sensor rises under load, first waiting up to 10 minutes for the machine to fall below 35% processor use. Then it runs the maintainer's build stage, writes all output to a log, prints a progress line every five minutes, and lets the temperature governor hold the processor near 75 C. Before each build attempt it deletes object files that are empty or have no valid header, which a killed build leaves behind. When the stage stops, the loop compares the output with a fixed list of known stops, first match wins. A known stop with a fix, for example a half-written object file, a header from a removed component, a missing toolchain, a required clobber, a doubled translation message, a console interrupt, an undeclared `jar.mn` or an empty `moz.build` list, runs its fix, records it in the journal, and tries again. The header fix reads the name of the file to edit out of the compiler's error message. Before it touches anything, it works out where that name really leads, following any `..` steps and folder shortcuts, and checks that the result is inside the working copy. If it is not, the fix refuses that file with the words "outside the working copy" and "refused, nothing written". Think of a courier who reads the address on the parcel but only delivers inside the one building he works in: a parcel addressed elsewhere goes back undelivered. It allows four retries per stage. The same stop twice in a row ends the loop, because the fix made no progress. An unknown stop, or a known one with no automatic fix (disk full, compiler missing, temperature abort, a process killed from outside), ends the loop with the first error lines in the journal for a person to turn into the next fix. When the loop gives up, it always prints the reason and the next step. The reason is one of three: "no known fix", "the same stop again after its fix: no progress", or "still stopping after" a number of fix attempts. The next step that follows is always the same: the signature and the first errors are in the journal; write the tool, add it to the list of stops, run again. Before packaging it removes excised files left in `dist/bin` and puts the Gorilla icon on the installer stub. It ends with `build-verify`.

### Step 9: Checking what came out (build-verify)

`fieldkit build-harness build-verify` checks that the tree and `mozconfig` are unchanged since the gate, that the installer and zip are newer than the gate (the maintainer's own tools once recorded a seven-month-old build), that each is at least 50 MB, and that the built `firefox.exe` reports the pinned version. It byte-matches the Gorilla icon images inside `firefox.exe` and the installer, runs the maintainer's logo and installer-icon tools, and runs the maintainer's preflight again. When every row passes it writes SHA-256 fingerprints of the installer and zip to `build-result.json`.

### Step 10: Checking a supervisor model from the evidence (audit)

`fieldkit build-harness audit` checks the work of whoever supervised the job against a baseline taken earlier with `fieldkit build-harness audit baseline`. It checks that the vault copy is unchanged, that the maintainer's Gorilla OpenCode settings files are byte-for-byte the same, that the maintainer's patch set and 155.0.1 source are untouched, that the working copy holds only the harness's own checkpoints and nothing uncommitted, that no test fails, that the passed-test count has not fallen, that no skip or expected-failure markers were added, that no test file was deleted, and that GitHub's main branch has not moved. From the journal it checks that no step was done by copying the old source, that no step was skipped, that nothing was marked done while the final checks were failing, and that the journal's fingerprint chain and earlier history are unchanged. It recounts the tally from the task record so you can compare it with what the supervisor told you. This is an accountant counting the till instead of reading the cashier's note.

### Step 11: Watching a model while it works (recorder and session watcher)

`fieldkit build-harness watch` prints findings live while a model works; `fieldkit build-harness report` judges a whole run and saves `recorder-report.json`. Both read the Gorilla OpenCode session database, Fieldkit's tool-call flight recorder and the task journal, all read-only. Each rule is plain and fixed. Examples: more than 20 tool calls in 60 seconds is command spam; the same call three times in a row is a retry loop; a model saying "done" or "fixed" without having called any tool is confabulation; a file edited more than six times is patch thrashing; a test file that loses `assert` lines or gains skip markers is testing theatre; a `git push` or `git reset --hard` is Git vandalism. An "incident" means the rule proves it; "review" means a person should look. The session watcher does the same for a coding-assistant session: it runs in its own terminal, reads the transcript from disk, and writes a report the supervised model cannot edit. Its verdict is "quiet", "watch closely" or a stop for the maintainer to look.

### Step 12: Slower checks you run on purpose

Three checks are too slow for the gate and run only when you ask. `fieldkit build-harness truthbound` holds every changed file against the maintainer's own built 155 tree and lists changes that tree does not explain and no step names. `fieldkit build-harness creep` lists new upstream code that names things the Gorilla patches remove; it left the gate after holding it for three hours. `fieldkit build-harness compare --reference FOLDER` compares the job's result file by file with a result a person made by hand.

## Quirky Things Worth Knowing

### A green build is not a pass

The checks here exist because builds succeeded and the browser was still broken. Do not treat "the compile finished" as the end. Run `build-verify` and look at the installed browser.

### NO-SIGNAL is not a pass

A hunk made of short lines such as a lone `}` cannot be judged by text. Verify counts it separately. Nothing in this group proves those hunks are correct.

### Without Node.js, JavaScript is neither checked nor repaired

When `node` is not installed, verify adds a FAIL row saying the JavaScript files were NOT syntax-checked, so the gate stays closed. The repair tool refuses every JavaScript repair in that case, puts the original file back and says "node is not installed: the repair cannot be proven". Nothing is quietly accepted. Install Node.js before you run `verify` or `repair`.

### A file name inside a build message is not trusted

The build loop's header fix takes the name of the file to edit from the compiler's own error text. That text is treated as a hint, not an order. The fix follows the name to where it really leads and edits the file only when it sits inside the porting working copy. Anything else is listed as refused, and nothing is written. The other two fixes that take a file name from a build message (the `jar.mn` fix and the empty-list fix) refuse a file outside the working copy in the same way.

### The same stop twice ends the loop on purpose

If a fix runs and the identical stop comes back, the loop stops instead of trying the same fix again. The source states the aim: the same stop never costs a second investigation.

### The build refuses to run blind

With no temperature sensor that rises under load, `build-run` refuses to start. This follows the measured laptop reset of 2026-10-02, when the governor read a sensor stuck at 41.85 C for 386 samples.

### `--force` only passes four named checks

It does not override the build gate. It only lets through the four maintainer checks that need a build folder to exist, and each forced name goes into the journal.

### The audit runs the whole test suite

Both `audit` and `audit baseline` run every Fieldkit test. The last measured full run took 194.89 s (584 passed, 1 skipped, 2 xfailed). Expect that wait.

### The pre-check's "finish this later" note is a search pattern

The offline pre-check flagged one TODO marker in `recorder.py`. That word sits inside a pattern that counts TODO markers added to test files as a sign of weakened tests. It is not a note about unfinished work in the recorder.

### Some paths are fixed to the maintainer's machine

The audit looks for the patch set, the 155.0.1 source and the Gorilla OpenCode settings in fixed folders under your home folder. The clobber and toolchain fixes expect MozillaBuild at `C:\mozilla-build`. `truthbound` expects a task named `firefox-155.0.1`. On another machine these checks report missing items.

### What this cannot do

It does not prove the browser works: the checks read text patterns, and a hunk made of short lines is `NO-SIGNAL`, not a pass. The member check follows only UPPER_CASE constants, so a moved method or field is not caught. It does not judge whether an automatic fix is the right change for your tree; it only proves the file still parses and is recorded. It does not protect files outside the working copy from programs it starts on purpose: the maintainer's build stage, `mach` and the clobber script run with your full rights. It does not check JavaScript at all without Node.js. It cannot recognise a build stop it has never seen; a person writes that fix. The recorder's findings are fixed rules whose misses and false alarms are not measured. The three protections added on 2026-10-02 (the working-copy check, the Node.js proof rule and the give-up message) have no automated test yet.

## What This Means For You

### Battery, Processor & Memory

The build itself loads every processor core for a long time; how long is not measured here. The governor aims to hold the processor near 75 C, caps it at 80% when only a surface sensor exists, and the watchdog kills the build at 95 C. `audit` runs the full test suite (194.89 s measured). `verify` reads every changed file; its run time is not measured. Memory use is not measured; preflight asks for 16 GB installed.

### Speed

These checks add time before and after a build, and save time when they catch a broken browser before you install it. The amount of time added or saved is not measured. The source notes that sweeping half-written object files takes about one second over 4,000 objects, and that the slow `creep` check was taken out of the gate after it held the gate for three hours.

### Your Privacy

The recorder and the session watcher read full session records, including everything you and the model wrote. They keep their reports on your machine (`recorder-report.json` in the task's state folder and a log in Fieldkit's `_private` folder). Do not share those files without reading them first. The source contains no upload of them.

### Your Internet

Three actions use the network: the audit's check of GitHub's main branch, the missing-toolchain fix that fetches a Mozilla toolchain, and the preflight `--model` check of a model server on your own machine. Data volume is not measured. The rest of the group works offline.

## The Off Switch

**What it is:** There is no single off switch. These controls stop or limit the group: the build gate (nothing compiles while any row fails); `build-run` refusing without a proven temperature sensor; the temperature watchdog that kills the build process tree at 95 C; pressing Ctrl+C in the build window, which the loop records as "interrupted"; and leaving out `--reopen`, `--fix-locks` and `--force`, so verify and preflight change nothing and build-dependent blockers stay blocking.

**Without it:** Without the gate, a tree with a broken JavaScript module or a false completion would compile and install, and you would find the damage in the running browser. Without the temperature guard, a build with a dead sensor could run the laptop until it resets, as it did on 2026-10-02.

**Think of it like:** A circuit breaker plus a pre-flight checklist: the checklist keeps you on the ground until every line is ticked, and the breaker cuts the power by itself when something overheats.

## How to use this

**Before you start:**
- Windows 11 with Fieldkit installed, so that `fieldkit` runs in a terminal.
- A Firefox porting task started with `fieldkit build-harness start firefox` and approved by the maintainer.
- Git, `patch` and Node.js installed. Node.js runs the JavaScript syntax check; without it `verify` fails its syntax row and `repair` refuses every JavaScript repair.
- For a build: the maintainer's Gorilla.firefox folder with `harness/gorilla_build.py`, MozillaBuild, 120 GB free disk space, 16 GB memory and the fan-control program running.

**Step 1:** Open PowerShell: press the Windows key, type `PowerShell`, and press Enter. A window with a blinking cursor opens.
  - You should see: Pass: a window with a line ending in `>` and a blinking cursor. Fail: nothing opens; press the Windows key again and check the spelling.
**Step 2:** Go to the Fieldkit folder. Type the command below, with your own folder in place of the part in angle brackets, and press Enter.

```powershell
cd "<your Fieldkit folder>"
```
  - You should see: Pass: the line before the cursor now ends with the Fieldkit folder name. Fail: "Cannot find path"; check the folder name and try again.
**Step 3:** Run the command below.

```powershell
fieldkit build-harness preflight --build
```
  - You should see: Pass: `PREFLIGHT: READY`. Fail: the failing rows; for stale Git locks the row tells you to rerun with `--fix-locks`.
**Step 4:** Run the command below.

```powershell
fieldkit build-harness verify --reopen
```
  - You should see: Pass: `VERIFY: CLEAN`. If false completions exist, the last line lists the steps that went back to pending; finish those steps before going on.
**Step 5:** If verify shows a JavaScript file that does not parse, a moved member or an empty build list, type the command below and press Enter.

```powershell
fieldkit build-harness repair
```
  - You should see: Pass: `REPAIR OK: N repaired, 0 refused`. Fail: `REPAIR NOT OK` with each refused item and its reason; a person fixes those. If the reason says "node is not installed: the repair cannot be proven", install Node.js and run it again.
**Step 6:** Run the command below.

```powershell
fieldkit build-harness build-gate
```
  - You should see: Pass: `BUILD-GATE: PASSED` and the tree is recorded. Fail: the failing rows.
**Step 7:** Type the command below and press Enter. Leave this window open until it finishes.

```powershell
fieldkit build-harness build-run
```
  - You should see: Pass: progress lines every five minutes, then `build OK` and `package OK`, the build-verify rows, and `BUILD OK`. Fail: `BUILD NOT OK` with the stop signatures, and a line saying why the loop gave up and what to do next; the first error lines are in the journal.
**Step 8:** If it stops because only build-dependent maintainer checks remain, type the command below and press Enter.

```powershell
fieldkit build-harness build-run --force
```
  - You should see: Pass: the build starts and the forced check names appear in the journal. Fail: a hard blocker is reported and nothing starts.
**Step 9:** Read the journal: type the command below and press Enter.

```powershell
fieldkit build-harness log
```
  - You should see: The last 40 events, including each `build-stop`, `build-fix` and `build-verified` line.

## If Something Goes Wrong

**`PREFLIGHT: NOT READY` with a Git locks row.**
A Git program crashed and left a lock file.
What to do: Close any Git programs, then run `fieldkit build-harness preflight --fix-locks`. It deletes the locks only when no Git process runs.

**Verify shows "node is not installed: changed .js/.mjs files were NOT syntax-checked".**
Node.js is missing, so JavaScript files cannot be checked.
What to do: Install Node.js and run `fieldkit build-harness verify` again.

**`REPAIR NOT OK` with "2 module(s) ... define" or "0 module(s)".**
The repair tool does not guess: more than one or no module could own the moved constant.
What to do: A person decides which module is right and edits the file; record the edit so the verifier judges it.

**`BUILD NOT OK` with the signature `unknown`, and a line that starts "no known fix; the signature and the first errors are in the journal".**
The stop matches no known pattern, so there is no automatic fix to run.
What to do: Read the first error lines in `fieldkit build-harness log` and the build log under the task's state folder. A developer adds a new entry to the list of stops, as the message says.

**`BUILD NOT OK` and a line ending "the same stop again after its fix: no progress" or "still stopping after" a number of fix attempts.**
A fix ran but the build stopped again: either in the identical way, or still stopping after every allowed attempt.
What to do: Do not run the build again unchanged. Read `fieldkit build-harness log`: the fix it ran and the first errors are there. A person decides the next fix.

**The build refuses with "no CPU temperature source responds to load".**
No sensor rose when the machine was loaded, so the build would run without a working temperature guard.
What to do: Make sure the fan-control program runs and the machine is idle, then run `fieldkit thermal prove` and try again.

**`audit` refuses with "no audit baseline".**
Nobody took the known-good snapshot.
What to do: While everything is known-good, the maintainer runs `fieldkit build-harness audit baseline`.

**The build log or journal says "outside the working copy" and "refused, nothing written".**
The compiler's error message named a file that does not lie inside the porting working copy, so the header fix refused to edit it.
What to do: Nothing was changed. Look at the named file and the error yourself. If the file truly belongs to the tree, the working copy may be in a different folder from the one the task records; check the task with `fieldkit build-harness status`.

**`REPAIR NOT OK` with "node is not installed: the repair cannot be proven" and "restored".**
Node.js is missing, so no repair could be proven, and each one was undone.
What to do: Install Node.js, open a new PowerShell window so it is found, and run `fieldkit build-harness repair` again.

## Why a Developer Would Do This

Each rule here comes from a failure that already happened: a work record that claimed 208 steps the tree did not hold, a browser whose Enter key did nothing, a build that stopped on an empty list, a laptop that reset mid-compile. The developer wrote one check and, where possible, one automatic fix per failure, so the same failure is caught by a program next time instead of by a person after hours of searching. The supervision tools exist because language models can report work they did not do; the safest answer is to check the files, the journal and the tool-call records, not the model's words.

## Why It Matters That You Can Read This

Every rule this group applies is a few lines of readable Python, with the reason and the date of the failure that caused it written next to it. You can see that a repair is refused when two modules qualify, that `NO-SIGNAL` hunks are not judged, and that the recorder's categories are fixed text patterns, not a model's opinion. That matters most for the supervision tools. Their whole point is that you do not have to trust the report of the model that did the work. If you could not read the rules, you would be trusting the checker's report blindly instead, which is the same problem one level up. Because the rules are open, someone who reads Python can confirm what a PASS means and what it does not cover.

## Glossary

**compile** — Turning source files into a program you can run.

**parse** — Read a file and confirm its grammar is valid for its language.

**`moz.build`** — A file that tells Mozilla's build system which folders and files to include.

**upstream** — Mozilla's own Firefox code, before the Gorilla changes.

**object file** — A compiled piece of the program that the linker later joins into `firefox.exe`.

**clobber** — Deleting the old build output so the next build starts clean.

**toolchain** — The compilers and helper programs the build needs.

**Fluent** — Mozilla's format for the text you see in the browser's menus and pages.

**Git checkpoint** — A saved snapshot of the working copy that can be compared or restored later.

**SHA-256** — A fingerprint of a file; any change to the file changes the fingerprint.

**working copy** — The porting folder that this group's automatic fixes are allowed to change, and nothing outside it.

**Node.js** — A program that runs JavaScript; here it only reads a file to confirm the file's grammar is valid.

## Claim Sources

| Claim | Basis | Evidence |
|-------|-------|----------|
| A green build can still give a broken address bar | 📄 stated in input | The file parses, the build is green, the lazy getter exists, and every keystroke throws |
| The work record once claimed 208 steps the tree did not hold | 📄 stated in input | found 208 'done' steps in the overnight working copy that the tree did not back |
| Verify changes no file unless --reopen, and then only the record | 📄 stated in input | even then it only changes the task record, never a file in the tree |
| Repair restores the file when Node refuses it | 📄 stated in input | If node still refuses, the file is restored and the step is left for a person |
| Moved-member repair refuses with two or zero candidates | 📄 stated in input | Two or zero candidates: refused, reported. |
| The same stop after its fix ends the loop | 📄 stated in input | the same stop again after its fix: no progress |
| The build refuses without a sensor proven under load | 📄 stated in input | no build without a CPU sensor proven to move under load |
| The laptop reset because the governor read a stuck sensor | 📄 stated in input | chassis ACPI zone stuck at 41.85 C for 386 samples |
| Group size is 2,881 lines | 📄 stated in input | verify-and-build 2,881 |
| Full test suite result | 📄 stated in input | 584 passed, 1 skipped, 2 xfailed in 194.89 s |
| The precheck TODO finding is a search pattern, not unfinished work | 🤖 model inference | *(none — model judgment)* |
| A wrong automatic fix costs a rebuild, not lasting damage | 🤖 model inference | *(none — model judgment)* |
| Text-pattern checks can pass a hunk that is semantically wrong | 🤖 model inference | *(none — model judgment)* |
| Session reports should be treated as private | 🤖 model inference | *(none — model judgment)* |
| Fixed folder paths make some checks machine-specific | 🤖 model inference | *(none — model judgment)* |
| A fix that takes a file name from a build message writes only inside the working copy | 📄 stated in input | only files under the task's workdir are ever edited |
| A missing Node.js makes a repair fail instead of pass | 📄 stated in input | A missing node is a failed proof |
| The build loop always says what to do next when it gives up | 📄 stated in input | always followed by what to do next |
| Repairs are recorded as port fixes | 📄 stated in input | kind="port" |
| No automated test covers the three protections added on 2026-10-02 | 🤖 model inference | *(none — model judgment)* |
| Programs the build loop starts on purpose are not confined to the working copy | 🤖 model inference | *(none — model judgment)* |


---
**How to verify this document:**
`📄 stated in input` — the model's phrasing of something your source text said.
Find the matching line in the original to verify.
`🤖 model inference` — the model's own judgment or synthesis. Treat as opinion,
not measurement. Re-run on the same input and check whether specific numbers
stay consistent between runs.

*Human Track. Its Developer Track twin covers the same changes in technical detail. Neither is a simplified copy of the other — they are the same truth in two languages.*