# Fieldkit core: the shared engine that runs jobs step by step, checks for secrets and records what changed — Plain Language Guide

> Generated 2026-10-02 from `core`

---

## Should You Run This?

Run `fieldkit host`, `fieldkit pipeline list`, `fieldkit pipeline plan`, `fieldkit next` and `fieldkit snapshot` freely: they only read, or write records inside the Fieldkit folder. Run `fieldkit privacy scan` before you publish anything, but treat a clean result as "no known pattern matched and nothing was left unread", not as proof the files are safe; read them as well, especially PDFs made from scanned paper and pictures, whose words it cannot read. Treat every `not-scanned` line as a file you still have to check yourself. Run `fieldkit pipeline run` only with the three pipelines that ship with Fieldkit or with pipeline files you wrote yourself, and run `fieldkit pipeline plan` first. Do not run a pipeline file someone sent you unless someone you trust has read it, because a stage can run any program on your computer. The Linux parts are not measured on Linux; on a Linux computer, check snapshot results by hand the first time.

## Worst Case, Honestly

The most likely real harm is a false sense of safety. You run `fieldkit privacy scan`, it prints `0 finding(s) in 0 file(s)`, and you publish. The scan only knows eight kinds of secret, four kinds of personal detail and your own word list. It does not look for phone numbers, street addresses, bank details or names you did not put on your list. It does not read words inside pictures, so a scanned letter saved as a PDF passes unread. It reads a file such as an OpenDocument `.odt` file, which is packed like a `.zip` but not on its list of archive types, only as raw packed bytes, which hides the words inside. A file larger than 5,000,000 bytes or a file it cannot open no longer passes in silence: it shows up as a `not-scanned` line and the scan does not end clean.

The second harm comes from running a pipeline file you did not write. A pipeline stage can run any program and any Python code on your computer, with your permissions. If a stranger's pipeline file contains a stage that deletes your documents, `fieldkit pipeline run` runs it. Fieldkit does not sandbox stages.

A smaller harm: when `fieldkit snapshot take --path` cannot open a file, it leaves that file out of the record without saying so. If the file could be opened in one snapshot and not in the other, `fieldkit snapshot diff` shows it as added or removed although nothing happened to it.

## What Data This Touches

Nothing in this group sends anything over the internet. No part of it opens a network connection.

What it reads: `fieldkit privacy scan` reads every file in the folder you name. It opens `.zip`, `.docx`, `.xlsx`, `.pptx` and similar files and reads each file packed inside them, including archives packed inside archives, up to three levels deep. In a PDF it unpacks the compressed parts where text hides and also reads the text a PDF reader would show you. It skips `.git`, `node_modules`, `__pycache__`, `.venv`, `venv` and `.pytest_cache` folders. `fieldkit snapshot take` reads the files you name with `--path`, and, only if you ask, the list of Windows services, scheduled tasks, installed programs (from the Windows uninstall list in the registry) and running program names. `settings` reads `fieldkit.local.json` or `fieldkit.local.yaml`, your private per-computer file. `proc` asks Windows or Linux when each program it started began running.

What it writes, all inside the Fieldkit folder under `state/`: pipeline memory (`state.json`), the last run report (`last-report.json`), one log file per program run in `logs/`, the list of programs Fieldkit is running right now with their start moments (`started-pids.json`), and snapshot files in `state/snapshots/`. A program is removed from `started-pids.json` when it finishes, and entries for programs that have ended are cleared out the next time Fieldkit starts one. The `.gitignore` file in the Fieldkit folder lists `state/`, `logs/` and `fieldkit.local.json`, so git does not publish them.

Be aware: a snapshot file stores the full path of every file it recorded, which includes your home folder name. A log file stores everything the program printed. Do not send those files to anyone without running `fieldkit privacy scan` on them first.

The privacy scan shows a shortened copy of what it found: the first four characters, an ellipsis and the last two. The report is therefore safe to paste, but it also means you must open the file at the given line to see the full text.

## Before You Trust It

Fieldkit runs programs on your computer and reads your files. Before you rely on it, check that it is installed, that its own tests pass, and that the privacy scan really catches something. You do not need to read code for any of these steps.

**Step 1:** Open PowerShell: press the Windows key, type `PowerShell`, and press Enter. Then type the line below and press Enter.

```powershell
fieldkit --version
```
  - Look for: Pass: one line starting `fieldkit` and a version number. Fail: `The term 'fieldkit' is not recognized`, which means Fieldkit is not installed for this account.
**Step 2:** Ask Fieldkit what it sees about your computer.

```powershell
fieldkit host
```
  - Look for: Pass: a short block between curly brackets with lines such as `"system": "Windows"`, `release`, `machine`, `python` and `cpus`, matching your computer. Fail: an error message, or a system name that is wrong.
**Step 3:** See the shipped pipelines without running any of them.

```powershell
fieldkit pipeline list
```
  - Look for: Pass: three lines, `debian-kernel`, `firefox-windows` and `office-deliver`, each followed by its stage names joined by `>`. Fail: an error, or names you do not recognise.
**Step 4:** Prove the privacy scan catches a planted secret. This makes a test file on your desktop holding the first line of a private key block (a marker only, not a real key; PowerShell joins the two halves, so this guide does not trip the scan itself), scans it, then deletes it. Type each line and press Enter after each.

```powershell
Set-Content "$HOME\Desktop\fk-test.txt" ("-----BEGIN " + "PRIVATE KEY-----")
fieldkit privacy scan "$HOME\Desktop\fk-test.txt"
Remove-Item "$HOME\Desktop\fk-test.txt"
```
  - Look for: Pass: the scan prints the file name, then a line with `line 1`, the word `private-key` and a shortened excerpt such as `----…--`, then `1 finding(s) in 1 file(s)`. Fail: `0 finding(s) in 0 file(s)`, which means the scan missed a known secret pattern.
**Step 5:** Prove the scan tells you when it could not read something. This makes a test file on your desktop of 6,000,000 letters (not measured: a size chosen for this test, larger than the 5,000,000-byte limit), scans it, then deletes it. Type each line and press Enter after each.

```powershell
Set-Content "$HOME\Desktop\fk-big.txt" ("x" * 6000000)
fieldkit privacy scan "$HOME\Desktop\fk-big.txt"
Remove-Item "$HOME\Desktop\fk-big.txt"
```
  - Look for: Pass: the file name, then a line with `line 0`, the word `not-scanned` and `not scanned: too large (6,000,002 bytes > 5,000,000)` (not measured: PowerShell adds two end-of-line characters, so the size may differ slightly on your computer), then `1 finding(s) in 1 file(s)`. Fail: `0 finding(s) in 0 file(s)`, which would mean a file it never read was reported as clean.
**Step 6:** If you have the Fieldkit folder and Python's test tool `pytest`, run the group's own tests from inside the Fieldkit folder, including the tests written for today's security fixes.

```powershell
python -m pytest tests/test_core.py tests/test_pipeline.py tests/test_next.py tests/test_snapshot.py tests/test_proc_security.py tests/test_snapshot_security.py tests/test_office_security.py
```
  - Look for: Pass: a last line ending in `passed` with no `failed`. Fail: any line with `FAILED`. The number of tests in these files is not measured; the measured full suite, from before today's fixes, is 584 passed, 1 skipped, 2 xfailed. Among the tests are `test_stop_refuses_a_reused_pid`, `test_timeout_stops_the_grandchild_too` and `test_diff_refuses_a_part_not_captured_on_either_side`, which check the new protections.

## The Big Picture

Fieldkit is a collection of tools you drive with the `fieldkit` command. The `core` group is the part every other Fieldkit tool leans on. It holds eight small parts. Each one does one job for you.

Four parts answer you directly. `fieldkit pipeline` runs a long job, such as a browser or kernel build, as a numbered list of stages and remembers which stages already passed. `fieldkit next` reads that memory and tells you the one next thing to do. `fieldkit privacy scan` reads your files, including the files packed inside archives, Office files and PDF files, and lists anything that must never be published: passwords and access keys, your home folder path, email addresses and words you have marked as private. When it cannot read something, it says so in the same list, so a file it could not open never looks clean. `fieldkit snapshot` writes down the state of a folder or of your computer, and later tells you exactly what is different. When your computer did not answer one of its questions, it writes that down too and refuses to compare that part.

Four parts work behind those commands. `host` finds out which computer you are on and where your programs live. `checks` tests that you have enough free disk space and the programs a job needs. `settings` reads settings files and fills in your own folder locations, so shared files never carry your name. `proc` starts other programs, keeps a written log of what they printed, and remembers each program by its number and the exact moment it started. It refuses to stop any program that Fieldkit did not start itself, and any program that has since been given a number Fieldkit once used.

## Key Concepts

| Name | What It Means | Real-World Comparison |
|------|--------------|------------------------|
| `pipeline` | A job written as an ordered list of stages, kept in a settings file. Each stage has a command to run and, ideally, a check that proves the stage worked. | A recipe card where you tick each step only after you taste the result, not after you set the timer. |
| `stage` | One step of a pipeline, with a short name such as `check`, `scrub` or `build`. | One line of the recipe. |
| `verify` | The proof attached to a stage: a file that must exist, text a file must contain, or words the stage must (or must not) print. A stage counts as `done` only when its proof passes. | Checking that the cake actually rose, instead of trusting that the oven was on. |
| `fingerprint` | A short code worked out from a stage's full definition. If anyone edits the stage, the code changes and Fieldkit runs the stage again. | A seal on a jar: a broken seal tells you someone opened it. |
| `state` | The pipeline's memory file, `state.json`, which records each stage's last result. | The ticks on your recipe card. |
| `snapshot` | A saved record, kept as a file under `state/snapshots/`, of file contents and, if you ask, the services, scheduled tasks, installed programs and running programs on your computer. Its name may use only letters, digits, full stops, underscores and hyphens. | A photo of a room before a builder starts, so you can compare it with the room afterwards. |
| `private terms` | Your own list of words to hunt for, such as your real name, kept in `fieldkit.local.json` under `privacy` then `terms`. Fieldkit never publishes that file. | A list of things you do not want to see in a newspaper, kept in your own drawer. |
| `exit code` | A number a command leaves behind for other programs to read. In this group, 0 means clean or finished, and 3 means findings, changes, something that could not be checked, or a stop. | A traffic light left on after the driver goes home. |
| `not-scanned finding` | A line in the privacy report that says a file, a folder, a part of an archive or a part of a PDF could not be read, and why. It counts as a finding, so the scan does not end clean. | A customs officer who writes "locked case, not opened" on the form instead of waving the case through as empty. |
| `fail closed` | The rule that when Fieldkit cannot check something, it treats it as a problem to report, never as a pass. | A door that stays locked when the power fails, and does not swing open. |
| `process creation time` | The exact moment a running program started. Fieldkit stores it next to the program's number (PID), because the computer hands old numbers out again to new programs. | A cloakroom ticket that shows both the number and the date: ticket 42 from last week does not fetch tonight's coat. |
| `process tree` | A program together with every program it started, and every program those started in turn. | A family tree: stopping a branch means the parent and all the children, not only the parent. |
| `not captured` | What a snapshot stores for a part, such as the list of services, when your computer did not answer in time, could not run the question, or answered with something unreadable. The reason is stored with it. | A photo that came out black, labelled "camera failed", not filed as a picture of an empty room. |

## How It Works — Step by Step

### Step 1: Fieldkit finds out which computer it is on

Before anything else, `host` asks the operating system for plain facts: Windows or Linux, the version, the processor type, the Python version and the number of processor cores. On Linux it also reads `/etc/os-release` to learn whether you are on Debian or Ubuntu. You can see this yourself with `fieldkit host`. It is like reading the label on a fuse box before you touch the wiring.

When a tool asks for a program, `host` looks on your normal program path first. On Windows it then tries a short list of known install places, for example `C:\Program Files\LibreOffice\program\soffice.exe` for LibreOffice and `C:\Program Files\Git\bin\bash.exe` for Git's bash.

### Step 2: Settings are read and your own folders are filled in

Pipelines and profiles are plain text files in JSON, YAML or TOML format. They must not contain your personal folder path, so they use placeholders instead: `${HOME}` for your home folder, `${DOCUMENTS}` for your Documents folder, `${FIELDKIT}` for the Fieldkit folder, `${ENV:NAME}` for a value from your computer's environment, and `${LOCAL:key}` for a value from your private `fieldkit.local.json` file. `settings` swaps each placeholder for the real value on your computer only. It is like a form letter: the shared template says "Dear [name]" and your copy says your name.

If a placeholder cannot be filled, a real run stops with the message `cannot expand ${...}`. A plan or dry run leaves the placeholder as it is instead.

### Step 3: The pipeline reads its stage list

`fieldkit pipeline run office-deliver` opens `office-deliver.yaml` from Fieldkit's pipeline folder. Three pipelines ship with Fieldkit: `debian-kernel` (eight stages), `firefox-windows` (ten stages) and `office-deliver` (three stages: `check`, `scrub`, `privacy`). Before running anything, it refuses a file that has no name, no stages, a stage without a unique `id`, or a stage without a `run` command. Values you pass with `--var`, such as `--var file=report.docx`, fill the `{file}` gaps in the stage list.

### Step 4: Each stage is skipped, run, or blocked

For each stage in order, the engine asks three questions.

1. Is this stage meant for this computer? A stage marked for `debian` cannot run on Windows. It is reported as `not-this-platform`, and the run stops there unless the stage says `optional: true`.
2. Did it pass before, with exactly the same definition, and does its proof still pass now? If yes, it is marked `up-to-date` and skipped. If you deleted the file the stage made, the proof fails and the stage runs again.
3. Otherwise it runs. A stage runs either a program (`cmd`) or a piece of Python code inside Fieldkit (`python`).

This is why a failure late in a multi-hour build never means redoing the early stages.

### Step 5: A program is started, logged and remembered by number and start moment

`proc` starts the program. Everything it prints goes to a log file named with the date, time and stage, as well as back to the pipeline. Before starting it, `proc` removes eight environment variables that coding assistants set. The source explains why: a build tool hid its own error messages when it saw them, which cost a Firefox build its real error message.

`proc` writes two facts about the program to `started-pids.json`: its process number (PID) and the exact moment the computer says it started. The number alone is not enough, because Windows and Linux reuse numbers: after the program ends, the same number can be given to a program you opened yourself. Think of a cloakroom: the ticket number is only proof when the date on it matches too.

If a stage has a `timeout` and the program runs too long, `proc` stops the program and every program it started (its process tree), and nothing outside that tree. It records exit code 124 and the note `timed out after N s; it and its child processes were stopped`. If you press Ctrl+C while a stage runs, the same tree is stopped before Fieldkit exits, so no half-finished build keeps running in the background. If the program does not exist, the result is exit code 127. When the program finishes, its entry is taken off the list.

### Step 6: The proof is checked, not the exit code

A program can finish with "success" and still produce nothing. So the stage counts as `done` only when its `verify` checks pass. A stage that ran without error but has no checks is marked `ran-unverified`, and the engine runs it again next time. A stage that fails is marked `failed`, and the run stops. Any later stage marked `always: true` still runs, for clean-up such as putting the power scheme back after a build.

If the failed stage names a `triage` set, Fieldkit reads its log and looks for known error patterns, with a known cause and fix.

### Step 7: A report is written

Every run writes `last-report.json` next to the pipeline memory. On screen you see one line per stage, its status and its check results, and then either `OK` or `STOPPED at <stage>`. The command leaves exit code 0 when everything passed and 3 when it stopped.

### Step 8: `fieldkit next` tells you the one next thing

`fieldkit next office-deliver` never runs a stage. It only reads the memory and the last report, and prints exactly one of four answers:

```text
DO: fieldkit pipeline run office-deliver --only check
   why: stage 'check' - never run
NEXT: run that command, then ask `fieldkit next` again.
```

or `BLOCKED: stage '<name>' failed: <cause> Fix: <fix>` followed by `CHOOSE:` and three numbered options, or `CANNOT HERE: stage '<name>' needs debian; this is Windows.` with two options, or `DONE: all 3 stages verified.` It is like a satnav that only ever shows the next turn.

### Step 9: The privacy scan reads every line, and reports what it could not read

`fieldkit privacy scan <path>` reads each file line by line and matches it against fixed patterns:

- Eight secret patterns: GitHub tokens (`ghp_`, `gho_`, `ghu_`, `ghs_`, `ghr_` and `github_pat_`), two AI-provider API key formats (`sk-` and `sk-ant-`), Google API keys (`AIza`), AWS access keys (`AKIA`), private key blocks, and a user name or token written inside a web address.
- Personal details: Windows user folder paths, Linux home folder paths and email addresses.
- Your private words from `fieldkit.local.json`, in the text and in file names. Words of two characters or fewer are ignored.

It ignores placeholder folder names such as `you`, `me`, `user`, `username` and `example`, and addresses such as `noreply` or `@example.com`. With `--git`, it checks only the files git would publish.

Packed files are unpacked first. A `.zip`, `.docx`, `.xlsx` or `.pptx` file is opened and every file inside it is read, and an archive inside it is opened in turn, up to three levels deep. A PDF keeps much of its text in compressed parts called streams. The scan unpacks the three common kinds of packing (FlateDecode, ASCIIHex and ASCII85), then also asks a PDF library, pypdfium2, for the text a reader would show. Picture data inside a PDF is not unpacked, because pixels hold no text.

Whatever it cannot read becomes a `not-scanned` line in the report, with the reason: a file or packed file larger than 5,000,000 bytes, a file or folder it is not allowed to open, a locked (encrypted) or damaged part of an archive, an archive packed more than three levels deep, or a PDF part packed in a way it cannot undo. It is like a customs officer who writes "locked case, not opened" on the form: the form is honest about what was not checked.

### Step 10: Snapshots record, then compare

`fieldkit snapshot take before --path <folder>` first checks the name: only letters, digits, `.`, `_` and `-` are allowed, so a name can never point into another folder. It then walks the folder and records each file's size, date and a SHA-256 content code (only for files of 64 MB or less). With `--services`, `--tasks`, `--programs` or `--processes`, it also asks Windows (through PowerShell, `tasklist` and the registry) or Linux (through `systemctl`, `dpkg-query` and `ps`) for those lists. On Linux it records, for each service, whether it is running and whether it is set to start by itself. Taking a snapshot changes nothing on your computer except writing the snapshot file.

Each question may take up to 180 seconds. If the computer does not answer in time, cannot run the question, or answers with something unreadable, that part is stored as "not captured" with the reason, instead of as an empty list.

Later, `fieldkit snapshot diff before after` lists what was added (`+`), removed (`-`) and changed (`~`). For files, the content code decides: a file whose date changed but whose content is the same is not reported. A part that was not captured in either snapshot is not compared at all. You see `NOT COMPARED - not captured` and the reason, never a long list of things that look removed.

## Quirky Things Worth Knowing

### Exit code 3 does not always mean an error

`fieldkit snapshot diff` leaves exit code 3 when it finds changes or a part it could not compare, and `fieldkit privacy scan` leaves 3 when it finds anything, including a `not-scanned` line. That is the answer you asked for, not a crash. A pipeline that stops and a `next` answer of BLOCKED or CANNOT HERE also leave 3.

### "Nothing found" now also means "nothing left unread", with three exceptions

A file the scan could not read is listed as `not-scanned` with `line 0` and the reason, for example `not scanned: too large (6,000,002 bytes > 5,000,000)` (not measured: one test file made while writing this guide). So a clean result means every file it looked at was read. Three things are still left out without a line in the report. First, anything inside `.git`, `node_modules`, `__pycache__`, `.venv`, `venv` and `.pytest_cache`, unless you use `--git`. Second, the words inside pictures. Third, the words inside packed files whose type is not on its archive list, such as `.odt` or `.epub`: their raw packed bytes are read, which proves little. If you name a path that does not exist, it stops with the message `nothing scanned is not the same as nothing found`.

### One marker silences a line

Any line containing the text `privacy-scan: allow` is not checked at all. It exists for test files that hold deliberate fake secrets. It also means a real secret on that same line passes unreported. Only that one line is silenced; the test `test_privacy_allow_marker_only_silences_its_own_line` checks this.

### Your folder name in the location does not count

When you scan a folder, private words are matched against file names inside that folder only. If the folder itself sits under your home folder, that part is not reported, because it is not published with the files.

### A stage without proof runs every time

If a stage has no `verify` checks, Fieldkit cannot prove it worked. It marks it `ran-unverified` and runs it again on the next run. Stages proved only by what they printed also re-run, because there is no printed output to re-check later.

### Editing a stage makes it run again

Fieldkit keeps a fingerprint of each stage's full definition. Change one word in a stage, or pass a different `--var` value, and that stage is no longer up to date. `fieldkit next` then says `its definition changed since it last ran`.

### Fieldkit will not stop a program it did not start, or one that took its old number

If you run the same program yourself, stopping it by name would close your copy too. `proc` refuses any process number not in its own `started-pids.json` list, with the message `PID <n> was not started by fieldkit; refusing to stop it`. It also refuses a number that is on its list but now belongs to a different program, because the start moment no longer matches, with the message `PID <n> is now a different process (creation time differs); refusing to stop it`. If its own program has already ended, it does nothing and takes the entry off the list. When it does stop a program, it stops that program and every program that program started, on Windows and on Linux.

### Linux parts are written but not measured here

The measured test run (584 passed, 1 skipped, 2 xfailed) ran on a Windows 11 laptop, before today's security fixes. The Linux branches of `snapshot` (services, timers, `dpkg` programs) and of `proc` (reading start moments from `/proc`, stopping a process group) only run on Linux. The new tests feed the snapshot code pretend `systemctl` answers. Whether these parts work on a real Linux computer is not measured.

### A PDF can show a `not-scanned` line although nothing is wrong with it

Some PDFs pack text in ways the scan cannot undo, such as LZW packing. The scan then reports that part as `not-scanned` with the name of the packing, and does not guess. If the PDF library pypdfium2 is missing or cannot open the file, every PDF gets the line `text layer could not be read`. Both make the scan end with exit code 3. Open the PDF, read it yourself, and decide.

### A file that ends in `.zip` but is not a real archive is read as ordinary text

The scan first checks that an archive really is one. A file named `.zip` that is not an archive is read line by line like any other file, and no `not-scanned` line appears for it.

### What this cannot do

The privacy scan cannot find phone numbers, street addresses, bank details or names you did not list, and it cannot read words inside pictures or scanned pages. A line marked `privacy-scan: allow` is never checked. The pipeline engine does not sandbox stages and does not check that a pipeline file is safe before running it. `fieldkit snapshot take --path` leaves out files it cannot open without saying so. `proc` never stops a program it did not start, even one that is stuck; you have to close that yourself. The start-moment check is only as good as the answer Windows or Linux gives: if the computer will not say when a program started, Fieldkit treats the program as gone and refuses to stop it. None of the Linux branches is measured on Linux. Treat a clean result as "no known pattern matched and nothing was left unread", not as "safe to publish".

## What This Means For You

### Battery, Processor & Memory

Not measured. The heaviest work in this group is computing content codes for every file under 64 MB during `fieldkit snapshot take --path`, and reading every file during `fieldkit privacy scan`, which now also unpacks PDF parts and archives inside archives. Each unpacked part is cut off at 5,000,000 bytes, which keeps memory use bounded. The programs a pipeline starts, such as a compiler, use far more than Fieldkit itself, but that use is not measured either.

### Speed

Not measured. The source sets three time limits: each PowerShell or Linux query in a snapshot may take up to 180 seconds; a Linux question about one service's start setting may take up to 30 seconds; and after a stage times out, Fieldkit waits up to 30 seconds for the stopped program to let go of its output. Pipeline stages may set their own `timeout`.

### Your Privacy

This group exists to protect your privacy before you publish. It does not send anything anywhere. It does write files that hold personal details: snapshot files list full file paths, and log files keep everything a program printed. Those files live under `state/`, which git ignores, but they are ordinary files on your disk.

### Your Internet

None from this group. No part of it opens a network connection. A pipeline stage can download things if the program it runs does so, for example a stage that fetches source code.

## The Off Switch

**What it is:** There is no single off switch. You have four ways to hold back the engine: `fieldkit pipeline run <name> --dry-run` shows what would run without running it; `fieldkit pipeline plan <name>` lists every stage and whether it runs on this computer, without running anything; `fieldkit next <name>` only reads and never runs; and pressing Ctrl+C in the PowerShell window stops the running stage together with every program it started. A stage's own `timeout` stops a program, and its whole process tree, when it runs too long.

**Without it:** Without `--dry-run` and `plan`, the first time you saw what a pipeline does would be when it did it. Without the start-list rule and the start-moment check in `proc`, Fieldkit could close programs you were using yourself, including one that happened to receive a number Fieldkit used earlier. Without stopping the whole tree, a timed-out build could leave its helper programs running in the background.

**Think of it like:** Reading the whole recipe before you turn the oven on, and a kitchen timer that turns the oven off on its own.

## How to use this

**Before you start:**
- Fieldkit installed, so that `fieldkit --version` prints a version (see the check above).
- PowerShell open (Windows key, type `PowerShell`, press Enter).
- For the pipeline steps: the file you want to work on, for example a Word document.

**Step 1:** Scan a folder for secrets and personal details before you share it. This example scans your Desktop.

```powershell
fieldkit privacy scan "$HOME\Desktop"
```
  - You should see: For each file with a finding, its path, then one line per finding: line number, kind (such as `email`, `github-token`, `windows-user-path` or `not-scanned`) and a shortened excerpt. A `not-scanned` line has line number 0 and says why the file was not read. Files inside archives and PDFs are named like `report.zip!notes.txt` or `letter.pdf!stream@1234`. The last line reads `N finding(s) in M file(s)`. `0 finding(s) in 0 file(s)` means nothing matched the patterns and nothing was left unread; see the limits above.

Pass: the last line is printed. Fail: a red error message instead of the report.
**Step 2:** If the folder is a git project, scan only what git would publish.

```powershell
fieldkit privacy scan "$HOME\Desktop" --git
```
  - You should see: The same report, limited to files git tracks or would add. If the folder is not a git project, you see `is not a git repository; drop --git to scan every file`.
**Step 3:** Take a "before" record of your Desktop and of your installed programs.

```powershell
fieldkit snapshot take before --path "$HOME\Desktop" --programs
```
  - You should see: A short block between curly brackets naming the new snapshot file, ending in `before.json`. Nothing else on your computer changes. If you choose a name with a space, a slash or `..`, you see `snapshot name '...' refused: use only letters, digits, '.', '_' and '-'` and nothing is written.
**Step 4:** Do the thing you want to check, such as installing or removing a program. Then take an "after" record with the same options.

```powershell
fieldkit snapshot take after --path "$HOME\Desktop" --programs
```
  - You should see: A short block naming a file ending in `after.json`.
**Step 5:** Compare the two records.

```powershell
fieldkit snapshot diff before after
```
  - You should see: Either `no changes`, or one summary line per part, for example `programs: +1 added, -0 removed, ~0 changed`, followed by up to 15 names per list marked `+`, `-` or `~`. If Windows did not answer when one snapshot was taken, you see `programs: NOT COMPARED - not captured (...)` with the reason instead; take that snapshot again.
**Step 6:** List the snapshots you have kept.

```powershell
fieldkit snapshot list
```
  - You should see: The names `after` and `before`, one per line, or `no snapshots`.
**Step 7:** Before running a pipeline, see its plan. This example uses the shipped `office-deliver` pipeline, which makes a Word, Excel, PowerPoint or PDF file safe to send.

```powershell
fieldkit pipeline plan office-deliver
```
  - You should see: One line per stage (`check`, `scrub`, `privacy`) with `here` or `NOT HERE`, the platforms, the last status and what it would run. Nothing runs.
**Step 8:** Ask what to do next.

```powershell
fieldkit next office-deliver
```
  - You should see: On a first run: `DO: fieldkit pipeline run office-deliver --only check`, a `why:` line and a `NEXT:` line.
**Step 9:** Run the pipeline on your file. Replace the path with your own document; keep the quotation marks.

```powershell
fieldkit pipeline run office-deliver --var file="$HOME\Desktop\report.docx"
```
  - You should see: One line per stage with its status (`done`, `up-to-date`, `failed` or `ran-unverified`) and its check lines, then `OK` or `STOPPED at <stage>`. The scrub stage keeps a `.bak` copy of your file.
**Step 10:** See the pipeline's memory, or make it forget one stage so that stage runs again next time.

```powershell
fieldkit pipeline status office-deliver
fieldkit pipeline reset office-deliver --stage check
```
  - You should see: `status` prints each stage's last status, fingerprint and finish time. `reset` prints a short block naming `check`. Resetting does not delete any file the stage made.

## If Something Goes Wrong

**`no pipeline 'xyz'; have: debian-kernel, firefox-windows, office-deliver`**
You typed a pipeline name that does not exist in Fieldkit's pipeline folder.
What to do: Use one of the names listed after `have:`, or give the full path to your own `.yaml` or `.json` pipeline file.

**`cannot expand ${LOCAL:...}` when you run a pipeline**
The pipeline uses a value from your private `fieldkit.local.json` file, and that value is missing.
What to do: Add the missing key to `fieldkit.local.json` in the Fieldkit folder, or run `fieldkit pipeline plan <name>` to see which value the pipeline needs. A plan never stops on this error.

**`CANNOT HERE: stage '...' needs debian; this is Windows.`**
That stage only runs on a Debian or Ubuntu computer.
What to do: Run `fieldkit pipeline run <name> --from <stage>` on a Debian computer, as option 1 says, or stop there.

**`BLOCKED: stage '...' failed` followed by `CHOOSE:` and three options**
The stage ran and its program failed, or its proof did not pass.
What to do: Read the cause and fix on the BLOCKED line. Pick one option: apply the fix and run the `--only` command, retry as it is, or stop and report the failure. The full program output is in the log file named in the run report.

**`snapshot take needs one NAME` or `say what to record: --path DIR and/or --services --tasks --programs --processes`**
You left out the snapshot name, or did not say what to record.
What to do: Give one name and at least one of `--path`, `--services`, `--tasks`, `--programs` or `--processes`.

**`snapshot diff` shows `services: NOT COMPARED - not captured (before: powershell timed out after 180 s)` or a similar line**
When that snapshot was taken, Windows (or Linux) did not answer the question in time, could not run it, or answered with something unreadable. Fieldkit stored the reason instead of an empty list, so it cannot compare that part.
What to do: Take the snapshot named after `before:` or `after:` again, ideally when the computer is less busy. Other parts in the same diff are still compared normally.

**A stage shows `ran-unverified` every time**
The stage has no `verify` checks, so Fieldkit cannot prove it worked and runs it again each time.
What to do: Ask whoever wrote the pipeline to add a check that proves the result, such as `files_exist` for the file the stage makes.

**`snapshot name '...' refused: use only letters, digits, '.', '_' and '-'`**
The name you chose contains a space, a slash, a backslash or another character that could make the snapshot file land outside its folder.
What to do: Choose a name such as `before-install` or `after_update_2`.

**The privacy scan ends with exit code 3 and every line says `not-scanned`**
Nothing secret was found, but some files or parts of files could not be read: too large, locked, damaged, or packed in a way the scan cannot undo. The reason is at the end of each line.
What to do: Open each named file yourself and check it, or remove it from what you publish. Do not treat the result as clean.

**`PID <n> is now a different process (creation time differs); refusing to stop it`**
The program Fieldkit started has ended, and the computer has given its number to another program, possibly one of yours.
What to do: Nothing to do: the program Fieldkit started is already gone, and Fieldkit has refused to touch the new one.

## Why a Developer Would Do This

Long jobs such as a browser build fail late. If every failure meant starting again, you would lose hours each time, so the engine remembers each stage and resumes. A program can report success and make nothing, so the engine insists on proof. The source says "a small model never has to plan": `fieldkit next` reduces each turn to one command or one numbered choice. Shared and published files must not carry secrets or home folder names, so the privacy scan exists, and shared settings use placeholders instead of real paths. Finally, snapshots compare the real before and after, so, in the source's words, "the model's own account is never needed".

## Why It Matters That You Can Read This

This group decides whether a job really finished and whether your files are safe to publish. Those are two claims you would otherwise take on trust. Because the source is readable, anyone can confirm that `done` requires a passed check and not only a clean exit, that the privacy scan masks what it finds and reports what it could not read, and that no part of the group connects to the internet. It also lets anyone see the limits stated in this guide: the 5,000,000-byte limit, the fixed list of eight secret patterns, the list of archive types and the `privacy-scan: allow` marker. With a closed tool, you would be trusting a "0 findings" message without knowing what it looked for or what it skipped.

## Glossary

**PowerShell** — The Windows window where you type commands; you open it from the Start menu.

**Command** — A line of text you type and confirm with Enter, such as `fieldkit host`.

**PID** — The number Windows or Linux gives each running program so it can be told apart from the others.

**Log file** — A text file holding everything a program printed while it ran.

**SHA-256** — A long code worked out from a file's exact contents; any change to the contents changes the code.

**Git** — A program that tracks versions of files and publishes them to sites such as GitHub.

**`.gitignore`** — A list of files and folders that git must never publish.

**Token** — A long secret string that works like a password for a website or service.

**YAML, JSON, TOML** — Three plain-text formats for writing settings that both people and programs can read.

**Debian** — A family of Linux systems that includes Ubuntu.

**PDF stream** — A packed block inside a PDF file that can hold page text, pictures or hidden details such as the author's name.

**Encrypted** — Locked with a password so that it cannot be read without that password.

**Ctrl+C** — Holding the Ctrl key and pressing C in a PowerShell window, which asks the running command to stop.

## Claim Sources

| Claim | Basis | Evidence |
|-------|-------|----------|
| No part of the group opens a network connection | 🤖 model inference | *(none — model judgment)* |
| A stage is done only when its checks pass | 📄 stated in input | a stage is only "done" when its verify checks pass |
| A stage without checks re-runs every time | 📄 stated in input | A stage with no verify is re-run every time. |
| next never runs a stage | 📄 stated in input | it never runs a stage |
| Fieldkit refuses to stop programs it did not start or whose number was reused | 📄 stated in input | the number was reused by somebody else's program |
| Assistant environment variables hid a build's real error | 📄 stated in input | which cost a Firefox build its real error message |
| Privacy findings are masked and safe to paste | 📄 stated in input | the scan report is itself safe to paste |
| Files over the size limit and unreadable files are reported, not skipped | 📄 stated in input | is itself a finding ("not-scanned"), |
| Archives and Office files are scanned member by member | 📄 stated in input | scanning their bytes proves nothing, so scan every member instead |
| A missing path is an error, not a clean result | 📄 stated in input | nothing scanned is not the same as nothing found |
| Taking a snapshot changes nothing else | 📄 stated in input | Read-only: taking one changes nothing. |
| Content decides file changes, not timestamps | 📄 stated in input | content, not timestamps, decides |
| A failed snapshot query is not compared and cannot look like everything was removed | 📄 stated in input | so a failed query can never look like everything was removed |
| A pipeline file from a stranger can run any program | 🤖 model inference | *(none — model judgment)* |
| The privacy scan misses phone numbers, addresses and unlisted names | 🤖 model inference | *(none — model judgment)* |
| Linux snapshot branches are not exercised by the measured Windows test run | 🤖 model inference | *(none — model judgment)* |
| Measured test suite result | 📄 stated in input | 584 passed, 1 skipped, 2 xfailed in 194.89 s |
| Snapshot and log files can hold personal paths and printed output | 🤖 model inference | *(none — model judgment)* |
| A timeout stops the whole process tree and nothing outside it | 📄 stated in input | A timeout stops the whole tree the runner started |
| PDF streams are decompressed and the text layer is read | 📄 stated in input | PDF streams are decompressed |
| Archives nested deeper than three levels are reported | 📄 stated in input | deeper is reported, not skipped |
| Snapshot names are limited to safe characters | 📄 stated in input | (no folders, no '..') |
| Picture data in PDFs is not decoded | 📄 stated in input | pixels hold no text |
| OpenDocument and EPUB files are read only as raw bytes because they are not on the archive list | 🤖 model inference | *(none — model judgment)* |
| snapshot take leaves out unreadable files without notice | 🤖 model inference | *(none — model judgment)* |
| Ctrl+C during a stage stops the stage's process tree | 🤖 model inference | *(none — model judgment)* |


---
**How to verify this document:**
`📄 stated in input` — the model's phrasing of something your source text said.
Find the matching line in the original to verify.
`🤖 model inference` — the model's own judgment or synthesis. Treat as opinion,
not measurement. Re-run on the same input and check whether specific numbers
stay consistent between runs.

*Human Track. Its Developer Track twin covers the same changes in technical detail. Neither is a simplified copy of the other — they are the same truth in two languages.*